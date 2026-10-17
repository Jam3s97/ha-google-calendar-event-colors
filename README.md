# Google Calendar (event colours)

A custom version of Home Assistant's built-in Google Calendar (`google`) integration. It is based on the core integration at Home Assistant **2026.10.1** and overrides the built-in one when installed. It adds one virtual calendar entity per Google event colour, so dashboard calendar cards can colour events per person while you keep using a single shared Google calendar.

> Work in progress. The colour split is not implemented yet. The code is currently core's integration, unchanged apart from the packaging changes listed below.

## Installation

1. In HACS, add this repository as a custom repository (category: Integration).
2. Install **Google Calendar (event colours)** and restart Home Assistant.
3. Home Assistant will log a warning that a custom integration overrides the built-in `google` integration. This is expected. Your existing Google Calendar config entry and entities are reused.

## Relationship to core

`custom_components/google/` was copied from [home-assistant/core](https://github.com/home-assistant/core/tree/2026.10.1/homeassistant/components/google) at tag `2026.10.1`. Commit `6ce6cd2` holds that copy unchanged, so `git diff 6ce6cd2 -- custom_components/google` shows everything this project changes.

Changes needed to run as a custom integration:

- `manifest.json`: adds `version` and `issue_tracker`, and points `documentation` and `codeowners` at this repo. Requirements use a range from core's version up to the next major (for example `gcal-sync>=9.2.0,<10`), because hassfest rejects exact pins on packages Home Assistant already depends on.
- `translations/en.json`: generated from core's `strings.json`, with its `[%key:...%]` references filled in. Custom integrations only load `translations/`.
- Tests (`tests/components/google/`) import from `custom_components.google` and use [`pytest-homeassistant-custom-component`](https://github.com/MatthewFlamm/pytest-homeassistant-custom-component) in place of core's test helpers.
- `.ruff.toml` and `tests/ruff.toml` are core's ruff configuration from the same tag.

Upgrading Home Assistant: re-copy from the matching core tag deliberately, then re-apply this project's diff.

## CI

- **Hassfest** passes, with one expected warning: "Domain collides with built-in core integration". Overriding `google` is deliberate, because it reuses the existing OAuth config entry.
- **HACS validation** ignores `brands`, since this repo has no brand images. It also checks GitHub repository settings: the repo needs a description and topics.
- **Lint** (ruff) and **Test** (pytest) run on every push and pull request.

## Development

Open the repo in the devcontainer and run `scripts/develop` to start a dev Home Assistant (2026.10.1) on port 8123. Run the tests with `python3 -m pytest`.

This repository was scaffolded from [ludeeus/integration_blueprint](https://github.com/ludeeus/integration_blueprint).
