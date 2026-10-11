"""playit.gg tunnel provider.

Runs the playit agent as a subprocess so a locally-hosted server becomes
reachable without router port-forwarding. The agent is authenticated with the
user's secret key (linked once from the playit.gg dashboard), and Nomad then
tries to read the public address the agent was assigned so it can be published
in the world lease.

playit.gg does not currently expose a public REST API for reading tunnel
addresses, so this provider launches the agent and parses its output. The
address extraction is deliberately isolated in ``_extract_address`` so it can be
tightened against the agent's exact output without touching anything else.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import threading
import time
from pathlib import Path

from launcher.secrets import secret_value
from launcher.tunnel import Tunnel, TunnelError, TunnelProvider

logger = logging.getLogger(__name__)

# The agent reports its assigned endpoint in forms like `1.2.3.4:12345` or
# `some-host.playit.gg:12345`. Best-effort; see the module docstring.
_ADDRESS_RE = re.compile(
    r"\b(?P<addr>(?:\d{1,3}\.){3}\d{1,3}:[0-9]{1,5}"
    r"|[a-z0-9][a-z0-9.-]*\.playit\.gg(?::[0-9]{1,5})?)\b",
    re.IGNORECASE,
)

_LINK_HINT = (
    "Link this machine to your playit.gg account, then set "
    "NOMAD_TUNNEL_PLAYIT_SECRET to the agent's secret key."
)

_ADDRESS_TIMEOUT_SECONDS = 20.0


def _extract_address(text: str) -> str | None:
    """Return the first public ``host:port`` seen in a line of agent output."""
    match = _ADDRESS_RE.search(text)
    if match is None:
        return None
    address = match.group("addr")
    return address.rstrip(".")


class PlayitTunnelProvider(TunnelProvider):
    name = "playit"

    def __init__(self, settings) -> None:
        self.settings = settings
        self._process: subprocess.Popen[str] | None = None

    # --- TunnelProvider --------------------------------------------------

    def start(self, local_port: int) -> Tunnel:
        secret = secret_value(self.settings.tunnel_playit_secret)
        if not secret:
            raise TunnelError(f"playit.gg secret key is not set. {_LINK_HINT}")
        binary = self._binary()

        logger.info("starting playit agent (%s) to expose local port %s", binary, local_port)
        env = dict(os.environ)
        env["SECRET_KEY"] = secret
        process = subprocess.Popen(
            [binary, "--secret", secret],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            env=env,
        )
        self._process = process

        address = self._wait_for_address(process)
        if address is None:
            logger.warning(
                "could not read the playit public address from agent output; "
                "falling back to the static public_address"
            )
        return Tunnel(provider=self.name, public_address=address)

    def stop(self, tunnel: Tunnel) -> None:
        process, self._process = self._process, None
        if process is None or process.poll() is not None:
            return
        logger.info("stopping playit agent")
        try:
            process.terminate()
            process.wait(timeout=5)
        except Exception:
            try:
                process.kill()
            except Exception:
                pass

    # --- internals -------------------------------------------------------

    def _binary(self) -> str:
        configured = (self.settings.tunnel_playit_bin or "").strip()
        if configured and Path(configured).exists():
            return configured
        if configured:
            found = shutil.which(configured)
            if found:
                return found
        found = shutil.which("playit")
        if found:
            return found
        raise TunnelError(
            "playit.gg agent not found. Install it or set NOMAD_TUNNEL_PLAYIT_BIN."
        )

    def _wait_for_address(self, process: subprocess.Popen[str]) -> str | None:
        lines: list[str] = []

        def read_output() -> None:
            assert process.stdout is not None
            for line in process.stdout:
                lines.append(line)
                logger.debug("playit: %s", line.rstrip())

        reader = threading.Thread(target=read_output, daemon=True)
        reader.start()

        deadline = time.monotonic() + _ADDRESS_TIMEOUT_SECONDS
        while time.monotonic() < deadline:
            if process.poll() is not None:
                break
            for line in lines:
                address = _extract_address(line)
                if address:
                    return address
            time.sleep(0.25)
        return None
