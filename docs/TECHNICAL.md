# Technical documentation

## Components

- `launcher/cloud.py` — S3/SigV4 client; `Lease`, `WorldStore`, `UsageCounter`.
- `launcher/storage/` — `WorldStoreProtocol` + `r2`, `git`, `vps`, `ssh` backends; `build_store` dispatch.
- `launcher/agent.py` — host lifecycle.
- `launcher/manifest.py` — inclusion manifest (`nomad.json`); include/exclude resolution, archiving.
- `launcher/process_runtime.py` — generic runtime: run an arbitrary server command.
- `launcher/tunnel.py` — reverse ssh tunnel (forward the game port through a box).
- `launcher/ssh_connection.py` — the shared SSH connection descriptor (target, key, control socket).
- `launcher/ssh_identity.py` — Nomad keypair generation, fingerprint, install command, provisioning steps.
- `launcher/ssh_provision.py` — guided setup: probe access, install the key, create the folder, verify.
- `launcher/secrets.py` — credential redaction and global log-record scrubbing.
- `launcher/audit.py` — non-secret audit trail of credential use.
- `launcher/registry.py` — local world list.
- `launcher/config.py` — settings + persistence.
- `launcher/minecraft/` — vanilla runtime (jar download, Java check, process control).
- `launcher/sync/` — world folder layout helpers.
- `launcher/cli/`, `launcher/ui/` — interfaces.

## Protocol

Per world, two objects (plus a hash companion):

```
worlds/<id>/lease.json       {"status","holder","address","acquired_at","expires_at"}
worlds/<id>/world.tar.gz     single world blob, overwrite-only
worlds/<id>/world.sha256     hex SHA-256 of the blob
```

### Lease

Compare-and-swap on the lease object.

- **acquire** — no lease: write with `If-None-Match: *`. Expired/released lease: write with `If-Match: <etag>`. One concurrent writer wins; the loser gets the backend's 412 and reads the current holder.
- **renew** — while hosting, write with `If-Match: <own etag>` every 20s, pushing `expires_at` forward by the lease duration.
- **release** — mark `released` with `If-Match: <own etag>` after the world upload lands.
- **expiry** — a lease past `expires_at` without renewal is expired; any client may steal it via the stale etag.

### World

- **upload** — tar the world dir, PUT the blob, PUT its SHA-256, then release the lease.
- **download** — GET the blob, verify against `world.sha256` if present, extract.

### git backend

Lease and blob are committed to a repo. The CAS is git's fast-forward-only push: a stale local history is rejected. Configure `NOMAD_GIT_REPO` / `NOMAD_GIT_REMOTE`. Limits: 100 MB per file, history grows per save.

### vps backend

Same S3 client against a self-hosted endpoint. Configure `NOMAD_VPS_ENDPOINT` / `NOMAD_VPS_BUCKET`.

### ssh backend

Shells out to the system `ssh` binary (`BatchMode=yes`, `StrictHostKeyChecking=accept-new`)
against a box you own. No server software beyond sshd. Configure
`NOMAD_SSH_TARGET` / `NOMAD_SSH_PATH` / `NOMAD_SSH_KEY` / `NOMAD_SSH_PORT`.

The lease CAS is the remote filesystem's atomic `mkdir`: claiming is
`mkdir <base>/<id>/lease` (succeeds for exactly one client). The lease dir holds
`lease.json` including a `lease_id`; `renew`/`release` only proceed if the stored
`lease_id` matches, so a host that lost the lease cannot renew or release it. An
expired lease is removed and re-claimed through the same `mkdir`, so the single
winner property holds under contention. Blob transfer is `cat`-over-ssh (upload
and download), with the SHA-256 companion verified on pull.

Renewal is read-then-write (two remote calls per heartbeat). All argv comes from
a shared `SshConnection` (`launcher/ssh_connection.py`), which sets
`ControlMaster=auto`, `ControlPath=<data_dir>/ssh/ctl-<hash>`,
`ControlPersist=yes`, `ServerAliveInterval=30`, and `-T`. The agent calls
`store.open()` at session start to establish the master, and `close()` issues
`ssh -O exit` (before the host, as ssh requires) and unlinks the socket. Windows
has no multiplexing and runs without it.

### SSH identity

`launcher/ssh_identity.py` keeps a Nomad-owned ed25519 keypair at
`data/ssh/id_ed25519_nomad` (`0600` in a `0700` dir), generated on demand with
`ssh-keygen` (no passphrase, because `BatchMode=yes` cannot prompt). Resolution
order for `-i`: `NOMAD_SSH_KEY` → the Nomad key if present → nothing (system
default).

### Guided SSH setup

`launcher/ssh_provision.py` runs the end-to-end setup behind `nomad ssh setup`
and the GUI's *Set up over SSH…*. It is separate from the storage transport
because it authenticates with the user's *existing* access, never with the key it
is installing. Order:

1. `probe` runs `ssh -o BatchMode=yes <target> true` with the default identity —
   no `-i`, so it reports whether passwordless access already works. Failures are
   classified as `auth` (needs a password) or `unreachable` (a password cannot
   help).
2. `install_key` sends one idempotent remote command that creates `~/.ssh`
   (`0700`), touches `authorized_keys` (`0600`), and appends the public key only
   if `grep -qxF` does not already find it. No `sudo`, no account creation.
3. `verify` reconnects with `-i <nomad key>` **and** `-o IdentitiesOnly=yes`.
   Without `IdentitiesOnly` a working agent identity would mask a key that was
   never installed, and the setup would report success falsely.
4. `prepare_remote` reads the remote's real `$HOME` and creates the world folder
   beneath it, returning an absolute path for `ssh_path` — root, macOS, and
   custom layouts do not use `/home/<user>`.

**Password fallback.** Bootstrapping needs *some* credential, so when the probe
reports `auth` the caller supplies a one-time password. It reaches ssh through
OpenSSH's `SSH_ASKPASS` hook: the password is written to
`data/ssh/.askpass-secret` (`0600`, deleted in a `finally`), and a two-line
helper at `data/ssh/askpass.sh` (POSIX) or `askpass.cmd` (Windows) prints it.
The helper is a shell script rather than a Python program on purpose — in a
frozen build `sys.executable` is the app, not an interpreter, so a Python helper
would break in the packaged binary. The install call runs with
`BatchMode=no -o PubkeyAuthentication=no -o NumberOfPasswordPrompts=1`, so
password auth is confined to that one invocation.

`nomad ssh init` remains the print-only route to a dedicated `useradd`-based
user; it prints commands and runs none of them.

### Reverse tunnel

`launcher/tunnel.py` runs `ssh -N -R <remote>:localhost:<local> <target>` as a
child process. The agent starts it before acquiring the lease and publishes
`<host>:<remote>` in the lease, so the advertised address exists before anyone
reads it; the tunnel is stopped in the host `finally`. `ExitOnForwardFailure=yes`
turns a failed remote bind into an immediate startup error. Requires the remote
`AllowTcpForwarding` (default on) and `GatewayPorts` for non-loopback binds.

## Inclusion manifest

`nomad.json` in the server directory selects what to archive and, optionally,
how to launch the server. It is the inclusion counterpart to `.gitignore`.

- `include` — glob patterns relative to the manifest directory; a directory
  pattern includes its subtree; empty means everything.
- `exclude` — removed from the set; exclude wins over include.
- `server.command` — argv run with `cwd` = the synced directory. When present,
  the agent uses `ProcessRuntime` instead of the vanilla Minecraft runtime.
- `server.stop_command` — written to stdin on shutdown; else the process is
  terminated.

Matching uses `fnmatch.fnmatchcase`, where `*` crosses separators (so `*.log`
matches at any depth). The manifest file itself is always archived; launcher
runtime artifacts (`nomad.pid`, `nomad.stop`, `upload.tar.gz`) are never.

Without a manifest the agent falls back to the built-in Minecraft behavior:
`ensure_world_level` plus `VanillaMinecraftRuntime`, archiving `world/`.

## Agent sequence

`acquire` → `download_world` → extract → load manifest → select runtime →
install/validate (or validate command) → start server → renew loop → stop → tar
(manifest-selected or `world/`) → `upload_world` → `release`.

Failure paths:

- Acquire denied → return 1; UI offers Join.
- Server exits early → stop, release, no upload.
- Renew fails (lease lost) → stop server, release, no upload.
- Crash without stop → lease expires in 5 minutes; next host pulls the last uploaded blob.

## Cost model

Metered: storage (GB-month) and operations. Not metered: egress (R2). Lease renewals dominate Class A usage for always-on worlds; world pulls are Class B and free on R2. One blob per world means storage scales with world count, not save count.

## Failure and edge cases

- Two concurrent acquires: single conditional write wins.
- Host killed mid-session: lease expiry frees the world; in-session changes are lost.
- Corrupted/tampered blob: SHA-256 mismatch rejects the download.
- Store unreachable: no one can acquire; existing hosts cannot renew.

## Credentials and logging

- Credential-bearing settings are `SecretStr`; use sites call
  `secrets.secret_value(...)`. `load_settings_file` registers the configured
  values with `secrets.register_secret` so exact-match scrubbing catches secrets
  that match no pattern.
- `secrets.install_log_redaction()` replaces the global log-record factory, so
  every record is redacted at creation regardless of logger or handler (a
  logger-level filter would miss child loggers; root-handler filters would miss
  handlers added later). Masks URL userinfo, SigV4 `Authorization`, AWS key ids,
  bearer tokens, signed-query params, and registered values.
- `data/settings.json` is written `0600` in a `0700` directory; on POSIX a
  loose file is tightened on load or the load refuses with the `chmod` to run.
- `audit.record(...)` writes provenance (`action`, `backend`, `target`, key
  fingerprint, `world`, `outcome`) to `data/logs/audit.log` (`0600`). Never a
  value.
- Known gap: `git remote add origin <url>` places the URL on argv; error text is
  redacted but the process listing is not.

## Testing

`python -m pytest`. Covers lease CAS, full host cycle, integrity verification,
SigV4 vs botocore, registry, config layering, storage dispatch, the inclusion
manifest, the SSH backend and connection, the reverse tunnel, secret redaction,
SSH key provisioning and the guided setup (access probe classification, the
idempotent install command, `IdentitiesOnly` verification, the askpass helper,
and keeping the password out of argv), git error sanitising, and the audit trail.
No external services required.
