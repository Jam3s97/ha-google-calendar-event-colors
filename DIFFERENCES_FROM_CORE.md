# Differences from core

This file lists every way `custom_components/google/` (and its tests) differs from Home Assistant core's `google` integration. Update it in the same commit as any change to the integration.

## Base

| | |
|--|--|
| Core version | `2026.10.1` |
| Source | [`homeassistant/components/google`](https://github.com/home-assistant/core/tree/2026.10.1/homeassistant/components/google) and [`tests/components/google`](https://github.com/home-assistant/core/tree/2026.10.1/tests/components/google) |
| Unmodified copy | commit `6ce6cd2` |

To see the full diff against core:

```sh
git diff 6ce6cd2 -- custom_components/google tests/components/google
```

## Feature changes

<!-- Add one entry per change: what it does, which files, and why core's behaviour wasn't enough. -->

### Colour entities

Core exposes one calendar entity per Google calendar, and per-event colour never reaches dashboard cards. This adds, for each calendar the user chooses to split, one virtual calendar entity per event colour label, so cards can colour events per entity.

| File | Change |
|--|--|
| `colours.py` | **New.** Colour tables (label colour, legacy event `colorId` and legacy calendar colour for each of the 24 picker colours) and `ColourSplit`, which assigns each event to a label: `eventLabelId`, else `colorId`, else the calendar's default colour when that label is named, else Other. |
| `const.py` | Adds the `CONF_SPLIT_CALENDARS` option. |
| `calendar.py` | `GoogleCalendarColourEntity` (a subclass of `GoogleCalendarEntity`) adds a colour check to `_event_filter` and **shares the real calendar's coordinator**, so there is no extra sync. `_async_colour_entities()` makes one `calendars.get` call per split calendar to read its labels, creates entities for named labels and Other, adds entities for unnamed labels once events use them, and removes stale colour entities after the first refresh. `async_setup_entry` calls it for split calendars and removes colour entities of calendars that are no longer split. Colour entity unique IDs are `<calendar unique id>-colour-<label id or other>`. |
| `config_flow.py` | The options flow adds a "Calendars to split by event colour" multi-select when the entry is loaded. It keeps the other option values when saving, because the field isn't shown when the calendar list is unavailable. |
| `strings.json`, `translations/en.json` | Label and help text for the new option. |

### Creating events with a colour label

Creating an event on a colour entity gives it that entity's label in Google. Core can't write labels: Google only accepts `eventLabelId` on writes when the request has the query parameter `eventLabelVersion=1`, and gcal_sync 9.2.0 has no way to send it.

| File | Change |
|--|--|
| `api.py` | **New** `LabelCalendarService`, a `GoogleCalendarService` subclass. Its `async_create_event` posts the same body as gcal_sync, plus `params={"eventLabelVersion": "1"}`, when the event has `event_label_id`; otherwise it calls gcal_sync unchanged. It keeps its own reference to the auth object rather than using gcal_sync's private `_auth`. Check it when upgrading gcal_sync, and drop it if gcal_sync adds the parameter. |
| `__init__.py` | `async_setup_entry` creates a `LabelCalendarService` instead of a `GoogleCalendarService`. |
| `calendar.py` | `GoogleCalendarEntity` gets a `new_event_label_id` property (None, so core behaviour is unchanged). The entity's `async_create_event` and the `google.create_event` action set it as the new event's `event_label_id`. `GoogleCalendarColourEntity` returns its label, except for Other and the default colour's entity, which write no label so the event follows the calendar's default colour. Colour entities are writable when the real calendar is, except Other when the default colour's label is named (an uncoloured event would land on that label's entity, not Other). Delete is core's, on the shared coordinator. Core has no update-event support, so there is none here either. |

Known limitation: a colour entity's state (the current/next event) only looks at the calendar's next 20 upcoming events (core's `MAX_UPCOMING_EVENTS`), so a colour with no event among them shows as off. Event lists from `async_get_events` (which cards use) are not affected.

## Packaging changes

These changes make core's code run as a custom integration. They don't change how it behaves.

| File | Change | Why |
|--|--|--|
| `manifest.json` | `name` is "Google Calendar (event colours)". Adds `version` and `issue_tracker`, and points `documentation` and `codeowners` at this repo. | Hassfest requires these for custom integrations. |
| `manifest.json` | `requirements` uses ranges such as `gcal-sync>=9.2.0,<10` instead of core's `==` pins. | Hassfest rejects exact pins on packages Home Assistant depends on. The `<next major` cap blocks untested breaking releases. The dev and CI requirements still use core's exact versions. |
| `translations/en.json` | Added. Generated from `strings.json` with the `[%key:...%]` references filled in from core's `homeassistant/strings.json`. | Core builds this file at release time, but a custom integration only loads `translations/`. |
| `strings.json`, `quality_scale.yaml` | Kept as files. `quality_scale.yaml` is unchanged. `strings.json` only changes for features listed above, and `translations/en.json` is regenerated from it. | Kept as a reference for diffing against core. Home Assistant ignores both files for a custom integration. |

## Test changes

| File | Change | Why |
|--|--|--|
| `tests/components/google/*.py` | `homeassistant.components.google` becomes `custom_components.google`, in imports and in `patch()` targets. `tests.*` helpers become `pytest_homeassistant_custom_component.*`. Imports re-sorted by ruff. | The tests have to run outside core. |
| `tests/conftest.py`, `pytest.ini`, `tests/__init__.py`, `tests/components/__init__.py` | Added. | They turn on custom integrations and asyncio mode for pytest. |
| `tests/conftest.py` | Defines the `snapshot` fixture with `HomeAssistantSnapshotExtension`, as core's `tests/conftest.py` does. | pytest-homeassistant-custom-component sets this from a plugin, so it only applies if that plugin loads after syrupy. In CI syrupy loaded last, looked in `__snapshots__/` and failed `test_diagnostics` with "Snapshot does not exist". |
| `tests/ruff.toml` | Core's test ruff config, with first-party packages adjusted. | Matches how core lints its tests. |
| `tests/components/google/test_colours.py` | **New.** Tests for the colour split rule, the colour entities, rename stability, stale-entity clean-up, the options flow, and creating (label in the body, `eventLabelVersion=1` in the URL only when writing a label) and deleting events on colour entities. | Tests for the colour entities. |

## Upgrading to a new core version

1. Clone core at the new tag into `../ha-core-ref`.
2. See what changed upstream since the base:
   ```sh
   git worktree add ../ha-base 6ce6cd2
   diff -r ../ha-base/custom_components/google ../ha-core-ref/homeassistant/components/google
   ```
3. Copy the new core files in as a fresh "unmodified" commit, then re-apply every change listed above.
4. Update this file's **Base** section, the version pins in `requirements_dev.txt` and `hacs.json`, and the `pytest-homeassistant-custom-component` version.
