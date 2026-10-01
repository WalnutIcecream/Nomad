# Technical documentation

## Components

- `launcher/cloud.py` — S3/SigV4 client; `Lease`, `WorldStore`, `ConnectionResult`, `UsageCounter`.
- `launcher/storage/` — `WorldStoreProtocol` + the `r2` and `server` backends; `build_store` dispatch and the `PROVIDERS` registry.
- `launcher/agent.py` — host lifecycle.
- `launcher/manifest.py` — inclusion manifest (`nomad.json`); include/exclude resolution, archiving.
- `launcher/process_runtime.py` — generic runtime: run an arbitrary server command.
- `launcher/credentials.py` — OS-keychain secret storage with a `0600`-file fallback.
- `launcher/secrets.py` — credential redaction and global log-record scrubbing.
- `launcher/audit.py` — non-secret audit trail of lease and credential use.
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

## Storage backends

Two backends, one internal implementation. Both are the same `WorldStore` over
the same stdlib SigV4 `S3Client`; they differ only in how the endpoint and
credentials are resolved.

- **`r2`** — `NOMAD_R2_ACCOUNT_ID` / `_ACCESS_KEY` / `_SECRET_KEY` / `_BUCKET`,
  optional `NOMAD_R2_ENDPOINT_URL` (the endpoint is derived from the account id
  otherwise).
- **`server`** — `NOMAD_SERVER_ENDPOINT` / `_BUCKET` / `_ACCESS_KEY` /
  `_SECRET_KEY`. This is "My own server": a self-hosted S3-compatible service
  (Garage) on a VPS or home box. The endpoint is normalised with an `https://`
  scheme if one is missing.

`build_store(settings, usage)` dispatches on `settings.storage_backend` and
raises `ValueError` for anything else, so a stale config naming a removed
backend fails loudly instead of silently.

### Connection test

`WorldStore.test_connection()` proves the whole read/write path, not just
reachability: it PUTs a small probe object under `nomad-connection-test/`,
GETs it back, checks the bytes, and DELETEs it. Failures are classified by
`_classify_connection_error` into a message written for a person (permissions,
missing bucket, unreachable server) with the raw detail kept alongside for the
"View technical details" affordance. The wizard only saves after this passes.

## Credentials

- Credential-bearing settings are `SecretStr`; use sites call
  `secrets.secret_value(...)`.
- `launcher/credentials.py` stores each secret in the OS keychain when a real
  backend is present (macOS Keychain, Windows Credential Manager, Secret
  Service via `keyring`), detected by rejecting keyring's `fail`/`null`
  fallbacks. `save_settings_file` writes secrets to the keychain only if *every*
  write succeeds, and otherwise falls back to the file so a credential is never
  silently dropped. `load_settings_file` reads the keychain first, then the
  file. `NOMAD_KEYCHAIN=0` forces file storage.
- Non-secret settings always live in `data/settings.json` (`0600` in a `0700`
  dir; loose files are tightened or refused on load).

## Agent sequence

`acquire` → `download_world` → extract → load manifest → select runtime →
install/validate (or validate command) → start server → renew loop → stop → tar
(manifest-selected or `world/`) → `upload_world` → `release`.

The agent reports each step through an optional `progress(phase)` callback
(`starting`, `pulling`, `booting`, `hosting`, `stopping`, `syncing`,
`releasing`, `done`), which the launcher maps onto card states. It is a plain
callback so the agent stays UI-agnostic and testable.

Failure paths:

- Acquire denied → return 1; the UI offers Join.
- Server exits early → stop, release, no upload.
- Renew fails (lease lost) → stop server, release, no upload.
- **Upload fails → the lease is preserved, not released.** Releasing would let
  the next host pull a world older than the copy still on this disk. The lease
  expires on its own (5 minutes), and the failure is surfaced to the user with
  the reassurance that the local copy is intact.
- Crash without stop → the lease expires in 5 minutes; the next host pulls the
  last uploaded blob.

## Cost model

Metered: storage (GB-month) and operations. Not metered: egress (R2). Lease
renewals dominate Class A usage for always-on worlds; world pulls are Class B
and free on R2. One blob per world means storage scales with world count, not
save count. The self-hosted backend has no per-operation meter at all.

## Failure and edge cases

- Two concurrent acquires: a single conditional write wins.
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
  When a keychain is in use, the file holds no secrets at all.
- `audit.record(...)` writes provenance (`action`, `backend`, `world`,
  `outcome`) to `data/logs/audit.log` (`0600`). Never a value.

## Testing

`python -m pytest`. Covers lease CAS, the full host cycle, upload-failure lease
preservation, progress phases, integrity verification, the connection test and
its error classification, SigV4 vs botocore, registry, config layering, storage
dispatch, credential storage with and without a keychain, the inclusion
manifest, secret redaction, and the audit trail. No external services required.
