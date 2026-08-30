"""Business logic for publishing *how to reach a hosted world*.

``connection_info`` is a JSONB column on the ``World`` entity, so this feature
has no repository of its own — it reads and writes that one column through
``WorldRepository.find_by_id`` and the entity itself. Keeping the little DB
work inline here (rather than inventing a one-method repository) is the
honest, readable choice.

Rules:
    * read    -> any member may fetch connection info
    * publish -> only the current host may set/overwrite it (403 otherwise)
    * token   -> only the current host may mint a relay token
"""

from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from backend.config import ControllerSettings
from backend.core.access import get_world_or_404, require_current_host, require_membership
from backend.domain.entities import User
from backend.domain.schemas import ConnectionInfoUpdate, RelayTokenResponse, WorldConnectionOut
from backend.features.worlds.repository import WorldRepository


class ConnectionService:
    def __init__(
        self,
        session: Session,
        settings: ControllerSettings,
        world_repository: WorldRepository | None = None,
    ) -> None:
        """Wire the service to the request session, settings and repository."""
        self.session = session
        self.settings = settings
        self.world_repository = world_repository if world_repository is not None else WorldRepository()

    def get_connection(self, world_id: uuid.UUID, user: User) -> WorldConnectionOut:
        """Current connection info for a world (any member may read it)."""
        world = get_world_or_404(self.session, world_id)
        require_membership(self.session, world, user)
        return WorldConnectionOut(world_id=world_id, connection=world.connection_info)

    def update_connection(
        self, world_id: uuid.UUID, user: User, payload: ConnectionInfoUpdate
    ) -> WorldConnectionOut:
        """The active host reports how players can reach its server."""
        world = get_world_or_404(self.session, world_id)
        require_membership(self.session, world, user)
        require_current_host(world, user)

        world.connection_info = payload.model_dump(exclude_none=True)
        self.session.commit()
        return WorldConnectionOut(world_id=world_id, connection=world.connection_info)

    def get_relay_token(self, world_id: uuid.UUID, user: User) -> RelayTokenResponse:
        """Mint a fresh relay token so hosts can be reached behind NAT."""
        world = get_world_or_404(self.session, world_id)
        require_membership(self.session, world, user)
        require_current_host(world, user)

        token = uuid.uuid4().hex
        world.connection_info = {
            "mode": "relay",
            "relay_token": token,
            "relay_host": self.settings.relay_host,
            "relay_port": self.settings.relay_port,
        }
        self.session.commit()
        return RelayTokenResponse(
            token=token,
            relay_host=self.settings.relay_host,
            relay_port=self.settings.relay_port,
        )