from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class ControllerSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="NOMAD_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    database_url: str = "postgresql+psycopg://nomad:nomad_dev@localhost:5432/nomad"
    blob_dir: str = "data/blobs"
    relay_host: str = "localhost"
    relay_port: int = 9000
    session_ttl_seconds: int = 60 * 60 * 24 * 7
    lease_duration_seconds: int = 300
    heartbeat_interval_seconds: int = 20
    upload_ttl_seconds: int = 60 * 60
