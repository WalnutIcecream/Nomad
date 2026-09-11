"""A single SSH connection descriptor shared by the storage backend and tunnel.

Both :class:`launcher.storage.ssh.SshTransport` and
:class:`launcher.tunnel.ReverseTunnel` build their argv from this object, so they
target the same host with the same identity **and the same multiplexed
control socket**. One handshake serves the whole host session: lease renewals,
world transfers, and the reverse tunnel all ride it.

Multiplexing needs Unix-domain sockets, so ``control_path`` is ``None`` on
Windows and the two consumers open independent connections there.
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path

_SSH_BASE_OPTIONS = ("BatchMode=yes", "StrictHostKeyChecking=accept-new")
_MULTIPLEX_OPTIONS = (
    "ControlMaster=auto",
    "ControlPersist=yes",
    "ServerAliveInterval=30",
    "ServerAliveCountMax=3",
)


@dataclass(frozen=True)
class SshConnection:
    """Everything needed to reach a box: target, identity, port, control socket."""

    target: str
    key: str = ""
    port: int | None = None
    control_path: Path | None = None

    @property
    def multiplexed(self) -> bool:
        return self.control_path is not None

    @property
    def user(self) -> str:
        return self.target.rsplit("@", 1)[0] if "@" in self.target else ""

    @property
    def host(self) -> str:
        return self.target.rsplit("@", 1)[-1]

    def option_args(self, extra: list[str] | None = None) -> list[str]:
        """Return ssh option tokens, excluding the ``ssh`` binary and target.

        Consumers append the target (and command/forward spec) themselves so the
        ordering ``ssh <options> <target> <command>`` is always respected.
        """
        args = ["-T"]
        for option in _SSH_BASE_OPTIONS:
            args += ["-o", option]
        if self.control_path is not None:
            for option in _MULTIPLEX_OPTIONS:
                args += ["-o", option]
            args += ["-o", f"ControlPath={self.control_path}"]
        for option in extra or []:
            args += ["-o", option]
        if self.key:
            args += ["-i", self.key]
        if self.port:
            args += ["-p", str(self.port)]
        return args

    def command(self, remote_cmd: str) -> list[str]:
        """Full argv running ``remote_cmd`` on the target."""
        return ["ssh", *self.option_args(), self.target, remote_cmd]

    def control_command(self, operation: str) -> list[str]:
        """Multiplex control argv, e.g. ``ssh -O exit [options] <target>``.

        ``-O`` is an ssh option and must precede the host; putting it after the
        host makes ssh treat it as the remote command.
        """
        return ["ssh", "-O", operation, *self.option_args(), self.target]

    @classmethod
    def build(cls, settings) -> "SshConnection":
        """Build from launcher settings, resolving the identity and control path."""
        from launcher.ssh_identity import resolve_key

        target = getattr(settings, "ssh_target", "") or ""
        port = getattr(settings, "ssh_port", 0) or None
        control_path = cls._control_path_for(settings, target, port)
        return cls(
            target=target,
            key=resolve_key(settings),
            port=port,
            control_path=control_path,
        )

    @staticmethod
    def _control_path_for(settings, target: str, port: int | None) -> Path | None:
        if os.name != "posix" or not target:
            return None
        data_dir = Path(getattr(settings, "data_dir", Path.home() / ".nomad"))
        directory = data_dir / "ssh"
        directory.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(directory, 0o700)
        except OSError:
            pass
        digest = hashlib.sha256(f"{target}:{port}".encode()).hexdigest()[:16]
        return directory / f"ctl-{digest}"