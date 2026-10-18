"""Preflight checks and the ``nomad doctor`` self-check.

Each check returns a :class:`Check`. The host agent runs the *fatal* subset
(:func:`preflight`) before acquiring a lease, so a machine that cannot host
fails fast with a clear reason instead of pulling a world it cannot serve.
``nomad doctor`` runs the full set for support and first-run diagnostics.

Checks are deliberately cheap and side-effect-free apart from a small probe
write in the data directory and a version probe of the JVM.
"""

from __future__ import annotations

import shutil
import socket
from dataclasses import dataclass
from pathlib import Path

_DISK_FLOOR_MB = 256
_PROBE_NAME = ".nomad-write-test"


@dataclass
class Check:
    name: str
    ok: bool
    detail: str = ""
    fix: str = ""


def check_data_dir(settings) -> Check:
    path = Path(settings.data_dir)
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / _PROBE_NAME
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
    except OSError as exc:
        return Check(
            "data directory",
            False,
            f"cannot write to {path}: {exc}",
            f"Make sure {path} exists and is writable.",
        )
    return Check("data directory", True, str(path))


def check_disk_space(settings, minimum_mb: int = _DISK_FLOOR_MB) -> Check:
    path = Path(settings.data_dir)
    try:
        path.mkdir(parents=True, exist_ok=True)
        free_mb = shutil.disk_usage(path).free // (1024 * 1024)
    except OSError as exc:
        return Check("free disk", False, str(exc), "Check the data directory path.")
    if free_mb < minimum_mb:
        return Check(
            "free disk",
            False,
            f"{free_mb} MB free (needs at least {minimum_mb} MB)",
            "Free up disk space; the world archive and server jar need room.",
        )
    return Check("free disk", True, f"{free_mb} MB free")


def check_port(settings) -> Check:
    port = int(settings.server_port)
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        try:
            sock.bind(("", port))
        except OSError as exc:
            return Check(
                "server port",
                False,
                f"port {port} is in use: {exc}",
                f"Stop whatever is using port {port}, or set NOMAD_SERVER_PORT.",
            )
    return Check("server port", True, f"{port} is free")


def check_java(settings) -> Check:
    from launcher import java

    configured = getattr(settings, "java_path", "java")
    major = java.java_major(configured)
    if major is not None:
        if major < 21:
            return Check(
                "java",
                False,
                f"Java {major} is too old (Minecraft 1.21.x needs 21+)",
                "Install Java 21 or newer, or let Nomad download one.",
            )
        return Check("java", True, f"Java {major}")

    provisioned = java.provisioned_java(settings)
    if provisioned is not None:
        return Check("java", True, f"provisioned runtime ({provisioned})")
    return Check(
        "java",
        False,
        f"not found: {configured}",
        "Nomad will download Java 21 automatically on first host; "
        "or install it and set NOMAD_JAVA_PATH.",
    )


def check_storage(settings) -> Check:
    from launcher.storage import build_store, provider_label

    try:
        store = build_store(settings)
    except Exception as exc:
        return Check(
            "storage",
            False,
            f"{provider_label(settings.storage_backend)} is not configured: {exc}",
            "Run the storage wizard or 'nomad storage set'.",
        )
    result = store.test_connection()
    if not result.ok:
        return Check(
            "storage",
            False,
            result.message,
            "Check the settings, then run 'nomad storage test'.",
        )
    return Check("storage", True, f"{provider_label(settings.storage_backend)} reachable")


def check_tunnel(settings) -> Check:
    from launcher.tunnel import build_tunnel, provider_label

    provider = getattr(settings, "tunnel_provider", "none") or "none"
    if provider == "none":
        address = settings.public_address or "no public address set"
        return Check("tunnel", True, f"manual ({address})")
    try:
        build_tunnel(settings)
    except Exception as exc:
        return Check("tunnel", False, f"{provider_label(provider)}: {exc}")

    from launcher.secrets import secret_value

    if not secret_value(getattr(settings, "tunnel_playit_secret", None)):
        return Check(
            "tunnel",
            False,
            "playit.gg secret key is not set",
            "Set NOMAD_TUNNEL_PLAYIT_SECRET or use Settings -> Port forwarding.",
        )
    return Check("tunnel", True, f"{provider_label(provider)} configured")


def preflight(settings) -> list[Check]:
    """Fatal subset: what must be true before a host session starts."""
    return [check_data_dir(settings), check_disk_space(settings)]


def run_all(settings) -> list[Check]:
    """Every check, for ``nomad doctor``."""
    return [
        check_data_dir(settings),
        check_disk_space(settings),
        check_port(settings),
        check_java(settings),
        check_storage(settings),
        check_tunnel(settings),
    ]
