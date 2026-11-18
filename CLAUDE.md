# CLAUDE.md

A custom version of Home Assistant's core `google` (Google Calendar) integration. It overrides the built-in integration, so the existing OAuth config entry is reused, and adds one virtual calendar entity per Google event colour label. The design, spike findings and current status are in [plan.md](plan.md).

## Rules

- **Track every difference from core.** `custom_components/google/` is core's integration at HA 2026.11.0, and commit `2555cde` is the unmodified copy (`git diff 2555cde -- custom_components/google tests/components/google`). Merge re-base pull requests with a merge commit, so the unmodified copy stays in the history. Any change to the integration or its tests must update [DIFFERENCES_FROM_CORE.md](DIFFERENCES_FROM_CORE.md) in the same commit: add a row to the Feature, Packaging or Test section saying what changed, in which files, and why. That file is how the changes get re-applied when re-basing on a newer core version.
- **No personal data in repo files.** The repo is public. Docs, tests, fixtures and commit messages use sample labels only (Parent1, Parent2, Child1, Child2, Medical, Everyone), never real label names, people's names, schools, calendar or entity IDs, or email addresses. The dev HA is connected to a real calendar: data read from it can be shown in chat, but goes into files only in this generic form. Check docs for real names before committing.
- **Never commit `config/`** other than `config/configuration.yaml`. It holds the dev HA's real Google credentials and data, and git ignores it.
- **Bump the release with the change.** A pull request that changes what users get (the integration's code, strings or requirements) also bumps `version` in [manifest.json](custom_components/google/manifest.json): patch for fixes, minor for new features or a new minimum Home Assistant version, major for breaking changes. Add the release to the status list in [plan.md](plan.md), and put draft release notes in the PR description, written for HACS users. After the PR is merged with a merge commit, tag the merge commit `v<version>` on `main` and publish a GitHub release from the tag; HACS offers the update from the release. Test-only, docs-only and CI-only changes don't bump the version.

## Commands

- `scripts/setup`: install the dev requirements (pinned to core's versions at 2026.11.0).
- `scripts/develop`: run the dev HA on port 8123, using `config/`.
- `scripts/lint`: `ruff format` and `ruff check --fix`. The repo uses ruff, not black (CONTRIBUTING.md is out of date).
- `python3 -m pytest`: run the tests (core's google tests, ported, plus `test_colours.py`).

CI runs hassfest, HACS validation, ruff and pytest, and weekly (`latest-ha.yml`, an early warning that a re-base is needed) diffs core's Google integration between the base version and the latest stable Home Assistant and runs pytest against the latest stable. Renovate is told not to bump Home Assistant or the integration's requirements, which stay at core's versions. Hassfest's "Domain collides with built-in core integration" warning is expected.
