"""Guided SSH provisioning: get Nomad's key onto a machine with minimal fuss.

The user supplies an address and a username; this module does the rest —
install the public key, create the world folder, and prove the key works on its
own. It is deliberately separate from the storage transport: provisioning runs
over the user's *existing* access (or a one-time password), never over the
Nomad key it is installing.

Bootstrapping needs a credential. Two paths, in order:

1. **Existing access** — reuse the user's ssh agent / ``~/.ssh`` identity. This
   is the common case for a machine someone already administers and needs no
   extra input.
2. **Password** — a one-time password, supplied through OpenSSH's ``SSH_ASKPASS``
   hook. The password is written to a ``0600`` file that the askpass helper
   reads; it never appears in argv, in an environment variable, or in a log.

The askpass helper is a two-line ``sh`` script (or a ``.cmd`` on Windows) rather
than a Python program on purpose: in a frozen build ``sys.executable`` is the
app, not an interpreter, so a helper that shelled out to Python would break in
the packaged app. Reading a file avoids that and sidesteps every quoting pitfall.
"""

from __future__ import annotations

import logging
import os
import shlex
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from launcher.secrets import redact

logger = logging.getLogger(__name__)

PROBE_TIMEOUT = 10
_SECRET_NAME = ".askpass-secret"
_HELPER_NAME = "askpass.cmd" if os.name == "nt" else "askpass.sh"


class ProvisionError(RuntimeError):
    """A provisioning step failed. ``detail`` is already redacted."""

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


class NeedPassword(ProvisionError):
    """The probe showed password auth is required and none was supplied."""

    def __init__(self, detail: str, probe: "ProbeResult") -> None:
        super().__init__(detail)
        self.probe = probe


@dataclass(frozen=True)
class ProbeResult:
    """Outcome of testing whether passwordless access already works."""

    ok: bool
    kind: str  # "ok" | "unreachable" | "auth" | "error"
    message: str

    @property
    def needs_password(self) -> bool:
        return self.kind == "auth"


# --- argument construction -----------------------------------------------

def _common_args(port: int | None) -> list[str]:
    args = ["ssh", "-T", "-o", "StrictHostKeyChecking=accept-new"]
    if port:
        args += ["-p", str(port)]
    return args


def probe_args(target: str, port: int | None = None, timeout: int = PROBE_TIMEOUT) -> list[str]:
    """Argv that tests passwordless access with the user's own identity."""
    args = ["ssh", "-T", "-o", "BatchMode=yes",
            "-o", "StrictHostKeyChecking=accept-new",
            "-o", f"ConnectTimeout={timeout}"]
    if port:
        args += ["-p", str(port)]
    return args + [target, "true"]


def verify_args(target: str, key_path: Path, port: int | None = None) -> list[str]:
    """Argv that tests the Nomad key *alone* — no agent identity may help."""
    args = ["ssh", "-T", "-o", "BatchMode=yes",
            "-o", "StrictHostKeyChecking=accept-new",
            "-o", "IdentitiesOnly=yes",
            "-i", str(key_path)]
    if port:
        args += ["-p", str(port)]
    return args + [target, "true"]


def install_args(
    target: str,
    command: str,
    port: int | None = None,
    password: bool = False,
) -> list[str]:
    """Argv that runs the install command over existing access or a password."""
    args = _common_args(port)
    if password:
        args += ["-o", "BatchMode=no",
                 "-o", "NumberOfPasswordPrompts=1",
                 "-o", "PubkeyAuthentication=no",
                 "-o", "PreferredAuthentications=password,keyboard-interactive"]
    else:
        args += ["-o", "BatchMode=yes"]
    return args + [target, command]


# --- askpass helper ------------------------------------------------------

def helper_path(data_dir: Path) -> Path:
    return Path(data_dir) / "ssh" / _HELPER_NAME


def secret_path(data_dir: Path) -> Path:
    return Path(data_dir) / "ssh" / _SECRET_NAME


def ensure_askpass_helper(data_dir: Path) -> Path:
    """Write the helper that prints the password from its secret file.

    POSIX gets a ``sh`` script, Windows a ``.cmd``; both merely emit the file's
    contents, so the password is never interpolated into a command line.
    """
    directory = Path(data_dir) / "ssh"
    directory.mkdir(parents=True, exist_ok=True)
    _chmod(directory, 0o700)

    secret = secret_path(data_dir)
    helper = helper_path(data_dir)
    if os.name == "nt":
        body = f'@type "{secret}"\r\n'
    else:
        body = f'#!/bin/sh\nexec cat "{secret}"\n'
    helper.write_text(body, encoding="utf-8", newline="")
    _chmod(helper, 0o700)
    return helper


def _chmod(path: Path, mode: int) -> None:
    if os.name != "posix":
        return
    try:
        os.chmod(path, mode)
    except OSError:
        logger.debug("could not chmod %s", path)


def _password_env(helper: Path) -> dict[str, str]:
    """Environment for a password-auth ssh call (password not in argv)."""
    env = dict(os.environ)
    env["SSH_ASKPASS"] = str(helper)
    env["SSH_ASKPASS_REQUIRE"] = "force"
    env.setdefault("DISPLAY", "nomad:0")  # older ssh consults DISPLAY
    return env


# --- steps ---------------------------------------------------------------

def probe(target: str, port: int | None = None, timeout: int = PROBE_TIMEOUT) -> ProbeResult:
    """Test whether passwordless access to ``target`` already works."""
    args = probe_args(target, port, timeout)
    try:
        result = subprocess.run(
            args, capture_output=True, text=True, check=False, timeout=timeout + 10
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return ProbeResult(False, "error", f"could not run ssh: {exc}")

    if result.returncode == 0:
        return ProbeResult(True, "ok", "existing access works")

    detail = redact((result.stderr or result.stdout or "").strip())
    return ProbeResult(False, _classify(detail), detail[:400] or "ssh failed")


def _classify(stderr: str) -> str:
    text = stderr.lower()
    unreachable = (
        "connection refused", "connection timed out", "no route to host",
        "could not resolve hostname", "network is unreachable",
        "connection closed by", "operation timed out", "host key verification failed",
    )
    auth = (
        "permission denied", "authentication failed",
        "no supported authentication methods", "too many authentication failures",
    )
    if any(marker in text for marker in auth):
        return "auth"
    if any(marker in text for marker in unreachable):
        return "unreachable"
    return "error"


def install_key(
    target: str,
    pubkey: str,
    port: int | None = None,
    password: str | None = None,
    data_dir: Path | None = None,
) -> None:
    """Install ``pubkey`` on ``target`` for the current user.

    Runs one idempotent remote command. With ``password`` the call authenticates
    with a one-time password via the askpass helper; otherwise the user's
    existing identity is reused. Raises :class:`ProvisionError` on failure.
    """
    from launcher.ssh_identity import install_key_command

    command = install_key_command(pubkey)
    env = None
    cleanup: list[Path] = []

    try:
        if password is not None:
            if data_dir is None:
                raise ProvisionError("password auth needs a data directory for the askpass helper")
            helper = ensure_askpass_helper(data_dir)
            secret_file = secret_path(data_dir)
            secret_file.write_text(password, encoding="utf-8")
            _chmod(secret_file, 0o600)
            env = _password_env(helper)
            cleanup.append(secret_file)

        args = install_args(target, command, port, password=password is not None)
        result = subprocess.run(
            args, capture_output=True, text=True, check=False, timeout=60, env=env
        )
        if result.returncode != 0:
            raise ProvisionError(
                redact((result.stderr or result.stdout or "").strip())[:400]
                or "ssh command failed"
            )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ProvisionError(redact(str(exc))) from exc
    finally:
        for path in cleanup:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass


def prepare_remote(
    target: str,
    key_path: Path,
    folder: str,
    port: int | None = None,
) -> str:
    """Create the world folder over the installed key; return its absolute path.

    A relative ``folder`` is resolved against the remote's real ``$HOME`` rather
    than assuming ``/home/<user>`` — root, macOS, and custom home layouts all
    differ, and the storage transport needs an absolute path.
    """
    from launcher.ssh_identity import make_dir_command

    folder = (folder or "nomad-worlds").strip()
    if folder.startswith("/"):
        base = folder.rstrip("/") or "/"
        command = make_dir_command(base)
    else:
        rel = folder.strip("/")
        # One call: report $HOME, create the directory beneath it.
        command = f'printf "%s\\n" "$HOME" && mkdir -p "$HOME"/{shlex.quote(rel)}'

    args = ["ssh", "-T", "-o", "BatchMode=yes", "-o", "IdentitiesOnly=yes",
            "-o", "StrictHostKeyChecking=accept-new", "-i", str(key_path)]
    if port:
        args += ["-p", str(port)]
    args += [target, command]

    try:
        result = subprocess.run(args, capture_output=True, text=True, check=False, timeout=60)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ProvisionError(redact(str(exc))) from exc
    if result.returncode != 0:
        raise ProvisionError(
            redact((result.stderr or result.stdout or "").strip())[:400]
            or "could not create the remote folder"
        )

    if folder.startswith("/"):
        return base
    home = result.stdout.strip().splitlines()[0] if result.stdout.strip() else ""
    if not home:
        raise ProvisionError("could not determine the remote home directory")
    return f"{home.rstrip('/')}/{rel}"


def verify(target: str, key_path: Path, port: int | None = None) -> bool:
    """True when the Nomad key alone authenticates to ``target``."""
    args = verify_args(target, key_path, port)
    try:
        result = subprocess.run(
            args, capture_output=True, text=True, check=False, timeout=30
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


def ssh_available() -> bool:
    """True when an ssh client is on PATH."""
    return shutil.which("ssh") is not None


# --- orchestration -------------------------------------------------------

@dataclass(frozen=True)
class SetupResult:
    target: str
    base: str
    key_path: Path
    fingerprint: str
    used_password: bool
    port: int | None = None


def run_setup(
    settings,
    host: str,
    user: str,
    folder: str = "nomad-worlds",
    port: int | None = None,
    password: str | None = None,
    on_step=None,
) -> SetupResult:
    """Do the whole guided setup and return the resolved target and folder.

    Raises :class:`NeedPassword` when existing access fails and no password was
    supplied, so a UI can reveal a password field and call again.
    """
    from launcher.ssh_identity import (
        ensure_keypair,
        fingerprint,
        nomad_key_path,
        public_key,
    )

    def step(message: str) -> None:
        logger.info("%s", message)
        if on_step is not None:
            on_step(message)

    if not ssh_available():
        raise ProvisionError("no ssh client found on PATH")
    host = (host or "").strip()
    user = (user or "").strip()
    if not host:
        raise ProvisionError("a host or IP address is required")
    target = f"{user}@{host}" if user else host

    step("Generating the Nomad key…")
    key = ensure_keypair(
        nomad_key_path(settings),
        comment=f"nomad@{getattr(settings, 'player_name', '') or 'launcher'}",
    )
    pub = public_key(key)

    step("Checking for existing access…")
    result = probe(target, port)
    if not result.ok and password is None:
        if result.kind == "auth":
            raise NeedPassword(result.message, result)
        raise ProvisionError(result.message)

    if password is not None:
        step("Installing the key (using the password once)…")
        install_key(target, pub, port=port, password=password, data_dir=settings.data_dir)
    else:
        step("Installing the key…")
        install_key(target, pub, port=port, data_dir=settings.data_dir)

    step("Verifying the key works on its own…")
    if not verify(target, key, port):
        raise ProvisionError(
            "the key was installed but does not authenticate by itself; "
            "check that the remote allows public-key auth"
        )

    step("Creating the worlds folder…")
    base = prepare_remote(target, key, folder, port)

    step("Done")
    return SetupResult(
        target=target,
        base=base,
        key_path=key,
        fingerprint=fingerprint(key),
        used_password=password is not None,
        port=port,
    )
