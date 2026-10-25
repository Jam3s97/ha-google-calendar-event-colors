"""Fix flow for the issue suggesting the user name colours in Google Calendar."""

from typing import Any

from homeassistant.components.repairs import RepairsFlow, RepairsFlowResult
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir

from .const import CONF_IGNORE_UNNAMED_COLOURS, DOMAIN


class UnnamedColoursRepairFlow(RepairsFlow):
    """Reload after naming colours, or stop the reminders."""

    def __init__(self, entry_id: str, placeholders: dict[str, str]) -> None:
        """Create the flow for the issue's config entry."""
        self._entry_id = entry_id
        self._placeholders = placeholders

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> RepairsFlowResult:
        """Show the unnamed colours, with a choice of what to do."""
        return self.async_show_menu(
            menu_options=["reload", "ignore_unnamed_colours"],
            description_placeholders=self._placeholders,
        )

    async def async_step_reload(
        self, user_input: dict[str, Any] | None = None
    ) -> RepairsFlowResult:
        """Reload the entry to read the new colour names."""
        self.hass.config_entries.async_schedule_reload(self._entry_id)
        return self.async_create_entry(data={})

    async def async_step_ignore_unnamed_colours(
        self, user_input: dict[str, Any] | None = None
    ) -> RepairsFlowResult:
        """Turn off the reminders for the entry, then reload it."""
        if entry := self.hass.config_entries.async_get_entry(self._entry_id):
            self.hass.config_entries.async_update_entry(
                entry, options={**entry.options, CONF_IGNORE_UNNAMED_COLOURS: True}
            )
            self.hass.config_entries.async_schedule_reload(self._entry_id)
        return self.async_create_entry(data={})


async def async_create_fix_flow(
    hass: HomeAssistant, issue_id: str, data: dict[str, Any] | None
) -> RepairsFlow:
    """Create the fix flow for an unnamed colours issue."""
    issue = ir.async_get(hass).async_get_issue(DOMAIN, issue_id)
    placeholders = issue.translation_placeholders if issue else None
    return UnnamedColoursRepairFlow(
        (data or {}).get("entry_id", ""), dict(placeholders or {})
    )
