from __future__ import annotations

from pathlib import Path

import pytest

from launcher.config import LauncherSettings
from launcher.tunnel import PROVIDERS, TunnelError, build_tunnel, provider_label
from launcher.tunnel.none import NoneTunnelProvider
from launcher.tunnel.playit import PlayitTunnelProvider, _extract_address


def _settings(tmp_path: Path) -> LauncherSettings:
    s = LauncherSettings(_env_file=None)
    s.data_dir = tmp_path / "data"
    return s


def test_provider_labels() -> None:
    assert provider_label("none") == "None (manual)"
    assert provider_label("playit") == "playit.gg"
    assert provider_label("bogus") == "bogus"
    assert set(PROVIDERS) == {"none", "playit"}


def test_build_tunnel_dispatch(tmp_path: Path) -> None:
    s = _settings(tmp_path)
    s.tunnel_provider = "none"
    assert isinstance(build_tunnel(s), NoneTunnelProvider)
    s.tunnel_provider = "playit"
    assert isinstance(build_tunnel(s), PlayitTunnelProvider)


def test_build_tunnel_rejects_unknown(tmp_path: Path) -> None:
    s = _settings(tmp_path)
    s.tunnel_provider = "ssh"
    with pytest.raises(ValueError, match="unknown tunnel provider"):
        build_tunnel(s)


def test_none_provider_publishes_static_address(tmp_path: Path) -> None:
    s = _settings(tmp_path)
    s.public_address = "203.0.113.5:25565"
    tunnel = build_tunnel(s).start(25565)
    assert tunnel.provider == "none"
    assert tunnel.public_address == "203.0.113.5:25565"


def test_none_provider_without_address(tmp_path: Path) -> None:
    s = _settings(tmp_path)
    s.public_address = ""
    assert build_tunnel(s).start(25565).public_address is None


def test_playit_requires_secret_before_binary(tmp_path: Path) -> None:
    s = _settings(tmp_path)
    s.tunnel_provider = "playit"
    s.tunnel_playit_bin = "/nonexistent/playit"
    provider = build_tunnel(s)
    with pytest.raises(TunnelError, match="secret key"):
        provider.start(25565)


def test_extract_address() -> None:
    assert _extract_address("tunnel ready: tcp 25565 -> 198.51.100.7:25565") == "198.51.100.7:25565"
    assert _extract_address("available at my-world.playit.gg:25565") == "my-world.playit.gg:25565"
    assert _extract_address("no address here") is None
