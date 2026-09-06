# Nomad

Distributed Minecraft hosting with no always-on coordinator. A shared object
store is the only infrastructure. The store holds, per world, a lease object
and a world archive; the launcher boots the vanilla server from a pull and
uploads the result on stop.

## Protocol

Each world is two objects under a single key prefix:

```
worlds/<world-id>/lease.json       # {"status","holder","address","expires_at"}
worlds/<world-id>/world.tar.gz     # single world blob; last write wins
```

### Lease

The lease is a compare-and-swap on the lease object. The backend provides
atomic conditional writes; Nomad uses them as the sole arbitration primitive
for "who is the host right now."

- **acquire**: PUT the lease. If the object does not exist, write with
  `If-None-Match: *`. If it exists but is expired/released, write with
  `If-Match: <current etag>`. Exactly one of two concurrent acquirers can land
  its write; the loser receives the backend's conditional-write rejection and
  learns the current holder's identity. No lock server; the store serializes
  the race.
- **renew**: while hosting, PUT the lease again with `If-Match: <own etag>`
  every 20 seconds, pushing `expires_at` forward by the lease duration. A host
  that loses the etag (another host stole or expired it) fails to renew and
  shuts down its server.
- **release**: mark the lease `released` AFTER the world upload completes,
  using `If-Match: <own etag>`. Ordering is deliberate: a new host can only
  acquire once the world it would pull is final.
- **expiry**: a lease whose `expires_at` passes without renewal is expired; any
  client may then steal it via the stale etag. This is the crash path — a dead
  host frees the world automatically rather than blocking it forever.

### World

A host pulls `world.tar.gz` before booting and uploads a fresh archive on stop.
There is exactly one object per world; nothing accumulates versions, so stored
bytes scale with world count, not save count. A player joining (not hosting)
only needs pull access.

## Storage backends

Nomad speaks to storage through a `WorldStoreProtocol`
(`launcher/storage/__init__.py`). The launcher, agent, and CLI depend only on
that protocol. Three backends ship; adding a fourth is a protocol
implementation plus one entry in `build_store`.

| backend | transport | cost model | world-size ceiling |
| --- | --- | --- | --- |
| `r2` | S3 API against Cloudflare R2 | free tier: 10 GB-month, 1M Class A, 10M Class B, `$0` egress, then metered | none practical |
| `git` | a git repo (git push rejection is the CAS) | zero per-op, zero per-GB; your repo's limits apply | 100 MB per-file (git hard cap); history grows with every save |
| `vps` | S3 API against self-hosted MinIO/Garage/SeaweedFS | your VPS disk/bandwidth; no per-op meter | VPS disk |

### git backend

The lease is `worlds/<id>/lease.json`; the world blob is
`worlds/<id>/world.tar.gz`. Atomicity comes from git's fast-forward-only push: a
concurrent acquirer whose local history is stale gets a rejected push, which is
the CAS rejection. Configure with `NOMAD_GIT_REPO` (local path) and optionally
`NOMAD_GIT_REMOTE` (a remote for sharing). Constraints: files over 100 MB are
rejected by git, and every save appends a full copy of the blob to history
(no delta for the same path), so long-lived worlds grow the repo unboundedly.

### vps backend

Reuses the same S3 client pointed at a self-hosted S3-compatible endpoint.
Configure `NOMAD_VPS_ENDPOINT` and `NOMAD_VPS_BUCKET` (credentials reuse the
R2 access/secret keys). One person operates the box (install MinIO, create a
bucket + key, share three values); the rest only need endpoint/bucket/keys.

### r2 backend (default)

`NOMAD_R2_ACCOUNT_ID`, `NOMAD_R2_ACCESS_KEY`, `NOMAD_R2_SECRET_KEY`,
`NOMAD_R2_BUCKET`, optional `NOMAD_R2_ENDPOINT_URL`. Everything behaves as
described in Protocol.

The default backend is `r2`; switch with `nomad storage set`. The active
backend and its settings persist to `data/settings.json`.

## Repository layout

```
launcher/
  cloud.py               S3/SigV4 client + WorldStore (lease, world blob, usage)
  registry.py            local address book: world id -> name, mc version
  agent.py               host lifecycle: acquire -> pull -> install -> boot -> renew -> push -> release
  server_properties.py   whitelisted server.properties subset
  storage/               WorldStoreProtocol + r2, git, vps backends + build_store
  minecraft/             vanilla runtime: jar download, Java 21 check, process control
  sync/                  world folder layout helpers
  cli/                   argparse CLI (nomad)
  tests/                 pytest suite
scripts/app/build_app.py PyInstaller + embedded Temurin JRE packaging
```

Architecture is deliberately layered: storage is behind a protocol, the agent
is a state sequence, and nothing below the protocol touches the backend.

## Setup

Requires Python >= 3.11 and a Java 21 runtime for hosting.

```bash
python -m pip install -e ".[dev]"     # core + test deps
python -m pip install -e ".[ui]"      # + PySide6 GUI
```

Create an R2 bucket and an API token with Object Read/Write scoped to it, then
either export the `NOMAD_R2_*` variables in your environment or let the GUI's
R2 Settings dialog persist them to `data/settings.json`.

## CLI

```bash
nomad storage set r2|git|vps     # select backend
nomad storage status             # show active backend + settings
nomad new <name>                 # create a world; prints its id
nomad join <world-id>            # register a friend's world
nomad play <world-id>            # host until Ctrl-C; pushes world on stop
nomad status <world-id>          # who hosts now
nomad worlds                     # list worlds with host status
nomad usage                      # local usage counters (free-tier awareness)
```

`nomad-gui` (or `python -m launcher.ui`) launches the PySide6 window.

## Configuration

pydantic-settings, `NOMAD_` prefix, `.env` supported. The GUI edits and persists
R2/VPS/git settings to `data/settings.json`. Precedence: OS environment vars >
`data/settings.json` > `.env` > built-in defaults.

| Variable | Default | Notes |
| --- | --- | --- |
| `NOMAD_STORAGE_BACKEND` | `r2` | `r2` \| `git` \| `vps` |
| `NOMAD_R2_ACCOUNT_ID` | — | r2 endpoint construction |
| `NOMAD_R2_ACCESS_KEY` / `NOMAD_R2_SECRET_KEY` | — | SigV4 signing |
| `NOMAD_R2_BUCKET` | — | r2 bucket |
| `NOMAD_R2_ENDPOINT_URL` | auto | override S3 endpoint |
| `NOMAD_GIT_REPO` | `~/.nomad/repo` | git backend repo path |
| `NOMAD_GIT_REMOTE` | — | git backend remote |
| `NOMAD_VPS_ENDPOINT` | — | vps S3 endpoint |
| `NOMAD_VPS_BUCKET` | — | vps bucket |
| `NOMAD_DATA_DIR` | platform default | launcher state, worlds |
| `NOMAD_PLAYER_NAME` | env username | holder name in the lease |
| `NOMAD_PUBLIC_ADDRESS` | — | address published in the lease (port-forward) |
| `NOMAD_MC_VERSION` | `1.21.1` | vanilla server jar version |
| `NOMAD_SERVER_PORT` | `25565` | server listen port |
| `NOMAD_JAVA_PATH` | `java` on PATH | JVM executable |
| `NOMAD_MEMORY` | `2G` | JVM heap |
| `NOMAD_EULA_ACCEPTED` | `false` | required before hosting |
| `NOMAD_LEASE_DURATION_SECONDS` | `300` | lease expiry without renewal |
| `NOMAD_HEARTBEAT_INTERVAL_SECONDS` | `20` | renewal cadence while hosting |

## Tests

```bash
python -m pytest
```

The suite covers: lease CAS (acquire/deny/renew/release/stale-steal) against an
in-memory S3 double with real conditional-write semantics, the full host cycle
(alice acquires/hosts/uploads; bob pulls and hosts; third party denied), world
archive round-trips, the SigV4 signer (byte-compared against botocore), world
registry, config layering, and the git backend. No external service required.

## Packaging

`scripts/app/build_app.py` builds a self-contained `dist/Nomad/` via PyInstaller
with an embedded Temurin JRE 21. Run it from the repo root with PyInstaller
installed; the JRE archive is downloaded once and cached under
`scripts/app/.cache`. Produces `Nomad` (GUI) and `nomad-cli` binaries plus a
`java/` runtime and a writable `data/` directory.

## Design constraints

- **No always-on process.** The store's conditional writes are the mutex; there
  is no controller to run or pay for. Availability of a world equals
  availability of its store.
- **No accounts.** Membership is a world id plus store access to that world's
  prefix. Sharing the id is the invite; permission is delegated to the store
  (shared token, or per-prefix tokens for finer control).
- **Bytes vs operations.** Storage and operation counts are the metered
  quantities, not egress (R2 egress is `$0`; git is free). Volatile, large
  worlds favor R2/VPS; small worlds fit git.
- **`usage` is a local, approximate counter** for free-tier awareness; the
  authoritative numbers are the store provider's dashboard.