from __future__ import annotations

import asyncio
import logging
import socket
import threading
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class ConnectionInfo:
    mode: str  # direct | relay
    address: str | None = None  # host:port for direct
    relay_token: str | None = None
    relay_host: str | None = None
    relay_port: int | None = None

    def to_dict(self) -> dict:
        data: dict = {"mode": self.mode}
        if self.address is not None:
            data["address"] = self.address
        if self.relay_token is not None:
            data["relay_token"] = self.relay_token
        if self.relay_host is not None:
            data["relay_host"] = self.relay_host
        if self.relay_port is not None:
            data["relay_port"] = self.relay_port
        return data

    @classmethod
    def from_dict(cls, data: dict | None) -> "ConnectionInfo | None":
        if not data:
            return None
        return cls(
            mode=data.get("mode", "direct"),
            address=data.get("address"),
            relay_token=data.get("relay_token"),
            relay_host=data.get("relay_host"),
            relay_port=data.get("relay_port"),
        )


def detect_lan_ip() -> str | None:
    """Best-effort local IP on the default route (the address a LAN friend
    could use to reach us)."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("8.8.8.8", 80))
            return sock.getsockname()[0]
    except OSError:
        return None


def build_direct_info(port: int) -> ConnectionInfo | None:
    lan_ip = detect_lan_ip()
    if lan_ip is None:
        return None
    return ConnectionInfo(mode="direct", address=f"{lan_ip}:{port}")


class RelayClient:
    """Outbound attachment to the relay.

    The host dials OUT to the relay (so NAT doesn't matter) and receives a
    token. Remote players JOIN the relay with that token; the relay pipes
    their traffic into the host's outbound stream, which this client forwards
    bidirectionally to the local Minecraft server. One player at a time is
    supported in the MVP fallback.
    """

    def __init__(self, relay_host: str, relay_port: int, local_port: int) -> None:
        self.relay_host = relay_host
        self.relay_port = relay_port
        self.local_port = local_port
        self.token: str | None = None

    async def start(self) -> str:
        """Connect to the relay, register, and start bridging traffic.

        Returns the token as soon as registration completes; the pipe tasks
        run in the background (the agent keeps the event loop alive).
        """
        relay_reader, relay_writer = await asyncio.open_connection(self.relay_host, self.relay_port)
        relay_writer.write(b"REGISTER\n")
        await relay_writer.drain()
        response = (await relay_reader.readline()).decode("utf-8", errors="replace").strip()
        if not response.startswith("OK "):
            relay_writer.close()
            raise RuntimeError(f"relay registration failed: {response}")
        self.token = response.split(" ", 1)[1]

        # Bridge the relay stream to the local Minecraft server.
        server_reader, server_writer = await asyncio.open_connection("127.0.0.1", self.local_port)

        async def pipe(src, dst) -> None:
            try:
                while True:
                    data = await src.read(65536)
                    if not data:
                        break
                    dst.write(data)
                    await dst.drain()
            except (asyncio.IncompleteReadError, ConnectionError, OSError):
                pass
            finally:
                try:
                    dst.close()
                except Exception:
                    pass

        asyncio.create_task(pipe(relay_reader, server_writer))
        asyncio.create_task(pipe(server_reader, relay_writer))
        return self.token


def attach_relay(relay_host: str, relay_port: int, local_port: int) -> str:
    """Synchronous entry point for the agent: attach to the relay and return
    the token. The client keeps running in a background thread until the
    session ends (the thread's event loop stays alive to serve the pipes)."""
    result: dict = {}
    ready = threading.Event()

    def worker() -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        client = RelayClient(relay_host, relay_port, local_port)

        async def run() -> None:
            token = await client.start()
            result["token"] = token
            ready.set()
            # Keep the loop alive: the bridge pipes must keep serving.
            try:
                while True:
                    await asyncio.sleep(3600)
            except asyncio.CancelledError:
                pass

        try:
            loop.run_until_complete(run())
        except Exception as exc:
            result["error"] = exc
        finally:
            loop.close()

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    ready.wait(timeout=15)
    if "error" in result:
        raise RuntimeError(f"relay attachment failed: {result['error']}")
    if "token" not in result:
        raise RuntimeError("relay attachment timed out")
    return result["token"]
