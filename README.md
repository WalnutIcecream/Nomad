# Nomad

Distributed Minecraft hosting with no always-on coordinator. A shared object
store is the only infrastructure. The store holds, per world, a lease object, a
world archive, and the archive's SHA-256; the launcher boots the vanilla server
from a verified pull and uploads the result on stop.

Project docs: [docs/USP.md](docs/USP.md) (selling points),
[docs/TECHNICAL.md](docs/TECHNICAL.md) (protocol and failure modes),
[docs/MARKETING.md](docs/MARKETING.md), [CHANGELOG.md](CHANGELOG.md),
[llms.txt](llms.txt).

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
that protocol. Four backends ship; adding a fifth is a protocol implementation
plus one entry in `build_store`.

| backend | transport | cost model | world-size ceiling |
| --- | --- | --- | --- |
| `r2` | S3 API against Cloudflare R2 | free tier: 10 GB-month, 1M Class A, 10M Class B, `$0` egress, then metered | none practical |
| `git` | a git repo (git push rejection is the CAS) | zero per-op, zero per-GB; your repo's limits apply | 100 MB per-file (git hard cap); history grows with every save |
| `vps` | S3 API against self-hosted MinIO/Garage/SeaweedFS | your VPS disk/bandwidth; no per-op meter | VPS disk |
| `ssh` | ssh to a home/bare-metal box | your box's disk; no per-op meter | box disk |

### ssh backend

A box you own, used as plain storage. No server software to install: sshd is the
only requirement (unlike the `vps` backend, which needs MinIO). Configure
`NOMAD_SSH_TARGET` (`user@host`), `NOMAD_SSH_PATH` (remote base dir, default
`nomad-worlds`), and optionally `NOMAD_SSH_KEY` / `NOMAD_SSH_PORT`.

SSH is not LAN-only: it crosses the internet. The constraint is that the box
must be *reachable* — a local IP on the LAN, a public address, or, for a box
behind NAT, the reverse tunnel below. A home box with no inbound path cannot be
connected to as a client; it can still host or tunnel outbound.

**Identity.** Nomad keeps its own ed25519 keypair so the backend does not depend
on your `~/.ssh` layout:

```bash
nomad ssh setup pi@192.168.1.20   # install the key, create the folder, verify
nomad ssh show                    # target, identity, fingerprint, tunnel state
nomad ssh init                    # print-only steps for an isolated dedicated user
```

`nomad ssh setup` is the normal path. It generates `data/ssh/id_ed25519_nomad`
(`0600` in a `0700` dir), installs the public key into your **existing** account's
`~/.ssh/authorized_keys`, creates the world folder, and verifies the key
authenticates on its own. It reuses your existing ssh access when it works and
falls back to a one-time password when it does not; either way there is no
`sudo`, no `useradd`, and no manual step. The GUI exposes the same flow as
**Set up over SSH…** in Settings.

`nomad ssh init` remains for the isolated layout: a dedicated user created with
`sudo useradd`, scoped by owning only the world directory. That flow is
print-only — `init` prints commands, it does not run them. Key resolution is:
`NOMAD_SSH_KEY` if set, else the Nomad key if it exists, else the system ssh
default.

**One connection for the session.** On POSIX the transport multiplexes ssh
(`ControlMaster=auto`, `ControlPath=data/ssh/ctl-<hash>`, `ControlPersist=yes`,
`ServerAliveInterval=30`). The agent opens it once at session start and the
reverse tunnel rides the *same* connection, so the whole session costs one
handshake instead of two per lease renewal. Windows OpenSSH lacks multiplexing
and opens connections per operation.

Remote layout and lease:

```
<base>/<world-id>/lease/            # presence == claimed (atomic mkdir)
<base>/<world-id>/lease/lease.json  # holder, expires_at, lease_id
<base>/<world-id>/world.tar.gz      # single blob, overwrite-only
<base>/<world-id>/world.sha256      # verified on pull
```

The lease uses the remote filesystem's atomic `mkdir`: a claim is
`mkdir <base>/<id>/lease`, which succeeds for exactly one client. An expired
lease is cleared and re-claimed through the same `mkdir`, so concurrent
acquirers still resolve to a single winner. `renew`/`release` verify the
`lease_id` stored inside `lease.json`, so a stale host cannot act on a lease it
no longer holds.

For scoped access, restrict the dedicated user's key in `authorized_keys` if you
can express it as a single command; otherwise the user is scoped by owning only
the world directory:

```
command="rrsync -wo /srv/nomad",no-pty,no-port-forwarding ssh-ed25519 AAAA...
```

### Reverse tunnel (host behind NAT)

A host that has an SSH box can publish its game port through it instead of
configuring a router:

```bash
nomad play <world-id> --tunnel
```

The agent opens `ssh -N -R <remote_port>:localhost:<server_port> user@host`
before acquiring the lease (so the address it publishes is real), writes
`<host>:<remote_port>` into the lease, and closes the tunnel when the session
ends. Configure with `NOMAD_SSH_REVERSE_TUNNEL=1`, `NOMAD_SSH_REMOTE_PORT`, and
`NOMAD_SSH_REMOTE_HOST` (the address friends connect to; defaults to the SSH
host). The remote sshd needs `AllowTcpForwarding yes` (default) and
`GatewayPorts yes` to bind a non-loopback address. `ExitOnForwardFailure=yes`
makes a failed bind abort at startup instead of silently.

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
  storage/               WorldStoreProtocol + r2, git, vps, ssh backends + build_store
  manifest.py            inclusion manifest: what to sync + how to launch any game
  process_runtime.py     generic runtime: run an arbitrary server command
  tunnel.py              reverse ssh tunnel (publish the game port through a box)
  ssh_connection.py      one SSH connection shared by storage and tunnel
  ssh_identity.py        Nomad keypair: generate, fingerprint, provisioning
  ssh_provision.py       guided setup: probe, install key, create folder, verify
  secrets.py             redaction + log scrubbing
  audit.py               non-secret credential-use audit trail
  minecraft/             vanilla runtime: jar download, Java 21 check, process control
  sync/                  world folder layout helpers
  cli/                   argparse CLI (nomad)
  ui/                    PySide6 launcher (incl. the Set up over SSH wizard)
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
python -m pip install -e ".[dev]"     # core + test deps
python -m pip install -e ".[ui]"      # + PySide6 GUI
```

Create an R2 bucket and an API token with Object Read/Write scoped to it, then
either export the `NOMAD_R2_*` variables in your environment or let the GUI's
R2 Settings dialog persist them to `data/settings.json`.

## CLI

```bash
nomad storage set r2|git|vps|ssh # select backend
nomad storage status             # show active backend + settings (credentials redacted)
nomad ssh setup <user@host>      # install the key, create the folder, verify, save
nomad ssh init                   # generate the Nomad key; print dedicated-user steps
nomad ssh show                   # target, identity fingerprint, tunnel state
nomad manifest init [dir]        # write a starter nomad.json
nomad manifest check [dir]       # preview what the manifest syncs
nomad new <name>                 # create a world; prints its id
nomad join <world-id>            # register a friend's world
nomad play <world-id>            # host until Ctrl-C; pushes world on stop
nomad play <world-id> --tunnel   # also publish the game port through NOMAD_SSH_TARGET
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
| `NOMAD_SSH_TARGET` | — | ssh backend: `user@host` |
| `NOMAD_SSH_PATH` | `nomad-worlds` | ssh backend: remote base dir |
| `NOMAD_SSH_KEY` | — | ssh backend: identity file |
| `NOMAD_SSH_PORT` | 22 | ssh backend: port |
| `NOMAD_SSH_REVERSE_TUNNEL` | `false` | publish the game port through the ssh host |
| `NOMAD_SSH_REMOTE_PORT` | server port | remote port to bind for the tunnel |
| `NOMAD_SSH_REMOTE_HOST` | ssh host | address written into the lease for the tunnel |
| `NOMAD_AUDIT_LOG` | `1` | write the non-secret credential-use audit trail |
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