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

The world has to live somewhere everyone can reach. Nomad gives you two choices.
You pick one; it only really matters to the person setting it up.

| Option | Plain English | Best for |
|---|---|---|
| **Cloudflare R2** | The world lives in a free-tier cloud bucket. | Most groups; friends anywhere on the internet, and nobody has to run anything. |
| **My own server** | The world lives on an S3-compatible server you run yourself (Garage), on a VPS or home machine. | People who already run a server and want to keep their world on their own hardware. |

Whichever you choose, Nomad's setup wizard walks you through it and **tests the
connection before saving**, so you find out immediately if a value is wrong
rather than the first time you press Play.

### Cloudflare R2

> "Put your world in a Cloudflare R2 bucket. This is the simplest option."

- **Who should use it:** most groups — it requires no server to run and friends
  can connect from anywhere.
- **Setup requires:** a free Cloudflare account, a bucket, and an API token;
  then the wizard asks for the account ID, keys and bucket.
- **Limitations:** usage is metered against a free tier (roughly 10 GB in
  storage and short monthly operation budgets). At friends-scale use this is
  usually far under the limit.

### My own server

> "Store your world on a server you run yourself."

- **Who should use it:** people who already operate a server (or a home machine)
  and prefer to keep their world on their own hardware.
- **Setup requires:** one person installs an S3-compatible service — **Garage**
  is the supported one — creates a bucket (for example `nomad-worlds`), and
  mints an access key. Then each player enters four values: **endpoint, bucket,
  access key, secret key**, and presses **Test Connection**.
- **Limitations:** the machine must stay reachable, and storage and bandwidth
  come out of its resources.

The server holds the world; it does **not** run Minecraft. The player's own
computer runs the game, exactly as with R2.

---

## My own server, explained

This answers the question: **"Where is my persistent world stored?"**

It means "I already own a server that's on most of the time, and I'll let Nomad
store the group's world on it." That machine does **not** run Minecraft — it only
holds the files. It is the *shared storage*.

```text
        STORAGE SERVER (Garage)
        +---------------------+
        |  S3 API             |
        |                     |
        |  nomad-worlds       |
        +----------+----------+
                   |
                S3 API
                   |
        +----------+----------+
        |                     |
      Alice                  Bob
      Nomad                  Nomad
      (Minecraft)            (Minecraft)
```

That server holds the world archive, the lease (who is hosting), and the
checksum. Anyone with access to that bucket can host or join the same world.

Nomad never configures Garage for you. Garage is server-side infrastructure: the
administrator installs it, enables its S3 API, creates the bucket and the key,
and hands out the four values above. The wizard has a "How do I set this up?"
section that says exactly this, so you can forward it to whoever runs the box.

If you are that administrator, the steps are:

1. Install Garage on a Linux server and enable its S3 API.
2. Create a bucket for the worlds (for example `nomad-worlds`).
3. Mint an access key and secret for Nomad.
4. Share the endpoint, bucket, access key and secret key with your players.

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
                      v
             the host's address
```

> Nomad decides **who** is hosting. The networking layer makes **that host
> reachable** to the other players.

### Direct connection

If the host's PC is directly reachable — same home network, or the router
forwards the game port (or the host published a public address) — friends join
the host's address directly. This is the simplest case and the default. It needs
no extra setup beyond the host publishing an address.

### When the host can't be reached directly

If the host is behind NAT or a restrictive router, friends on the internet can't
reach that PC directly. Nomad does not try to solve this for you: it uses
whatever the project already uses for Minecraft networking. The reliable fixes
are on your side — forward the game port on the router and set
`NOMAD_PUBLIC_ADDRESS` to that `host:port`, or host on a machine that is already
reachable.

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
1. acquire the lease          -> if someone else holds it: "Join" instead
2. pull latest world, verify  -> or start fresh if there is none yet
3. resolve how to run:
     nomad.json manifest?  -> run its command (any game)
     otherwise             -> vanilla Minecraft runtime
4. renew the lease every 20s while the server runs
5. on stop: shut server down, tar the world, upload it, then release the lease
```

The launcher shows each step as it happens — Ready, Starting, Hosting, Stopping,
Syncing, then Ready again — so you can always tell what the world is doing.

On **any** error or early exit, Nomad releases the lease if it still holds it
(expiry frees it if even the release fails). The world is never held hostage by
a crashed host.

The one deliberate exception is a **failed upload**. If the world can't be saved
back to shared storage, Nomad does **not** release the lease — releasing would
let the next host pull a world older than the copy still on your disk. Instead it
tells you the sync failed and that your local copy is intact, and the lease
expires on its own a few minutes later.

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
`data/settings.json` (written by the wizard) > `.env` > built-in defaults.
Storage secrets go to your **operating system keychain** when one is available,
and otherwise to `settings.json` (`0600`, owner-only). Either way they are
redacted from all logs.

| Variable | Default | Notes |
|---|---|---|
| `NOMAD_STORAGE_BACKEND` | `r2` | `r2` \| `server` |
| `NOMAD_R2_ACCOUNT_ID` | — | R2 endpoint construction |
| `NOMAD_R2_ACCESS_KEY` / `NOMAD_R2_SECRET_KEY` | — | SigV4 signing |
| `NOMAD_R2_BUCKET` | — | R2 bucket |
| `NOMAD_R2_ENDPOINT_URL` | auto | override the R2 S3 endpoint |
| `NOMAD_SERVER_ENDPOINT` | — | your own server's S3 endpoint |
| `NOMAD_SERVER_BUCKET` | — | your own server's bucket |
| `NOMAD_SERVER_ACCESS_KEY` / `NOMAD_SERVER_SECRET_KEY` | — | your own server's credentials |
| `NOMAD_KEYCHAIN` | `1` | `0` forces credentials into the settings file |
| `NOMAD_AUDIT_LOG` | `1` | write the non-secret lease/credential-use audit trail |
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
nomad storage set r2|server         # select where worlds are stored
nomad storage status                # active backend + settings (secrets redacted)
nomad storage test                  # prove the storage can read and write
nomad manifest init [dir]           # write a starter nomad.json
nomad manifest check [dir]          # preview what the manifest syncs
nomad new <name>                    # create a world; prints its id
nomad join <world-id>               # add a friend's world by id
nomad play <world-id>               # host until Ctrl-C; pushes world on stop
nomad status <world-id>             # who hosts now
nomad worlds                        # list worlds with live host status
nomad usage                         # approximate local usage counters
```

Environment setup: `python -m pip install -e ".[dev]"` (core + tests),
`python -m pip install -e ".[ui]"` (adds the PySide6 GUI), or
`python -m pip install -e ".[ui,secure]"` (adds OS-keychain credential storage).
Requires Python ≥ 3.11 and Java 21 to host.

## Advanced: editing the raw connection values

The wizard is the normal path, but **Settings → Storage → Advanced** exposes the
raw values for the active backend — endpoint, bucket, access key, secret key —
with a **Test connection** button, for people who would rather paste env-style
values than step through a wizard.

## Security

- Credentials are `SecretStr`; every log record is **globally scrubbed** of
  credential-shaped text.
- Secrets live in the **OS keychain** when available; otherwise
  `settings.json` is written owner-only (`0600`/`0700`), and on load a
  world-readable credentials file is refused (with the exact `chmod` command) or
  tightened.
- Opt-in **audit log** (`NOMAD_AUDIT_LOG=1`) records which lease action happened
  and when — the *non-secret* facts, so debugging never leaks the secrets.
- Integrity: the world archive is **SHA-256** verified on every download, and
  archives are extracted safely (symlinks/hardlinks rejected, path-escape
  rejected).

## Diagnostics

- **`nomad config`** — current backend, readiness, player name, published address.
- **`nomad storage status`** — active backend, its settings (redacted), and a
  `ready: yes/no` check.
- **`nomad storage test`** — writes, reads back and deletes a probe object, to
  prove the credentials and bucket really work.
- **`nomad worlds` / `nomad status <id>`** — who hosts a world right now.
- **`nomad usage`** — approximate local usage counters (uploads, downloads, API
  requests) to gauge against the free tier. It is a *local estimate*; the
  authoritative numbers are on the provider's dashboard.

## Troubleshooting

- **"Storage unavailable" / "Nomad couldn't access this bucket"** → press
  **Test connection** in the wizard or Settings; the message says whether it is a
  permission, bucket-name, or reachability problem, and "View technical details"
  has the raw error.
- **"EULA not accepted"** when hosting → `nomad play --accept-eula` once, or set
  `NOMAD_EULA_ACCEPTED=true`.
- **"Couldn't start — someone else may be hosting"** → check `nomad status <id>`;
  the lease is held. Join instead, or wait for it to expire.
- **"Your world couldn't be synced"** → the upload to storage failed, so the
  lease was kept (your local copy is safe). Check the storage is reachable, then
  press Play again — the lease expires on its own within a few minutes.
- **Java too old** → install or point at Java 21 (`NOMAD_JAVA_PATH`).
- **Friends can't connect** → set `NOMAD_PUBLIC_ADDRESS` to a port-forwarded
  address, or host on a machine that is already reachable.