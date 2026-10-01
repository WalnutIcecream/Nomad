# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Guided storage wizard for the two supported backends: **Cloudflare R2** and
  **My own server** (an S3-compatible server you run yourself, such as Garage on
  a VPS or home box). It is a step-by-step flow with a **Test Connection** step
  that writes, reads back and deletes a small probe object, and only saves after
  the storage has actually been proven to work.
- Human-readable storage errors. Instead of `ECONNREFUSED 10.0.0.12:3900`, the
  app says what happened and what to check (permissions, bucket name, server
  reachability), with the raw error kept behind "View technical details".
- Credentials are stored in the operating system keychain when one is available
  (macOS Keychain, Windows Credential Manager, Secret Service), and fall back to
  the `0600` settings file otherwise. Opt in to `keyring` with the `secure`
  extra; disable entirely with `NOMAD_KEYCHAIN=0`.
- `nomad storage test` verifies the configured storage from the command line.
- Redesigned launcher UI on a real design-token theme (dark palette, status
  colours, spacing): world cards with clear states (Ready, Starting, Hosting,
  Someone is hosting, Syncing, Stopping, Offline, Storage unavailable, Error), a
  per-world menu (rename, delete, copy World ID), a Create World form, and a
  **Join a world** flow by World ID — which previously existed only in the CLI.

### Changed

- The product exposes exactly two storage choices: Cloudflare R2 and "My own
  server" (self-hosted S3/Garage). The `vps` backend was renamed `server` and now
  takes its own credentials (`NOMAD_SERVER_ENDPOINT`, `NOMAD_SERVER_BUCKET`,
  `NOMAD_SERVER_ACCESS_KEY`, `NOMAD_SERVER_SECRET_KEY`) rather than borrowing the
  R2 keys.
- The host agent narrates its phases (starting, pulling, booting, hosting,
  stopping, syncing, releasing) so the UI can show truthful states instead of a
  single "hosting" flag.
- Lease operations are now recorded in the audit trail for the S3 backends, not
  just the removed SSH backend.
- One entry point everywhere: a single `nomad` command runs every CLI operation,
  and `nomad gui` opens the desktop window. The separate `nomad-gui` script and
  the second packaging binary are gone, which removes the Windows
  `Nomad.exe`/`nomad.exe` name collision and the `nomad-cli` rename workaround,
  so the same command works on Windows, Linux and macOS.

### Fixed

- A failed world upload no longer releases the lease. Releasing would let the
  next host pull a world older than the copy still on disk; the lease is now
  preserved and allowed to expire, and the user is told their local copy is
  intact.

### Removed

- The git storage backend and the SSH storage backend, plus their supporting
  modules (`launcher/storage/git.py`, `launcher/storage/ssh.py`,
  `launcher/ssh_connection.py`, `launcher/ssh_identity.py`,
  `launcher/ssh_provision.py`, `launcher/tunnel.py`), the SSH setup dialog, and
  the `nomad ssh` commands. `NOMAD_GIT_*` and `NOMAD_SSH_*` are gone.


  directories to sync, the inclusion counterpart to `.gitignore`. Adds
  `include` / `exclude` glob patterns and an optional `server.command` block.
- Generic server runtime (`ProcessRuntime`) so any game server with a save
  directory can be hosted, not just Minecraft.
- `nomad manifest init|check` to author and preview a manifest.
- SSH storage backend (`nomad storage set ssh`): a home or bare-metal box used
  as plain storage. Lease CAS via the remote filesystem's atomic `mkdir`; no
  server software beyond sshd. Reuses the existing ssh keys/agent.
- Integrity verification for world pulls (SHA-256 companion object) across the
  object-store, git, and ssh backends.
- SSH connection multiplexing (`ControlMaster`/`ControlPersist`) so lease
  renewals reuse one connection instead of reconnecting every heartbeat.
- Optional reverse SSH tunnel (`nomad play --tunnel`) that publishes the local
  game port through the ssh box, so a host behind NAT needs no router config.
- Guided SSH setup (`nomad ssh setup <user@host>`, plus *Set up over SSH…* in the
  GUI). Generates the Nomad key, installs it into the user's existing account,
  creates the world folder, verifies the key authenticates on its own, and saves
  the settings — no `sudo`, no `useradd`, no commands to copy. It reuses existing
  ssh access when it works and falls back to a one-time password when it does
  not; on failure it shows the exact command with a Copy button.

### Security

- Credential fields are `SecretStr`, so they cannot be logged or repr'd by
  accident.
- Global log-record redaction: URL userinfo, SigV4 `Authorization`, AWS key ids,
  bearer tokens, signed-query params, and registered secret values are masked in
  every log record, regardless of source.
- `data/settings.json` is written `0600` in a `0700` directory; a loose file is
  tightened on load or the load refuses with the exact `chmod` to run.
- `nomad storage status` no longer prints a git remote URL with embedded
  credentials, and git error output is redacted before it reaches an exception.
- Nomad-owned ed25519 SSH identity (`nomad ssh init` / `nomad ssh show`) with a
  print-only provisioning flow for a dedicated remote user.
- The guided setup's password fallback never exposes the password: it is written
  to `data/ssh/.askpass-secret` (`0600`, deleted immediately after) and read by
  an `SSH_ASKPASS` helper, so it reaches neither argv nor the environment. The
  install call pins `PubkeyAuthentication=no` and `NumberOfPasswordPrompts=1`, so
  password auth is confined to that one invocation.
- The guided setup verifies with `-o IdentitiesOnly=yes`. Without it a working
  agent identity would mask a key that was never installed and the setup would
  report false success.
- Documentation corrected: `nomad ssh init` is print-only, but `nomad ssh setup`
  deliberately writes to the remote. The previous blanket "Nomad never mutates
  the remote" claim no longer held.
- Audit trail of credential *use* (`data/logs/audit.log`, `0600`): action,
  backend, target, key fingerprint, world, outcome — never a value.
- Fixed a latent bug: the ssh multiplex shutdown was sent as `ssh <host> -O
  exit`, which ssh parses as a remote command; it is now `ssh -O exit <host>`.

## [0.2.0]

### Added

- Pluggable storage backends behind a `WorldStoreProtocol`: Cloudflare R2
  (default), git, and self-hosted S3 (MinIO/Garage).
- `nomad storage set|status` and a multi-backend GUI settings dialog.
- SHA-256 integrity verification for world downloads (object-store and git
  backends). Tampered or corrupt blobs are rejected.
- `nomad usage` — approximate local counters for storage, uploads, downloads,
  and API requests.
- `--version` and a single version source (`launcher.__version__`).
- CI workflow testing Linux, Windows, and macOS on Python 3.11 and 3.13.
- Frozen app build with an embedded Temurin JRE 21 and an application icon.
- Documentation: README, llms.txt, docs/USP.md, docs/TECHNICAL.md,
  docs/MARKETING.md.

### Changed

- World storage is overwrite-only: one blob per world, so stored bytes scale
  with world count, not save count.
- Settings persist to `data/settings.json`; precedence is env > settings file
  > `.env` > defaults.

### Removed

- The FastAPI/PostgreSQL controller, accounts, the relay, and the embedded
  PostgreSQL hub. A shared object store is now the only infrastructure.

## [0.1.0]

### Added

- Initial launcher: lease, host agent, vanilla Minecraft runtime, PySide6 UI,
  and CLI.
