from __future__ import annotations

from enum import StrEnum


class WorldStatus(StrEnum):
    SLEEPING = "sleeping"
    STARTING = "starting"
    HOSTING = "hosting"
    STOPPING = "stopping"
    SYNCING = "syncing"
    ERROR = "error"


class MemberRole(StrEnum):
    OWNER = "owner"
    MEMBER = "member"


class LauncherState(StrEnum):
    IDLE = "idle"
    CHECK_AUTH = "check_auth"
    CHECK_WORLD = "check_world"
    HOST_EXISTS = "host_exists"
    ACQUIRE_HOST = "acquire_host"
    DOWNLOAD = "download"
    VALIDATE = "validate"
    START_SERVER = "start_server"
    HOSTING = "hosting"
    STOPPING = "stopping"
    SNAPSHOTTING = "snapshotting"
    UPLOADING = "uploading"
    RELEASE_LEASE = "release_lease"
    RECOVER = "recover"
    ERROR = "error"
