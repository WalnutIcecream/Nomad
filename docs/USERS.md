# Nomad — User Guide

> **The world stays. The host changes.**
>
> Everyone opens a phone, think of Nomad as the way your group shares **one
> persistent Minecraft world** without anyone having to leave a server running
> forever. Whoever happens to be online can run the world for a while; when they
> stop, the world is saved back where it lives and someone else continues it
> later.

This guide is in two layers:

- **Layer 1 — "How do I use Nomad?"** (the first half). Written for a normal
  Minecraft player. No jargon.
- **Layer 2 — "How does Nomad work inside?"** (the second half). The technical
  details, configuration tables, CLI, and troubleshooting, for people who want
  to dive in.

---

# LAYER 1 — USING NOMAD

## What is Nomad?

Nomad is a launcher that lets a group of friends share a single Minecraft world
without needing one dedicated computer to run it 24/7.

With a normal server, one machine has to be on all the time and everyone else
connects to it. With Nomad:

- the **world** lives permanently in shared storage,
- the **computer that hosts it** is whoever presses Play at the moment,
- when the host closes the game, the world is saved back to storage,
- anyone else can press Play later and continue the **same world**.

> **WHO** — your group of friends.
> **WHAT** — one shared Minecraft world.
> **WHERE** — the world lives in shared storage; the game runs on whoever's PC is hosting right now.
> **WHY** — so nobody has to leave a server running all the time.

## The idea: "The world stays. The host changes."

```
                    SHARED WORLD
                         |
              +----------+----------+
              |                     |
          PLAYER HOST           PLAYER HOST
          Alice's PC             Bob's PC
              |                     |
              +----------+----------+
                         |
                       PLAYERS
```

The **world** is the persistent thing. The **computer running Minecraft** is
temporary — it is whichever player is hosting at the time. When Alice stops,
Bob can start the same world on his PC and everyone continues where they left
off.

Concretely, that means:

- A group has **one persistent Minecraft world**.
- That world is stored in **shared storage**.
- **Whoever is online can become the host.**
- The **first person to press Play** becomes host if nobody else already is.
- The host's PC runs Minecraft, and the rest of the group joins it.
- When the host stops, the world is **uploaded back to shared storage**.
- Another player can then become host and **continue from the same world**.
- **There is no permanent Minecraft server** and no always-on computer required.

## How a group actually uses Nomad

```text
                Someone opens Nomad
                        |
                        v
                   Clicks PLAY
                        |
                        v
              Is someone already hosting?
                 /                  \
               YES                  NO
                |                    |
                v                    v
          Join existing        Become host
              host                  |
                                     v
                               Download world
                                     |
                                     v
                                Start Minecraft
                                     |
                                     v
                               Friends join
```

In the normal case you never have to decide *which machine* runs the server.
You just press Play. If nobody else is hosting, your PC hosts. If someone else
already is, you simply join them. Nomad sorts it out.

### First-time setup — the 30-second version

Someone (one person) sets up shared storage once, then everyone connects to it.

1. **Choose where the shared world will be stored.** (See "Choosing storage".)
2. **Configure that storage once** — in Nomad's settings.
3. **Create a Nomad world.** Nomad gives you a **World ID**.
4. **Share the World ID with your friends.**
5. **Everyone installs Nomad and connects to the same storage**, then enters
   the World ID they were given.
6. **Click Play.**

Important: **only one person sets up the shared storage.** The other players do
not create their own storage — they just point at the same place you set up and
use the World ID you shared.

There are **no Nomad accounts**. Being "in the group" just means you have the
World ID *and* access to that same shared storage.

> **WHO** sets up storage — one person in the group.
> **WHAT** — a World ID, which is the invite.
> **WHERE** — everyone points at the same shared storage.
> **WHY** — because that is how the group shares one world.

## Choosing storage: "Where should your shared world live?"

The world has to live somewhere everyone can reach. Nomad gives you four
choices. You pick one; it only really matters to the person setting it up.

| Option | Plain English | Best for |
|---|---|---|
| **Cloudflare R2** | The world lives in a free-tier cloud bucket. | Most groups; friends anywhere on the internet. |
| **Git** | The world lives in a shared Git repository. | Smaller worlds; people who already use Git. |
| **Your own S3-compatible server** | The world lives on a server you run (MinIO/Garage/SeaweedFS). | People who already run a box and want control. |
| **SSH / another computer** | The world lives on a machine you already own, reached over SSH. | People who have a spare always-on machine. |

### Cloudflare R2

> "Put your world in a Cloudflare R2 bucket. This is the simplest cloud option."

- **Who should use it:** most groups — it requires no server to run and friends
  can connect from anywhere.
- **Setup requires:** a free Cloudflare account, a bucket, and an API token;
  then paste four values into Nomad's settings.
- **Limitations:** usage is metered against a free tier (roughly 10 GB in
  storage and short monthly operation budgets). Right at friends-scale use this
  is usually far under the limit.

### Git

> "Use a shared Git repository as the world store. Best for smaller worlds and
> people who already use Git."

- **Who should use it:** small worlds, and groups that already have a shared Git
  repo.
- **Setup requires:** a repo (locally, or a remote you can push to), entered in
  Nomad's settings.
- **Limitations:** Git rejects single files over **100 MB**, and every save adds
  a full copy of the world to the repo's history, so a long-lived world grows
  the repo. Great for tiny saves, not for huge ones.

### Your own S3-compatible server (VPS)

> "Use your own MinIO/Garage/SeaweedFS server."

- **Who should use it:** people who already operate a server and prefer to host
  their own data.
- **Setup requires:** one person installs MinIO (or similar), creates a bucket
  and a key, and shares three values (endpoint, bucket, credentials).
- **Limitations:** the box must stay reachable; storage and bandwidth come out
  of that box's resources.

### SSH / another computer you already own

> "Use another machine you already own as the place where Nomad stores the
> shared world. You only need SSH access to that machine."

See the SSH section below for a proper walkthrough — it is the friendliest of
the "own machine" options because Nomad sets most of it up for you.

---

## SSH storage, explained

SSH storage answers the question: **"Where is my persistent world stored?"**

It means "I already own a computer that's on most of the time, and I'll let
Nomad store the group's world on it." That machine does **not** run Minecraft —
it only holds the files. It is the *shared storage*.

```text
        STORAGE MACHINE
        +-------------+
        | SSH server  |
        |             |
        | Nomad worlds|
        +------+------+
               |
              SSH
               |
        +------+------+
        |             |
      Alice          Bob
      Nomad          Nomad
```

That machine holds:

- **the world** (the `.tar.gz` archive),
- **the lease** (who is hosting, see Layer 2),
- **the shared Nomad data** that lets the group coordinate.

Anyone with access to that machine can host or join the same world.

### Setting up SSH storage

The way it is meant to be done — no terminal, no SSH-key knowledge required:

1. Have a machine reachable over SSH (an address like `192.168.1.20` or a
   hostname, plus a username).
2. Open Nomad.
3. Choose **"Set up over SSH"**.
4. Enter `user@host` (the username and address).
5. Nomad **generates and configures** the SSH key for you.
6. Nomad **verifies** the connection works.
7. Storage is ready.

You do **not** normally need to understand SSH keys, `authorized_keys`, remote
paths, or ports. Nomad handles all of that automatically — it only asks for the
one thing it can't know: the machine's address. If the automatic setup ever
fails, Nomad shows the exact one-line command you can run instead, with a copy
button.

> The manual, advanced way to configure SSH (a dedicated user, custom keys,
> scoped access) is preserved in the Advanced section at the bottom. You almost
> certainly don't need it.

---

## Playing a world

### Hosting (you run the world)

1. Open Nomad and find your world.
2. Press **PLAY**.
3. Nomad downloads the latest copy of the world, starts Minecraft on your PC,
   and tells you when it's up.
4. Everyone else joins you (below).
5. When you're done, press **STOP** — Nomad saves the world back to shared
   storage so it's ready for the next person.

While you host, the card shows **"You are hosting."** Your PC is the Minecraft
server for everyone right now.

### Joining (you play on someone else's world)

1. Open Nomad (or install it and enter the World ID your friend shared).
2. Wait until the world card shows **"Hosting by \<name\>"** — that means someone
   has the world running.
3. Press **JOIN**.
4. Nomad shows you the address to enter in Minecraft (**Multiplayer → Direct
   Connect**). Copy it, paste it, you're in.
5. If the world is **Sleeping** (nobody hosting), just press PLAY yourself — you
   become the host and everyone can join you.

### When the host leaves

When the current host stops, the group doesn't lose anything — the world simply
moves to storage, ready for the next host:

```text
Alice is hosting
      |
      v
Alice stops
      |
      v
Minecraft shuts down
      |
      v
World is uploaded to shared storage
      |
      v
Alice releases hosting
      |
      v
(shows as "Sleeping")
      |
      v
Bob clicks PLAY
      |
      v
Bob becomes host
      |
      v
Bob continues the same world
```

**"The world stays. The host changes."** This is the whole idea of Nomad.

If a host's computer crashes or goes offline without stopping cleanly, Nomad
waits a short while and then automatically frees the world so someone else can
take over — the world is never lost.

---

## Networking: how friends actually reach the host

Storage is **where the world lives**. Networking is **how the host is reachable**.
These are two separate things.

```
                    NOMAD
                      |
             Who is hosting?
                      |
                      v
              Current host
                      |
                      v
                 Networking
                      |
              +-------+-------+
              |               |
          Direct/LAN       Relay/Tunnel
```

> Nomad decides **who** is hosting. The networking layer makes **that host
> reachable** to the other players.

### Direct connection

If the host's PC is directly reachable — same home network, or the router
forwards the game port (or the host published a public address) — friends join
the host's address directly. This is the simplest case and the default. It needs
no extra setup beyond the host publishing an address.

### SSH tunneling (for a host stuck behind NAT)

Sometimes the host's PC is behind NAT/CGNAT or a restrictive router, so friends
on the internet can't reach it directly — even though *that PC* can still make
**outgoing** connections. Tunneling answers a completely different question from
SSH storage:

> **SSH STORAGE:** "Where do I store the world?"
> **SSH TUNNEL:** "How can my friends connect to the Minecraft server running on my PC?"

```text
                 INTERNET
                    |
                    v
              SSH / VPS BOX
              public address
                    |
              SSH reverse tunnel
                    |
                    v
               MY PC
                    |
                    v
             Minecraft :25565
```

With a tunnel, Nomad opens a secure connection **from your PC out to a box that
has a public address**, and publishes the game through it. Friends connect to
that box's address, and the tunnel carries the traffic straight to your
Minecraft.

- This is useful only when the **host** can't be reached directly.
- It does **not** come free with SSH storage — they are separate features.
  Using SSH *storage* does **not** automatically make the Minecraft server
  publicly reachable.

**How to enable it:** either pass `--tunnel` when you play, or turn on "Publish
the game port through this host (reverse tunnel)" in the SSH settings. You need
an SSH box that allows forwarding (the remote side needs `AllowTcpForwarding yes`
and, to reach the public internet, `GatewayPorts yes`).

### Managed relay / provider (conceptual, coming later)

In the future Nomad may also support a **managed relay**: a service that holds a
stable public endpoint for the group. When the host changes, Nomad updates which
machine that endpoint points to, so friends keep using the **same address**:

```text
Alice hosts                 Alice leaves                Bob hosts
     |                            |                        |
     v                            v                        v
Public endpoint -> Alice    (world saved)       Public endpoint -> Bob
```

Players continue using the same public endpoint the whole time. This is a
planned/optional integration — it is **not yet implemented** in the current
version of Nomad.

---

## Hybrid hosting (conceptual, coming later)

Nomad's future hosting model lets the *world* stay the same while the *machine
running it* is chosen automatically. Two kinds of "compute" can host:

```text
                      NOMAD
                      |
             Where should it run?
                /           \
               /             \
        PLAYER PC          CLOUD
        compute            compute
           |                 |
           +--------+--------+
                    |
               SAME WORLD
                    |
                 PLAYERS
```

The intended experience:

- If a player **can** host on their PC, the world runs on their PC (free).
- If **nobody suitable** can host, a cloud server can run it instead.
- The world is the same either way — only the machine running Minecraft changes.

Potential modes (this is a design sketch, **not implemented yet**):

- **Self-hosted** — players provide the computer; you pay only for networking /
  tunneling if you need it.
- **Hybrid** — prefer player PCs, fall back to cloud hosting when necessary;
  cloud is billed only while it's actually in use.
- **Cloud** — always run on cloud compute.

Right now Nomad runs only on player PCs. The cloud-host concept is documented
here so the user experience is ready for it.

---

# LAYER 2 — HOW NOMAD WORKS INSIDE

## The internal model: worlds, leases, and archives

Each world is three objects in shared storage under one key prefix:

```
worlds/<world-id>/lease.json       # "who is hosting, until when, at what address"
worlds/<world-id>/world.tar.gz     # the world, as one archive (last write wins)
worlds/<world-id>/world.sha256     # checksum of the archive, verified on download
```

There is exactly **one** world archive per world — nothing accumulates versions.
The last host's upload *is* the world.

### The lease

The lease answers "who is the host right now?" using the storage backend's
atomic conditional write — the only coordination primitive Nomad needs. There is
no lock server and no always-on coordinator.

- **acquire** — write the lease. When it doesn't exist, Nomad writes with
  `If-None-Match: *`; when it exists but is expired/released, it writes with
  `If-Match: <current etag>`. Of two simultaneous players, exactly one wins; the
  loser learns the current host's identity from the rejection.
- **renew (the "heartbeat")** — while hosting, Nomad re-writes the lease every
  **20 seconds** (default), pushing the expiry forward by the lease duration.
  A host that loses the lease (someone else took it over) shuts down cleanly.
- **release** — marks the lease released **after** the world upload completes.
  Ordering matters: a new host can only acquire once the world they'd pull is
  final.
- **expiry** — if a host crashes and never renews, the lease expires (default
  **300 seconds** later) and anyone can take it over. This is the crash-recovery
  path: a dead host frees the world automatically instead of blocking it forever.

### The world archive

A host **pulls** `world.tar.gz` (verifying its SHA-256 against the stored
checksum) before booting, and **uploads** a fresh archive on stop. A player who
is only joining just needs pull access — they never download or host anything.

## The host lifecycle

The full host session is a fixed sequence:

```
1. connect to storage
2. (optional) start reverse tunnel
3. acquire the lease          -> if someone else holds it: "Join" instead
4. pull latest world, verify  -> or start fresh if there is none yet
5. resolve how to run:
     nomad.json manifest?  -> run its command (any game)
     otherwise             -> vanilla Minecraft runtime
6. renew the lease every 20s while the server runs
7. on stop: shut server down, tar the world, upload it, then release the lease
```

On **any** error or early exit, Nomad releases the lease if it still holds it
(expiry frees it if even the release fails). The world is never held hostage by
a crashed host.

## The vanilla Minecraft runtime

For worlds with no manifest, Nomad runs the official vanilla server:

- downloads `server.jar` for the configured version (default `1.21.1`) from
  Mojang,
- requires **Java 21** and validates it before booting,
- writes a whitelisted `server.properties` and an auto-accepted `eula.txt`,
- boots with `java -Xmx<memory> -Xms<memory> -jar server.jar nogui`,
- stops gracefully by sending `stop` to the process's stdin.

## Hosting any game with a manifest (`nomad.json`)

Nomad isn't tied to Minecraft. A `nomad.json` file in the server directory is
the *inclusion* counterpart to `.gitignore`: it says what to sync and,
optionally, how to launch the server.

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

- `include` — glob patterns relative to the directory; a pattern naming a
  directory includes its whole subtree; empty means "everything".
- `exclude` — removed from the set; exclude always wins.
- `server.command` — argv to launch (cwd = synced directory). Present = run this
  instead of the vanilla Minecraft runtime.
- `server.stop_command` — a line written to stdin for graceful shutdown; absent =
  the process is terminated.

The manifest **travels with the world**, so whoever hosts next boots the same
game. Launcher artifacts (`nomad.pid`, `nomad.stop`, `upload.tar.gz`) are always
excluded automatically.

```bash
nomad manifest init ./my-server     # write a starter nomad.json
nomad manifest check ./my-server    # preview what would sync
```

## Configuration

Settings come from (highest priority first): **OS environment variables** >
`data/settings.json` (written by the GUI) > `.env` > built-in defaults.
Credentials are stored encrypted-at-rest only via filesystem permissions and are
redacted from all logs.

| Variable | Default | Notes |
|---|---|---|
| `NOMAD_STORAGE_BACKEND` | `r2` | `r2` \| `git` \| `vps` \| `ssh` |
| `NOMAD_R2_ACCOUNT_ID` | — | R2 endpoint construction |
| `NOMAD_R2_ACCESS_KEY` / `NOMAD_R2_SECRET_KEY` | — | SigV4 signing |
| `NOMAD_R2_BUCKET` | — | R2 bucket |
| `NOMAD_R2_ENDPOINT_URL` | auto | override S3 endpoint |
| `NOMAD_GIT_REPO` | `~/.nomad/repo` | git backend repo path |
| `NOMAD_GIT_REMOTE` | — | git backend remote URL |
| `NOMAD_VPS_ENDPOINT` | — | VPS S3 endpoint |
| `NOMAD_VPS_BUCKET` | — | VPS bucket |
| `NOMAD_SSH_TARGET` | — | ssh storage: `user@host` |
| `NOMAD_SSH_PATH` | `nomad-worlds` | ssh storage: remote base dir |
| `NOMAD_SSH_KEY` | — | ssh storage: identity file |
| `NOMAD_SSH_PORT` | `22` | ssh storage: port |
| `NOMAD_SSH_REVERSE_TUNNEL` | `false` | publish the game port through the ssh host |
| `NOMAD_SSH_REMOTE_PORT` | server port | remote port to bind for the tunnel |
| `NOMAD_SSH_REMOTE_HOST` | ssh host | address published in the lease for the tunnel |
| `NOMAD_AUDIT_LOG` | `1` | write the non-secret credential-use audit trail |
| `NOMAD_DATA_DIR` | platform default | launcher state, worlds, registry |
| `NOMAD_PLAYER_NAME` | env username | holder name shown in the lease ("the host") |
| `NOMAD_PUBLIC_ADDRESS` | — | LAN/port-forwarded address to publish in the lease |
| `NOMAD_MC_VERSION` | `1.21.1` | vanilla server jar version |
| `NOMAD_SERVER_PORT` | `25565` | server listen port |
| `NOMAD_JAVA_PATH` | `java` on PATH | JVM executable |
| `NOMAD_MEMORY` | `2G` | JVM heap |
| `NOMAD_EULA_ACCEPTED` | `false` | required before hosting |
| `NOMAD_LEASE_DURATION_SECONDS` | `300` | lease expiry without renewal |
| `NOMAD_HEARTBEAT_INTERVAL_SECONDS` | `20` | renewal cadence while hosting |

## CLI reference

```bash
nomad storage set r2|git|vps|ssh    # select a storage backend
nomad storage status                # active backend + settings (secrets redacted)
nomad ssh setup <user@host>         # guided: install key, create folder, verify
nomad ssh init                      # generate the Nomad key; print dedicated-user steps
nomad ssh show                      # target, identity, fingerprint, tunnel state
nomad manifest init [dir]           # write a starter nomad.json
nomad manifest check [dir]          # preview what the manifest syncs
nomad new <name>                    # create a world; prints its id
nomad join <world-id>               # add a friend's world by id
nomad play <world-id>               # host until Ctrl-C; pushes world on stop
nomad play <world-id> --tunnel      # also publish the game port through NOMAD_SSH_TARGET
nomad status <world-id>             # who hosts now
nomad worlds                        # list worlds with live host status
nomad usage                         # approximate local usage counters
```

Environment setup: `python -m pip install -e ".[dev]"` (core + tests) or
`python -m pip install -e ".[ui]"` (adds the PySide6 GUI). Requires Python
≥ 3.11 and Java 21 to host.

## Advanced: manual SSH configuration

The guided setup covers almost everyone. The manual/advanced pieces, for custom
layouts:

- **Identity.** Nomad keeps its own ed25519 keypair at
  `data/ssh/id_ed25519_nomad` (`0600` in a `0700` dir) so it never depends on
  your `~/.ssh` layout. Key resolution: `NOMAD_SSH_KEY` if set, else the Nomad
  key if it exists, else the system ssh default.
- **Isolated dedicated user (print-only).** `nomad ssh init` generates the key
  and *prints* the commands to create a scoped `useradd` user who owns only the
  world directory. It prints commands; it does not run them.
- **Scoped access.** Restrict the key in `authorized_keys` to a single command
  (e.g. `rrsync` scoped to the world dir) with `no-pty,no-port-forwarding`, or
  let the dedicated user be scoped by owning only the world directory.
- **Remote layout.** Under `<base>/<world-id>/`, the ssh backend keeps
  `lease/` (presence == claimed, atomic `mkdir`), `world.tar.gz`, and
  `world.sha256`. The guarantee is the same as R2, implemented with the remote
  filesystem's atomic `mkdir` instead of an S3 conditional write.
- **One connection per session.** On POSIX, connections are multiplexed
  (`ControlMaster=auto`, `ControlPersist=yes`) so the whole session costs one
  handshake; the reverse tunnel rides the same connection. Windows OpenSSH opens
  a connection per operation.

## Advanced: tunnel configuration

`ssh -N -R <remote_port>:localhost:<server_port> user@host` is opened *before*
acquiring the lease (so the published address is real), written into the lease
as `<remote_host>:<remote_port>`, and closed when the session ends. The remote
`sshd` needs `AllowTcpForwarding yes` (default) and, to bind a non-loopback
address, `GatewayPorts yes`. `ExitOnForwardFailure=yes` makes a failed bind abort
at startup instead of silently continuing.

## Security

- Credentials are `SecretStr`; every log record is **globally scrubbed** of
  credential-shaped text, and any remote URL's userinfo (a token in a git URL)
  is redacted before printing.
- `settings.json` and the SSH key are written owner-only (`0600`/`0700`); on
  load, a world-readable credentials file is refused (with the exact `chmod`
  command) or tightened.
- Opt-in **audit log** (`NOMAD_AUDIT_LOG=1`) records which credential was used
  and when — the *non-secret* facts, so debugging never leaks the secrets.
- Integrity: the world archive is **SHA-256** verified on every download, and
  archives are extracted safely (symlinks/hardlinks rejected, path-escape
  rejected).

## Diagnostics

- **`nomad config`** — current backend, readiness, player name, published address.
- **`nomad storage status`** — active backend, its settings (redacted), and a
  `ready: yes/no` check.
- **`nomad worlds` / `nomad status <id>`** — who hosts a world right now.
- **`nomad usage`** — approximate local usage counters (uploads, downloads, API
  requests) to gauge against the free tier. It is a *local estimate*; the
  authoritative numbers are on the provider's dashboard.

## Troubleshooting

- **"EULA not accepted"** when hosting → `nomad play --accept-eula` once, or set
  `NOMAD_EULA_ACCEPTED=true`.
- **"Couldn't host — is someone else hosting?"** → check `nomad status <id>`; the
  lease is held. Join instead, or wait for it to expire.
- **"Storage not configured"** → `nomad storage status` shows what's missing for
  the active backend; fill it in the GUI or via env vars.
- **Java too old** → install or point at Java 21 (`NOMAD_JAVA_PATH`).
- **Host behind NAT, friends can't connect** → enable the SSH `--tunnel`, or set
  `NOMAD_PUBLIC_ADDRESS` to a port-forwarded address, or host on a box that's
  reachable.
- **SSH setup asks for a password every time** → it needs a one-time password to
  install the key, then the key alone works. Run setup in a real terminal if the
  GUI can't prompt.
- **Git backend rejects an upload** → the blob is over Git's 100 MB per-file cap,
  or the shared repo is stale/not a shared remote; use R2/VPS/SSH for large
  worlds.