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

None yet. Planned: one virtual calendar entity per Google event colour, an options flow that maps colours to labels, and creating events with a colour from a virtual entity.

<!-- Add one entry per change: what it does, which files, and why core's behaviour wasn't enough. -->

## Packaging changes

These changes make core's code run as a custom integration. They don't change how it behaves.

| File | Change | Why |
|--|--|--|
| `manifest.json` | `name` is "Google Calendar (event colours)". Adds `version` and `issue_tracker`, and points `documentation` and `codeowners` at this repo. | Hassfest requires these for custom integrations. |
| `manifest.json` | `requirements` uses ranges such as `gcal-sync>=9.2.0,<10` instead of core's `==` pins. | Hassfest rejects exact pins on packages Home Assistant depends on. The `<next major` cap blocks untested breaking releases. The dev and CI requirements still use core's exact versions. |
| `translations/en.json` | Added. Generated from `strings.json` with the `[%key:...%]` references filled in from core's `homeassistant/strings.json`. | Core builds this file at release time, but a custom integration only loads `translations/`. |
| `strings.json`, `quality_scale.yaml` | Kept unchanged. | Kept as a reference for diffing against core. Home Assistant ignores both files for a custom integration. |

## Test changes

| File | Change | Why |
|--|--|--|
| `tests/components/google/*.py` | `homeassistant.components.google` becomes `custom_components.google`, in imports and in `patch()` targets. `tests.*` helpers become `pytest_homeassistant_custom_component.*`. Imports re-sorted by ruff. | The tests have to run outside core. |
| `tests/conftest.py`, `pytest.ini`, `tests/__init__.py`, `tests/components/__init__.py` | Added. | They turn on custom integrations and asyncio mode for pytest. |
| `tests/ruff.toml` | Core's test ruff config, with first-party packages adjusted. | Matches how core lints its tests. |

## Upgrading to a new core version

1. Clone core at the new tag into `../ha-core-ref`.
2. See what changed upstream since the base:
   ```sh
   git worktree add ../ha-base 6ce6cd2
   diff -r ../ha-base/custom_components/google ../ha-core-ref/homeassistant/components/google
   ```
3. Copy the new core files in as a fresh "unmodified" commit, then re-apply every change listed above.
4. Update this file's **Base** section, the version pins in `requirements_dev.txt` and `hacs.json`, and the `pytest-homeassistant-custom-component` version.
