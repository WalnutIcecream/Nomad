from __future__ import annotations

from pathlib import Path

import pytest

from shared.protocol.enums import LauncherState

from launcher.state.machine import LauncherStateMachine
from launcher.state.persist import StateStore


@pytest.fixture
def machine(tmp_path: Path) -> LauncherStateMachine:
    return LauncherStateMachine(StateStore(tmp_path / "state.json"))


def test_initial_state(machine: LauncherStateMachine) -> None:
    assert machine.state == LauncherState.IDLE


def test_valid_transition(machine: LauncherStateMachine) -> None:
    machine.transition(LauncherState.CHECK_AUTH)
    assert machine.state == LauncherState.CHECK_AUTH


def test_invalid_transition_raises(machine: LauncherStateMachine) -> None:
    with pytest.raises(ValueError, match="invalid transition"):
        machine.transition(LauncherState.HOSTING)


def test_state_persists_across_instances(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "state.json")
    first = LauncherStateMachine(store)
    first.transition(LauncherState.CHECK_AUTH)

    second = LauncherStateMachine(StateStore(tmp_path / "state.json"))
    assert second.state == LauncherState.CHECK_AUTH


def test_start_at_begins_mid_flow(machine: LauncherStateMachine) -> None:
    machine.start_at(LauncherState.SNAPSHOTTING)
    assert machine.state == LauncherState.SNAPSHOTTING


def test_context_update_and_retrieve(machine: LauncherStateMachine) -> None:
    machine.update_context(world_id="abc", base_version=3)
    context = machine.get_context()
    assert context["world_id"] == "abc"
    assert context["base_version"] == 3
