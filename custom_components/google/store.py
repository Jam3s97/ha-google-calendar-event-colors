"""Google Calendar local storage."""

from dataclasses import dataclass, field
import logging
from typing import TYPE_CHECKING, Any, override

from gcal_sync.api import GoogleCalendarService
from gcal_sync.const import EVENT_SYNC, ITEMS, SYNC_TOKEN
from gcal_sync.store import CalendarStore

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .colours import ColourSplit, ColourUsage
from .const import DOMAIN

if TYPE_CHECKING:
    from .coordinator import CalendarSyncUpdateCoordinator

_LOGGER = logging.getLogger(__name__)

STORAGE_KEY_FORMAT = "{domain}.{entry_id}"
STORAGE_VERSION = 1
# Buffer writes every few minutes (plus guaranteed to be written at shutdown)
STORAGE_SAVE_DELAY_SECONDS = 120
# Key gcal_sync writes on every stored event since it started reading labels
EVENT_LABEL_ID = "event_label_id"

type GoogleConfigEntry = ConfigEntry[GoogleRuntimeData]


@dataclass
class GoogleRuntimeData:
    """Google runtime data."""

    service: GoogleCalendarService
    store: LocalCalendarStore
    coordinators: dict[str, CalendarSyncUpdateCoordinator] = field(default_factory=dict)
    """Sync coordinator of each calendar's main entity, keyed by calendar id."""
    unnamed_colours: dict[str, tuple[str, list[ColourUsage]]] = field(
        default_factory=dict
    )
    """Calendar name and unnamed colours in use, keyed by split calendar id."""
    split_calendars: dict[str, SplitCalendar] = field(default_factory=dict)
    """Colour split of each split calendar, keyed by calendar id, for diagnostics."""


@dataclass
class SplitCalendar:
    """A calendar split by colour, as diagnostics needs it."""

    split: ColourSplit
    coordinator: CalendarSyncUpdateCoordinator
    store_key: str
    """Key of the calendar's sync data in the store."""


def reset_stale_event_syncs(data: dict[str, Any]) -> int:
    """Make gcal_sync re-sync calendars whose stored events have no label field.

    Older gcal_sync versions didn't read `eventLabelId`, and gcal_sync didn't
    invalidate stored events when it started to. Sync only fetches changed
    events, so events stored before then never get their label, and those in
    colours without a `colorId` are bucketed as uncoloured. Clearing the sync
    token and events, as gcal_sync does for an invalid token, makes the next
    sync fetch everything again. Returns the number of calendars reset.
    """
    reset = 0
    for scope in data.values():
        if not isinstance(scope, dict):
            continue
        for sync_data in scope.get(EVENT_SYNC, {}).values():
            items: dict[str, Any] = sync_data.get(ITEMS) or {}
            if any(EVENT_LABEL_ID not in item for item in items.values()):
                sync_data[SYNC_TOKEN] = None
                sync_data[ITEMS] = {}
                reset += 1
    return reset


class LocalCalendarStore(CalendarStore):
    """Storage for local persistence of calendar and event data."""

    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        """Initialize LocalCalendarStore."""
        self._store = Store[dict[str, Any]](
            hass,
            STORAGE_VERSION,
            STORAGE_KEY_FORMAT.format(domain=DOMAIN, entry_id=entry_id),
            private=True,
        )
        self._data: dict[str, Any] | None = None

    @override
    async def async_load(self) -> dict[str, Any] | None:
        """Load data."""
        if self._data is None:
            self._data = await self._store.async_load() or {}
            if reset := reset_stale_event_syncs(self._data):
                _LOGGER.info(
                    "Re-syncing %d calendar(s) whose stored events were saved "
                    "without colour labels",
                    reset,
                )
        return self._data

    @override
    async def async_save(self, data: dict[str, Any]) -> None:
        """Save data."""
        self._data = data

        def provide_data() -> dict:
            return data

        self._store.async_delay_save(provide_data, STORAGE_SAVE_DELAY_SECONDS)

    async def async_flush(self) -> None:
        """Write cached data and finish pending saves before unloading."""
        if self._data is not None:
            await self._store.async_save(self._data)

    async def async_remove(self) -> None:
        """Remove persisted data and prevent further writes."""
        self._store.make_read_only()
        # Wait for active writes before removing the file.
        await self.async_flush()
        await self._store.async_remove()
