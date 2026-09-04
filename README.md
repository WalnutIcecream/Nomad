# Nomad

**Distributed Minecraft hosting with no servers of your own to run.** The
"coordinator" is a Cloudflare R2 bucket: a single `lease.json` decides who may
host a world at any moment, and a single `world.tar.gz` is the one shared
version of that world. Everything else — accounts, a database, a relay, an
always-on process — has been deliberately removed.

## The whole protocol

Each world is two objects in one R2 bucket:

```
worlds/<world-id>/
    lease.json       # {"status","holder","address","acquired_at","expires_at"}
    world.tar.gz     # the single shared version; last upload wins
```

**Lease = who may host right now.** The lease object is written with a
compare-and-swap (an S3 conditional write):

- **Play** → if no lease exists, `PUT If-None-Match: *` wins instantly. If a
  lease exists but is expired/released, `PUT If-Match: <old etag>` steals it.
  When two friends press Play in the same second, exactly one conditional write
  lands; the loser gets `412` and the UI offers Join. No coordinator, no lock
  server — the object store's atomicity *is* the arbitration.
- **While hosting** → the host re-asserts the lease (a heartbeat PUT with its
  own etag) every 20s. A crashed host stops renewing and the lease expires
  after 5 minutes, freeing the world automatically.
- **Stop** → the host archives the world, uploads it, *then* releases the
  lease. The next host can only acquire once the world is final.

**World = whoever hosts next gets the newest build.** The next host downloads
`world.tar.gz`, extracts it, boots the server; on stop the world is re-archived
and uploaded as the new single version. No version numbers, no conflict rules:
the lease serializes everything.

## Repository layout

| Path | What it is |
| --- | --- |
| `launcher/cloud.py` | Stdlib-only S3/R2 client (SigV4) + the lease protocol (`WorldStore`) |
| `launcher/agent.py` | Host lifecycle: lease → pull → boot → renew → push → release |
| `launcher/registry.py` | Local address book of worlds (id, name, mc version) |
| `launcher/config.py` | Settings (R2 creds, Java, memory); persisted to `data/settings.json` |
| `launcher/server_properties.py` | Whitelisted Minecraft `server.properties` subset |
| `launcher/minecraft/` | Vanilla server runtime: jar download, Java 21 check, process control |
| `launcher/sync/worldfolder.py` | Folder validation / level-layout helpers |
| `launcher/ui/` | PySide6 launcher: worlds list, Play/Stop/Join, R2 settings dialog |
| `launcher/cli/` | `nomad` command line |

## Setup

1. **Create an R2 bucket** at dash.cloudflare.com (R2 → Create bucket) and an
   API token with **Object Read & Write** scoped to that bucket.
2. **Tell Nomad about it** — either environment variables
   (`NOMAD_R2_ACCOUNT_ID`, `NOMAD_R2_ACCESS_KEY`, `NOMAD_R2_SECRET_KEY`,
   `NOMAD_R2_BUCKET`) or the launcher's **R2 Settings…** dialog (first launch
   prompts automatically).
3. **Install**: `python -m pip install -e ".[dev]"` (add `.[ui]` for the GUI).

## Use

```bash
python -m launcher.ui          # the launcher window
nomad-gui                      # same, via the console script

nomad new "Survival"           # create a world; prints its id
nomad join <world-id>          # play a friend's world (they share the id)
nomad play <world-id>          # host from the terminal (Ctrl-C stops + pushes)
nomad status <world-id>        # who's hosting right now?
nomad worlds                   # list worlds with host status
```

Everyone who plays a world shares the same **world id** and has **bucket
access**. The host can publish a `public_address` (via R2 Settings) that lands
in the lease, so friends know where to point their Minecraft client — set your
port-forward/public IP there when you get to that step.

## How hosting actually goes (agent)

1. **Acquire** the lease (atomic conditional write). Denied? Someone else is
   hosting — you join instead.
2. **Pull** `world.tar.gz`, extract into `data/worlds/<id>/`, ensure the level
   layout (`world/` subdir) the vanilla server expects.
3. **Boot** `java -Xmx2G -Xms2G -jar server.jar nogui` (jar auto-downloaded for
   the configured version; Java 21 required).
4. **Renew** the lease every 20s while the server runs.
5. **On stop**: graceful `stop` command flushes the world → tar → upload →
   release lease. If the process dies, the lease expires on its own in 5
   minutes and the next host steals it from the stale etag.

## Configuration

All settings are pydantic-settings with the `NOMAD_` prefix (`.env` supported).
The R2 fields are also editable in the GUI and persist to `data/settings.json`.

| Variable | Default | Used by |
| --- | --- | --- |
| `NOMAD_R2_ACCOUNT_ID` | — | R2 endpoint construction |
| `NOMAD_R2_ACCESS_KEY` / `NOMAD_R2_SECRET_KEY` | — | S3 SigV4 signing |
| `NOMAD_R2_BUCKET` | — | the shared bucket |
| `NOMAD_R2_ENDPOINT_URL` | `https://<account_id>.r2.cloudflarestorage.com` | optional override |
| `NOMAD_DATA_DIR` | platform data dir | launcher state, worlds |
| `NOMAD_PLAYER_NAME` | `USERNAME` env | name published in the lease as host |
| `NOMAD_PUBLIC_ADDRESS` | empty | address published in the lease (port-forward later) |
| `NOMAD_MC_VERSION` | `1.21.1` | vanilla server jar version |
| `NOMAD_SERVER_PORT` | `25565` | local Minecraft server |
| `NOMAD_JAVA_PATH` | `java` on PATH | JVM executable |
| `NOMAD_MEMORY` | `2G` | JVM heap |
| `NOMAD_EULA_ACCEPTED` | `false` | must accept before hosting |
| `NOMAD_LEASE_DURATION_SECONDS` | `300` | lease expiry without renewal |
| `NOMAD_HEARTBEAT_INTERVAL_SECONDS` | `20` | lease renewal cadence |

## Tests

```bash
python -m pytest
```

Covers the lease CAS protocol (acquire / deny / renew / release / stale-steal)
against an in-memory S3 double with real conditional-write semantics, world
archive round-trips, the SigV4 signer (byte-compared against botocore so R2
accepts it), the registry, and world-folder helpers.

## Design notes

- **No backend to deploy.** The object store is the coordinator; conditional
  writes are the mutex. Works as long as everyone has bucket access.
- **No accounts.** A world id + bucket permission is the whole membership
  model. Sharing the id is the invite.
- **Deliberately deferred** (per scope): port forwarding/discovery (fill in
  `public_address` for now), NAT relay, friend-invite UX, and a bundled exe
  (this repo is the run-from-source launcher).
