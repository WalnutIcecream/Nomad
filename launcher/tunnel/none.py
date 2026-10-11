"""No-tunnel provider: publish the static public address (manual port-forward)."""

from __future__ import annotations

from launcher.tunnel import Tunnel, TunnelProvider


class NoneTunnelProvider(TunnelProvider):
    name = "none"

    def __init__(self, settings) -> None:
        self.settings = settings

    def start(self, local_port: int) -> Tunnel:
        return Tunnel(provider=self.name, public_address=self.settings.public_address or None)

    def stop(self, tunnel: Tunnel) -> None:
        pass
