"""Nomad-owned SSH identity: generate, inspect, and provision.

Nomad keeps its own keypair in the data directory so the SSH backend is
self-contained and does not depend on the user's ``~/.ssh`` layout. The private
key is created ``0600`` in a ``0700`` directory; the public key is printed with
the exact commands an administrator runs on the box to create a dedicated user.

Nomad never mutates the remote: provisioning is print-only.
"""

from __future__ import annotations

import logging
import os
import shlex
import shutil
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)

KEY_FILENAME = "id_ed25519_nomad"
SSH_KEYGEN = "ssh-keygen"


def ssh_dir(settings) -> Path:
    return Path(getattr(settings, "data_dir", Path.home() / ".nomad")) / "ssh"


def nomad_key_path(settings) -> Path:
    """Path of the Nomad-managed private key (may not exist yet)."""
    return ssh_dir(settings) / KEY_FILENAME


def _pub_path(key: Path) -> Path:
    return Path(str(key) + ".pub")


def _chmod(path: Path, mode: int) -> None:
    if os.name != "posix":
        return
    try:
        os.chmod(path, mode)
    except OSError:
        logger.debug("could not chmod %s", path)


def ensure_keypair(path: Path, comment: str = "nomad") -> Path:
    """Create the keypair if absent. Returns the private key path.

    The key is ed25519 with no passphrase, because the launcher must use it
    non-interactively (``BatchMode=yes``); its safety comes from file
    permissions, not a passphrase.
    """
    path = Path(path)
    if path.exists():
        _chmod(path, 0o600)
        if _pub_path(path).exists():
            _chmod(_pub_path(path), 0o644)
        return path

    if shutil.which(SSH_KEYGEN) is None:
        raise RuntimeError(
            "ssh-keygen not found on PATH; install an OpenSSH client to "
            "generate a Nomad key, or set NOMAD_SSH_KEY to an existing key"
        )

    path.parent.mkdir(parents=True, exist_ok=True)
    _chmod(path.parent, 0o700)
    result = subprocess.run(
        [SSH_KEYGEN, "-t", "ed25519", "-N", "", "-C", comment, "-f", str(path)],
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    if result.returncode != 0:
        raise RuntimeError(f"ssh-keygen failed: {(result.stderr or result.stdout).strip()}")
    _chmod(path, 0o600)
    if _pub_path(path).exists():
        _chmod(_pub_path(path), 0o644)
    logger.info("generated Nomad ssh key at %s", path)
    return path


def resolve_key(settings) -> str:
    """Pick the ``-i`` identity: explicit setting, else the Nomad key, else none.

    An empty return means "let the system ssh client decide" (agent, config,
    default identities).
    """
    explicit = getattr(settings, "ssh_key", "") or ""
    if explicit:
        return explicit
    candidate = nomad_key_path(settings)
    return str(candidate) if candidate.exists() else ""


def public_key(path: Path) -> str:
    pub = _pub_path(Path(path))
    if not pub.exists():
        raise FileNotFoundError(f"public key not found: {pub}")
    return pub.read_text(encoding="utf-8").strip()


def fingerprint(path: Path) -> str:
    """SHA256 fingerprint of a key. Safe to log or display."""
    if shutil.which(SSH_KEYGEN) is None:
        return ""
    result = subprocess.run(
        [SSH_KEYGEN, "-lf", str(path)],
        capture_output=True,
        text=True,
        check=False,
        timeout=15,
    )
    if result.returncode != 0:
        return ""
    fields = result.stdout.split()
    return fields[1] if len(fields) > 1 else result.stdout.strip()


def resolve_remote_base(settings, user: str) -> str:
    """Absolute remote base directory for the world data."""
    return remote_base(getattr(settings, "ssh_path", "nomad-worlds"), user)


def provision_commands(user: str, pubkey: str, base: str) -> list[str]:
    """The commands an administrator runs on the box to enable Nomad.

    Print-only: Nomad never executes these. The user needs a real shell because
    the storage backend runs ``mkdir``/``cat``/``rm`` remotely, so the account is
    scoped by filesystem ownership (it owns only ``base``), not by a forced
    command.
    """
    home = f"/home/{user}"
    return [
        f"sudo useradd --create-home {user}",
        f"sudo mkdir -p {base} {home}/.ssh",
        f"sudo chown -R {user}:{user} {base} {home}/.ssh",
        f"sudo chmod 700 {home}/.ssh",
        f"echo '{pubkey}' | sudo tee -a {home}/.ssh/authorized_keys >/dev/null",
        f"sudo chmod 600 {home}/.ssh/authorized_keys",
        f"sudo chown {user}:{user} {home}/.ssh/authorized_keys",
    ]


def install_key_command(pubkey: str) -> str:
    """Remote shell script that installs ``pubkey`` for the current user.

    Idempotent: the key is appended only when absent, so re-running the guided
    setup does not duplicate it. The user's existing account is used, so there
    is no ``sudo`` and no account creation. The script is POSIX ``sh`` and is
    executed by the remote login shell.
    """
    key = shlex.quote(pubkey.strip())
    return (
        "umask 077; "
        'mkdir -p "$HOME/.ssh" && chmod 700 "$HOME/.ssh" && '
        'touch "$HOME/.ssh/authorized_keys" && chmod 600 "$HOME/.ssh/authorized_keys" && '
        f'grep -qxF {key} "$HOME/.ssh/authorized_keys" || echo {key} >> "$HOME/.ssh/authorized_keys"'
    )


def make_dir_command(base: str) -> str:
    """Remote shell script that creates the world directory."""
    return f"mkdir -p {shlex.quote(base)}"


def remote_base(folder: str, user: str) -> str:
    """Absolute remote path for ``folder`` when its home is unknown.

    Used only as a fallback; the guided setup prefers asking the remote for its
    real ``$HOME`` (root and macOS do not use ``/home/<user>``).
    """
    folder = (folder or "nomad-worlds").strip()
    if folder.startswith("/"):
        return folder.rstrip("/")
    return f"/home/{user}/{folder.strip('/')}"