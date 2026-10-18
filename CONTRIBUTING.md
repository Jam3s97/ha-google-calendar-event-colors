# Contributing

Bug reports, questions and pull requests are welcome. This is a small, personal project: a custom version of Home Assistant's Google Calendar integration that adds one calendar entity per event colour. Its event colour features were written with Claude Code (see the [README](README.md)).

## Reporting a bug

[Open an issue](../../issues/new/choose) using the bug template. Helpful details:

- Your Home Assistant version (this release is built for 2026.10.1).
- What you did, what you expected, and what happened.
- Debug logs. Add this to `configuration.yaml`, restart and reproduce:

  ```yaml
  logger:
    logs:
      custom_components.google: debug
  ```

Leave out anything private: calendar names, event titles, email addresses and calendar IDs.

## Pull requests

1. Fork the repository and branch from `main`.
2. Make your change, with tests where it changes behaviour.
3. **Record any change to the integration or its tests in [DIFFERENCES_FROM_CORE.md](DIFFERENCES_FROM_CORE.md)**, in the same pull request: what changed, in which files, and why. `custom_components/google/` is Home Assistant core's integration with this project's changes on top, and that file is how they're re-applied on a newer core version. Keep changes to core's own code as small as you can.
4. Use sample data only in code, tests and docs (labels such as Parent1, Child1, Medical, Everyone), never real names, calendars or email addresses.
5. Make sure lint and tests pass (below), then open the pull request.

## Development

Open the repository in VS Code's devcontainer. It comes with a development Home Assistant on the same version as the integration's core code.

- `scripts/setup`: install the development requirements.
- `scripts/develop`: run the development Home Assistant on port 8123, using `config/`. Only `config/configuration.yaml` is tracked; the rest of `config/` (including your Google login) is ignored by git.
- `scripts/lint`: format and lint with ruff, using core's ruff configuration.
- `python3 -m pytest`: run the tests (core's Google Calendar tests plus this project's).

CI runs hassfest, HACS validation, ruff and pytest on every push and pull request, and once a week runs the tests against the latest stable Home Assistant as an early warning that a re-base is needed.

## License

By contributing, you agree that your contributions are licensed under the [Apache License 2.0](LICENSE), the same as the rest of the project.
