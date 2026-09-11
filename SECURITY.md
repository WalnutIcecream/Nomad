# Security

## Reporting

Open a private security advisory on the repository (Security → Advisories) or
email the maintainer. Do not open a public issue for a vulnerability.

## Credential handling

Nomad holds two kinds of credential:

- **Object-store keys** (R2 / VPS access key and secret).
- **An SSH identity**, either the Nomad-generated key or a key you point it at.

Rules the code enforces:

- **Secrets are `SecretStr`**, so `repr(settings)` and `str(secret)` render `***`
  and a use site must call `.get_secret_value()` explicitly.
- **Every log record is redacted at creation** (`launcher.secrets`). URL
  userinfo, SigV4 `Authorization` values, AWS-style key ids, bearer tokens,
  signed-URL query params, and any value passed to `register_secret` are
  masked. This applies regardless of which logger or handler emits the record.
- **The credentials file is owner-only.** `data/settings.json` is written
  `0600` inside a `0700` directory. On POSIX, loading a file that is readable by
  others tightens it automatically, and refuses to load if it cannot (`chmod 600`
  is printed). Windows is protected by the ACL on `%APPDATA%`; Unix mode bits do
  not apply there.
- **The Nomad SSH key** lives at `data/ssh/id_ed25519_nomad` (`0600`, dir
  `0700`). It has no passphrase because the launcher must use it
  non-interactively (`BatchMode=yes`); its safety is file permissions, so treat
  the data directory as sensitive. Override with `NOMAD_SSH_KEY`, which may point
  at an existing key.

## What is not protected

- **`git remote add origin <url>` puts the URL on the command line** for the
  moment it runs, so a personal access token embedded as URL userinfo is briefly
  visible in a process listing. Error output is redacted, argv is not. Prefer a
  credential helper over a token-in-URL.
- **A `.env` or configured `settings.json` placed inside a shared app bundle**
  exposes the credentials to everyone with the bundle. Keep the data directory
  private.
- **Redaction is defence in depth, not a guarantee.** It masks known shapes and
  registered values; a secret re-encoded in a novel way (base64, split across
  lines) will not match.

## Integrity

World blobs are SHA-256 verified on download against a companion hash
(`world.sha256` for object-store and SSH backends; `worlds/<id>/world.sha256`
committed for the git backend). This detects corruption and tampering in transit
or at rest. It is **not** authentication: a party with write access can replace
both the blob and its hash. For authenticity, sign manifests with a key the
storage provider cannot change.

## Audit trail

`data/logs/audit.log` (`0600`) records credential *use*, never values:
`action=acquire backend=ssh target=nomad@box key=SHA256:... world=<id> outcome=ok`.
It is the way to debug an access problem ("which identity authenticated?", "who
took the lease?") without printing a secret. Disable with `NOMAD_AUDIT_LOG=0`.

## SSH specifics

- Authentication is host-key-verified with `StrictHostKeyChecking=accept-new`:
  a new host key is trusted on first use and recorded; a *changed* key aborts.
  This is trust-on-first-use, not a pinned key. Pin the host in
  `~/.ssh/known_hosts` if you need stricter behaviour.
- `BatchMode=yes` everywhere, so a launcher can never hang on a prompt.
- The connection is multiplexed (`ControlMaster`/`ControlPath` in
  `data/ssh/`), so one handshake serves the session. The control socket is
  created under the data directory with owner-only permissions.
- **Guided setup mutates the remote; `init` does not.** `nomad ssh setup` (and
  the GUI's *Set up over SSH…*) deliberately writes to the machine: it appends the
  Nomad public key to your existing account's `~/.ssh/authorized_keys`, sets
  `0700`/`0600` on `~/.ssh`, and creates the world folder. It runs no `sudo` and
  creates no users. `nomad ssh init` stays print-only: it prints the
  `useradd`-based commands for the isolated dedicated-user layout and runs none
  of them.
- **The password path is one-time and narrow.** When existing access fails, the
  wizard asks for a password and uses it for a single ssh call. The password is
  written to `data/ssh/.askpass-secret` (`0600`, deleted immediately afterwards)
  and read by a two-line askpass helper that OpenSSH invokes via `SSH_ASKPASS`.
  It is never placed in argv, never in an environment variable, and never logged;
  the log-record factory in `launcher/secrets.py` scrubs credential-shaped text
  from every record. Password auth is offered only for that install call
  (`PubkeyAuthentication=no`, `NumberOfPasswordPrompts=1`) and is not used for
  lease or world traffic.
- **The guided setup grants Nomad's key the same shell access as the chosen
  account.** This is the trade-off of not needing `sudo`: the key can run
  `mkdir`/`cat`/`rm` as that user, not just inside the world folder. Use
  `nomad ssh init` with a dedicated user if you want the key scoped by
  filesystem ownership alone. The dedicated user needs a real shell because the
  storage backend runs `mkdir`/`cat`/`rm` remotely, so it is scoped by owning
  only the world directory, not by a forced command.

## Trust model

Access control is delegated entirely to the storage provider or the SSH host.
There is no coordinator to enforce permissions. Anyone with write access to a
world's prefix (or to the SSH base directory) can host, overwrite, or delete
that world.
