from __future__ import annotations

from typing import Any

from shared.protocol.enums import LauncherState

from launcher.state.persist import StateStore


class LauncherStateMachine:
    """Explicit state transitions for the launcher lifecycle.

    The machine is intentionally small: it validates allowed transitions and
    persists the current state, but actual side effects live in the CLI/runtime
    layers so the machine stays restart-friendly.
    """

    ALLOWED_TRANSITIONS: dict[LauncherState, set[LauncherState]] = {
        LauncherState.IDLE: {LauncherState.CHECK_AUTH},
        LauncherState.CHECK_AUTH: {LauncherState.CHECK_WORLD, LauncherState.ERROR},
        LauncherState.CHECK_WORLD: {LauncherState.HOST_EXISTS, LauncherState.ACQUIRE_HOST, LauncherState.ERROR},
        LauncherState.HOST_EXISTS: {LauncherState.IDLE, LauncherState.ERROR},
        LauncherState.ACQUIRE_HOST: {LauncherState.DOWNLOAD, LauncherState.HOST_EXISTS, LauncherState.ERROR},
        LauncherState.DOWNLOAD: {LauncherState.VALIDATE, LauncherState.IDLE, LauncherState.ERROR},
        LauncherState.VALIDATE: {LauncherState.START_SERVER, LauncherState.ERROR},
        LauncherState.START_SERVER: {LauncherState.HOSTING, LauncherState.ERROR},
        LauncherState.HOSTING: {LauncherState.STOPPING, LauncherState.RECOVER},
        LauncherState.STOPPING: {LauncherState.SNAPSHOTTING, LauncherState.ERROR},
        LauncherState.SNAPSHOTTING: {LauncherState.UPLOADING, LauncherState.IDLE, LauncherState.ERROR},
        LauncherState.UPLOADING: {LauncherState.RELEASE_LEASE, LauncherState.ERROR},
        LauncherState.RELEASE_LEASE: {LauncherState.IDLE, LauncherState.ERROR},
        LauncherState.RECOVER: {LauncherState.IDLE, LauncherState.ERROR},
        LauncherState.ERROR: {LauncherState.IDLE, LauncherState.RECOVER},
    }

    def __init__(self, store: StateStore) -> None:
        self.store = store
        self._state = LauncherState(store.load()["state"])

    @property
    def state(self) -> LauncherState:
        return self._state

    def transition(self, target: LauncherState) -> None:
        if target not in self.ALLOWED_TRANSITIONS.get(self._state, set()):
            raise ValueError(f"invalid transition: {self._state.value} -> {target.value}")
        self._state = target
        self._persist()

    def start_at(self, target: LauncherState) -> None:
        """Jump directly to a state for standalone operations.

        This bypasses normal transition validation and is intended for
        commands like ``snapshot create`` that begin mid-flow without a
        running server.
        """
        self._state = target
        self._persist()

    def update_context(self, **fields: Any) -> None:
        data = self.store.load()
        data.update(fields)
        self.store.save(data)

    def _persist(self) -> None:
        data = self.store.load()
        data["state"] = self._state.value
        self.store.save(data)

    def get_context(self) -> dict[str, Any]:
        data = self.store.load()
        data["state"] = self._state.value
        return data
