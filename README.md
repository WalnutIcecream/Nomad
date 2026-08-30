# Nomad — Distributed, Cloud-Persistent Minecraft Hosting

> A Minecraft world with persistent identity and state, but no permanent server.
> The compute moves to whichever authorized player holds the host lease; the world itself lives in cloud storage.

**TL;DR:** Nomad lets a group of friends share one persistent Minecraft world. When nobody is playing, the world sleeps in cloud storage. When someone clicks Play, they become the host, download the latest snapshot, and run the server locally. Friends join the active host; when they stop, the world snapshots back to the cloud and returns to sleep.

**Current status:** All 7 stages implemented. The complete MVP: persistent worlds with player-owned hosting, controller-coordinated leases, and a desktop launcher UI.

---

## Requirements

- Python ≥ 3.11 (Linux, macOS, or Windows)
- Java ≥ 21 (for the Minecraft 1.21.x server)
- Docker (for Postgres) — or any local Postgres 15+
- Git (for GitStorage; git-lfs optional)

Nomad is OS-neutral: the launcher, controller, and relay use only Python
stdlib + cross-platform libraries. `nomad stop` works via a marker file on all
platforms (no POSIX signals required), data lives in `%APPDATA%\nomad` on
Windows and `~/.local/share/nomad` on Linux/macOS.

---

## How It Runs (Stages 1–3)

```bash
# Requirements: Python ≥ 3.11, Java ≥ 21 (for Minecraft 1.21.x)

python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"

# Start a local server (downloads vanilla server.jar on first run)
NOMAD_EULA_ACCEPTED=true .venv/bin/nomad play --world-dir worlds/test

# In another terminal, stop it gracefully
.venv/bin/nomad stop --world-dir worlds/test

# Local storage (default): save the stopped world as a snapshot
.venv/bin/nomad snapshot create --world-dir worlds/test

# Git storage: Alice pushes her world to a shared git remote
git init --bare remote.git
NOMAD_STORAGE_BACKEND=git \
NOMAD_STORAGE_REPO=./alice-repo \
NOMAD_STORAGE_REMOTE=$PWD/remote.git \
  .venv/bin/nomad snapshot create --world-dir worlds/test

# Bob pulls Alice's latest world from the same remote
NOMAD_STORAGE_BACKEND=git \
NOMAD_STORAGE_REPO=./bob-repo \
NOMAD_STORAGE_REMOTE=$PWD/remote.git \
  .venv/bin/nomad snapshot list
NOMAD_STORAGE_BACKEND=git \
NOMAD_STORAGE_REPO=./bob-repo \
NOMAD_STORAGE_REMOTE=$PWD/remote.git \
  .venv/bin/nomad snapshot restore --version 1 --world-dir worlds/bob

# Relaunch — the world is exactly as it was
.venv/bin/nomad play --world-dir worlds/test
```

## Hosting with the Controller (Stage 5)

The launcher now talks to the controller end to end:

```bash
# Start Postgres + controller
cd infrastructure/docker && docker compose up -d
.venv/bin/alembic upgrade head
.venv/bin/python -m uvicorn backend.main:app --port 8000

# Login and grab a token, then point the launcher at the controller
TOKEN=$(curl -s -X POST http://localhost:8000/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"username":"alice","password":"password123"}' | jq -r .token)

# List your worlds
NOMAD_CONTROLLER_URL=http://localhost:8000 NOMAD_CONTROLLER_TOKEN=$TOKEN \
  .venv/bin/nomad worlds

# Alice hosts Walnut SMP (downloads world, starts server, heartbeats, snapshots on stop)
NOMAD_CONTROLLER_URL=http://localhost:8000 NOMAD_CONTROLLER_TOKEN=$TOKEN \
  .venv/bin/nomad host --world <world-id> --accept-eula

# Bob later does the same and gets Alice's latest world
NOMAD_CONTROLLER_URL=http://localhost:8000 NOMAD_CONTROLLER_TOKEN=$TOKEN \
  .venv/bin/nomad host --world <world-id> --accept-eula
```

Host migration is fully automatic: Bob's `host` acquires the lease, downloads the
latest snapshot, starts his local server, and pushes a new snapshot when he stops.
If Alice crashes, her lease expires and Bob can take over — the last known-good
cloud version is never touched.

## Networking (Stage 6)

The host publishes how players can reach its server:

- **Direct:** the launcher detects the LAN IP and reports `host:port` (works on
  LAN or with manual port forwarding).
- **Relay fallback:** with `NOMAD_RELAY_ENABLED=true`, the host dials OUT to the
  relay (so NAT doesn't matter), receives a token, and publishes it via the
  controller. Friends `nomad join --world <id>` and the relay pipes their
  Minecraft traffic into the host's server. The relay forwards bytes only — it
  never runs the Minecraft server.

```bash
# Run the relay
.venv/bin/python -m relay.server --host 0.0.0.0 --port 9000

# Host with relay fallback
NOMAD_RELAY_ENABLED=true NOMAD_RELAY_HOST=relay.example.com \
  .venv/bin/nomad host --world <world-id> --accept-eula

# A friend sees the connection target
NOMAD_CONTROLLER_URL=http://localhost:8000 NOMAD_CONTROLLER_TOKEN=$TOKEN \
  .venv/bin/nomad join --world <world-id>
```

## Desktop Launcher (Stage 7)

The PySide6 GUI bundles everything into one-click actions:

```bash
# On NixOS, launcher/ui.sh wires up Qt's system libs automatically
./launcher/ui.sh

# Otherwise
QT_QPA_PLATFORM=${QT_QPA_PLATFORM:-wayland} .venv/bin/python -m launcher.ui
```

Log in (or register) in the window, then:

- **Sleeping world** → `▶ PLAY`: acquires the lease, downloads the latest
  world, starts the server, publishes connection info, and snapshots on stop.
- **Someone else hosting** → `⇥ JOIN`: shows the direct address or relay token
  to connect your Minecraft client.
- **You are hosting** → `■ STOP SERVER`: graceful shutdown + snapshot + release.

World cards poll the controller every 5 seconds, so status, host, and version
stay live.

The milestone that must hold before moving on:

```text
launch server → play locally → stop server
→ snapshot create → destroy local world
→ snapshot restore → launch server → world loads identically
→ Alice pushes snapshot → Bob restores → Bob sees Alice's exact world
```

---

## Architecture

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

**Responsibility boundaries:**

- **Controller** — authoritative for *who* owns a world, *who* may access it, *who* holds the lease, *what* version is latest. Never the Git repo.
- **WorldStorage** — opaque byte/blob layer. Knows nothing about leases or membership.
- **Launcher** — Minecraft process lifecycle, snapshot capture/restore, heartbeats, networking, UI. Never trusted for authorization.
- **Minecraft server** — unmodified vanilla `server.jar`.

---

## Repository Layout

```text
nomad/
├── backend/               # FastAPI controller (Stage 4: auth, worlds, leases, versions)
│   ├── routers/           # auth, worlds, members, leases, versions
│   ├── migrations/        # Alembic
│   └── tests/             # race/crash/conflict tests against real Postgres
├── launcher/
│   ├── cli/               # nomad play | stop | status | snapshot | host | worlds | join
│   ├── minecraft/         # MinecraftRuntime abstraction + vanilla impl
│   ├── state/             # explicit state machine + atomic persistence
│   ├── storage/           # WorldStorage abstraction + LocalStorage + GitStorage
│   ├── sync/              # snapshot capture/restore + controller push/pull
│   ├── agent.py           # HostAgent: lease + heartbeat + lifecycle orchestration
│   ├── controller.py      # ControllerClient (httpx API client)
│   ├── networking.py      # ConnectionInfo, LAN detection, RelayClient bridge
│   ├── stopfile.py        # cross-platform stop marker watcher (no POSIX signals)
│   ├── ui/                # PySide6 desktop launcher (login, world cards, actions)
│   └── tests/
├── relay/
│   └── server.py          # TCP relay: REGISTER/JOIN token forwarding
├── shared/
│   └── protocol/          # pydantic domain models + enums (only shared dep)
├── infrastructure/
│   └── docker/            # PostgreSQL + backend dev (scaffold)
├── docs/
│   ├── architecture.md
│   ├── api.md
│   ├── database.md
│   └── threat-model.md
└── README.md
```

`shared/protocol` is the only cross-component dependency. Launcher and backend both import it; nothing imports across `backend/` ↔ `launcher/` otherwise.

---

## Host Lease Model

The controller owns all coordination. A lease is:

```text
world_id, host_user_id, lease_id (random secret), expires_at
```

- **Acquire** is atomic (row lock + partial unique index): either you get the lease or you learn who else holds it. Never both.
- **Heartbeats** extend `expires_at`; missed heartbeats do not revoke immediately — the lease simply approaches expiry.
- **Release** is idempotent.
- **Stale uploads are rejected** by re-validating lease ownership at snapshot-complete time.

Config defaults: heartbeat every 20 s, lease duration 300 s.

---

## World Synchronization

```text
acquire lease
  → pull latest snapshot
  → validate
  → start Minecraft
  → HOSTING (heartbeats)
  → stop Minecraft (graceful flush)
  → snapshot (only after full stop)
  → two-phase upload (prepare → stream → complete)
  → release lease
```

Two-phase upload means an interrupted transfer never advances the version ledger — the last known-good cloud snapshot stays intact. Conflict rule: if the launcher's `base_version` no longer equals the cloud latest, the upload is rejected; the launcher recovers from the latest cloud version rather than overwriting it.

---

## Git Storage (Stage 3)

`GitStorage` implements the same `WorldStorage` interface over a git repository:

- A version is a commit tagged `v<version_number>`; its tree holds `world.tar.gz` + `version.json`.
- The `latest` tag is a moving ref; `vN` tags are immutable.
- Binary blobs (`*.mca`, `*.dat`, `*.tar.gz`, …) route through Git LFS when `git-lfs` is installed.
- Restores verify the archive sha256 against the manifest before extraction.
- Branch pushes are non-forced — a divergent remote fails loudly rather than overwriting newer state.

GitHub limits are real: the adapter never assumes unlimited storage, and an S3/R2 adapter can replace it without touching the launcher or sync layer.

---

## Development Order

```text
Stage 1  Local prototype          — done
Stage 2  Local world snapshots    — done
Stage 3  Git-backed persistence   — done
Stage 4  Controller               — done: auth, worlds, leases, versions
Stage 5  Host migration           — done: launcher ↔ controller end to end
Stage 6  Networking               — done: direct + relay fallback
Stage 7  Polished UX              — done: PySide6 desktop launcher
```

The MVP is complete: user auth, world creation, invitations, one-click play,
lease acquisition, world download, local server start, joinable hosting,
graceful stop + snapshot, remote persistence, and crash-safe host migration.

---

## Packaging (Bundled Application)

`dist/Nomad` is a standalone, portable build of the whole product (Windows
x64). Double-click `Nomad.exe` to boot an embedded PostgreSQL, the controller,
the relay, and the launcher UI as one process — no Docker, no install.

```bash
# Build it (needs the project venv; downloads PostgreSQL binaries once)
.venv\Scripts\python.exe scripts/bundle/build.py

# Inspect / run the result
dist\Nomad\README.txt              # user-facing guide inside the bundle
dist\Nomad\Nomad.exe               # the app — just run it
dist\Nomad\nomad-cli.exe           # the `nomad` CLI as a onefile exe
dist\Nomad\nomad-controller.exe    # controller only
dist\Nomad\nomad-relay.exe         # relay only
```

The bundle is built from the `nomad_app` hub package (embedded-Postgres
lifecycle, in-process controller/relay, first-run migration, Qt server panel)
on top of the existing `backend`, `launcher`, and `relay` packages. Everything
the app writes stays in `dist\Nomad\data\` (portable; delete it to reset).
Headless operation for scripts and CI: `Nomad.exe --serve --quit-after 30` or
`--stop-flag <path>`.

---

## Running Tests

```bash
.venv/bin/pytest
```

---

## License

MIT
