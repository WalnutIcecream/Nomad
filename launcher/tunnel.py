"""Reverse SSH tunnel: expose the local game port through a box you own.

A home host behind NAT usually cannot be reached without router configuration.
If they have an SSH box, they can publish their game port through it instead::

    ssh -N -R 25565:localhost:25565 user@box

Friends then connect to ``box:25565``. The agent starts the tunnel before
acquiring the lease (so the published address is real) and stops it when the
session ends.

The tunnel reuses the same :class:`~launcher.ssh_connection.SshConnection` as the
storage backend, so it rides the same multiplexed connection: one handshake, one
control socket, no second login.

The remote sshd must permit it: ``AllowTcpForwarding yes`` (default) and, to bind
on a non-loopback address, ``GatewayPorts yes`` (or ``clientspecified``).
``ExitOnForwardFailure=yes`` makes ssh abort immediately when the remote port
cannot be bound, so a failure surfaces at startup instead of silently.
"""

from __future__ import annotations

import logging
import subprocess
import time

from launcher.secrets import redact
from launcher.ssh_connection import SshConnection

logger = logging.getLogger(__name__)

_READY_TIMEOUT = 5.0


class TunnelError(RuntimeError):
    """Raised when the tunnel cannot be established."""


class ReverseTunnel:
    """Owns an ``ssh -R`` child process forwarding a remote port to localhost."""

    def __init__(
        self,
        connection: SshConnection,
        remote_port: int,
        local_port: int,
        remote_host: str = "",
    ) -> None:
        self.connection = connection
        self.remote_port = remote_port
        self.local_port = local_port
        self.remote_host = remote_host
        self._process: subprocess.Popen[bytes] | None = None

    # --- command ---------------------------------------------------------

    def command(self) -> list[str]:
        forward = f"{self.remote_port}:localhost:{self.local_port}"
        return [
            "ssh",
            "-N",
            "-R",
            forward,
            *self.connection.option_args(["ExitOnForwardFailure=yes"]),
            self.connection.target,
        ]

    @property
    def address(self) -> str:
        """The address friends connect to (``host:port``)."""
        host = self.remote_host or self.connection.host
        return f"{host}:{self.remote_port}"

    # --- lifecycle -------------------------------------------------------

    def start(self) -> "ReverseTunnel":
        """Launch the tunnel and confirm it stayed up."""
        if self._process is not None:
            return self
        # Log the destination, not the argv, so no identity path is recorded.
        logger.info(
            "opening reverse tunnel to %s (%d -> localhost:%d)",
            self.connection.target,
            self.remote_port,
            self.local_port,
        )
        self._process = subprocess.Popen(
            self.command(),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        deadline = time.monotonic() + _READY_TIMEOUT
        while time.monotonic() < deadline:
            if self._process.poll() is not None:
                detail = ""
                if self._process.stderr is not None:
                    detail = redact(
                        self._process.stderr.read().decode("utf-8", "replace").strip()
                    )
                self._process = None
                raise TunnelError(
                    f"reverse tunnel to {self.connection.target} failed"
                    + (f": {detail}" if detail else "")
                )
            time.sleep(0.1)
        return self

    def stop(self) -> None:
        if self._process is None:
            return
        if self._process.poll() is None:
            self._process.terminate()
            try:
                self._process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._process.kill()
        self._process = None

    def is_running(self) -> bool:
        return self._process is not None and self._process.poll() is None

    def __enter__(self) -> "ReverseTunnel":
        return self.start()

    def __exit__(self, *exc) -> None:
        self.stop()