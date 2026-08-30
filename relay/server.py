from __future__ import annotations

import argparse
import asyncio
import logging
import secrets
import time
from typing import Any

logger = logging.getLogger(__name__)

# 256-bit token; a client that knows it can attach to a host's relay session.
TOKEN_BYTES = 32
STALE_SESSION_SECONDS = 600


class RelaySession:
    """One host's relay attachment. The host connects out, gets a token, and
    registers the token on the controller. Players dial the relay with the
    token and are piped to the host's outbound connection.

    The host's read loop is owned by the session (single reader); joined
    players write into the host and receive whatever the host's reader
    produces.
    """

    __slots__ = ("token", "host_reader", "host_writer", "player_writer", "created_at", "lock")

    def __init__(self, token: str, host_reader: asyncio.StreamReader, host_writer: asyncio.StreamWriter) -> None:
        self.token = token
        self.host_reader = host_reader
        self.host_writer = host_writer
        self.player_writer: asyncio.StreamWriter | None = None
        self.created_at = time.monotonic()
        self.lock = asyncio.Lock()


class RelayServer:
    def __init__(self, host: str, port: int) -> None:
        self.host = host
        self.port = port
        self.sessions: dict[str, RelaySession] = {}

    async def handle_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            line = (await reader.readline()).decode("utf-8", errors="replace").strip()
            parts = line.split()
            if not parts:
                return
            if parts[0] == "REGISTER":
                await self._handle_register(reader, writer)
            elif parts[0] == "JOIN":
                await self._handle_join(reader, writer, parts[1] if len(parts) > 1 else "")
            else:
                logger.info("unknown command from %s", writer.get_extra_info("peername"))
        except (asyncio.IncompleteReadError, ConnectionError, OSError):
            pass
        finally:
            try:
                writer.close()
            except Exception:
                pass

    async def _handle_register(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        token = secrets.token_hex(TOKEN_BYTES)
        session = RelaySession(token, reader, writer)
        self.sessions[token] = session
        writer.write(f"OK {token}\n".encode())
        await writer.drain()
        logger.info("relay session registered: %s", token[:12])

        # Single owner of the host's read stream: forward whatever the host
        # sends to the currently joined player (if any).
        try:
            while True:
                data = await reader.read(65536)
                if not data:
                    break
                async with session.lock:
                    player = session.player_writer
                if player is not None and not player.is_closing():
                    try:
                        player.write(data)
                        await player.drain()
                    except (ConnectionError, OSError):
                        pass
        except (asyncio.IncompleteReadError, ConnectionError, OSError):
            pass
        finally:
            self.sessions.pop(token, None)
            logger.info("relay session ended: %s", token[:12])

    async def _handle_join(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter, token: str) -> None:
        session = self.sessions.get(token)
        if session is None or session.host_writer.is_closing():
            writer.write(b"ERR unknown-token\n")
            await writer.drain()
            logger.info("join failed: unknown token %s", token[:12])
            return
        if time.monotonic() - session.created_at > STALE_SESSION_SECONDS:
            writer.write(b"ERR session-stale\n")
            await writer.drain()
            return

        # Only one player at a time in the MVP fallback.
        async with session.lock:
            if session.player_writer is not None and not session.player_writer.is_closing():
                writer.write(b"ERR session-busy\n")
                await writer.drain()
                return
            session.player_writer = writer

        writer.write(b"OK\n")
        await writer.drain()
        logger.info("join accepted for token %s", token[:12])

        # Forward the player's bytes into the host's outbound stream. The
        # host's own read loop delivers the return traffic.
        try:
            while True:
                data = await reader.read(65536)
                if not data:
                    break
                if session.host_writer.is_closing():
                    break
                session.host_writer.write(data)
                await session.host_writer.drain()
        except (asyncio.IncompleteReadError, ConnectionError, OSError):
            pass
        finally:
            async with session.lock:
                if session.player_writer is writer:
                    session.player_writer = None

    async def run(self) -> None:
        server = await asyncio.start_server(self.handle_client, self.host, self.port)
        logger.info("relay listening on %s:%d", self.host, self.port)
        async with server:
            await server.serve_forever()


def main() -> None:
    parser = argparse.ArgumentParser(prog="nomad-relay")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=9000)
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args()
    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    asyncio.run(RelayServer(args.host, args.port).run())


if __name__ == "__main__":
    main()
