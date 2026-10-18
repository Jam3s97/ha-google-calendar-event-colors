"""Repairs issues suggesting the user name colours in Google Calendar."""

from collections.abc import Callable, Collection
import datetime
import hashlib

from gcal_sync.model import Event

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import issue_registry as ir

from .colours import ColourUsage
from .const import DOMAIN

ISSUE_PREFIX = "unnamed_colours"


def format_colour_usages(usages: Collection[ColourUsage]) -> str:
    """Return a markdown list of unnamed colours with sample events."""
    lines = []
    for usage in usages:
        noun = "event" if usage.event_count == 1 else "events"
        examples = ", ".join(
            f'"{event.summary or "(no title)"}" ({_format_date(event)})'
            for event in usage.samples
        )
        lines.append(
            f"- **{usage.label.display_name}**: {usage.event_count} {noun},"
            f" for example {examples}"
        )
    return "\n".join(lines)


@callback
def async_update_unnamed_colours_issue(
    hass: HomeAssistant,
    entry_id: str,
    parent_unique_id: str,
    calendar_name: str,
    usages: Collection[ColourUsage],
) -> None:
    """Create, replace or delete the issue for a split calendar.

    The issue id includes the set of unnamed labels, so an issue the user
    ignored comes back as a new issue when another unnamed colour is used.
    """
    prefix = _calendar_prefix(entry_id, parent_unique_id)
    issue_id = None
    if usages:
        label_ids = ",".join(sorted(usage.label.id for usage in usages))
        issue_id = f"{prefix}{_short_hash(label_ids)}"
    _async_delete_issues(
        hass, lambda other: other.startswith(prefix) and other != issue_id
    )
    if issue_id is None:
        return
    ir.async_create_issue(
        hass,
        DOMAIN,
        issue_id,
        is_fixable=False,
        severity=ir.IssueSeverity.WARNING,
        translation_key="unnamed_colours",
        translation_placeholders={
            "calendar": calendar_name,
            "colours": format_colour_usages(usages),
        },
    )


@callback
def async_delete_unnamed_colours_issues(
    hass: HomeAssistant, entry_id: str, keep_parent_unique_ids: Collection[str] = ()
) -> None:
    """Delete the entry's issues, except those of the given split calendars."""
    entry_prefix = f"{ISSUE_PREFIX}_{entry_id}_"
    keep = tuple(_calendar_prefix(entry_id, uid) for uid in keep_parent_unique_ids)
    _async_delete_issues(
        hass,
        lambda issue_id: (
            issue_id.startswith(entry_prefix)
            and not any(issue_id.startswith(prefix) for prefix in keep)
        ),
    )


def _calendar_prefix(entry_id: str, parent_unique_id: str) -> str:
    return f"{ISSUE_PREFIX}_{entry_id}_{_short_hash(parent_unique_id)}_"


def _short_hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()[:10]


@callback
def _async_delete_issues(hass: HomeAssistant, predicate: Callable[[str], bool]) -> None:
    for domain, issue_id in list(ir.async_get(hass).issues):
        if domain == DOMAIN and predicate(issue_id):
            ir.async_delete_issue(hass, DOMAIN, issue_id)


def _format_date(event: Event) -> str:
    start = event.start.value
    day = start.date() if isinstance(start, datetime.datetime) else start
    return f"{day.day} {day:%b %Y}"
