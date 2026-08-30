"""Git-backed WorldStorage.

A version is a git commit on ``master`` tagged ``v<number>``; its tree holds
``world.tar.gz`` plus ``version.json`` (the sha256 manifest). The ``latest``
tag is a moving ref while ``vN`` tags are immutable. Binary world files route
through Git LFS when ``git-lfs`` is installed (the archive itself stays a plain
blob so sync/restore never depends on an LFS transfer). Branch pushes are
non-forced, so a divergent remote fails loudly rather than overwriting newer
cloud state.
"""

from __future__ import annotations

import logging
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from shared.protocol.models import VersionMetadata

from launcher.storage.archives import (
    compute_sha256,
    create_deterministic_archive,
    extract_archive_safely,
    parse_version_manifest,
    remove_file,
    write_version_manifest,
)

logger = logging.getLogger(__name__)

_TAG_RE = re.compile(r"v([0-9]+)")
_LFS_PATTERNS = ("*.mca", "*.dat", "*.region", "*.zip", "*.7z", "*.png", "*.jpg", "*.jpeg")
_COMMITTER = ("-c", "user.name=Nomad Launcher", "-c", "user.email=launcher@nomad.invalid")


def _tag_number(tag: str) -> int | None:
    match = _TAG_RE.fullmatch(tag)
    return int(match.group(1)) if match else None


class GitStorage:
    """A local clone mirror of the world's cloud repository.

    The local working tree always mirrors ``origin/master`` — the cloud is
    the source of truth and this clone is a scratch mirror, so a hard reset
    on fetch is safe.
    """

    def __init__(self, repo_dir: Path, remote_url: str | None = None) -> None:
        self.repository = repo_dir
        self.remote_url = remote_url

    # --- plumbing ------------------------------------------------------

    def _git(
        self,
        *args: str,
        check: bool = True,
        cwd: Path | None = None,
        timeout: float | None = None,
    ) -> str:
        workdir = str(cwd) if cwd is not None else str(self.repository)
        result = subprocess.run(
            ["git", *args],
            cwd=workdir,
            capture_output=True,
            text=True,
            check=check,
            timeout=timeout,
        )
        return result.stdout.strip()

    def _try_rev_parse(self, ref: str) -> str:
        try:
            return self._git("rev-parse", "--verify", "-q", ref)
        except subprocess.CalledProcessError:
            return ""

    def _commit(self, message: str) -> None:
        self._git(
            *_COMMITTER,
            "commit",
            "-m",
            message,
        )

    def _configure_identity(self) -> None:
        try:
            self._git("config", "user.name")
        except subprocess.CalledProcessError:
            self._git("config", "user.name", "Nomad Launcher")
        try:
            self._git("config", "user.email")
        except subprocess.CalledProcessError:
            self._git("config", "user.email", "launcher@nomad.invalid")

    # --- repo bootstrap ------------------------------------------------

    def _ensure_master(self) -> None:
        """Initialise the clone (once) and bring ``master`` up to date."""
        self.repository.mkdir(parents=True, exist_ok=True)
        if not (self.repository / ".git").exists():
            self._git("init", "--initial-branch=master")
        self._configure_identity()

        if self.remote_url:
            remotes = self._git("remote")
            if "origin" not in remotes.split():
                self._git("remote", "add", "origin", self.remote_url)
            self._git("fetch", "origin", "--tags", "--force")
            if self._try_rev_parse("refs/remotes/origin/master"):
                self._git("checkout", "-B", "master", "refs/remotes/origin/master")
            elif not self._try_rev_parse("HEAD"):
                self._initial_commit()
        elif not self._try_rev_parse("HEAD"):
            self._initial_commit()

        self._setup_lfs()

    def _initial_commit(self) -> None:
        self._git("symbolic-ref", "HEAD", "refs/heads/master")
        self._git("add", "-A")
        self._git(*_COMMITTER, "commit", "--allow-empty", "-m", "root")

    # --- LFS -----------------------------------------------------------

    def _lfs_available(self) -> bool:
        return shutil.which("git-lfs") is not None

    def _setup_lfs(self) -> None:
        """Arm Git LFS filters and track binary world files, when installed.

        ``*.tar.gz`` is deliberately not tracked: the archive is the unit of
        sync and must materialise identically everywhere, independent of an
        LFS object store.
        """
        if not self._lfs_available():
            return
        try:
            self._git("lfs", "install", "--local")
        except subprocess.CalledProcessError:
            return
        gitattributes = self.repository / ".gitattributes"
        lines = (
            []
            if not gitattributes.exists()
            else [line.rstrip() for line in gitattributes.read_text(encoding="utf-8").splitlines()]
        )
        for pattern in _LFS_PATTERNS:
            if any(line.startswith(pattern) for line in lines):
                continue
            lines.append(f"{pattern} filter=lfs diff=lfs merge=lfs -text")
        gitattributes.write_text("\n".join(lines) + "\n", encoding="utf-8")

    def _materialize(self, tag: str) -> Path:
        """Checkout ``tag`` into a temporary detached worktree."""
        work = Path(tempfile.mkdtemp(prefix="nomad-git-checkout-"))
        try:
            self._git("worktree", "add", "--detach", str(work), tag)
        except subprocess.CalledProcessError:
            shutil.rmtree(work, ignore_errors=True)
            raise
        if self._lfs_available():
            try:
                self._git("lfs", "pull", cwd=work, timeout=60)
            except subprocess.CalledProcessError:
                logger.debug("git lfs pull skipped in %s", work)
        return work

    def _remove_worktree(self, work: Path) -> None:
        try:
            self._git("worktree", "remove", "--force", str(work))
        except subprocess.CalledProcessError:
            pass
        if work.exists():
            shutil.rmtree(work, ignore_errors=True)

    def _tag_exists(self, tag: str) -> bool:
        return bool(self._try_rev_parse(f"refs/tags/{tag}"))

    def _push(self, version_tag: str) -> None:
        if not self.remote_url:
            return
        self._git("push", "origin", "master")
        self._git("push", "origin", f"refs/tags/{version_tag}")
        self._git("push", "origin", "+refs/tags/latest")

    # --- WorldStorage --------------------------------------------------

    def list_versions(self) -> list[VersionMetadata]:
        self._ensure_master()
        tags = [
            tag
            for tag in self._git("tag", "-l", "v*").splitlines()
            if _tag_number(tag) is not None
        ]
        tags.sort(key=lambda tag: int(_tag_number(tag)))

        versions: list[VersionMetadata] = []
        for tag in tags:
            try:
                raw = self._git("show", f"{tag}:version.json")
            except subprocess.CalledProcessError:
                logger.warning("tag %s has no version.json; skipping", tag)
                continue
            versions.append(parse_version_manifest(raw))
        return versions

    def get_latest_version(self) -> VersionMetadata | None:
        versions = self.list_versions()
        return max(versions, key=lambda meta: meta.version) if versions else None

    def push_snapshot(
        self, version: int, src_dir: Path, meta: VersionMetadata
    ) -> VersionMetadata:
        self._ensure_master()
        tag = f"v{version}"
        if self._tag_exists(tag):
            raise FileExistsError(f"snapshot already exists: {tag}")

        archive = create_deterministic_archive(src_dir)
        target = self.repository / "world.tar.gz"
        try:
            shutil.move(str(archive), str(target))
        finally:
            remove_file(archive)

        meta = meta.model_copy(
            update={"storage_key": tag, "sha256": compute_sha256(target)}
        )
        write_version_manifest(meta, self.repository / "version.json")

        self._git("add", "-A")
        self._commit(f"world version {tag}")
        commit = self._git("rev-parse", "HEAD")
        self._git("tag", tag, commit)
        self._git("tag", "-f", "latest", commit)
        self._push(tag)
        return meta

    def pull_snapshot(self, storage_key: str, dest_dir: Path) -> None:
        self._ensure_master()
        if not self._tag_exists(storage_key):
            raise FileNotFoundError(f"version not found: {storage_key}")

        manifest = parse_version_manifest(
            self._git("show", f"{storage_key}:version.json")
        )
        work = self._materialize(storage_key)
        try:
            actual = compute_sha256(work / "world.tar.gz")
            if manifest.sha256 and actual != manifest.sha256:
                raise OSError(
                    f"checksum mismatch for {storage_key}: "
                    f"expected {manifest.sha256}, got {actual}"
                )
            extract_archive_safely(work / "world.tar.gz", dest_dir)
        finally:
            self._remove_worktree(work)

    def restore_version(self, version: int, dest_dir: Path) -> None:
        tag = f"v{version}"
        if not self._tag_exists(tag):
            raise FileNotFoundError(f"version not found: {tag}")
        self.pull_snapshot(tag, dest_dir)