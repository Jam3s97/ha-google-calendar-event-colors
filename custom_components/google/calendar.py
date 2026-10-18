"""Support for Google Calendar Search binary sensors."""

from collections.abc import Mapping
import dataclasses
from datetime import datetime, timedelta
import logging
from typing import Any, cast, override

from gcal_sync.api import Range, SyncEventsRequest
from gcal_sync.exceptions import ApiException
from gcal_sync.model import (
    AccessRole,
    Calendar,
    DateOrDatetime,
    Event,
    EventTypeEnum,
    ResponseStatus,
)
from gcal_sync.store import ScopedCalendarStore
from gcal_sync.sync import CalendarEventSyncManager

from homeassistant.components.calendar import (
    CREATE_EVENT_SCHEMA,
    ENTITY_ID_FORMAT,
    EVENT_DESCRIPTION,
    EVENT_END,
    EVENT_LOCATION,
    EVENT_RRULE,
    EVENT_START,
    EVENT_SUMMARY,
    CalendarEntity,
    CalendarEntityDescription,
    CalendarEntityFeature,
    CalendarEvent,
    CalendarEventStatus,
    extract_offset,
    is_offset_reached,
)
from homeassistant.const import CONF_DEVICE_ID, CONF_ENTITIES, CONF_NAME, CONF_OFFSET
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.exceptions import HomeAssistantError, PlatformNotReady
from homeassistant.helpers import entity_platform, entity_registry as er
from homeassistant.helpers.entity import generate_entity_id
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from . import (
    CONF_IGNORE_AVAILABILITY,
    CONF_SEARCH,
    CONF_TRACK,
    DEFAULT_CONF_OFFSET,
    YAML_DEVICES,
    get_calendar_info,
    load_config,
    update_config,
)
from .api import get_feature_access
from .colours import OTHER, ColourLabel, ColourSplit
from .const import (
    CONF_SPLIT_CALENDARS,
    EVENT_END_DATE,
    EVENT_END_DATETIME,
    EVENT_IN,
    EVENT_IN_DAYS,
    EVENT_IN_WEEKS,
    EVENT_START_DATE,
    EVENT_START_DATETIME,
    FeatureAccess,
)
from .coordinator import CalendarQueryUpdateCoordinator, CalendarSyncUpdateCoordinator
from .store import GoogleConfigEntry

_LOGGER = logging.getLogger(__name__)

# Avoid syncing super old data on initial syncs. Note that old but active
# recurring events are still included.
SYNC_EVENT_MIN_TIME = timedelta(days=-90)

# Window scanned for unnamed colour labels in use, which get their own entity.
COLOUR_SCAN_MAX_TIME = timedelta(days=365)
COLOUR_UNIQUE_ID_MARKER = "-colour-"

# Events have a transparency that determine whether or not they block time on calendar.
# When an event is opaque, it means "Show me as busy" which is the default.  Events that
# are not opaque are ignored by default.
OPAQUE = "opaque"

# Google calendar prefixes recurrence rules with RRULE: which
# we need to strip when working with the frontend recurrence rule values
RRULE_PREFIX = "RRULE:"

SERVICE_CREATE_EVENT = "create_event"
FILTERED_EVENT_TYPES = [EventTypeEnum.BIRTHDAY, EventTypeEnum.WORKING_LOCATION]


@dataclasses.dataclass(frozen=True, kw_only=True)
class GoogleCalendarEntityDescription(CalendarEntityDescription):
    """Google calendar entity description."""

    name: str | None
    entity_id: str | None
    read_only: bool
    ignore_availability: bool
    offset: str | None
    search: str | None
    local_sync: bool
    device_id: str
    event_type: EventTypeEnum | None = None


def _get_entity_descriptions(
    hass: HomeAssistant,
    config_entry: GoogleConfigEntry,
    calendar_item: Calendar,
    calendar_info: Mapping[str, Any],
) -> list[GoogleCalendarEntityDescription]:
    """Create entity descriptions for the calendar.

    The entity descriptions are based on the type of Calendar from the API
    and optional calendar_info yaml configuration that is the older way to
    configure calendars before they supported UI based config.

    The yaml config may map one calendar to multiple entities and they do not
    have a unique id. The yaml config also supports additional options like
    offsets or search.
    """
    calendar_id = calendar_item.id
    num_entities = len(calendar_info[CONF_ENTITIES])
    entity_descriptions = []
    for data in calendar_info[CONF_ENTITIES]:
        if num_entities > 1:
            key = ""
        else:
            key = calendar_id
        entity_enabled = data.get(CONF_TRACK, True)
        if not entity_enabled:
            _LOGGER.warning(
                "The 'track' option in google_calendars.yaml has been deprecated."
                " The setting has been imported to the UI, and should now be"
                " removed from google_calendars.yaml"
            )
        read_only = not (
            calendar_item.access_role.is_writer
            and get_feature_access(config_entry) is FeatureAccess.read_write
        )
        # Prefer calendar sync down of resources when possible. However,
        # sync does not work for search. Also free-busy calendars denormalize
        # recurring events as individual events which is not efficient for sync
        local_sync = True
        if (
            search := data.get(CONF_SEARCH)
        ) or calendar_item.access_role == AccessRole.FREE_BUSY_READER:
            read_only = True
            local_sync = False
        entity_description = GoogleCalendarEntityDescription(
            key=key,
            name=data[CONF_NAME].capitalize(),
            entity_id=generate_entity_id(
                ENTITY_ID_FORMAT, data[CONF_DEVICE_ID], hass=hass
            ),
            read_only=read_only,
            ignore_availability=data.get(CONF_IGNORE_AVAILABILITY, False),
            offset=data.get(CONF_OFFSET, DEFAULT_CONF_OFFSET),
            search=search,
            local_sync=local_sync,
            entity_registry_enabled_default=entity_enabled,
            device_id=data[CONF_DEVICE_ID],
            initial_color=calendar_item.background_color,
        )
        entity_descriptions.append(entity_description)
        _LOGGER.debug(
            "calendar_item.primary=%s, search=%s, calendar_item.access_role=%s - %s",
            calendar_item.primary,
            search,
            calendar_item.access_role,
            local_sync,
        )
        if calendar_item.primary and local_sync:
            # Create a separate calendar for birthdays
            entity_descriptions.append(
                dataclasses.replace(
                    entity_description,
                    key=f"{key}-birthdays",
                    translation_key="birthdays",
                    event_type=EventTypeEnum.BIRTHDAY,
                    name=None,
                    entity_id=None,
                    ignore_availability=True,
                )
            )
            # Create an optional disabled by default entity for Work Location
            entity_descriptions.append(
                dataclasses.replace(
                    entity_description,
                    key=f"{key}-work-location",
                    translation_key="working_location",
                    event_type=EventTypeEnum.WORKING_LOCATION,
                    name=None,
                    entity_id=None,
                    entity_registry_enabled_default=False,
                    ignore_availability=True,
                )
            )
    return entity_descriptions


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: GoogleConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the google calendar platform."""
    calendar_service = config_entry.runtime_data.service
    store = config_entry.runtime_data.store
    try:
        result = await calendar_service.async_list_calendars()
    except ApiException as err:
        raise PlatformNotReady(str(err)) from err

    entity_registry = er.async_get(hass)
    registry_entries = er.async_entries_for_config_entry(
        entity_registry, config_entry.entry_id
    )
    entity_entry_map = {
        entity_entry.unique_id: entity_entry for entity_entry in registry_entries
    }

    # Yaml configuration may override objects from the API
    calendars = await hass.async_add_executor_job(
        load_config, hass.config.path(YAML_DEVICES)
    )
    new_calendars = []
    entities: list[GoogleCalendarEntity] = []
    split_calendar_ids = set(config_entry.options.get(CONF_SPLIT_CALENDARS, []))
    split_parent_unique_ids: set[str] = set()
    for calendar_item in result.items:
        calendar_id = calendar_item.id
        if calendars and calendar_id in calendars:
            calendar_info = calendars[calendar_id]
        else:
            calendar_info = get_calendar_info(
                hass, calendar_item.model_dump(exclude_unset=True)
            )
            new_calendars.append(calendar_info)

        for entity_description in _get_entity_descriptions(
            hass, config_entry, calendar_item, calendar_info
        ):
            unique_id = (
                f"{config_entry.unique_id}-{entity_description.key}"
                if entity_description.key
                else None
            )
            # Migrate to new unique_id format which supports
            # multiple config entries as of 2022.7
            for old_unique_id in (
                calendar_id,
                f"{calendar_id}-{entity_description.device_id}",
            ):
                if not (entity_entry := entity_entry_map.get(old_unique_id)):
                    continue
                if unique_id:
                    _LOGGER.debug(
                        "Migrating unique_id for %s from %s to %s",
                        entity_entry.entity_id,
                        old_unique_id,
                        unique_id,
                    )
                    entity_registry.async_update_entity(
                        entity_entry.entity_id, new_unique_id=unique_id
                    )
                else:
                    _LOGGER.debug(
                        "Removing entity registry entry for %s from %s",
                        entity_entry.entity_id,
                        old_unique_id,
                    )
                    entity_registry.async_remove(
                        entity_entry.entity_id,
                    )
            _LOGGER.debug("Creating entity with unique_id=%s", unique_id)
            coordinator: CalendarSyncUpdateCoordinator | CalendarQueryUpdateCoordinator
            if not entity_description.local_sync:
                coordinator = CalendarQueryUpdateCoordinator(
                    hass,
                    config_entry,
                    calendar_service,
                    entity_description.name or entity_description.key,
                    calendar_id,
                    entity_description.search,
                )
            else:
                request_template = SyncEventsRequest(
                    calendar_id=calendar_id,
                    start_time=dt_util.now() + SYNC_EVENT_MIN_TIME,
                )
                sync = CalendarEventSyncManager(
                    calendar_service,
                    store=ScopedCalendarStore(
                        store, unique_id or entity_description.device_id
                    ),
                    request_template=request_template,
                )
                coordinator = CalendarSyncUpdateCoordinator(
                    hass,
                    config_entry,
                    sync,
                    entity_description.name or entity_description.key,
                )
            entities.append(
                GoogleCalendarEntity(
                    coordinator,
                    calendar_id,
                    entity_description,
                    unique_id,
                )
            )
            if (
                calendar_id in split_calendar_ids
                and unique_id
                and entity_description.event_type is None
                and isinstance(coordinator, CalendarSyncUpdateCoordinator)
            ):
                split_parent_unique_ids.add(unique_id)
                entities.extend(
                    await _async_colour_entities(
                        hass,
                        config_entry,
                        calendar_item,
                        coordinator,
                        entity_description,
                        unique_id,
                        async_add_entities,
                    )
                )

    # Remove colour entities of calendars that are no longer split
    for entity_entry in registry_entries:
        parent_unique_id, marker, _ = entity_entry.unique_id.rpartition(
            COLOUR_UNIQUE_ID_MARKER
        )
        if marker and parent_unique_id not in split_parent_unique_ids:
            entity_registry.async_remove(entity_entry.entity_id)

    async_add_entities(entities)

    if calendars and new_calendars:

        def append_calendars_to_config() -> None:
            path = hass.config.path(YAML_DEVICES)
            for calendar in new_calendars:
                update_config(path, calendar)

        await hass.async_add_executor_job(append_calendars_to_config)

    platform = entity_platform.async_get_current_platform()
    if (
        any(calendar_item.access_role.is_writer for calendar_item in result.items)
        and get_feature_access(config_entry) is FeatureAccess.read_write
    ):
        platform.async_register_entity_service(
            SERVICE_CREATE_EVENT,
            CREATE_EVENT_SCHEMA,
            async_create_event,
            required_features=CalendarEntityFeature.CREATE_EVENT,
        )


class GoogleCalendarEntity(
    CoordinatorEntity[CalendarSyncUpdateCoordinator | CalendarQueryUpdateCoordinator],
    CalendarEntity,
):
    """A calendar event entity."""

    entity_description: GoogleCalendarEntityDescription
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: CalendarSyncUpdateCoordinator | CalendarQueryUpdateCoordinator,
        calendar_id: str,
        entity_description: GoogleCalendarEntityDescription,
        unique_id: str | None,
    ) -> None:
        """Create the Calendar event device."""
        super().__init__(coordinator)
        _LOGGER.debug("entity_description.entity_id=%s", entity_description.entity_id)
        _LOGGER.debug("entity_description=%s", entity_description)
        self.calendar_id = calendar_id
        self.entity_description = entity_description
        self._ignore_availability = entity_description.ignore_availability
        self._offset = entity_description.offset
        self._event: CalendarEvent | None = None
        if entity_description.entity_id:
            self.entity_id = entity_description.entity_id
        self._attr_unique_id = unique_id
        if not entity_description.read_only:
            self._attr_supported_features = (
                CalendarEntityFeature.CREATE_EVENT | CalendarEntityFeature.DELETE_EVENT
            )

    @property
    @override
    def extra_state_attributes(self) -> dict[str, bool]:
        """Return the device state attributes."""
        return {"offset_reached": self.offset_reached}

    @property
    def offset_reached(self) -> bool:
        """Return whether or not the event offset was reached."""
        (event, offset_value) = self._event_with_offset()
        if event is not None and offset_value is not None:
            return is_offset_reached(event.start_datetime_local, offset_value)
        return False

    @property
    @override
    def event(self) -> CalendarEvent | None:
        """Return the next upcoming event."""
        (event, _) = self._event_with_offset()
        return event

    @property
    def new_event_label_id(self) -> str | None:
        """Return the colour label for events created here, or None for no colour."""
        return None

    def _event_filter(self, event: Event) -> bool:
        """Return True if the event is visible and not declined."""

        if any(
            attendee.is_self and attendee.response_status == ResponseStatus.DECLINED
            for attendee in event.attendees
        ):
            return False
        # Calendar enttiy may be limited to a specific event type
        if (
            self.entity_description.event_type is not None
            and self.entity_description.event_type != event.event_type
        ):
            return False
        # Default calendar entity omits the special types but includes all the others
        if (
            self.entity_description.event_type is None
            and event.event_type in FILTERED_EVENT_TYPES
        ):
            return False
        if self._ignore_availability:
            return True
        return event.transparency == OPAQUE

    @override
    async def async_added_to_hass(self) -> None:
        """When entity is added to hass."""
        await super().async_added_to_hass()

        # We do not ask for an update with async_add_entities()
        # because it will update disabled entities. This is started as a
        # task to let if sync in the background without blocking startup
        self.coordinator.config_entry.async_create_background_task(
            self.hass,
            self.coordinator.async_request_refresh(),
            "google.calendar-refresh",
        )

    @override
    async def async_get_events(
        self, hass: HomeAssistant, start_date: datetime, end_date: datetime
    ) -> list[CalendarEvent]:
        """Get all events in a specific time frame."""
        result_items = await self.coordinator.async_get_events(start_date, end_date)
        return [
            _get_calendar_event(event)
            for event in filter(self._event_filter, result_items)
        ]

    def _event_with_offset(
        self,
    ) -> tuple[CalendarEvent | None, timedelta | None]:
        """Get the calendar event and offset if any."""
        if api_event := next(
            filter(
                self._event_filter,
                self.coordinator.upcoming or [],
            ),
            None,
        ):
            event = _get_calendar_event(api_event)
            if self._offset:
                (event.summary, offset_value) = extract_offset(
                    event.summary, self._offset
                )
            return event, offset_value
        return None, None

    @override
    async def async_create_event(self, **kwargs: Any) -> None:
        """Add a new event to calendar."""
        dtstart = kwargs[EVENT_START]
        dtend = kwargs[EVENT_END]
        start: DateOrDatetime
        end: DateOrDatetime
        if isinstance(dtstart, datetime):
            start = DateOrDatetime(
                date_time=dt_util.as_local(dtstart),
                timezone=str(dt_util.get_default_time_zone()),
            )
            end = DateOrDatetime(
                date_time=dt_util.as_local(dtend),
                timezone=str(dt_util.get_default_time_zone()),
            )
        else:
            start = DateOrDatetime(date=dtstart)
            end = DateOrDatetime(date=dtend)
        event = Event.model_validate(
            {
                EVENT_SUMMARY: kwargs[EVENT_SUMMARY],
                "start": start,
                "end": end,
                EVENT_DESCRIPTION: kwargs.get(EVENT_DESCRIPTION),
            }
        )
        if location := kwargs.get(EVENT_LOCATION):
            event.location = location
        if rrule := kwargs.get(EVENT_RRULE):
            event.recurrence = [f"{RRULE_PREFIX}{rrule}"]
        if label_id := self.new_event_label_id:
            event.event_label_id = label_id

        try:
            await cast(
                CalendarSyncUpdateCoordinator, self.coordinator
            ).sync.store_service.async_add_event(event)
        except ApiException as err:
            raise HomeAssistantError(f"Error while creating event: {err!s}") from err
        await self.coordinator.async_refresh()

    @override
    async def async_delete_event(
        self,
        uid: str,
        recurrence_id: str | None = None,
        recurrence_range: str | None = None,
    ) -> None:
        """Delete an event on the calendar."""
        range_value: Range = Range.NONE
        if recurrence_range == Range.THIS_AND_FUTURE:
            range_value = Range.THIS_AND_FUTURE
        await cast(
            CalendarSyncUpdateCoordinator, self.coordinator
        ).sync.store_service.async_delete_event(
            ical_uuid=uid,
            event_id=recurrence_id,
            recurrence_range=range_value,
        )
        await self.coordinator.async_refresh()


class GoogleCalendarColourEntity(GoogleCalendarEntity):
    """A virtual calendar with the events of one colour label of a calendar."""

    def __init__(
        self,
        coordinator: CalendarSyncUpdateCoordinator,
        calendar_id: str,
        entity_description: GoogleCalendarEntityDescription,
        unique_id: str,
        split: ColourSplit,
        bucket: str,
    ) -> None:
        """Create the colour calendar, sharing the real calendar's coordinator."""
        super().__init__(coordinator, calendar_id, entity_description, unique_id)
        self._split = split
        self.bucket = bucket

    @override
    def _event_filter(self, event: Event) -> bool:
        """Return True if the event is visible and has this entity's colour."""
        return super()._event_filter(event) and self._split.bucket(event) == self.bucket

    @property
    @override
    def new_event_label_id(self) -> str | None:
        """Return this entity's label, or None where no colour lands here.

        Events with no colour follow the calendar's default colour, so the
        default colour's entity and Other write no label.
        """
        default_label = self._split.default_label
        if self.bucket == OTHER or (default_label and self.bucket == default_label.id):
            return None
        return self.bucket


async def _async_colour_entities(
    hass: HomeAssistant,
    config_entry: GoogleConfigEntry,
    calendar_item: Calendar,
    coordinator: CalendarSyncUpdateCoordinator,
    parent: GoogleCalendarEntityDescription,
    parent_unique_id: str,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> list[GoogleCalendarColourEntity]:
    """Create the colour entities of a calendar that is split by colour.

    Named labels and Other get an entity up front. Unnamed labels get one
    (named after the colour) once events use them. After the first refresh,
    colour entities that are no longer wanted are removed from the registry.
    """
    try:
        calendar = await config_entry.runtime_data.service.async_get_calendar(
            calendar_item.id
        )
    except ApiException as err:
        raise PlatformNotReady(str(err)) from err
    split = ColourSplit(calendar, calendar_item.background_color)
    unique_id_prefix = f"{parent_unique_id}{COLOUR_UNIQUE_ID_MARKER}"

    def build(bucket: str, label: ColourLabel | None) -> GoogleCalendarColourEntity:
        label_name = label.display_name if label else "Other"
        description = dataclasses.replace(
            parent,
            key=f"{parent.key}{COLOUR_UNIQUE_ID_MARKER}{bucket}",
            name=f"{parent.name} {label_name}",
            entity_id=generate_entity_id(
                ENTITY_ID_FORMAT, f"{parent.device_id} {label_name}", hass=hass
            ),
            # Events created on Other get no colour, so they only land on Other
            # when the default colour's label is unnamed
            read_only=parent.read_only
            or (bucket == OTHER and split.default_label is not None),
            initial_color=label.background_color if label else parent.initial_color,
        )
        return GoogleCalendarColourEntity(
            coordinator,
            calendar_item.id,
            description,
            f"{unique_id_prefix}{bucket}",
            split,
            bucket,
        )

    colour_entities = {OTHER: build(OTHER, None)}
    for label in split.named_labels:
        colour_entities[label.id] = build(label.id, label)
    cleaned_up = False

    @callback
    def async_update_colour_entities() -> None:
        nonlocal cleaned_up
        if not coordinator.last_update_success or coordinator.data is None:
            return
        now = dt_util.now()
        used = {
            split.bucket(event)
            for event in coordinator.data.overlapping(
                now + SYNC_EVENT_MIN_TIME, now + COLOUR_SCAN_MAX_TIME
            )
        }
        new_entities = [
            build(bucket, split.labels[bucket])
            for bucket in used
            if bucket not in colour_entities
        ]
        for entity in new_entities:
            colour_entities[entity.bucket] = entity
        if new_entities:
            async_add_entities(new_entities)
        if cleaned_up:
            return
        cleaned_up = True
        wanted = {entity.unique_id for entity in colour_entities.values()}
        entity_registry = er.async_get(hass)
        for entity_entry in er.async_entries_for_config_entry(
            entity_registry, config_entry.entry_id
        ):
            if (
                entity_entry.unique_id.startswith(unique_id_prefix)
                and entity_entry.unique_id not in wanted
            ):
                entity_registry.async_remove(entity_entry.entity_id)

    config_entry.async_on_unload(
        coordinator.async_add_listener(async_update_colour_entities)
    )
    return list(colour_entities.values())


def _get_calendar_event(event: Event) -> CalendarEvent:
    """Return a CalendarEvent from an API event."""
    rrule: str | None = None
    # Home Assistant expects a single RRULE: and all other
    # rule types are unsupported or ignored
    if (
        len(event.recurrence) == 1
        and (raw_rule := event.recurrence[0])
        and raw_rule.startswith(RRULE_PREFIX)
    ):
        rrule = raw_rule.removeprefix(RRULE_PREFIX)
    return CalendarEvent(
        uid=event.ical_uuid,
        recurrence_id=event.id if event.recurring_event_id else None,
        rrule=rrule,
        summary=event.summary,
        start=event.start.value,
        end=event.end.value,
        description=event.description,
        location=event.location,
        # The Google API defaults an omitted status to confirmed, and gcal_sync
        # applies that default, so this is never None. It drops cancelled
        # events when building the timeline, so only the statuses a calendar
        # entity reports reach here, already in lower case.
        status=CalendarEventStatus(event.status.value),
    )


async def async_create_event(entity: GoogleCalendarEntity, call: ServiceCall) -> None:
    """Add a new event to calendar."""
    start: DateOrDatetime | None = None
    end: DateOrDatetime | None = None
    hass = entity.hass

    if EVENT_IN in call.data:
        if EVENT_IN_DAYS in call.data[EVENT_IN]:
            today = dt_util.now().date()

            start_in = today + timedelta(days=call.data[EVENT_IN][EVENT_IN_DAYS])
            end_in = start_in + timedelta(days=1)

            start = DateOrDatetime(date=start_in)
            end = DateOrDatetime(date=end_in)

        elif EVENT_IN_WEEKS in call.data[EVENT_IN]:
            today = dt_util.now().date()

            start_in = today + timedelta(weeks=call.data[EVENT_IN][EVENT_IN_WEEKS])
            end_in = start_in + timedelta(days=1)

            start = DateOrDatetime(date=start_in)
            end = DateOrDatetime(date=end_in)

    elif EVENT_START_DATE in call.data and EVENT_END_DATE in call.data:
        start = DateOrDatetime(date=call.data[EVENT_START_DATE])
        end = DateOrDatetime(date=call.data[EVENT_END_DATE])

    elif EVENT_START_DATETIME in call.data and EVENT_END_DATETIME in call.data:
        start_dt = call.data[EVENT_START_DATETIME]
        end_dt = call.data[EVENT_END_DATETIME]
        start = DateOrDatetime(date_time=start_dt, timezone=str(hass.config.time_zone))
        end = DateOrDatetime(date_time=end_dt, timezone=str(hass.config.time_zone))

    if start is None or end is None:
        raise ValueError("Missing required fields to set start or end date/datetime")

    event = Event(
        summary=call.data[EVENT_SUMMARY],
        description=call.data[EVENT_DESCRIPTION],
        start=start,
        end=end,
    )
    if location := call.data.get(EVENT_LOCATION):
        event.location = location
    if label_id := entity.new_event_label_id:
        event.event_label_id = label_id
    try:
        await cast(
            CalendarSyncUpdateCoordinator, entity.coordinator
        ).sync.api.async_create_event(
            entity.calendar_id,
            event,
        )
    except ApiException as err:
        raise HomeAssistantError(str(err)) from err
    entity.async_write_ha_state()
