# Differences from core

This file lists every way `custom_components/google/` (and its tests) differs from Home Assistant core's `google` integration. Update it in the same commit as any change to the integration.

## Base

| | |
|--|--|
| Core version | `2026.11.0` |
| Source | [`homeassistant/components/google`](https://github.com/home-assistant/core/tree/2026.11.0/homeassistant/components/google) and [`tests/components/google`](https://github.com/home-assistant/core/tree/2026.11.0/tests/components/google) |
| Unmodified copy | commit `2555cde` (the previous base, 2026.10.1, was `6ce6cd2`) |

To see the full diff against core:

```sh
git diff 2555cde -- custom_components/google tests/components/google
```

## Feature changes

<!-- Add one entry per change: what it does, which files, and why core's behaviour wasn't enough. -->

### Colour entities

Core exposes one calendar entity per Google calendar, and per-event colour never reaches dashboard cards. This adds, for each calendar the user chooses to split, one virtual calendar entity per event colour label, so cards can colour events per entity.

| File | Change |
|--|--|
| `colours.py` | **New.** Colour tables (label colour, legacy event `colorId` and legacy calendar colour for each of the 24 picker colours) and `ColourSplit`, which assigns each event to a label: `eventLabelId`, else `colorId`, else the calendar's default colour (named or not), else Other. |
| `const.py` | Adds the `CONF_SPLIT_CALENDARS` option. |
| `calendar.py` | `GoogleCalendarColourEntity` (a subclass of `GoogleCalendarEntity`) adds a colour check to `_event_filter` and **shares the real calendar's coordinator**, so there is no extra sync. `_async_colour_entities()` makes one `calendars.get` call per split calendar to read its labels, creates entities up front for named labels and the default colour's label (named or not), and for Other only when the calendar's colour matches no label. It adds entities for unnamed labels, and otherwise Other, once events use them, and removes stale colour entities after the first refresh. `async_setup_entry` calls it for split calendars and removes colour entities of calendars that are no longer split. Colour entity unique IDs are `<calendar unique id>-colour-<label id or other>`. |
| `config_flow.py` | The options flow adds a "Calendars to split by event colour" multi-select when the entry is loaded. It keeps the other option values when saving, because the field isn't shown when the calendar list is unavailable. |
| `strings.json`, `translations/en.json` | Label and help text for the new option. |

### Creating events with a colour label

Creating an event on a colour entity gives it that entity's label in Google. Core can't write labels: Google only accepts `eventLabelId` on writes when the request has the query parameter `eventLabelVersion=1`, and gcal_sync 9.2.0 has no way to send it.

| File | Change |
|--|--|
| `api.py` | **New** `LabelCalendarService`, a `GoogleCalendarService` subclass. Its `async_create_event` posts the same body as gcal_sync, plus `params={"eventLabelVersion": "1"}`, when the event has `event_label_id`; otherwise it calls gcal_sync unchanged. It keeps its own reference to the auth object rather than using gcal_sync's private `_auth`. Check it when upgrading gcal_sync, and drop it if gcal_sync adds the parameter. |
| `__init__.py` | `async_setup_entry` creates a `LabelCalendarService` instead of a `GoogleCalendarService`. |
| `calendar.py` | `GoogleCalendarEntity` gets a `new_event_label_id` property (None, so core behaviour is unchanged). The entity's `async_create_event` sets it as the new event's `event_label_id`. `GoogleCalendarColourEntity` returns its label, except for Other and the default colour's entity, which write no label so the event follows the calendar's default colour. Colour entities are writable when the real calendar is, except Other when the calendar's colour matches a label (an uncoloured event would land on that label's entity, not Other). Delete is core's, on the shared coordinator. Core has no update-event support, so there is none here either. |
| `services.py` | The `google.create_event` action sets the entity's `new_event_label_id` as the new event's `event_label_id`. (Core moved the action here from `calendar.py` in 2026.11.0.) |

### Prompting the user to name colours

Colours without a name in Google get entities named after the colour. Core has nothing like this, so this adds a Repairs issue and an options-flow step that list them with sample events, and an option to turn both off for users who are happy with colour names.

| File | Change |
|--|--|
| `colours.py` | `unnamed_colours_in_use()` returns, for each unnamed label in use, the number of events (a recurring series counts once) and up to 3 samples, upcoming first. `events_in_scan_window()` holds the scan window (90 days back, a year ahead) shared by the entity code and the options flow. |
| `const.py` | Adds the `CONF_IGNORE_UNNAMED_COLOURS` option ("Don't remind me to name colours"). |
| `colour_issues.py` | **New.** Creates, replaces and deletes the fixable Repairs issue `unnamed_colours` (with `data={"entry_id": ...}`), one per split calendar. The issue id is `unnamed_colours_<entry id>_<calendar hash>_<hash of the unnamed label ids>`, so an ignored issue comes back as a new one when the set of unnamed colours changes. |
| `repairs.py` | **New.** The issue's fix flow: a menu that repeats the colour list and offers **I've named them: reload the integration** (schedules a reload) or **Don't remind me to name colours** (sets `CONF_IGNORE_UNNAMED_COLOURS` and schedules a reload). The menu passes `step_id`, which Home Assistant requires from 2027.5.0. |
| `store.py` | `GoogleRuntimeData` gains `coordinators` (each calendar's main sync coordinator, keyed by calendar id) and `unnamed_colours` (the latest result per split calendar), both read by the options flow. |
| `calendar.py` | Registers each calendar's main sync coordinator in `runtime_data.coordinators`. The colour entities' coordinator listener also stores the unnamed colours and updates the issue; with `CONF_IGNORE_UNNAMED_COLOURS` on it stores none, which deletes the issue. Setup deletes the issues of calendars that are no longer split. |
| `__init__.py` | `async_remove_entry` deletes the entry's issues. |
| `config_flow.py` | The options form adds a "Don't remind me to name colours" checkbox (shown with the split multi-select). Unless it's ticked, the options form's description lists unnamed colours in calendars that are already split. When a newly ticked calendar uses unnamed colours, a second step (`unnamed_colours`) lists them with samples and asks **Split now** or **Wait**. Wait is the default; it leaves those calendars unsplit and is followed by a `not_split` confirmation step (a form, because the options dialog ignores a `create_entry` description). It reads the labels with one `calendars.get` call per newly ticked calendar, and the events from that calendar's coordinator. With the checkbox ticked (in the same submit or before), the `unnamed_colours` step is skipped. |
| `strings.json`, `translations/en.json` | Text for the options description, the `unnamed_colours` and `not_split` steps, the `unnamed_action` selector, the `ignore_unnamed_colours` checkbox, and the `unnamed_colours` issue and its fix flow. |

### Colour attribute

Some cards (for example the Daylight Calendar Card) set a colour per entity in their own config and ignore the entity colour Home Assistant stores. This shows each colour entity's Google colour, so the user can copy it into the card's config.

| File | Change |
|--|--|
| `calendar.py` | `GoogleCalendarColourEntity.extra_state_attributes` adds `color`: the label's hex colour from Google, or the calendar's colour for Other (the same value as the entity's `initial_color`). The real calendar entity's attributes are unchanged. |

Known limitation: a colour entity's state (the current/next event) only looks at the calendar's next 20 upcoming events (core's `MAX_UPCOMING_EVENTS`), so a colour with no event among them shows as off. Event lists from `async_get_events` (which cards use) are not affected.

## Packaging changes

These changes make core's code run as a custom integration. They don't change how it behaves.

| File | Change | Why |
|--|--|--|
| `manifest.json` | `name` is "Google Calendar (event colours)". Adds `version` and `issue_tracker`, and points `documentation` and `codeowners` at this repo. | Hassfest requires these for custom integrations. |
| `manifest.json` | Sets the integration version (`1.1.0`). | HACS and the Marketplace show it, and hassfest requires it. |
| `manifest.json` | `requirements` uses ranges such as `gcal-sync>=9.2.0,<10` instead of core's `==` pins. | Hassfest rejects exact pins on packages Home Assistant depends on. The `<next major` cap blocks untested breaking releases. The dev and CI requirements still use core's exact versions. |
| `translations/en.json` | Added. Generated from `strings.json` with the `[%key:...%]` references filled in from core's `homeassistant/strings.json`. | Core builds this file at release time, but a custom integration only loads `translations/`. |
| `strings.json`, `quality_scale.yaml` | Kept as files. `quality_scale.yaml` is unchanged. `strings.json` only changes for features listed above, and `translations/en.json` is regenerated from it. | Kept as a reference for diffing against core. Home Assistant ignores both files for a custom integration. |

## Test changes

| File | Change | Why |
|--|--|--|
| `tests/components/google/*.py` | `homeassistant.components.google` becomes `custom_components.google`, in imports and in `patch()` targets. Core's test helpers (`pytest_homeassistant.*` since 2026.11.0, `tests.*` before) become `pytest_homeassistant_custom_component.*`. Imports re-sorted by ruff. | The tests have to run outside core. |
| `tests/conftest.py`, `pytest.ini`, `tests/__init__.py`, `tests/components/__init__.py` | Added. | They turn on custom integrations and asyncio mode for pytest. |
| `tests/conftest.py` | Defines the `snapshot` fixture with `HomeAssistantSnapshotExtension`, as core's `tests/conftest.py` does. | pytest-homeassistant-custom-component sets this from a plugin, so it only applies if that plugin loads after syrupy. In CI syrupy loaded last, looked in `__snapshots__/` and failed `test_diagnostics` with "Snapshot does not exist". |
| `tests/ruff.toml` | Core's test ruff config, with first-party packages adjusted. | Matches how core lints its tests. |
| `tests/components/google/test_colours.py` | **New.** Tests for the colour split rule, the colour entities, rename stability, stale-entity clean-up, the options flow, creating (label in the body, `eventLabelVersion=1` in the URL only when writing a label) and deleting events on colour entities, unnamed-colour detection, the Repairs issue (including that an ignored issue returns for a new colour) and its fix flow, the options flow's unnamed-colours step, the "Don't remind me" option, an unnamed default colour, when Other is created or removed, and the `color` attribute. | Tests for the colour entities. |

## Upgrading to a new core version

The unmodified copies form a side branch: each one is core's files at a tag, committed on top of the previous copy. Merging the new copy into this branch makes git apply core's upstream changes to the modified files, so only real conflicts need resolving. (Used for 2026.11.0.)

1. Export core's files at the new tag, for example with a blobless clone: `git fetch --depth 1 --filter=blob:none origin refs/tags/<tag>:refs/tags/<tag>`, then `git archive <tag> homeassistant/components/google tests/components/google`.
2. Make the new unmodified copy on top of the current base, byte for byte:
   ```sh
   git worktree add -b core/<tag> ../ha-base <current base>
   # in ../ha-base: replace custom_components/google and tests/components/google with core's files
   git commit -am "Import core google integration from HA <tag> (unmodified)"
   ```
3. Merge it into a working branch: `git merge --no-ff core/<tag>`. Resolve conflicts by keeping core's change and re-applying ours as listed above.
4. In the tests, rename core's test helper imports (`pytest_homeassistant.*`) and `homeassistant.components.google` as described under **Test changes**. New core test code arrives through the merge, so check the whole files, not only the conflicts.
5. Regenerate `translations/en.json` from `strings.json`, filling the `[%key:...%]` references from core's `homeassistant/strings.json` at the new tag.
6. Update this file's **Base** section, the version pins in `requirements_dev.txt` (Home Assistant, the integration's requirements, and the `pytest-homeassistant-custom-component` release that pins that Home Assistant version), `hacs.json`, and `CLAUDE.md`.
7. Merge the pull request with a merge commit, not squash or rebase, so the unmodified copy stays in the history.
