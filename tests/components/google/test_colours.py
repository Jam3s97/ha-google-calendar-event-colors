"""Tests for splitting a calendar into one virtual calendar per colour label."""

import datetime
from http import HTTPStatus
from typing import Any
from unittest.mock import patch
import urllib

from gcal_sync.model import CalendarBasic, Event
import pytest

from custom_components.google.colours import OTHER, ColourSplit
from custom_components.google.const import CONF_SPLIT_CALENDARS, DOMAIN
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.typing import ClientSessionGenerator

from .conftest import (
    CALENDAR_ID,
    EMAIL_ADDRESS,
    TEST_API_ENTITY,
    TEST_EVENT,
    ApiResult,
    ComponentSetup,
)

# The test calendar's default colour is legacy calendar colour #16a765 (Basil)
LABEL_CHILD1 = "label-banana"
LABEL_MEDICAL = "label-peacock"
LABEL_BASIL = "label-basil"
LABEL_RADICCHIO = "label-radicchio"
LABEL_TOMATO = "label-tomato"
PARENT_UNIQUE_ID = f"{EMAIL_ADDRESS}-{CALENDAR_ID}"


def calendar_labels(basil_name: str | None = "Everyone") -> dict[str, Any]:
    """Return a calendars.get response with colour labels."""
    labels = [
        {"id": LABEL_CHILD1, "backgroundColor": "#f6bf26", "name": "Child1"},
        {"id": LABEL_MEDICAL, "backgroundColor": "#039be5", "name": "Medical"},
        {"id": LABEL_BASIL, "backgroundColor": "#0b8043", "name": basil_name},
        {"id": LABEL_RADICCHIO, "backgroundColor": "#ad1457"},
        {"id": LABEL_TOMATO, "backgroundColor": "#d50000"},
    ]
    return {
        "id": CALENDAR_ID,
        "summary": "Test",
        "labelProperties": {
            "eventLabels": [
                {k: v for k, v in label.items() if v is not None} for label in labels
            ]
        },
    }


def event(event_id: str, days: int, **colour: str) -> dict[str, Any]:
    """Return an all-day test event with the given colour fields."""
    start = dt_util.now().date() + datetime.timedelta(days=days)
    return {
        **TEST_EVENT,
        "id": event_id,
        "iCalUID": f"{event_id}@google.com",
        "summary": event_id,
        "start": {"date": start.isoformat()},
        "end": {"date": (start + datetime.timedelta(days=1)).isoformat()},
        **colour,
    }


EVENTS = [
    event("child1-label-and-colorid", 1, eventLabelId=LABEL_CHILD1, colorId="5"),
    event("medical-colorid-only", 2, colorId="7"),
    event("no-colour", 3),
    event("radicchio-label-only", 4, eventLabelId=LABEL_RADICCHIO),
    event("basil-explicit", 5, eventLabelId=LABEL_BASIL, colorId="10"),
    event("unknown-label", 6, eventLabelId="label-unknown"),
]


@pytest.fixture(autouse=True)
def mock_test_setup(
    test_api_calendar: dict[str, Any],
    mock_calendars_list: ApiResult,
) -> None:
    """Set up the calendar list returned during integration setup."""
    mock_calendars_list({"items": [test_api_calendar]})


@pytest.fixture
def config_entry_options() -> dict[str, Any] | None:
    """Split the test calendar by colour."""
    return {CONF_SPLIT_CALENDARS: [CALENDAR_ID]}


async def get_event_summaries(
    hass_client: ClientSessionGenerator, entity_id: str
) -> list[str]:
    """Return the summaries of the entity's events for the next 30 days."""
    client = await hass_client()
    now = dt_util.now()
    start = urllib.parse.quote(now.isoformat())
    end = urllib.parse.quote((now + datetime.timedelta(days=30)).isoformat())
    response = await client.get(f"/api/calendars/{entity_id}?start={start}&end={end}")
    assert response.status == HTTPStatus.OK
    return sorted(item["summary"] for item in await response.json())


def split_for(basil_name: str | None, calendar_color: str = "#16a765") -> ColourSplit:
    """Return a ColourSplit for the test labels."""
    return ColourSplit(CalendarBasic(**calendar_labels(basil_name)), calendar_color)


def bucket_of(split: ColourSplit, raw: dict[str, Any]) -> str:
    """Return the bucket of a raw API event."""
    return split.bucket(Event(**raw))


def test_bucket_by_label_then_color_id_then_default() -> None:
    """Test labels win, colorId is the fallback and no colour uses the default."""
    split = split_for("Everyone")
    assert [bucket_of(split, raw) for raw in EVENTS] == [
        LABEL_CHILD1,
        LABEL_MEDICAL,
        LABEL_BASIL,
        LABEL_RADICCHIO,
        LABEL_BASIL,
        OTHER,
    ]


def test_bucket_default_colour_unnamed() -> None:
    """Test events with no colour go to Other when the default label is unnamed."""
    split = split_for(None)
    assert split.default_label is None
    assert bucket_of(split, event("no-colour", 1)) == OTHER
    assert bucket_of(split, event("explicit", 1, colorId="10")) == LABEL_BASIL


def test_bucket_custom_calendar_colour() -> None:
    """Test a calendar colour outside the palette sends no-colour events to Other."""
    split = split_for("Everyone", calendar_color="#123456")
    assert bucket_of(split, event("no-colour", 1)) == OTHER


def test_label_metadata() -> None:
    """Test label names, fallback names and writable colour ids."""
    split = split_for("Everyone")
    assert [label.display_name for label in split.named_labels] == [
        "Child1",
        "Medical",
        "Everyone",
    ]
    assert split.labels[LABEL_RADICCHIO].display_name == "Radicchio"
    assert split.labels[LABEL_CHILD1].event_color_id == "5"
    assert split.labels[LABEL_RADICCHIO].event_color_id is None


async def test_colour_entities(
    hass: HomeAssistant,
    hass_client: ClientSessionGenerator,
    mock_calendar_get,
    mock_events_list_items,
    component_setup: ComponentSetup,
) -> None:
    """Test each event appears on exactly one colour entity."""
    mock_calendar_get(CALENDAR_ID, calendar_labels())
    mock_events_list_items(EVENTS)
    assert await component_setup()

    expected = {
        f"{TEST_API_ENTITY}_child1": ["child1-label-and-colorid"],
        f"{TEST_API_ENTITY}_medical": ["medical-colorid-only"],
        f"{TEST_API_ENTITY}_everyone": ["basil-explicit", "no-colour"],
        f"{TEST_API_ENTITY}_radicchio": ["radicchio-label-only"],
        f"{TEST_API_ENTITY}_other": ["unknown-label"],
    }
    for entity_id, summaries in expected.items():
        assert await get_event_summaries(hass_client, entity_id) == summaries
    assert hass.states.get(f"{TEST_API_ENTITY}_tomato") is None

    # The real calendar is unfiltered
    assert await get_event_summaries(hass_client, TEST_API_ENTITY) == sorted(
        raw["summary"] for raw in EVENTS
    )

    state = hass.states.get(f"{TEST_API_ENTITY}_child1")
    assert state.name == "We are, we are, a... test calendar Child1"
    # Colour entities are read-only until creating events is implemented
    assert state.attributes.get("supported_features", 0) == 0


async def test_renamed_label_keeps_entity_id(
    hass: HomeAssistant,
    entity_registry: er.EntityRegistry,
    config_entry: MockConfigEntry,
    mock_calendar_get,
    mock_events_list_items,
    component_setup: ComponentSetup,
) -> None:
    """Test entities are keyed by label id, so renaming a label keeps the entity."""
    config_entry.add_to_hass(hass)
    entity_registry.async_get_or_create(
        "calendar",
        DOMAIN,
        f"{PARENT_UNIQUE_ID}-colour-{LABEL_CHILD1}",
        suggested_object_id="old_label_name",
        config_entry=config_entry,
    )
    mock_calendar_get(CALENDAR_ID, calendar_labels())
    mock_events_list_items(EVENTS)
    assert await component_setup()

    state = hass.states.get("calendar.old_label_name")
    assert state
    assert state.name == "We are, we are, a... test calendar Child1"
    assert hass.states.get(f"{TEST_API_ENTITY}_child1") is None


async def test_stale_colour_entities_removed(
    hass: HomeAssistant,
    entity_registry: er.EntityRegistry,
    config_entry: MockConfigEntry,
    mock_calendar_get,
    mock_events_list_items,
    component_setup: ComponentSetup,
) -> None:
    """Test unnamed labels that are no longer used lose their entity."""
    config_entry.add_to_hass(hass)
    stale = entity_registry.async_get_or_create(
        "calendar",
        DOMAIN,
        f"{PARENT_UNIQUE_ID}-colour-{LABEL_TOMATO}",
        config_entry=config_entry,
    )
    used = entity_registry.async_get_or_create(
        "calendar",
        DOMAIN,
        f"{PARENT_UNIQUE_ID}-colour-{LABEL_RADICCHIO}",
        config_entry=config_entry,
    )
    mock_calendar_get(CALENDAR_ID, calendar_labels())
    mock_events_list_items(EVENTS)
    assert await component_setup()

    assert entity_registry.async_get(stale.entity_id) is None
    assert entity_registry.async_get(used.entity_id)


@pytest.mark.parametrize("config_entry_options", [None])
async def test_unsplit_calendar_removes_colour_entities(
    hass: HomeAssistant,
    entity_registry: er.EntityRegistry,
    config_entry: MockConfigEntry,
    mock_events_list_items,
    component_setup: ComponentSetup,
) -> None:
    """Test colour entities are removed when a calendar is no longer split."""
    config_entry.add_to_hass(hass)
    colour_entry = entity_registry.async_get_or_create(
        "calendar",
        DOMAIN,
        f"{PARENT_UNIQUE_ID}-colour-{LABEL_CHILD1}",
        config_entry=config_entry,
    )
    mock_events_list_items(EVENTS)
    assert await component_setup()

    assert entity_registry.async_get(colour_entry.entity_id) is None
    assert hass.states.get(TEST_API_ENTITY)


async def test_label_fetch_failure_retries_setup(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    mock_calendar_get,
    mock_events_list_items,
    component_setup: ComponentSetup,
) -> None:
    """Test a failure reading labels retries the platform instead of dropping them."""
    mock_calendar_get(CALENDAR_ID, {}, status=HTTPStatus.INTERNAL_SERVER_ERROR)
    mock_events_list_items(EVENTS)
    assert await component_setup()

    assert config_entry.state is ConfigEntryState.LOADED
    assert hass.states.get(f"{TEST_API_ENTITY}_child1") is None


@pytest.mark.parametrize("config_entry_options", [None])
async def test_options_flow_split_calendars(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    mock_calendars_list: ApiResult,
    test_api_calendar: dict[str, Any],
    mock_events_list_items,
    component_setup: ComponentSetup,
) -> None:
    """Test choosing calendars to split in the options flow."""
    mock_events_list_items([])
    assert await component_setup()

    result = await hass.config_entries.options.async_init(config_entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    assert set(result["data_schema"].schema) == {"calendar_access", "split_calendars"}

    with patch("custom_components.google.async_setup_entry", return_value=True):
        result = await hass.config_entries.options.async_configure(
            result["flow_id"],
            user_input={
                "calendar_access": "read_write",
                "split_calendars": [CALENDAR_ID],
            },
        )
        await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert config_entry.options == {
        "calendar_access": "read_write",
        "split_calendars": [CALENDAR_ID],
    }
