"""Tunnel providers for publishing a hosted world's address.

Mirrors ``launcher.storage``: the host agent talks only to a ``TunnelProvider``
protocol, and the provider is selected once by the global ``tunnel_provider``
setting. A tunnel answers one question — "what address should friends dial to
reach my server?" — and manages whatever local process makes that address real.

Built-in providers:

* ``none``   -> no tunnel; publish the static ``public_address`` (manual
  port-forward or LAN play, the historical behavior)
* ``playit`` -> run the playit.gg agent and publish its assigned address
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

PROVIDERS: dict[str, str] = {
    "none": "None (manual)",
    "playit": "playit.gg",
}


def provider_label(provider: str) -> str:
    """Human name for a tunnel provider id."""
    return PROVIDERS.get(provider, provider)


class TunnelError(Exception):
    """Raised when a tunnel cannot be established."""


@dataclass
class Tunnel:
    """A running (or configured) tunnel.

    ``public_address`` is the ``host:port`` friends should dial, or None when no
    address could be determined yet.
    """

    provider: str
    public_address: str | None = None


@runtime_checkable
class TunnelProvider(Protocol):
    """The tunnel contract the host agent and CLI depend on."""

    name: str

    def start(self, local_port: int) -> Tunnel:
        """Open a tunnel for ``local_port`` and return its public address."""
        ...

    def stop(self, tunnel: Tunnel) -> None:
        """Tear the tunnel down."""
        ...


def build_tunnel(settings) -> TunnelProvider:
    """Construct the configured tunnel provider from launcher settings."""
    provider = getattr(settings, "tunnel_provider", "none") or "none"
    if provider == "none":
        from launcher.tunnel.none import NoneTunnelProvider

        return NoneTunnelProvider(settings)
    if provider == "playit":
        from launcher.tunnel.playit import PlayitTunnelProvider

        return PlayitTunnelProvider(settings)
    raise ValueError(
        f"unknown tunnel provider: {provider!r} (expected one of: {', '.join(PROVIDERS)})"
    )


__all__ = [
    "PROVIDERS",
    "provider_label",
    "Tunnel",
    "TunnelError",
    "TunnelProvider",
    "build_tunnel",
]
