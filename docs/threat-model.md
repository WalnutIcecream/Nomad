# Nomad Threat Model

The launcher is untrusted software. All user IDs, world IDs, lease status,
version numbers, and permissions are validated server-side from the session
token.

## Threats and Mitigations

| Threat | Mitigation |
|---|---|
| Unauthorized world download/upload | Membership check on every world endpoint; no repo creds to launcher |
| Host impersonation / lease theft | `lease_id` is an unguessable secret; heartbeat/release/upload require it server-side |
| Replay of old requests | HTTPS + short-lived session tokens + signed upload URLs with expiry |
| Stale host overwrites newer version | `snapshot/prepare` checks `base_version == latest` + lease ownership |
| Two simultaneous hosts (split-brain) | `FOR UPDATE` row lock + partial unique index; controller authoritative |
| Host crash loses world | Two-phase upload; version ledger only advances on `complete` |
| Malicious world metadata | Version numbers/types/sizes validated; storage_key never trusted from client |
| Path traversal during extraction | Paths sanitized, resolved inside sandbox dir, `..` rejected |
| Arbitrary command execution via config | Config key whitelist; no eval; version validated against Mojang manifest |
| Token leakage | Argon2id password hashing; sha256 hashed session tokens; GitHub App token server-side only |
| Secrets in world repo | `storage_config` holds repo name only; credentials live in controller env/secrets |

## Security Boundaries

- Launcher → Controller: bearer session token only.
- Controller → WorldStorage: server-side credentials only (GitHub App token).
- Launcher → WorldStorage: never direct; always mediated by the controller.

## Crash Recovery Matrix

| Failure | Recovery |
|---|---|
| Minecraft crash while hosting | Launcher detects exit, stops heartbeats, stays in RECOVER |
| Launcher/PC crash | Heartbeats stop → lease expires → another member acquires |
| Internet disconnect | Same as launcher crash; no upload commits without `complete` |
| Upload failure mid-stream | Version ledger untouched; `latest` unchanged |
| Download failure | Local partial copy discarded; retry from `latest` |
| Controller/DB failure | PostgreSQL durable; leases/versions persist across restart |
| GitHub failure | Storage errors surface as `SYNCING`→`ERROR`; `latest` unchanged |
| Lease expiry mid-upload | `snapshot/complete` rejects; upload abandoned |

Fundamental rule:

> Never destroy the last known-good cloud version merely because a host failed.
