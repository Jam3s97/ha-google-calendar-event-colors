"""Config flow for Google integration."""

import asyncio
from collections.abc import Mapping
import logging
from typing import Any, override

from gcal_sync.api import GoogleCalendarService
from gcal_sync.exceptions import ApiException, ApiForbiddenException
from gcal_sync.model import AccessRole, Calendar
import probatio

from homeassistant.config_entries import (
    SOURCE_REAUTH,
    ConfigFlowResult,
    OptionsFlowWithReload,
)
from homeassistant.core import callback
from homeassistant.helpers import config_entry_oauth2_flow
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
)
from homeassistant.util import dt as dt_util

from .api import (
    DEVICE_AUTH_CREDS,
    AccessTokenAuthImpl,
    DeviceFlow,
    GoogleHybridAuth,
    InvalidCredential,
    OAuthError,
    async_create_device_flow,
)
from .colour_issues import format_colour_usages
from .colours import (
    ColourSplit,
    ColourUsage,
    events_in_scan_window,
    unnamed_colours_in_use,
)
from .const import (
    CONF_CALENDAR_ACCESS,
    CONF_CREDENTIAL_TYPE,
    CONF_SPLIT_CALENDARS,
    DEFAULT_FEATURE_ACCESS,
    DOMAIN,
    CredentialType,
    FeatureAccess,
)
from .store import GoogleConfigEntry

_LOGGER = logging.getLogger(__name__)

CONF_UNNAMED_ACTION = "unnamed_action"
UNNAMED_ACTION_SPLIT = "split"
UNNAMED_ACTION_WAIT = "wait"


class OAuth2FlowHandler(
    config_entry_oauth2_flow.AbstractOAuth2FlowHandler, domain=DOMAIN
):
    """Config flow to handle Google Calendars OAuth2 authentication.

    Historically, the Google Calendar integration instructed users to use
    Device Auth. Device Auth was considered easier to use since it did not
    require users to configure a redirect URL. Device Auth is meant for
    devices with limited input, such as a television.
    https://developers.google.com/identity/protocols/oauth2/limited-input-device

    Device Auth is limited to a small set of Google APIs (calendar is allowed)
    and is considered less secure than Web Auth. It is not generally preferred
    and may be limited/deprecated in the future similar to App/OOB Auth
    https://developers.googleblog.com/2022/02/making-oauth-flows-safer.html

    Web Auth is the preferred method by Home Assistant and Google, and a benefit
    is that the same credentials may be used across many Google integrations in
    Home Assistant. Web Auth is now easier for user to setup using my.home-assistant.io
    redirect urls.

    The Application Credentials integration does not currently record which type
    of credential the user entered (and if we ask the user, they may not know or may
    make a mistake) so we try to determine the credential type automatically. This
    implementation first attempts Device Auth by talking to the token API in the first
    step of the device flow, then if that fails it will redirect using Web Auth.
    There is not another explicit known way to check.
    """

    DOMAIN = DOMAIN

    _exchange_finished_task: asyncio.Task[bool] | None = None

    def __init__(self) -> None:
        """Set up instance."""
        super().__init__()
        self._device_flow: DeviceFlow | None = None
        # First attempt is device auth, then fallback to web auth
        self._web_auth = False

    @property
    @override
    def logger(self) -> logging.Logger:
        """Return logger."""
        return logging.getLogger(__name__)

    @property
    def _calendar_access(self) -> FeatureAccess:
        """Return the access the entry being authorized asks for."""
        if self.source == SOURCE_REAUTH and (
            reauth_options := self._get_reauth_entry().options
        ):
            return FeatureAccess[reauth_options[CONF_CALENDAR_ACCESS]]

        return DEFAULT_FEATURE_ACCESS

    @property
    @override
    def extra_authorize_data(self) -> dict[str, Any]:
        """Extra data that needs to be appended to the authorize url."""
        return {
            "scope": self._calendar_access.scope,
            # Add params to ensure we get back a refresh token
            "access_type": "offline",
            "prompt": "consent",
        }

    @override
    async def async_step_auth(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Create an entry for auth."""
        # The default behavior from the parent class is to redirect the
        # user with an external step. When using the device flow, we instead
        # prompt the user to visit a URL and enter a code. The device flow
        # background task will poll the exchange endpoint to get valid
        # creds or until a timeout is complete.
        if self._web_auth:
            return await super().async_step_auth(user_input)

        if self._exchange_finished_task and self._exchange_finished_task.done():
            return self.async_show_progress_done(next_step_id="creation")

        if not self._device_flow:
            _LOGGER.debug("Creating GoogleHybridAuth flow")
            if not isinstance(self.flow_impl, GoogleHybridAuth):
                _LOGGER.error(
                    "Unexpected OAuth implementation does not support device auth: %s",
                    self.flow_impl,
                )
                return self.async_abort(reason="oauth_error")
            try:
                device_flow = await async_create_device_flow(
                    self.hass,
                    self.flow_impl.client_id,
                    self.flow_impl.client_secret,
                    self._calendar_access,
                )
            except TimeoutError as err:
                _LOGGER.error("Timeout initializing device flow: %s", str(err))
                return self.async_abort(reason="timeout_connect")
            except InvalidCredential:
                _LOGGER.debug("Falling back to Web Auth and restarting flow")
                self._web_auth = True
                return await super().async_step_auth()
            except OAuthError as err:
                _LOGGER.error("Error initializing device flow: %s", str(err))
                return self.async_abort(reason="oauth_error")
            self._device_flow = device_flow

            exchange_finished_evt = asyncio.Event()
            self._exchange_finished_task = self.hass.async_create_task(
                exchange_finished_evt.wait()
            )

            def _exchange_finished() -> None:
                self.external_data = {
                    DEVICE_AUTH_CREDS: device_flow.creds
                }  # is None on timeout/expiration
                exchange_finished_evt.set()

            device_flow.async_set_listener(_exchange_finished)
            device_flow.async_start_exchange()

        return self.async_show_progress(
            step_id="auth",
            description_placeholders={
                "url": self._device_flow.verification_url,
                "user_code": self._device_flow.user_code,
            },
            progress_action="exchange",
            progress_task=self._exchange_finished_task,
        )

    @override
    async def async_step_creation(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle external yaml configuration."""
        if not self._web_auth and self.external_data.get(DEVICE_AUTH_CREDS) is None:
            return self.async_abort(reason="code_expired")
        return await super().async_step_creation(user_input)

    @override
    async def async_oauth_create_entry(self, data: dict) -> ConfigFlowResult:
        """Create an entry for the flow, or update existing entry."""
        data[CONF_CREDENTIAL_TYPE] = (
            CredentialType.WEB_AUTH if self._web_auth else CredentialType.DEVICE_AUTH
        )
        if self.source == SOURCE_REAUTH:
            return self.async_update_reload_and_abort(
                self._get_reauth_entry(), data=data
            )
        calendar_service = GoogleCalendarService(
            AccessTokenAuthImpl(
                async_get_clientsession(self.hass), data["token"]["access_token"]
            )
        )
        try:
            primary_calendar = await calendar_service.async_get_calendar("primary")
        except ApiForbiddenException as err:
            _LOGGER.error(
                "Error reading primary calendar, make sure"
                " Google Calendar API is enabled: %s",
                err,
            )
            return self.async_abort(
                reason="calendar_api_disabled",
                description_placeholders={
                    "calendar_api_url": "https://console.cloud.google.com/apis/library/calendar-json.googleapis.com"
                },
            )
        except ApiException as err:
            _LOGGER.error("Error reading primary calendar: %s", err)
            return self.async_abort(reason="cannot_connect")
        await self.async_set_unique_id(primary_calendar.id)

        if found := self.hass.config_entries.async_entry_for_domain_unique_id(
            self.handler, primary_calendar.id
        ):
            _LOGGER.debug("Found existing '%s' entry: %s", primary_calendar.id, found)

        self._abort_if_unique_id_configured()
        return self.async_create_entry(
            title=primary_calendar.id,
            data=data,
            options={
                CONF_CALENDAR_ACCESS: DEFAULT_FEATURE_ACCESS.name,
            },
        )

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """Perform reauth upon an API authentication error."""
        self._web_auth = entry_data.get(CONF_CREDENTIAL_TYPE) == CredentialType.WEB_AUTH
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Confirm reauth dialog."""
        if user_input is None:
            return self.async_show_form(step_id="reauth_confirm")
        return await self.async_step_user()

    @staticmethod
    @callback
    @override
    def async_get_options_flow(
        config_entry: GoogleConfigEntry,
    ) -> OptionsFlowHandler:
        """Create an options flow."""
        return OptionsFlowHandler()


class OptionsFlowHandler(OptionsFlowWithReload):
    """Google Calendar options flow."""

    def __init__(self) -> None:
        """Initialize the options flow."""
        self._options: dict[str, Any] = {}
        self._calendars: dict[str, Calendar] = {}
        self._unnamed: dict[str, tuple[str, list[ColourUsage]]] = {}

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Manage the options."""
        options = self.config_entry.options
        if user_input is not None:
            self._options = {**options, **user_input}
            newly_split = [
                calendar_id
                for calendar_id in user_input.get(CONF_SPLIT_CALENDARS, [])
                if calendar_id not in options.get(CONF_SPLIT_CALENDARS, [])
            ]
            self._unnamed = await self._async_unnamed_colours(newly_split)
            if self._unnamed:
                return await self.async_step_unnamed_colours()
            return self.async_create_entry(title="", data=self._options)

        schema: dict[Any, Any] = {
            probatio.Required(
                CONF_CALENDAR_ACCESS,
                default=options.get(CONF_CALENDAR_ACCESS),
            ): probatio.In(
                {
                    "read_write": "Read/Write access (can create events)",
                    "read_only": "Read-only access",
                }
            )
        }
        self._calendars = await self._async_splittable_calendars()
        if self._calendars:
            schema[
                probatio.Optional(
                    CONF_SPLIT_CALENDARS,
                    default=[
                        calendar_id
                        for calendar_id in options.get(CONF_SPLIT_CALENDARS, [])
                        if calendar_id in self._calendars
                    ],
                )
            ] = SelectSelector(
                SelectSelectorConfig(
                    options=[
                        SelectOptionDict(value=calendar_id, label=calendar.summary)
                        for calendar_id, calendar in self._calendars.items()
                    ],
                    multiple=True,
                    mode=SelectSelectorMode.LIST,
                )
            )
        return self.async_show_form(
            step_id="init",
            data_schema=probatio.Schema(schema),
            description_placeholders={
                "unnamed_colours": self._describe_split_calendars(
                    options.get(CONF_SPLIT_CALENDARS, [])
                )
            },
        )

    async def async_step_unnamed_colours(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Let the user wait with splitting calendars that use unnamed colours."""
        if user_input is not None:
            if user_input[CONF_UNNAMED_ACTION] == UNNAMED_ACTION_WAIT:
                self._options[CONF_SPLIT_CALENDARS] = [
                    calendar_id
                    for calendar_id in self._options[CONF_SPLIT_CALENDARS]
                    if calendar_id not in self._unnamed
                ]
                return await self.async_step_not_split()
            return self.async_create_entry(title="", data=self._options)

        return self.async_show_form(
            step_id="unnamed_colours",
            data_schema=probatio.Schema(
                {
                    probatio.Required(
                        CONF_UNNAMED_ACTION, default=UNNAMED_ACTION_WAIT
                    ): SelectSelector(
                        SelectSelectorConfig(
                            options=[UNNAMED_ACTION_SPLIT, UNNAMED_ACTION_WAIT],
                            mode=SelectSelectorMode.LIST,
                            translation_key=CONF_UNNAMED_ACTION,
                        )
                    )
                }
            ),
            description_placeholders={
                "colours": "\n\n".join(
                    f"**{name}**\n\n{format_colour_usages(usages)}"
                    for name, usages in self._unnamed.values()
                )
            },
        )

    async def async_step_not_split(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Confirm which calendars weren't split, then save the other options.

        The options dialog ignores a create_entry description, so this is a
        form step rather than a message on the saved screen.
        """
        if user_input is not None:
            return self.async_create_entry(title="", data=self._options)
        return self.async_show_form(
            step_id="not_split",
            data_schema=probatio.Schema({}),
            description_placeholders={
                "calendars": ", ".join(name for name, _ in self._unnamed.values())
            },
            last_step=True,
        )

    async def _async_splittable_calendars(self) -> dict[str, Calendar]:
        """Return the calendars that can be split by colour, keyed by id."""
        runtime_data = getattr(self.config_entry, "runtime_data", None)
        if runtime_data is None:
            return {}
        try:
            result = await runtime_data.service.async_list_calendars()
        except ApiException as err:
            _LOGGER.debug("Unable to list calendars: %s", err)
            return {}
        return {
            calendar.id: calendar
            for calendar in result.items
            if calendar.access_role != AccessRole.FREE_BUSY_READER
        }

    async def _async_unnamed_colours(
        self, calendar_ids: list[str]
    ) -> dict[str, tuple[str, list[ColourUsage]]]:
        """Return the unnamed colours in use, for calendars about to be split."""
        runtime_data = getattr(self.config_entry, "runtime_data", None)
        if runtime_data is None:
            return {}
        now = dt_util.now()
        unnamed = {}
        for calendar_id in calendar_ids:
            coordinator = runtime_data.coordinators.get(calendar_id)
            calendar_item = self._calendars.get(calendar_id)
            if coordinator is None or coordinator.data is None or not calendar_item:
                continue
            try:
                calendar = await runtime_data.service.async_get_calendar(calendar_id)
            except ApiException as err:
                _LOGGER.debug("Unable to read labels of %s: %s", calendar_id, err)
                continue
            split = ColourSplit(calendar, calendar_item.background_color)
            if usages := unnamed_colours_in_use(
                split, events_in_scan_window(coordinator.data, now), now
            ):
                unnamed[calendar_id] = (calendar_item.summary, usages)
        return unnamed

    def _describe_split_calendars(self, split_calendar_ids: list[str]) -> str:
        """Return a note on unnamed colours in calendars that are already split."""
        runtime_data = getattr(self.config_entry, "runtime_data", None)
        if runtime_data is None:
            return ""
        lines = [
            f"- **{name}**: " + ", ".join(usage.label.display_name for usage in usages)
            for calendar_id, (name, usages) in runtime_data.unnamed_colours.items()
            if usages and calendar_id in split_calendar_ids
        ]
        if not lines:
            return ""
        return (
            "\n\nThese colours in split calendars have no name in Google"
            " Calendar. See Settings > System > Repairs for example events and"
            " how to name them.\n\n" + "\n".join(lines)
        )
