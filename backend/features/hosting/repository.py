"""Database access for host leases (hosting feature).

A lease is the controller's way of guaranteeing that only one authorized
player hosts a world at a time. Persistence for those leases lives here:
the atomic acquire algorithm (row lock + active-lease lookup + insert) is
orchestrated by ``HostingService`` on top of these primitives.

Repository layer rules:
    * No HTTP errors and no commits here — the service owns both.
    * ``create`` leaves ``status`` at its default (``active``).
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.domain.entities import HostLease


class HostLeaseRepository:
    def find_active_for_world(
        self, session: Session, world_id: uuid.UUID
    ) -> HostLease | None:
        """The one active lease for a world (the unique index enforces <= 1)."""
        statement = select(HostLease).where(
            HostLease.world_id == world_id, HostLease.status == "active"
        )
        return session.execute(statement).scalar_one_or_none()

    def find_by_world_and_lease(
        self, session: Session, world_id: uuid.UUID, lease_id: uuid.UUID
    ) -> HostLease | None:
        """A specific lease row for a world, regardless of status."""
        statement = select(HostLease).where(
            HostLease.world_id == world_id, HostLease.id == lease_id
        )
        return session.execute(statement).scalar_one_or_none()

    def find_by_world_lease_and_host(
        self,
        session: Session,
        world_id: uuid.UUID,
        lease_id: uuid.UUID,
        host_user_id: uuid.UUID,
    ) -> HostLease | None:
        """A lease row owned by a specific user, regardless of status.

        Ownership is part of the lookup so that no user can act on a lease
        that is not theirs — the service still verifies "active" separately.
        """
        statement = select(HostLease).where(
            HostLease.world_id == world_id,
            HostLease.id == lease_id,
            HostLease.host_user_id == host_user_id,
        )
        return session.execute(statement).scalar_one_or_none()

    def create(
        self,
        session: Session,
        world_id: uuid.UUID,
        host_user_id: uuid.UUID,
        expires_at: datetime,
    ) -> HostLease:
        """Persist a new active lease (not committed)."""
        new_lease = HostLease(
            world_id=world_id,
            host_user_id=host_user_id,
            status="active",
            expires_at=expires_at,
        )
        session.add(new_lease)
        return new_lease

    def mark_inactive(
        self,
        session: Session,
        lease: HostLease,
        new_status: str,
        released_at: datetime,
    ) -> None:
        """Flip a lease into expired/released and stamp when that happened."""
        lease.status = new_status
        lease.released_at = released_at