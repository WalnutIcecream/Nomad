"""SSH backend — a home or bare-metal box you own, used as plain storage.

The world lives on the remote filesystem. Hosts still lease, pull, boot
locally, and push back; only the transport changes from an object store to
``ssh``. No server software to install (unlike the vps backend, which needs
MinIO): sshd is already there.

Lease atomicity uses the remote filesystem's own atomic ``mkdir``. A claim is
``mkdir <base>/<id>/lease``: it succeeds for exactly one client. The lease
directory holds ``lease.json``; an expired lease is cleared and re-claimed
through the same ``mkdir``, so there is still a single winner.

Remote layout::

    <base>/<world-id>/lease/            presence == claimed (atomic mkdir)
    <base>/<world-id>/lease/lease.json  holder/expires_at/lease_id
    <base>/<world-id>/world.tar.gz      single world blob, overwrite-only
    <base>/<world-id>/world.sha256      SHA-256 of the blob, verified on pull

Auth uses your existing ssh setup (agent, config, keys): pass ``NOMAD_SSH_KEY``
to select an identity explicitly. Connections are non-interactive
(``BatchMode=yes``) so a launcher never hangs on a prompt.
"""

from __future__ import annotations

import datetime
import hashlib
import json
import logging
import posixpath
import shlex
import subprocess
import uuid
from pathlib import Path
from typing import Protocol

from launcher import audit
from launcher.cloud import CloudError, Lease, LeaseError, UsageCounter
from launcher.secrets import redact
from launcher.ssh_connection import SshConnection
from launcher.storage import WorldStoreProtocol

logger = logging.getLogger(__name__)

LEASE_SECONDS = 300
_SSH_TIMEOUT = 300
_ACQUIRE_ATTEMPTS = 8


class RemoteTransport(Protocol):
    """The remote filesystem operations the store needs (fakeable in tests)."""

    def mkdir(self, remote: str) -> bool:
        """Create ``remote`` atomically. True if created, False if it existed."""
        ...

    def exists(self, remote: str) -> bool: ...

    def read(self, remote: str) -> bytes | None: ...

    def write(self, remote: str, data: bytes) -> None: ...

    def remove_tree(self, remote: str) -> None: ...

    def push_file(self, local: Path, remote: str) -> None: ...

    def pull_file(self, remote: str, local: Path) -> bool: ...


class SshTransport:
    """Real transport: shells out to the system ``ssh`` binary.

    All argv comes from a shared :class:`~launcher.ssh_connection.SshConnection`,
    so this transport and the reverse tunnel reach the box over the *same*
    multiplexed connection (one handshake per host session).
    """

    def __init__(self, connection: "SshConnection") -> None:
        self.connection = connection

    @property
    def target(self) -> str:
        return self.connection.target

    def _argv(self, remote_cmd: str) -> list[str]:
        return self.connection.command(remote_cmd)

    def open(self) -> None:
        """Force the multiplexed master up so later calls are cheap.

        With ``ControlMaster=auto`` + ``ControlPersist=yes`` the first call
        creates the master and it stays up until :meth:`close`; running a trivial
        command here means the handshake happens once, at session start, instead
        of on the first lease renewal.
        """
        if not self.connection.multiplexed:
            return
        result = self._run("true")
        if result.returncode != 0:
            raise CloudError(
                result.returncode,
                (result.stderr or b"").decode("utf-8", "replace")[:300]
                or "could not establish ssh connection",
            )

    def identity_fingerprint(self) -> str:
        """Fingerprint of the identity in use (safe to log). Empty if unknown."""
        from launcher.ssh_identity import fingerprint

        key = self.connection.key
        if not key:
            return ""
        try:
            return fingerprint(Path(key))
        except Exception:
            return ""

    def close(self) -> None:
        """Tear down the multiplexed connection (a no-op when not multiplexing)."""
        control_path = self.connection.control_path
        if control_path is None:
            return
        subprocess.run(
            self.connection.control_command("exit"),
            capture_output=True,
            check=False,
            timeout=15,
        )
        try:
            control_path.unlink(missing_ok=True)
        except OSError:
            pass

    def _run(self, remote_cmd: str, stdin: bytes | None = None) -> subprocess.CompletedProcess:
        return subprocess.run(
            self._argv(remote_cmd),
            input=stdin,
            capture_output=True,
            check=False,
            timeout=_SSH_TIMEOUT,
        )

    # --- RemoteTransport ------------------------------------------------

    def mkdir(self, remote: str) -> bool:
        # mkdir -p on the parent is idempotent; the bare mkdir on the leaf is
        # the atomic claim and fails if it already exists.
        parent = posixpath.dirname(remote)
        cmd = f"mkdir -p {shlex.quote(parent)} && mkdir {shlex.quote(remote)}"
        result = self._run(cmd)
        return result.returncode == 0

    def exists(self, remote: str) -> bool:
        return self._run(f"test -e {shlex.quote(remote)}").returncode == 0

    def read(self, remote: str) -> bytes | None:
        result = self._run(f"cat {shlex.quote(remote)}")
        if result.returncode != 0:
            return None
        return result.stdout

    def write(self, remote: str, data: bytes) -> None:
        parent = posixpath.dirname(remote)
        result = self._run(
            f"mkdir -p {shlex.quote(parent)} && cat > {shlex.quote(remote)}", stdin=data
        )
        if result.returncode != 0:
            raise CloudError(
                result.returncode, redact(result.stderr.decode("utf-8", "replace")[:300])
            )

    def remove_tree(self, remote: str) -> None:
        self._run(f"rm -rf {shlex.quote(remote)}")

    def push_file(self, local: Path, remote: str) -> None:
        parent = posixpath.dirname(remote)
        with open(local, "rb") as handle:
            result = subprocess.run(
                self._argv(
                    f"mkdir -p {shlex.quote(parent)} && cat > {shlex.quote(remote)}"
                ),
                stdin=handle,
                capture_output=True,
                check=False,
                timeout=_SSH_TIMEOUT,
            )
        if result.returncode != 0:
            raise CloudError(result.returncode, "ssh upload failed")

    def pull_file(self, remote: str, local: Path) -> bool:
        local.parent.mkdir(parents=True, exist_ok=True)
        with open(local, "wb") as handle:
            result = subprocess.run(
                self._argv(f"cat {shlex.quote(remote)}"),
                stdout=handle,
                stderr=subprocess.PIPE,
                check=False,
                timeout=_SSH_TIMEOUT,
            )
        if result.returncode != 0:
            local.unlink(missing_ok=True)
            return False
        return True


class SshWorldStore(WorldStoreProtocol):
    """SSH transport to a home/bare-metal box holding the world."""

    name = "ssh"

    def __init__(self, transport: RemoteTransport, base: str, player_name: str, usage: UsageCounter | None = None) -> None:
        self.transport = transport
        self.base = base.rstrip("/")
        self.player_name = player_name
        self.usage = usage

    @classmethod
    def build(cls, settings, usage: UsageCounter | None = None) -> "SshWorldStore":
        from launcher.ssh_identity import resolve_remote_base

        if not settings.ssh_target:
            raise ValueError("SSH backend requires NOMAD_SSH_TARGET (user@host)")
        connection = SshConnection.build(settings)
        user = connection.user or "nomad"
        base = resolve_remote_base(settings, user)
        return cls(SshTransport(connection), base, settings.player_name, usage=usage)

    def _track(self, **delta: int) -> None:
        if self.usage is not None:
            self.usage.record(**delta)

    def _audit(self, action: str, **fields) -> None:
        """Record credential *use* (never values) for the audit trail."""
        target = getattr(self.transport, "target", "")
        fingerprint = ""
        getter = getattr(self.transport, "identity_fingerprint", None)
        if callable(getter):
            fingerprint = getter()
        audit.record(
            action,
            backend="ssh",
            target=target,
            key=fingerprint or None,
            **fields,
        )

    def open(self) -> None:
        """Bring the shared ssh connection up for the whole session."""
        opener = getattr(self.transport, "open", None)
        if callable(opener):
            opener()

    def close(self) -> None:
        """Release the multiplexed ssh connection, if the transport has one."""
        close = getattr(self.transport, "close", None)
        if callable(close):
            close()

    # --- remote paths ----------------------------------------------------

    def _world_dir(self, world_id: str) -> str:
        return posixpath.join(self.base, world_id)

    def _lease_dir(self, world_id: str) -> str:
        return posixpath.join(self._world_dir(world_id), "lease")

    def _lease_file(self, world_id: str) -> str:
        return posixpath.join(self._lease_dir(world_id), "lease.json")

    def _blob(self, world_id: str) -> str:
        return posixpath.join(self._world_dir(world_id), "world.tar.gz")

    def _hash(self, world_id: str) -> str:
        return posixpath.join(self._world_dir(world_id), "world.sha256")

    # --- lease -----------------------------------------------------------

    def _read_lease(self, world_id: str) -> Lease | None:
        raw = self.transport.read(self._lease_file(world_id))
        if raw is None:
            return None
        try:
            data = json.loads(raw.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return None
        return Lease(
            status=data.get("status", "active"),
            holder=data.get("holder", "?"),
            address=data.get("address"),
            acquired_at=data.get("acquired_at"),
            expires_at=data.get("expires_at"),
            # The lease id travels inside the file so renew/release can prove
            # ownership across processes.
            etag=data.get("lease_id"),
        )

    def _write_lease(self, world_id: str, lease: Lease) -> None:
        payload = lease.to_dict()
        payload["lease_id"] = lease.etag
        self.transport.write(self._lease_file(world_id), json.dumps(payload).encode("utf-8"))

    def acquire(self, world_id: str, address: str | None = None) -> Lease:
        self._track(api_request_count=1)
        now = datetime.datetime.now(datetime.timezone.utc)
        lease_id = uuid.uuid4().hex
        lease = Lease(
            status="active",
            holder=self.player_name,
            address=address,
            acquired_at=now.isoformat(),
            expires_at=(now + datetime.timedelta(seconds=LEASE_SECONDS)).isoformat(),
            etag=lease_id,
        )

        for _ in range(_ACQUIRE_ATTEMPTS):
            if self.transport.mkdir(self._lease_dir(world_id)):
                # We won the atomic mkdir claim.
                self._write_lease(world_id, lease)
                self._track(world_count=1)
                self._audit("acquire", world=world_id, outcome="ok")
                return lease

            # The lease dir exists: read it and decide.
            current = self._read_lease(world_id)
            if current is None:
                continue  # dir created but json not written yet; retry
            if current.is_active:
                self._audit("acquire", world=world_id, outcome="denied", held_by=current.holder)
                raise LeaseError(
                    409, f"world is hosted by {current.holder} until {current.expires_at}"
                )
            # Expired or released: clear it and retry. mkdir remains the
            # arbiter, so at most one of several racing clients ends up owning it.
            self.transport.remove_tree(self._lease_dir(world_id))

        self._audit("acquire", world=world_id, outcome="contention")
        raise LeaseError(409, "could not acquire lease after repeated contention")

    def renew(self, world_id: str, lease: Lease) -> Lease:
        self._track(api_request_count=1)
        current = self._read_lease(world_id)
        if current is None or current.etag != lease.etag:
            raise LeaseError(409, "lease lost; another host holds the world")
        now = datetime.datetime.now(datetime.timezone.utc)
        updated = Lease(
            status="active",
            holder=lease.holder,
            address=lease.address,
            acquired_at=lease.acquired_at,
            expires_at=(now + datetime.timedelta(seconds=LEASE_SECONDS)).isoformat(),
            etag=lease.etag,
        )
        self._write_lease(world_id, updated)
        return updated

    def release(self, world_id: str, lease: Lease) -> None:
        self._track(api_request_count=1)
        current = self._read_lease(world_id)
        if current is None or current.etag != lease.etag:
            self._audit("release", world=world_id, outcome="stale")
            return  # already gone; nothing to do
        self.transport.remove_tree(self._lease_dir(world_id))
        self._audit("release", world=world_id, outcome="ok")

    def status(self, world_id: str) -> dict:
        self._track(api_request_count=1)
        lease = self._read_lease(world_id)
        if lease is None:
            return {"hosted": False, "holder": None, "address": None}
        if not lease.is_active:
            return {
                "hosted": False,
                "holder": lease.holder,
                "address": lease.address,
                "expired": True,
            }
        return {
            "hosted": True,
            "holder": lease.holder,
            "address": lease.address,
            "expires_at": lease.expires_at,
        }

    # --- world blob ------------------------------------------------------

    def download_world(self, world_id: str, dest: Path) -> bool:
        if not self.transport.pull_file(self._blob(world_id), dest):
            return False
        raw_hash = self.transport.read(self._hash(world_id))
        if raw_hash is not None:
            expected = raw_hash.decode("utf-8").strip()
            actual = hashlib.sha256(dest.read_bytes()).hexdigest()
            if expected and actual != expected:
                dest.unlink(missing_ok=True)
                self._audit("download", world=world_id, outcome="integrity-failed")
                raise CloudError(
                    422,
                    f"world integrity check failed for {world_id}: "
                    f"expected {expected[:12]}, got {actual[:12]}",
                )
        self._track(download_count=1)
        self._audit("download", world=world_id, outcome="ok")
        return True

    def upload_world(self, world_id: str, archive: Path) -> None:
        size = archive.stat().st_size
        digest = hashlib.sha256(archive.read_bytes()).hexdigest()
        self.transport.push_file(archive, self._blob(world_id))
        self.transport.write(self._hash(world_id), digest.encode("utf-8"))
        self._track(upload_count=1, uploaded_bytes=size)
        self._audit("upload", world=world_id, outcome="ok", bytes=size)