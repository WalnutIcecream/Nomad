from __future__ import annotations

from pathlib import Path

from launcher.minecraft.vanilla import VanillaMinecraftRuntime


def test_install_uses_cached_jar(tmp_path: Path, monkeypatch) -> None:
    dest = tmp_path / "runtime"
    dest.mkdir()
    (dest / "server.jar").write_bytes(b"already-here")

    calls: list[int] = []
    monkeypatch.setattr("launcher.minecraft.vanilla.download", lambda *a, **k: calls.append(1))

    out = VanillaMinecraftRuntime().install("1.21.1", dest)
    assert out == dest / "server.jar"
    assert out.read_bytes() == b"already-here"
    assert calls == []


def test_install_downloads_when_missing(tmp_path: Path, monkeypatch) -> None:
    dest = tmp_path / "runtime"
    seen: dict = {}

    def fake_download(url, path, **kwargs):
        seen["url"] = url
        seen["cb"] = kwargs.get("on_progress")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"jar-bytes")
        return path

    monkeypatch.setattr("launcher.minecraft.vanilla.download", fake_download)
    monkeypatch.setattr(
        VanillaMinecraftRuntime, "_resolve_server_url", lambda self, v: "https://example/server.jar"
    )

    def progress(done, total):
        return None

    out = VanillaMinecraftRuntime(on_progress=progress).install("1.21.1", dest)

    assert out.read_bytes() == b"jar-bytes"
    assert seen["url"] == "https://example/server.jar"
    assert seen["cb"] is progress
