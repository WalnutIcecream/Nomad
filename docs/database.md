# Nomad Database Schema

PostgreSQL ≥ 15. The database is the single source of truth for ownership,
membership, leases, and version history. Git is storage, not coordination.

```sql
CREATE TABLE users (
    id            uuid PRIMARY KEY,
    username      text UNIQUE NOT NULL,
    password_hash text NOT NULL,
    created_at    timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE worlds (
    id                uuid PRIMARY KEY,
    name              text NOT NULL,
    owner_id          uuid NOT NULL REFERENCES users(id),
    latest_version_id uuid,
    status            text NOT NULL DEFAULT 'SLEEPING',
    minecraft_version text NOT NULL,
    server_software   text NOT NULL DEFAULT 'vanilla',
    configuration     jsonb NOT NULL DEFAULT '{}',
    storage_backend   text NOT NULL DEFAULT 'github',
    storage_config    jsonb NOT NULL DEFAULT '{}',
    created_at        timestamptz NOT NULL DEFAULT now(),
    updated_at        timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE world_members (
    world_id   uuid NOT NULL REFERENCES worlds(id) ON DELETE CASCADE,
    user_id    uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    role       text NOT NULL DEFAULT 'member',
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (world_id, user_id)
);

CREATE TABLE host_leases (
    id           uuid PRIMARY KEY,
    world_id     uuid NOT NULL REFERENCES worlds(id) ON DELETE CASCADE,
    host_user_id uuid NOT NULL REFERENCES users(id),
    status       text NOT NULL DEFAULT 'active',
    expires_at   timestamptz NOT NULL,
    created_at   timestamptz NOT NULL DEFAULT now(),
    released_at  timestamptz
);

CREATE UNIQUE INDEX one_active_lease_per_world
    ON host_leases (world_id)
    WHERE status = 'active';

CREATE TABLE world_versions (
    id                uuid PRIMARY KEY,
    world_id          uuid NOT NULL REFERENCES worlds(id) ON DELETE CASCADE,
    version_number    bigint NOT NULL,
    storage_key       text NOT NULL,
    created_by        uuid NOT NULL REFERENCES users(id),
    minecraft_version text NOT NULL,
    server_version    text,
    created_at        timestamptz NOT NULL DEFAULT now(),
    UNIQUE (world_id, version_number)
);

ALTER TABLE worlds
    ADD CONSTRAINT fk_latest_version
    FOREIGN KEY (latest_version_id) REFERENCES world_versions(id);

CREATE TABLE sessions (
    id         uuid PRIMARY KEY,
    user_id    uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    token_hash text NOT NULL,
    expires_at timestamptz NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);
```

## Invariants

- `HOSTING`/`STARTING`/`STOPPING`/`SYNCING` status requires an active lease.
- `SLEEPING` requires no active lease.
- Lease validity is judged only by `host_leases.expires_at`.
- `latest_version_id` advances only on snapshot-complete success.
- `storage_config` holds non-secret repo metadata; credentials live in controller env/secrets.
