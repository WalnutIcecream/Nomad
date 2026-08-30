from __future__ import annotations

from pathlib import Path

from shared.protocol.enums import LauncherState
from shared.protocol.models import ServerProperties


def test_server_properties_render() -> None:
    props = ServerProperties(level_name="test_world", gamemode="creative")
    lines = props.to_properties_lines()
    rendered = dict(line.split("=", 1) for line in lines)
    assert rendered["level-name"] == "test_world"
    assert rendered["gamemode"] == "creative"
    assert rendered["motd"] == "A Nomad Minecraft Server"
