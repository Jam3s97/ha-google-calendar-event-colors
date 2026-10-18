"""Tests for splitting a calendar into one virtual calendar per colour label."""

import datetime
from http import HTTPStatus
from typing import Any
from unittest.mock import patch
import urllib

from gcal_sync.auth import API_BASE_URL
from gcal_sync.model import CalendarBasic, Event
import pytest

from custom_components.google.colours import OTHER, ColourSplit, unnamed_colours_in_use
from custom_components.google.const import CONF_SPLIT_CALENDARS, DOMAIN
from homeassistant.components.calendar import CalendarEntityFeature
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import entity_registry as er, issue_registry as ir
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker
from pytest_homeassistant_custom_component.typing import (
    ClientSessionGenerator,
    WebSocketGenerator,
)

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
    assert state.attributes["supported_features"] == (
        CalendarEntityFeature.CREATE_EVENT | CalendarEntityFeature.DELETE_EVENT
    )
    # Events created with no colour land on Everyone (the default colour), not
    # Other, so Other can't create events
    state = hass.states.get(f"{TEST_API_ENTITY}_other")
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
    mock_calendar_get,
    component_setup: ComponentSetup,
) -> None:
    """Test choosing calendars to split in the options flow."""
    mock_events_list_items([])
    mock_calendar_get(CALENDAR_ID, calendar_labels())
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


async def create_event(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    domain: str,
    entity_id: str,
) -> tuple[dict[str, Any], dict[str, str]]:
    """Create an event with an action and return the request body and query."""
    aioclient_mock.clear_requests()
    aioclient_mock.post(f"{API_BASE_URL}/calendars/{CALENDAR_ID}/events")
    aioclient_mock.get(
        f"{API_BASE_URL}/calendars/{CALENDAR_ID}/events",
        json={"items": EVENTS, "nextSyncToken": "sync-token"},
    )
    await hass.services.async_call(
        domain,
        "create_event",
        {
            "summary": "New event",
            "description": "Created from Home Assistant",
            "start_date": "2026-11-01",
            "end_date": "2026-11-02",
        },
        target={"entity_id": entity_id},
        blocking=True,
    )
    posts = [call for call in aioclient_mock.mock_calls if call[0] == "post"]
    assert len(posts) == 1
    _, url, body, _ = posts[0]
    return body, dict(url.query)


@pytest.mark.parametrize("domain", ["calendar", DOMAIN])
@pytest.mark.parametrize(
    ("entity_id", "expected_label"),
    [
        (f"{TEST_API_ENTITY}_child1", LABEL_CHILD1),
        # A newer colour with no legacy colorId
        (f"{TEST_API_ENTITY}_radicchio", LABEL_RADICCHIO),
        # The default colour's entity writes no colour
        (f"{TEST_API_ENTITY}_everyone", None),
        (TEST_API_ENTITY, None),
    ],
)
async def test_create_event_writes_label(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    mock_calendar_get,
    mock_events_list_items,
    component_setup: ComponentSetup,
    domain: str,
    entity_id: str,
    expected_label: str | None,
) -> None:
    """Test events get the entity's label, sent with eventLabelVersion=1."""
    mock_calendar_get(CALENDAR_ID, calendar_labels())
    mock_events_list_items(EVENTS)
    assert await component_setup()

    body, query = await create_event(hass, aioclient_mock, domain, entity_id)

    assert body.get("eventLabelId") == expected_label
    assert "colorId" not in body
    # Google ignores eventLabelId without the parameter, and ignores colorId
    # with it, so it is only sent when writing a label
    assert query.get("eventLabelVersion") == ("1" if expected_label else None)


async def test_other_writable_when_default_colour_unnamed(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    mock_calendar_get,
    mock_events_list_items,
    component_setup: ComponentSetup,
) -> None:
    """Test Other creates uncoloured events when they would land on Other."""
    mock_calendar_get(CALENDAR_ID, calendar_labels(basil_name=None))
    mock_events_list_items(EVENTS)
    assert await component_setup()

    state = hass.states.get(f"{TEST_API_ENTITY}_other")
    assert state.attributes["supported_features"] & CalendarEntityFeature.CREATE_EVENT

    body, query = await create_event(
        hass, aioclient_mock, "calendar", f"{TEST_API_ENTITY}_other"
    )
    assert "eventLabelId" not in body
    assert "eventLabelVersion" not in query


@pytest.mark.parametrize("calendar_access_role", ["reader"])
async def test_colour_entities_read_only_calendar(
    hass: HomeAssistant,
    mock_calendar_get,
    mock_events_list_items,
    component_setup: ComponentSetup,
) -> None:
    """Test colour entities of a read-only calendar can't create events."""
    mock_calendar_get(CALENDAR_ID, calendar_labels())
    mock_events_list_items(EVENTS)
    assert await component_setup()

    state = hass.states.get(f"{TEST_API_ENTITY}_child1")
    assert state.attributes.get("supported_features", 0) == 0


async def test_delete_event_on_colour_entity(
    hass: HomeAssistant,
    hass_ws_client: WebSocketGenerator,
    aioclient_mock: AiohttpClientMocker,
    mock_calendar_get,
    mock_events_list_items,
    component_setup: ComponentSetup,
) -> None:
    """Test deleting from a colour entity deletes the event in the real calendar."""
    mock_calendar_get(CALENDAR_ID, calendar_labels())
    mock_events_list_items(EVENTS)
    assert await component_setup()

    aioclient_mock.clear_requests()
    aioclient_mock.delete(
        f"{API_BASE_URL}/calendars/{CALENDAR_ID}/events/child1-label-and-colorid"
    )
    mock_events_list_items(EVENTS[1:])

    client = await hass_ws_client(hass)
    await client.send_json(
        {
            "id": 1,
            "type": "calendar/event/delete",
            "entity_id": f"{TEST_API_ENTITY}_child1",
            "uid": "child1-label-and-colorid@google.com",
        }
    )
    response = await client.receive_json()
    assert response["success"]
    assert [call[0] for call in aioclient_mock.mock_calls] == ["delete", "get"]


def google_issues(hass: HomeAssistant) -> list[ir.IssueEntry]:
    """Return the integration's unnamed colour issues."""
    return [
        issue
        for (domain, issue_id), issue in ir.async_get(hass).issues.items()
        if domain == DOMAIN and issue_id.startswith("unnamed_colours_")
    ]


def test_unnamed_colours_in_use() -> None:
    """Test unnamed colours are counted per series, with upcoming samples first."""
    split = split_for("Everyone")
    now = dt_util.now()
    weekly = [
        event("weekly", days, eventLabelId=LABEL_RADICCHIO) for days in (7, 14, 21)
    ]
    events = [
        event("past", -10, eventLabelId=LABEL_RADICCHIO),
        *weekly,
        event("soon", 1, eventLabelId=LABEL_RADICCHIO),
        event("later", 30, eventLabelId=LABEL_RADICCHIO),
        event("named", 2, eventLabelId=LABEL_CHILD1),
        event("tomato", 3, colorId="11"),
    ]
    # Recurring instances share an iCalUID
    for raw in weekly:
        raw["iCalUID"] = "weekly@google.com"

    usages = unnamed_colours_in_use(split, [Event(**raw) for raw in events], now)

    assert [usage.label.display_name for usage in usages] == ["Radicchio", "Tomato"]
    radicchio = usages[0]
    assert radicchio.event_count == 4
    assert [sample.summary for sample in radicchio.samples] == [
        "soon",
        "weekly",
        "later",
    ]
    assert usages[1].event_count == 1


async def test_unnamed_colours_issue(
    hass: HomeAssistant,
    mock_calendar_get,
    mock_events_list_items,
    component_setup: ComponentSetup,
) -> None:
    """Test a Repairs issue lists unnamed colours in use with sample events."""
    mock_calendar_get(CALENDAR_ID, calendar_labels())
    mock_events_list_items(EVENTS)
    assert await component_setup()

    issues = google_issues(hass)
    assert len(issues) == 1
    issue = issues[0]
    assert not issue.is_fixable
    assert issue.translation_key == "unnamed_colours"
    assert issue.translation_placeholders["calendar"] == (
        "We are, we are, a... Test Calendar"
    )
    assert issue.translation_placeholders["colours"].startswith(
        '- **Radicchio**: 1 event, for example "radicchio-label-only" ('
    )


async def test_no_issue_when_colours_are_named(
    hass: HomeAssistant,
    mock_calendar_get,
    mock_events_list_items,
    component_setup: ComponentSetup,
) -> None:
    """Test no issue is raised when every colour in use is named."""
    mock_calendar_get(CALENDAR_ID, calendar_labels())
    mock_events_list_items(
        [raw for raw in EVENTS if raw["id"] != "radicchio-label-only"]
    )
    assert await component_setup()

    assert google_issues(hass) == []


async def test_ignored_issue_returns_for_new_unnamed_colour(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    mock_calendar_get,
    mock_events_list_items,
    component_setup: ComponentSetup,
) -> None:
    """Test an ignored issue comes back as a new issue when another colour is used."""
    mock_calendar_get(CALENDAR_ID, calendar_labels())
    mock_events_list_items(EVENTS)
    assert await component_setup()
    (issue,) = google_issues(hass)
    ir.async_ignore_issue(hass, DOMAIN, issue.issue_id, True)

    # The mocker answers with the first matching mock, so replace them
    aioclient_mock.clear_requests()
    mock_calendar_get(CALENDAR_ID, calendar_labels())
    mock_events_list_items([*EVENTS, event("tomato", 7, colorId="11")])
    async_fire_time_changed(hass, dt_util.utcnow() + datetime.timedelta(minutes=30))
    await hass.async_block_till_done()

    (new_issue,) = google_issues(hass)
    assert new_issue.issue_id != issue.issue_id
    assert new_issue.dismissed_version is None
    assert "**Tomato**" in new_issue.translation_placeholders["colours"]


async def test_unsplit_calendar_removes_issue(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    mock_calendar_get,
    mock_events_list_items,
    component_setup: ComponentSetup,
) -> None:
    """Test the issue is removed when the calendar is no longer split."""
    mock_calendar_get(CALENDAR_ID, calendar_labels())
    mock_events_list_items(EVENTS)
    assert await component_setup()
    assert len(google_issues(hass)) == 1

    hass.config_entries.async_update_entry(config_entry, options={})
    await hass.config_entries.async_reload(config_entry.entry_id)
    await hass.async_block_till_done()

    assert google_issues(hass) == []


async def test_options_flow_notes_unnamed_colours(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    mock_calendar_get,
    mock_events_list_items,
    component_setup: ComponentSetup,
) -> None:
    """Test the options form lists unnamed colours of split calendars."""
    mock_calendar_get(CALENDAR_ID, calendar_labels())
    mock_events_list_items(EVENTS)
    assert await component_setup()

    result = await hass.config_entries.options.async_init(config_entry.entry_id)
    assert result["step_id"] == "init"
    note = result["description_placeholders"]["unnamed_colours"]
    assert "Repairs" in note
    assert "- **We are, we are, a... Test Calendar**: Radicchio" in note


@pytest.mark.parametrize("config_entry_options", [None])
@pytest.mark.parametrize(
    ("action", "expected_split"), [("split", [CALENDAR_ID]), ("wait", [])]
)
async def test_options_flow_unnamed_colours_step(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    mock_calendar_get,
    mock_events_list_items,
    component_setup: ComponentSetup,
    action: str,
    expected_split: list[str],
) -> None:
    """Test splitting a calendar with unnamed colours asks to split now or wait."""
    mock_events_list_items(EVENTS)
    assert await component_setup()
    mock_calendar_get(CALENDAR_ID, calendar_labels())

    result = await hass.config_entries.options.async_init(config_entry.entry_id)
    assert result["description_placeholders"]["unnamed_colours"] == ""
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        user_input={"calendar_access": "read_write", "split_calendars": [CALENDAR_ID]},
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "unnamed_colours"
    assert "**Radicchio**" in result["description_placeholders"]["colours"]
    # Wait is the default, so submitting without reading doesn't split
    assert result["data_schema"]({}) == {"unnamed_action": "wait"}

    with patch("custom_components.google.async_setup_entry", return_value=True):
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], user_input={"unnamed_action": action}
        )
        if action == "wait":
            # A confirmation page says which calendars weren't split
            assert result["type"] is FlowResultType.FORM
            assert result["step_id"] == "not_split"
            assert result["description_placeholders"] == {
                "calendars": "We are, we are, a... Test Calendar"
            }
            result = await hass.config_entries.options.async_configure(
                result["flow_id"], user_input={}
            )
        await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert config_entry.options[CONF_SPLIT_CALENDARS] == expected_split
