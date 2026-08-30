"""Business rules for the host lease lifecycle: acquire, heartbeat, release.

The algorithm (unchanged semantics, clearer shape):

    ACQUIRE
      1. load the world (404) and check membership (403)
      2. lock the world row FOR UPDATE — serialises concurrent acquirers
      3. expire a stale lease if its deadline already passed
      4. if another active lease remains -> tell the caller who owns it
      5. otherwise insert our lease, mark the world STARTING, commit

    HEARTBEAT
      1. find our lease for this world; it must be active and ours (409)
      2. push its expiry forward by the configured lease duration

    RELEASE
      1. find our lease (404 if missing or not ours)
      2. if still active, mark it released and put the world back to SLEEPING

Why expiry is not instant: a transient network blip drops heartbeats, so the
lease merely starts approaching its deadline. Ownership is only truly lost
once ``expires_at`` passes without renewal.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from backend.config import ControllerSettings
from backend.core.access import get_world_or_404, require_membership
from backend.domain.entities import HostLease, User, World
from backend.domain.schemas import AcquireResponse, HeartbeatRequest, ReleaseRequest
from backend.features.auth.repository import UserRepository
from backend.features.hosting.repository import HostLeaseRepository
from backend.features.worlds.repository import WorldRepository


def is_lease_active(lease: HostLease, now: datetime) -> bool:
    """A lease is live only while it is active AND not past its deadline."""
    return lease.status == "active" and lease.expires_at > now


class HostingService:
    def __init__(
        self,
        session: Session,
        settings: ControllerSettings,
        world_repository: WorldRepository | None = None,
        lease_repository: HostLeaseRepository | None = None,
        user_repository: UserRepository | None = None,
    ) -> None:
        """Wire the service to the request session, settings and repositories."""
        self.session = session
        self.settings = settings
        self.world_repository = world_repository if world_repository is not None else WorldRepository()
        self.lease_repository = lease_repository if lease_repository is not None else HostLeaseRepository()
        self.user_repository = user_repository if user_repository is not None else UserRepository()

    def _host_name(self, host_user_id: uuid.UUID) -> str | None:
        """Username for a host id, or None (defensive: host could be gone)."""
        host = self.user_repository.find_by_id(self.session, host_user_id)
        return host.username if host is not None else None

    def _lease_expiry(self, now: datetime) -> datetime:
        """When a freshly acquired lease should expire."""
        return now + timedelta(seconds=self.settings.lease_duration_seconds)

    def _expire_stale_lease(self, world: World, now: datetime) -> None:
        """If the active lease has passed its deadline, retire it and let the
        world fall back to SLEEPING."""
        active_lease = self.lease_repository.find_active_for_world(
            self.session, world.id
        )
        if active_lease is not None and active_lease.expires_at <= now:
            self.lease_repository.mark_inactive(
                self.session, active_lease, new_status="expired", released_at=now
            )
            world.status = "sleeping"
            world.current_host_id = None

    def acquire(self, world_id: uuid.UUID, user: User) -> AcquireResponse:
        """Try to become the host of a world; never fails partially."""
        world = get_world_or_404(self.session, world_id)
        require_membership(self.session, world, user)
        now = datetime.now(timezone.utc)

        # Serialise concurrent acquirers for this world.
        locked_world = self.world_repository.lock_by_id(self.session, world_id)
        if locked_world is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="world not found"
            )

        # Re-check under the lock: previous work may have expired meanwhile.
        self._expire_stale_lease(locked_world, now)
        active_lease = self.lease_repository.find_active_for_world(
            self.session, world_id
        )

        if active_lease is not None:
            # Someone else already hosts. Commit the no-op (the lock must be
            # released with a transaction) and report who holds it.
            self.session.commit()
            return AcquireResponse(
                acquired=False,
                current_host=active_lease.host_user_id,
                current_host_name=self._host_name(active_lease.host_user_id),
            )

        lease = self.lease_repository.create(
            self.session,
            world_id=world_id,
            host_user_id=user.id,
            expires_at=self._lease_expiry(now),
        )
        locked_world.status = "starting"
        locked_world.current_host_id = user.id
        self.session.commit()

        return AcquireResponse(
            acquired=True,
            lease_id=lease.id,
            host_user_id=user.id,
            current_host=user.id,
            current_host_name=user.username,
        )

    def heartbeat(
        self, world_id: uuid.UUID, user: User, payload: HeartbeatRequest
    ) -> AcquireResponse:
        """Extend a lease, proving the host is still alive."""
        world = get_world_or_404(self.session, world_id)
        require_membership(self.session, world, user)
        now = datetime.now(timezone.utc)

        lease = self.lease_repository.find_by_world_and_lease(
            self.session, world_id, payload.lease_id
        )
        if lease is None or lease.host_user_id != user.id or not is_lease_active(lease, now):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT, detail="lease is not active"
            )

        lease.expires_at = self._lease_expiry(now)
        world.status = "hosting"  # a heartbeat proves the server is actually up
        self.session.commit()
        return AcquireResponse(
            acquired=True,
            lease_id=lease.id,
            host_user_id=user.id,
            current_host=user.id,
            current_host_name=user.username,
        )

    def release(
        self, world_id: uuid.UUID, user: User, payload: ReleaseRequest
    ) -> None:
        """Gracefully give up hosting and put the world back to SLEEPING."""
        world = get_world_or_404(self.session, world_id)
        require_membership(self.session, world, user)
        now = datetime.now(timezone.utc)

        lease = self.lease_repository.find_by_world_and_lease(
            self.session, world_id, payload.lease_id
        )
        if lease is None or lease.host_user_id != user.id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="lease not found"
            )

        if is_lease_active(lease, now):
            self.lease_repository.mark_inactive(
                self.session, lease, new_status="released", released_at=now
            )
            world.status = "sleeping"
            world.current_host_id = None
        self.session.commit()