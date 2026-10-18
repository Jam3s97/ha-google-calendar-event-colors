# Google Calendar (event colours)

A custom version of Home Assistant's built-in Google Calendar (`google`) integration. It is based on the core integration at Home Assistant **2026.10.1** and overrides the built-in one when installed. It adds one virtual calendar entity per Google event colour, so dashboard calendar cards can colour events per person while you keep using a single shared Google calendar.

> Work in progress. The colour split is not implemented yet; the design and spike findings are in [plan.md](plan.md). The code is currently core's integration, unchanged apart from the packaging changes in [DIFFERENCES_FROM_CORE.md](DIFFERENCES_FROM_CORE.md).

## Installation

1. In HACS, add this repository as a custom repository (category: Integration).
2. Install **Google Calendar (event colours)** and restart Home Assistant.
3. Home Assistant will log a warning that a custom integration overrides the built-in `google` integration. This is expected. Your existing Google Calendar config entry and entities are reused.

## Relationship to core

`custom_components/google/` was copied from [home-assistant/core](https://github.com/home-assistant/core/tree/2026.10.1/homeassistant/components/google) at tag `2026.10.1`. Commit `6ce6cd2` holds that copy unchanged, so `git diff 6ce6cd2 -- custom_components/google` shows everything this project changes.

Every difference from core, and why it was made, is listed in [DIFFERENCES_FROM_CORE.md](DIFFERENCES_FROM_CORE.md), along with the steps for upgrading to a new core version.

## CI

- **Hassfest** passes, with one expected warning: "Domain collides with built-in core integration". Overriding `google` is deliberate, because it reuses the existing OAuth config entry.
- **HACS validation** ignores `brands`, since this repo has no brand images. It also checks GitHub repository settings: the repo needs a description and topics.
- **Lint** (ruff) and **Test** (pytest) run on every push and pull request.

## Development

Open the repo in the devcontainer and run `scripts/develop` to start a dev Home Assistant (2026.10.1) on port 8123. Run the tests with `python3 -m pytest`.

This repository was scaffolded from [ludeeus/integration_blueprint](https://github.com/ludeeus/integration_blueprint).
