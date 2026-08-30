from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from shared.protocol.enums import WorldStatus


class World(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: UUID
    name: str
    owner_id: UUID
    members: list[UUID] = Field(default_factory=list)
    latest_version: int | None = None
    current_host: UUID | None = None
    minecraft_version: str
    server_software: str = "vanilla"
    configuration: dict = Field(default_factory=dict)
    storage_backend: str = "github"
    status: WorldStatus = WorldStatus.SLEEPING


class Lease(BaseModel):
    model_config = ConfigDict(frozen=True)

    world_id: UUID
    host_user_id: UUID
    lease_id: UUID
    expires_at: datetime


class VersionMetadata(BaseModel):
    model_config = ConfigDict(frozen=True)

    version: int
    created_at: datetime
    created_by: UUID
    minecraft_version: str
    server_version: str | None = None
    storage_key: str
    sha256: str | None = None


Gamemode = Literal["survival", "creative", "adventure", "spectator"]
Difficulty = Literal["peaceful", "easy", "normal", "hard"]


class ServerProperties(BaseModel):
    """Whitelisted, validated subset of Minecraft's server.properties."""

    model_config = ConfigDict(frozen=True)

    motd: str = "A Nomad Minecraft Server"
    level_name: str = "world"
    level_type: str = "minecraft:normal"
    level_seed: str = ""
    gamemode: Gamemode = "survival"
    difficulty: Difficulty = "normal"
    white_list: bool = False
    enforce_whitelist: bool = False
    max_players: int = Field(default=20, ge=1, le=1000)
    online_mode: bool = True
    view_distance: int = Field(default=10, ge=3, le=32)
    simulation_distance: int = Field(default=10, ge=3, le=32)
    pvp: bool = True
    spawn_protection: int = Field(default=16, ge=0, le=100)
    server_port: int = Field(default=25565, ge=1, le=65535)
    server_ip: str = ""
    enable_command_block: bool = False
    hardcore: bool = False

    _PROPERTY_KEYS = {
        "motd": "motd",
        "level_name": "level-name",
        "level_type": "level-type",
        "level_seed": "level-seed",
        "gamemode": "gamemode",
        "difficulty": "difficulty",
        "white_list": "white-list",
        "enforce_whitelist": "enforce-whitelist",
        "max_players": "max-players",
        "online_mode": "online-mode",
        "view_distance": "view-distance",
        "simulation_distance": "simulation-distance",
        "pvp": "pvp",
        "spawn_protection": "spawn-protection",
        "server_port": "server-port",
        "server_ip": "server-ip",
        "enable_command_block": "enable-command-block",
        "hardcore": "hardcore",
    }

    def to_properties_lines(self) -> list[str]:
        lines: list[str] = []
        for field_name, key in self._PROPERTY_KEYS.items():
            value = getattr(self, field_name)
            if isinstance(value, bool):
                rendered = "true" if value else "false"
            else:
                rendered = str(value)
            lines.append(f"{key}={rendered}")
        return lines
