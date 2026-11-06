"""Global fixtures for the custom google integration tests."""

import pytest
from syrupy.assertion import SnapshotAssertion

from pytest_homeassistant.syrupy import HomeAssistantSnapshotExtension


@pytest.fixture
def snapshot(snapshot: SnapshotAssertion) -> SnapshotAssertion:
    """Return snapshot assertion fixture with the Home Assistant extension.

    pytest-homeassistant overrides syrupy's fixture from a plugin, so which
    one wins depends on plugin load order, which differs between machines.
    When syrupy wins, snapshots are looked up in `__snapshots__/` instead of
    `snapshots/`. A conftest fixture always wins.
    """
    return snapshot.use_extension(HomeAssistantSnapshotExtension)
