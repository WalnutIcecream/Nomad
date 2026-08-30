# Nomad Architecture

Nomad turns a Minecraft world into a persistent cloud object with an ephemeral,
player-owned compute layer. The design splits responsibility cleanly:

- **Controller** — identity, membership, authorization, host leases, version ledger.
- **WorldStorage** — opaque snapshot/blob persistence, backend-agnostic.
- **Launcher** — local Minecraft lifecycle, snapshot capture/restore, networking.
- **Minecraft server** — unmodified vanilla server software.

## Implementation Status

| Component | Status |
|---|---|
| Launcher: server lifecycle (install/start/stop/crash-detect) | implemented |
| Launcher: explicit state machine + atomic state persistence | implemented |
| Launcher: local snapshots (deterministic tar, sha256, safe extract) | implemented |
| Launcher: git-backed storage (tags, LFS, non-forced pushes) | implemented |
| Controller: auth (argon2id, hashed sessions) | implemented |
| Controller: worlds + membership (owner/member enforcement) | implemented |
| Controller: atomic host leases (row lock + partial unique index) | implemented |
| Controller: heartbeats + lease expiry | implemented |
| Controller: version ledger + two-phase snapshot | implemented |
| Controller: real upload/download streaming (blob store) | implemented |
| Launcher: ControllerClient (httpx API client) | implemented |
| Launcher: HostAgent (full lifecycle + heartbeat loop + crash/lease-loss detection) | implemented |
| Networking: direct connection info publishing (LAN/port-forward) | implemented |
| Networking: relay fallback (outbound REGISTER + token JOIN, byte forwarding) | implemented |
| Desktop UI: PySide6 launcher (login, world cards, one-click play/join/stop) | implemented |

## Components

```text
                        ┌──────────────────────────────┐
                        │         Controller           │
                        │  (FastAPI + PostgreSQL)      │
                        │                              │
                        │  auth         host leases    │
                        │  worlds       membership     │
                        │  versions     heartbeats     │
                        │  sync coordination           │
                        └─────────────┬────────────────┘
                                      │  WorldStorage (interface)
                          ┌───────────┴───────────┐
                          │                       │
                   ┌──────▼──────┐        ┌───────▼───────┐
                   │ GitStorage  │        │ (S3/R2/Local) │
                   │  Git + LFS  │        │  future       │
                   │  GitHub     │        │  adapters     │
                   └─────────────┘        └───────────────┘
                              │
                     world snapshots + versions
                              │
        ┌─────────────────────┼─────────────────────┐
        │                     │                     │
 ┌──────▼──────┐       ┌──────▼──────┐       ┌──────▼──────┐
 │  Launcher   │       │  Launcher   │       │  Launcher   │
 │  + Server   │       │  + Server   │       │  + Server   │
 │   Alice     │       │   Bob       │       │   Charlie   │
 └─────────────┘       └─────────────┘       └─────────────┘
```

## Key Decisions

| Decision | Rationale |
|---|---|
| PostgreSQL as sole source of truth | Leases and versions need ACID transactions; Git is not a coordination layer |
| Python + CLI-first launcher | One language across backend/launcher; Qt GUI later |
| Git LFS behind `WorldStorage` | Honest about GitHub size limits; S3/R2 drop in without rewriting |
| Version = git tag, `latest` = moving ref | Immutable versions + cheap latest resolution |
| Two-phase snapshot upload | Interrupted uploads never advance the version ledger |
| Explicit launcher state machine | Restart-safe; crash mid-flow is recoverable |
| Heartbeats + lease expiry (not instant revoke) | Graceful tolerance for transient network blips |

## Host Lease Algorithm

Acquire is atomic. Pseudocode:

```text
BEGIN
  SELECT * FROM worlds WHERE id = :world_id FOR UPDATE;
  UPDATE host_leases SET status='expired'
    WHERE world_id=:world_id AND status='active' AND expires_at <= now();
  SELECT host_user_id FROM host_leases
    WHERE world_id=:world_id AND status='active';
  IF found:
      COMMIT; return {current_host: found.host_user_id};
  INSERT INTO host_leases (...) VALUES ('active', now() + lease_duration);
  UPDATE worlds SET status='STARTING', current_host=:user_id;
  COMMIT; return {lease_id, host_user_id};
```

The `FOR UPDATE` row lock serializes concurrent acquires per world. A partial
unique index `(world_id) WHERE status='active'` is a second line of defense.

## Git Storage (Stage 3)

- A version is a git commit tagged `v<version_number>`; its tree holds `world.tar.gz` + `version.json`.
- The `latest` tag is a moving ref; `vN` tags are immutable.
- Binary blobs (`*.mca`, `*.dat`, `*.tar.gz`, …) route through Git LFS when `git-lfs` is installed.
- Restores verify the archive sha256 against the manifest before extraction.
- Branch pushes are non-forced — a divergent remote fails loudly rather than overwriting newer state.
- GitHub limits are real; the adapter never assumes unlimited storage.

---

## Snapshot Lifecycle

```text
START → download latest → validate → start Minecraft
      → HOSTING (heartbeats)
      → stop Minecraft (graceful flush)
      → snapshot (post-stop copy + deterministic tar)
      → upload (prepare → stream → complete)
      → release lease
```

The fundamental safety rule:

> Never destroy the last known-good cloud version merely because a host failed.
