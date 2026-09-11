"""Git backend — a completely-free, storage-unlimited direction.

The whole world (lease + blob) lives in a normal git repo. Because git only
accepts fast-forward pushes to a branch, a concurrent acquirer whose history is
stale gets a rejected push — that rejection is the compare-and-swap, so "first
to push wins" holds without any server or API budget.

Layout per world::

    worlds/<id>/lease.json      # {"status","holder","address","expires_at"}
    worlds/<id>/world.tar.gz    # single per-world blob, last push wins

Costs: zero per-operation, zero per-GB, unlimited storage in your own repo.
Caveats: ``world.tar.gz`` is capped at git's 100 MB per-file limit (long worlds
should use R2/VPS), and friends need push access (SSH key / PAT).
"""

from __future__ import annotations

import datetime
import hashlib
import json
import shutil
import subprocess
from pathlib import Path

from launcher.cloud import Lease, LeaseError, UsageCounter
from launcher.secrets import redact
from launcher.storage import WorldStoreProtocol

LEASE_SECONDS = 300


class GitWorldStore(WorldStoreProtocol):
    name = "git"

    def __init__(self, repo_dir: Path, remote_url: str | None = None, name: str = "git") -> None:
        self.repo_dir = Path(repo_dir)
        self.remote_url = remote_url
        self._name = name

    @classmethod
    def build(cls, settings, usage: UsageCounter | None = None) -> "GitWorldStore":
        return cls(
            repo_dir=settings.git_repo_dir,
            remote_url=settings.git_remote_url or None,
            name=settings.player_name or "git",
        )

    # --- git plumbing ---------------------------------------------------

    def _git(self, *args: str, check: bool = True, cwd: Path | None = None) -> str:
        workdir = str(cwd) if cwd else str(self.repo_dir)
        result = subprocess.run(
            ["git", *args],
            cwd=workdir,
            capture_output=True,
            text=True,
            check=False,
            timeout=120,
        )
        if check and result.returncode != 0:
            raise LeaseError(result.returncode, redact((result.stderr or result.stdout).strip()))
        return result.stdout.strip()

    def _ensure_repo(self) -> None:
        self.repo_dir.mkdir(parents=True, exist_ok=True)
        if not (self.repo_dir / ".git").exists():
            self._git("init", "--initial-branch=master")
            self._git("commit", "--allow-empty", "-m", "root", check=False)
        if self.remote_url:
            remotes = self._git("remote").split()
            if "origin" not in remotes:
                self._git("remote", "add", "origin", self.remote_url)

    def _sync_from_remote(self) -> None:
        """Bring the local mirror up to the remote tip, if a remote exists."""
        if not self.remote_url:
            return
        self._git("fetch", "origin", "--force", check=False)
        if self._git("rev-parse", "--verify", "-q", "refs/remotes/origin/master", check=False):
            self._git("reset", "--hard", "refs/remotes/origin/master")

    def _push(self) -> None:
        if not self.remote_url:
            return
        result = subprocess.run(
            ["git", "push", "origin", "master"],
            cwd=self.repo_dir,
            capture_output=True,
            text=True,
            check=False,
            timeout=120,
        )
        if result.returncode != 0:
            raise LeaseError(result.returncode, redact((result.stderr or result.stdout).strip()))

    # --- world paths ----------------------------------------------------

    def _world_dir(self, world_id: str) -> Path:
        return self.repo_dir / "worlds" / world_id

    def _lease_path(self, world_id: str) -> Path:
        return self._world_dir(world_id) / "lease.json"

    def _blob_path(self, world_id: str) -> Path:
        return self._world_dir(world_id) / "world.tar.gz"

    def _read_lease(self, world_id: str) -> Lease | None:
        lease_path = self._lease_path(world_id)
        if not lease_path.exists():
            return None
        try:
            data = json.loads(lease_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None
        return Lease(
            status=data.get("status", "active"),
            holder=data.get("holder", "?"),
            address=data.get("address"),
            acquired_at=data.get("acquired_at"),
            expires_at=data.get("expires_at"),
        )

    def _commit_lease(self, world_id: str, lease: Lease, message: str) -> None:
        world_dir = self._world_dir(world_id)
        world_dir.mkdir(parents=True, exist_ok=True)
        (world_dir / "lease.json").write_text(
            json.dumps(lease.to_dict(), indent=2), encoding="utf-8"
        )
        self._git("add", "-A")
        self._git("commit", "-m", message)

    # --- WorldStoreProtocol ---------------------------------------------

    def acquire(self, world_id: str, address: str | None = None) -> Lease:
        self._ensure_repo()
        self._sync_from_remote()
        now = datetime.datetime.now(datetime.timezone.utc)
        lease = Lease(
            status="active",
            holder=self._name,
            address=address,
            acquired_at=now.isoformat(),
            expires_at=(now + datetime.timedelta(seconds=LEASE_SECONDS)).isoformat(),
        )
        existing = self._read_lease(world_id)
        if existing is not None and existing.is_active:
            raise LeaseError(409, f"world is hosted by {existing.holder} until {existing.expires_at}")

        self._commit_lease(world_id, lease, f"acquire {world_id}")
        try:
            self._push()
        except LeaseError:
            # Lost the race — someone pushed a lease since our fetch.
            self._sync_from_remote()
            current = self._read_lease(world_id)
            if current is not None and current.is_active:
                raise LeaseError(409, f"world is hosted by {current.holder} until {current.expires_at}")
            raise LeaseError(409, "lease acquisition failed after a race")
        return lease

    def renew(self, world_id: str, lease: Lease) -> Lease:
        now = datetime.datetime.now(datetime.timezone.utc)
        updated = Lease(
            status="active",
            holder=lease.holder,
            address=lease.address,
            acquired_at=lease.acquired_at,
            expires_at=(now + datetime.timedelta(seconds=LEASE_SECONDS)).isoformat(),
        )
        self._commit_lease(world_id, updated, f"renew {world_id}")
        try:
            self._push()
        except LeaseError:
            raise LeaseError(409, "lease lost; another host holds the world")
        return updated

    def release(self, world_id: str, lease: Lease) -> None:
        released = Lease(
            status="released",
            holder=lease.holder,
            address=lease.address,
            acquired_at=lease.acquired_at,
            expires_at=lease.expires_at,
        )
        self._commit_lease(world_id, released, f"release {world_id}")
        try:
            self._push()
        except LeaseError:
            raise LeaseError(409, "release failed; lease no longer held")

    def status(self, world_id: str) -> dict:
        self._ensure_repo()
        self._sync_from_remote()
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

    def download_world(self, world_id: str, dest: Path) -> bool:
        self._ensure_repo()
        self._sync_from_remote()
        blob = self._blob_path(world_id)
        if not blob.exists():
            return False
        raw = blob.read_bytes()
        hash_path = self._world_dir(world_id) / "world.sha256"
        if hash_path.exists():
            expected = hash_path.read_text(encoding="utf-8").strip()
            actual = hashlib.sha256(raw).hexdigest()
            if expected and actual != expected:
                raise LeaseError(
                    422,
                    f"world integrity check failed for {world_id}: "
                    f"expected {expected[:12]}, got {actual[:12]}",
                )
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(raw)
        return True

    def upload_world(self, world_id: str, archive: Path) -> None:
        self._ensure_repo()
        self._sync_from_remote()
        world_dir = self._world_dir(world_id)
        world_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(archive, self._blob_path(world_id))
        digest = hashlib.sha256(archive.read_bytes()).hexdigest()
        (world_dir / "world.sha256").write_text(digest, encoding="utf-8")
        self._git("add", "-A")
        self._git("commit", "-m", f"world {world_id}")
        try:
            self._push()
        except LeaseError:
            raise LeaseError(409, "world upload lost a race against another host")