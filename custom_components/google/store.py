"""Google Calendar local storage."""

from dataclasses import dataclass, field
import logging
from typing import TYPE_CHECKING, Any, override

from gcal_sync.api import GoogleCalendarService
from gcal_sync.store import CalendarStore

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .colours import ColourUsage
from .const import DOMAIN

if TYPE_CHECKING:
    from .coordinator import CalendarSyncUpdateCoordinator

_LOGGER = logging.getLogger(__name__)

STORAGE_KEY_FORMAT = "{domain}.{entry_id}"
STORAGE_VERSION = 1
# Buffer writes every few minutes (plus guaranteed to be written at shutdown)
STORAGE_SAVE_DELAY_SECONDS = 120

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
        return self._data

    @override
    async def async_save(self, data: dict[str, Any]) -> None:
        """Save data."""
        self._data = data

        def provide_data() -> dict:
            return data

        self._store.async_delay_save(provide_data, STORAGE_SAVE_DELAY_SECONDS)

    async def async_remove(self) -> None:
        """Remove data."""
        await self._store.async_remove()
