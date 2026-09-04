from __future__ import annotations

import json
import logging
import re
import shutil
import subprocess
from pathlib import Path
from typing import Iterator
from urllib.request import urlopen

from launcher.server_properties import ServerProperties

from launcher.minecraft.process import ProcessHandle

logger = logging.getLogger(__name__)

MANIFEST_URL = "https://piston-meta.mojang.com/mc/game/version_manifest_v2.json"

_EULA_TEXT = """#By changing the setting below to TRUE you are indicating your agreement to our EULA
#(https://aka.ms/MinecraftEULA).
eula={eula}
"""


class VanillaMinecraftRuntime:
    def install(self, version: str, dest: Path) -> Path:
        dest.mkdir(parents=True, exist_ok=True)
        jar_path = dest / "server.jar"
        if jar_path.exists() and jar_path.stat().st_size > 0:
            return jar_path

        server_url = self._resolve_server_url(version)
        logger.info("downloading server jar for %s from %s", version, server_url)
        with urlopen(server_url) as response:
            data = response.read()
        jar_path.write_bytes(data)
        return jar_path

    def validate(self, install_dir: Path, java_path: str) -> tuple[bool, str]:
        jar_path = install_dir / "server.jar"
        if not jar_path.exists() or jar_path.stat().st_size == 0:
            return False, f"server.jar missing or empty in {install_dir}"

        java = shutil.which(java_path)
        if java is None:
            return False, f"java executable not found: {java_path}"

        try:
            result = subprocess.run(
                [java, "-version"],
                capture_output=True,
                text=True,
                check=False,
            )
        except OSError as exc:
            return False, f"failed to run java: {exc}"

        if result.returncode != 0:
            return False, "java -version failed"

        output = result.stderr or result.stdout
        match = re.search(r'version "(\d+)', output)
        if match is None:
            return False, f"cannot parse java version from: {output.strip()[:80]}"
        major = int(match.group(1))
        if major < 21:
            return (
                False,
                f"java {major} is too old for Minecraft 1.21.x — Java 21 or newer required "
                f"(found: {output.strip()[:60]})",
            )
        return True, ""

    def start(
        self,
        install_dir: Path,
        world_dir: Path,
        properties: ServerProperties,
        java_path: str,
        memory: str,
    ) -> ProcessHandle:
        world_dir.mkdir(parents=True, exist_ok=True)
        self._write_server_properties(world_dir, properties)
        self._write_eula(world_dir)

        jar_path = install_dir / "server.jar"
        java = shutil.which(java_path)
        if java is None:
            raise FileNotFoundError(f"java executable not found: {java_path}")

        command = [java, f"-Xmx{memory}", f"-Xms{memory}", "-jar", str(jar_path), "nogui"]
        logger.info("starting server: %s", " ".join(command))
        process = subprocess.Popen(
            command,
            cwd=world_dir,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        return ProcessHandle(process, world_dir)

    def stop(self, handle: ProcessHandle) -> None:
        if not handle.is_running():
            return
        handle.send_command("stop")

    def is_running(self, handle: ProcessHandle) -> bool:
        return handle.is_running()

    def get_logs(self, handle: ProcessHandle) -> Iterator[str]:
        yield from handle.logs()

    def get_version(self, install_dir: Path) -> str:
        version_file = install_dir / "version.json"
        if version_file.exists():
            metadata = json.loads(version_file.read_text(encoding="utf-8"))
            return str(metadata.get("id", "unknown"))
        return "unknown"

    def _resolve_server_url(self, version: str) -> str:
        with urlopen(MANIFEST_URL) as response:
            manifest = json.loads(response.read().decode("utf-8"))

        for entry in manifest.get("versions", []):
            if entry.get("id") == version:
                version_manifest_url = entry["url"]
                with urlopen(version_manifest_url) as version_response:
                    version_manifest = json.loads(version_response.read().decode("utf-8"))
                server_entry = version_manifest.get("downloads", {}).get("server", {})
                return str(server_entry["url"])

        raise ValueError(f"unknown Minecraft version: {version}")

    def _write_server_properties(self, world_dir: Path, properties: ServerProperties) -> None:
        world_dir.mkdir(parents=True, exist_ok=True)
        content = "\n".join(properties.to_properties_lines()) + "\n"
        (world_dir / "server.properties").write_text(content, encoding="utf-8")

    def _write_eula(self, world_dir: Path) -> None:
        eula_file = world_dir / "eula.txt"
        if eula_file.exists():
            return
        eula_file.write_text(_EULA_TEXT.format(eula="true"), encoding="utf-8")
