"""Provides diagnostics for google calendar."""

from collections import Counter
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from .colours import events_in_scan_window
from .store import EVENT_LABEL_ID, GoogleConfigEntry, SplitCalendar

TO_REDACT = {
    "id",
    "ical_uuid",
    "summary",
    "description",
    "location",
    "attendees",
    "recurring_event_id",
}


def redact_store(data: dict[str, Any]) -> dict[str, Any]:
    """Redact personal information from calendar events in the store."""
    id_num = 0
    diagnostics = {}
    for store_data in data.values():
        local_store: dict[str, Any] = store_data.get("event_sync", {})
        for calendar_data in local_store.values():
            id_num += 1
            items: dict[str, Any] = calendar_data.get("items", {})
            diagnostics[f"calendar#{id_num}"] = {
                "events": [
                    async_redact_data(item, TO_REDACT) for item in items.values()
                ],
                "sync_token_version": calendar_data.get("sync_token_version"),
            }
    return diagnostics


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, config_entry: GoogleConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    payload: dict[str, Any] = {
        "now": dt_util.now().isoformat(),
        "timezone": str(dt_util.get_default_time_zone()),
        "system_timezone": str(dt_util.naive_now().astimezone().tzinfo),
    }

    store = config_entry.runtime_data.store
    if data := await store.async_load():
        payload["store"] = redact_store(data)
    if split_calendars := config_entry.runtime_data.split_calendars:
        payload["colour_splits"] = [
            colour_split_diagnostics(split_calendar, data or {})
            for split_calendar in split_calendars.values()
        ]
    return payload


def store_numbers(data: dict[str, Any]) -> dict[str, str]:
    """Return the `calendar#N` name redact_store gives each store key."""
    numbers: dict[str, str] = {}
    id_num = 0
    for store_key, store_data in data.items():
        for _ in store_data.get("event_sync", {}).values():
            id_num += 1
            numbers.setdefault(store_key, f"calendar#{id_num}")
    return numbers


def colour_split_diagnostics(
    split_calendar: SplitCalendar, data: dict[str, Any]
) -> dict[str, Any]:
    """Return how a split calendar's events are bucketed, without label names."""
    split = split_calendar.split
    stored_items = [
        item
        for sync_data in data.get(split_calendar.store_key, {})
        .get("event_sync", {})
        .values()
        for item in sync_data.get("items", {}).values()
    ]
    result: dict[str, Any] = {
        "store": store_numbers(data).get(split_calendar.store_key),
        "calendar_color": split.calendar_background_color,
        "default_label": split.default_label.id if split.default_label else None,
        "labels": [
            {
                "id": label.id,
                "background_color": label.background_color,
                "colour": label.colour.name if label.colour else None,
                "named": label.name is not None,
            }
            for label in split.labels.values()
        ],
        "stored_events_without_label_field": sum(
            EVENT_LABEL_ID not in item for item in stored_items
        ),
        "events": None,
    }
    if (timeline := split_calendar.coordinator.data) is not None:
        buckets: Counter[str] = Counter()
        reasons: Counter[str] = Counter()
        for event in events_in_scan_window(timeline, dt_util.now()):
            bucket, reason = split.classify(event)
            buckets[bucket] += 1
            reasons[reason] += 1
        result["events"] = {
            "buckets": dict(sorted(buckets.items())),
            "reasons": dict(sorted(reasons.items())),
        }
    return result
