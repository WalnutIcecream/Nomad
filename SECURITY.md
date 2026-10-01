# Security

## Reporting

Open a private security advisory on the repository (Security → Advisories) or
email the maintainer. Do not open a public issue for a vulnerability.

## Credential handling

Nomad holds one kind of credential: the **object-store keys** for whichever
backend is configured (Cloudflare R2, or your own S3-compatible server).

Rules the code enforces:

- **Secrets are `SecretStr`**, so `repr(settings)` and `str(secret)` render `***`
  and a use site must call `.get_secret_value()` explicitly.
- **Secrets prefer the operating system keychain.** `launcher/credentials.py`
  stores each key in macOS Keychain, Windows Credential Manager, or the Linux
  Secret Service when a real backend is available. Keyring's `fail` and `null`
  fallbacks are rejected, so a headless machine degrades to the file instead of
  silently losing a credential. Set `NOMAD_KEYCHAIN=0` to force file storage.
- **The fallback file is owner-only.** When no keychain is available,
  `data/settings.json` is written `0600` inside a `0700` directory. On POSIX,
  loading a file that is readable by others tightens it automatically, and
  refuses to load if it cannot (`chmod 600` is printed). Windows is protected by
  the ACL on `%APPDATA%`; Unix mode bits do not apply there.
- **Every log record is redacted at creation** (`launcher.secrets`). URL
  userinfo, SigV4 `Authorization` values, AWS-style key ids, bearer tokens,
  signed-URL query params, and any value passed to `register_secret` are
  masked. This applies regardless of which logger or handler emits the record.
- **Secrets are never shown back after entry in normal use.** The settings form
  masks secret fields, and the connection test reports a message about what is
  wrong rather than echoing the credential.

## What is not protected

- **A `.env` or configured `settings.json` placed inside a shared app bundle**
  exposes the credentials to everyone with the bundle. Keep the data directory
  private.
- **Redaction is defence in depth, not a guarantee.** It masks known shapes and
  registered values; a secret re-encoded in a novel way (base64, split across
  lines) will not match.
- **The self-hosted server is your responsibility.** Nomad only speaks to its S3
  API; hardening Garage, its users, its bucket policy, and its network exposure
  is the administrator's job.

## Integrity

World blobs are SHA-256 verified on download against a companion hash
(`worlds/<id>/world.sha256`). This detects corruption and tampering in transit
or at rest. It is **not** authentication: a party with write access can replace
both the blob and its hash. For authenticity, sign manifests with a key the
storage provider cannot change.

## Audit trail

`data/logs/audit.log` (`0600`) records lease *use*, never values:
`action=acquire backend=r2 world=<id> outcome=ok`. It is the way to debug an
access problem ("who took the lease?") without printing a secret. Disable with
`NOMAD_AUDIT_LOG=0`.

## Trust model

Access control is delegated entirely to the storage provider. There is no
coordinator to enforce permissions, and no account system. Anyone with write
access to a world's prefix can host, overwrite, or delete that world. Sharing a
World ID is the invite; the real permission boundary is the storage credential
you hand out. For finer control, issue per-prefix tokens at the provider so one
credential can only touch one world.
