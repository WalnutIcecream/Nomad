from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from shared.protocol.enums import WorldStatus


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    username: str


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=8, max_length=256)


class LoginResponse(BaseModel):
    token: str
    user: UserOut


class WorldCreate(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    minecraft_version: str = Field(min_length=1, max_length=32)


class MemberOut(BaseModel):
    user_id: uuid.UUID
    username: str
    role: str


class MemberAddRequest(BaseModel):
    user_id: uuid.UUID


class WorldOut(BaseModel):
    id: uuid.UUID
    name: str
    status: WorldStatus
    latest_version: int | None = None
    current_host: uuid.UUID | None = None
    current_host_name: str | None = None
    minecraft_version: str
    server_software: str
    member_count: int = 0
    connection: ConnectionInfo | None = None


class WorldStatusOut(BaseModel):
    id: uuid.UUID
    status: WorldStatus
    current_host: uuid.UUID | None = None
    current_host_name: str | None = None
    latest_version: int | None = None


class AcquireResponse(BaseModel):
    acquired: bool
    lease_id: uuid.UUID | None = None
    host_user_id: uuid.UUID | None = None
    current_host: uuid.UUID | None = None
    current_host_name: str | None = None


class ConnectionInfo(BaseModel):
    mode: str = "direct"  # direct | relay
    address: str | None = None  # host:port for direct
    relay_token: str | None = None  # token for relay mode
    relay_host: str | None = None
    relay_port: int | None = None


class ConnectionInfoUpdate(BaseModel):
    mode: str
    address: str | None = None
    relay_token: str | None = None
    relay_host: str | None = None
    relay_port: int | None = None


class WorldConnectionOut(BaseModel):
    world_id: uuid.UUID
    connection: ConnectionInfo | None = None


class RelayTokenResponse(BaseModel):
    token: str
    relay_host: str
    relay_port: int


class HeartbeatRequest(BaseModel):
    lease_id: uuid.UUID


class ReleaseRequest(BaseModel):
    lease_id: uuid.UUID


class SnapshotPrepareRequest(BaseModel):
    lease_id: uuid.UUID
    base_version: int


class SnapshotPrepareResponse(BaseModel):
    upload_id: uuid.UUID
    upload_url: str
    expected_size_bytes: int = 0


class SnapshotCompleteRequest(BaseModel):
    upload_id: uuid.UUID
    lease_id: uuid.UUID
    minecraft_version: str
    server_version: str | None = None


class SnapshotCompleteResponse(BaseModel):
    version_number: int


class VersionOut(BaseModel):
    version_number: int
    created_by: uuid.UUID
    minecraft_version: str
    server_version: str | None = None
    created_at: datetime


class RestoreRequest(BaseModel):
    version_number: int


class RestoreResponse(BaseModel):
    download_url: str
