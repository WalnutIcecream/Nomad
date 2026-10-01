# Nomad

Distributed Minecraft hosting with no always-on coordinator. A shared object
store is the only infrastructure. The store holds, per world, a lease object, a
world archive, and the archive's SHA-256; the launcher boots the server from a
verified pull and uploads the result on stop.

The world stays. The host changes.

Project docs: [docs/USERS.md](docs/USERS.md) (user guide),
[docs/TECHNICAL.md](docs/TECHNICAL.md) (protocol and failure modes),
[docs/USP.md](docs/USP.md) (selling points), [docs/MARKETING.md](docs/MARKETING.md),
[CHANGELOG.md](CHANGELOG.md), [SECURITY.md](SECURITY.md), [llms.txt](llms.txt).

## How it works

Alice presses Play. Nobody is hosting, so Nomad gives her the lease, downloads
the world, and starts the server on her PC. Bob presses Play: Nomad sees Alice's
active lease, so Bob does not start a second server — he joins hers. When Alice
stops, the world is saved, uploaded, and the lease is released, so the next
player to press Play becomes the host and carries on from exactly where Alice
stopped. There is never more than one host for a world.

## Protocol

Each world is three objects under a single key prefix:

```
worlds/<world-id>/lease.json       # {"status","holder","address","expires_at"}
worlds/<world-id>/world.tar.gz     # single world blob; last write wins
worlds/<world-id>/world.sha256     # hex SHA-256 of the blob; verified on pull
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
  acquire once the world it would pull is final. If the upload fails, the lease
  is deliberately **not** released — otherwise the next host could pull a world
  older than the copy still on the failed host's disk — and is left to expire.
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
that protocol. There are exactly two backends, and they are the only two
choices the product offers:

| backend | presented as | transport | cost model |
| --- | --- | --- | --- |
| `r2` | Cloudflare R2 | S3 API against Cloudflare R2 | free tier: 10 GB-month, 1M Class A, 10M Class B, `$0` egress, then metered |
| `server` | My own server | S3 API against an S3-compatible server you run (Garage) | your server's disk and bandwidth; no per-op or per-GB bill |

Both are configured once, and the launcher's setup wizard verifies the
connection before saving.

### Cloudflare R2

Create a bucket and an API token with Object Read/Write scoped to it, then
enter the account ID, access key, secret key and bucket in the wizard (or set
`NOMAD_R2_*`). R2 egress is free, so players can pull worlds without a bill.

### My own server (Garage)

Your worlds live on an S3-compatible server you operate — a VPS or a home
machine running [Garage](https://garagehq.deuxfleurs.fr/). Nomad never
configures Garage for you; Garage is server-side infrastructure and Nomad only
talks to its S3 API.

The administrator needs to:

1. Install Garage on a Linux server and enable its S3 API.
2. Create a bucket for the worlds (for example `nomad-worlds`).
3. Mint an access key and secret for Nomad.

Then each player enters four values in the wizard — endpoint, bucket, access
key, secret key — and presses **Test Connection**, which writes, reads back and
deletes a small probe object under the bucket.

```
NOMAD_STORAGE_BACKEND=server
NOMAD_SERVER_ENDPOINT=https://storage.example.com
NOMAD_SERVER_BUCKET=nomad-worlds
NOMAD_SERVER_ACCESS_KEY=...
NOMAD_SERVER_SECRET_KEY=...
```

The server holds the world; it does not run Minecraft. The player's own
computer runs the server, exactly as with R2.

## Storage configuration vs world configuration

These are separate on purpose. Storage is global to the machine and configured
once (Settings → Storage, or the first-run wizard). Worlds are then created
against that storage and each has an ID, metadata, and lease state. Nomad never
asks for storage credentials again when you create or join a world.

## Repository layout

```
launcher/
  cloud.py               S3/SigV4 client + WorldStore (lease, world blob, connection test, usage)
  credentials.py         OS keychain credential storage, with a 0600-file fallback
  registry.py            local address book: world id -> name, mc version
  agent.py               host lifecycle: acquire -> pull -> boot -> renew -> push -> release
  server_properties.py   whitelisted server.properties subset
  storage/               WorldStoreProtocol + r2 and server backends + build_store
  manifest.py            inclusion manifest: what to sync + how to launch any game
  process_runtime.py     generic runtime: run an arbitrary server command
  secrets.py             redaction + log scrubbing
  audit.py               non-secret lease/credential-use audit trail
  minecraft/             vanilla runtime: jar download, Java 21 check, process control
  sync/                  world folder layout helpers
  cli/                   argparse CLI (nomad)
  ui/                    PySide6 launcher (theme, storage wizard, world cards)
  tests/                 pytest suite
scripts/app/build_app.py PyInstaller + embedded Temurin JRE packaging
```

Architecture is deliberately layered: storage is behind a protocol, the agent
is a state sequence, and nothing below the protocol touches the backend.

## Inclusion manifest (host any game)

`nomad.json` in the server directory is a pointer file: the inclusion
counterpart to `.gitignore`. It lists the files and directories that make up a
server's persistent state, and optionally how to launch the server, so Nomad is
not tied to Minecraft.

```json
{
  "version": 1,
  "name": "My Terraria Server",
  "include": ["Worlds/", "config.json", "players/*.plr"],
  "exclude": ["*.log", "logs/", "core"],
  "server": {
    "command": ["./TerrariaServer", "-config", "config.json"],
    "stop_command": "exit",
    "port": 7777
  }
}
```

- `include` — glob patterns relative to the manifest's directory. A pattern
  naming a directory includes its whole subtree. Empty means "everything".
- `exclude` — glob patterns removed from the set. Exclude always wins.
- `server.command` — argv to launch the server (cwd = the synced directory).
  When present, Nomad runs this instead of the vanilla Minecraft runtime.
- `server.stop_command` — line written to stdin for a graceful shutdown. When
  absent the process is terminated.

Without a manifest, Nomad uses the built-in Minecraft behavior (world layout,
vanilla runtime). With one, it syncs exactly the listed paths and runs the
listed command. The manifest itself travels with the world, so whoever hosts
next boots the same game.

```bash
nomad manifest init ./my-server     # write a starter nomad.json
nomad manifest check ./my-server    # preview what would sync
```

## Setup

Requires Python >= 3.11 and a Java 21 runtime for hosting.

```bash
python -m pip install -e ".[dev]"        # core + test deps
python -m pip install -e ".[ui]"         # + PySide6 GUI
python -m pip install -e ".[ui,secure]"  # + OS-keychain credential storage
```

Then run `nomad-gui` and follow the storage wizard. The command-line
equivalent is `nomad storage set r2|server` followed by `nomad storage test`.

## CLI

```bash
nomad storage set r2|server      # choose where worlds are stored
nomad storage status             # show the active backend + settings (credentials redacted)
nomad storage test               # prove the storage can read and write
nomad manifest init [dir]        # write a starter nomad.json
nomad manifest check [dir]       # preview what the manifest syncs
nomad new <name>                 # create a world; prints its id
nomad join <world-id>            # register a friend's world
nomad play <world-id>            # host until Ctrl-C; pushes world on stop
nomad status <world-id>          # who hosts now
nomad worlds                     # list worlds with host status
nomad usage                      # local usage counters
```

`nomad-gui` (or `python -m launcher.ui`) launches the PySide6 window.

## Configuration

pydantic-settings, `NOMAD_` prefix, `.env` supported. The wizard and Settings
dialog persist non-secret values to `data/settings.json`; secrets go to the OS
keychain when available and otherwise to that file. Precedence: OS environment
vars > `data/settings.json` > `.env` > built-in defaults.

| Variable | Default | Notes |
| --- | --- | --- |
| `NOMAD_STORAGE_BACKEND` | `r2` | `r2` \| `server` |
| `NOMAD_R2_ACCOUNT_ID` | — | r2 endpoint construction |
| `NOMAD_R2_ACCESS_KEY` / `NOMAD_R2_SECRET_KEY` | — | SigV4 signing |
| `NOMAD_R2_BUCKET` | — | r2 bucket |
| `NOMAD_R2_ENDPOINT_URL` | auto | override the r2 S3 endpoint |
| `NOMAD_SERVER_ENDPOINT` | — | self-hosted S3 endpoint |
| `NOMAD_SERVER_BUCKET` | — | self-hosted bucket |
| `NOMAD_SERVER_ACCESS_KEY` / `NOMAD_SERVER_SECRET_KEY` | — | self-hosted credentials |
| `NOMAD_KEYCHAIN` | `1` | `0` forces credentials into the settings file |
| `NOMAD_AUDIT_LOG` | `1` | write the non-secret lease/credential-use audit trail |
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
(alice acquires/hosts/uploads; bob pulls and hosts; third party denied),
upload-failure lease preservation, world archive round-trips, the connection
test and its error classification, the SigV4 signer (byte-compared against
botocore), credential storage with and without a keychain, world registry,
config layering, the inclusion manifest, and the audit trail. No external
service required.

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
- **Two backends only.** One hosted option for people who do not want to run
  anything, and self-hosted storage for people who do. No half-supported third
  paths.
- **The architecture stays invisible.** Users think "I connected my storage,
  created a world, and whoever plays hosts it" — not "I configured an
  S3-compatible object store and manage leases".
