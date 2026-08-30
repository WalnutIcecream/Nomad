from __future__ import annotations

import asyncio
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from backend.tests.helpers import _auth, _create_world, _login, _register
from launcher.networking import ConnectionInfo, build_direct_info, detect_lan_ip


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _run_relay_server(port: int) -> subprocess.Popen:
    """Start the relay in a subprocess so it runs in its own event loop."""
    proc = subprocess.Popen(
        [sys.executable, "-m", "relay.server", "--host", "127.0.0.1", "--port", str(port), "--log-level", "WARNING"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    # Wait for the port to accept.
    deadline = time.time() + 10
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                return proc
        except OSError:
            time.sleep(0.1)
    proc.terminate()
    raise RuntimeError("relay did not start")


class TestRelayForwarding:
    def test_join_pipes_bytes_through_relay(self) -> None:
        relay_port = _free_port()
        proc = _run_relay_server(relay_port)
        try:
            # A fake "Minecraft server" on a local port.
            server_port = _free_port()
            server_output: list[bytes] = []
            server_ready = threading.Event()

            def run_server() -> None:
                with socket.socket() as sock:
                    sock.bind(("127.0.0.1", server_port))
                    sock.listen(2)
                    server_ready.set()
                    conn, _ = sock.accept()
                    data = conn.recv(1024)
                    server_output.append(data)
                    conn.sendall(b"SERVER-HELLO")
                    time.sleep(1)
                    conn.close()

            threading.Thread(target=run_server, daemon=True).start()
            server_ready.wait(timeout=5)

            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

            # Simplest: test the relay alone with a direct REGISTER + JOIN pair.
            async def player_join_with_token() -> bytes:
                # Register a second session as a stand-in host.
                host_r, host_w = await asyncio.open_connection("127.0.0.1", relay_port)
                host_w.write(b"REGISTER\n")
                await host_w.drain()
                tok = (await host_r.readline()).decode().split()[1]
                # Bridge this host session to the fake server manually.
                srv_r, srv_w = await asyncio.open_connection("127.0.0.1", server_port)

                async def pipe(src, dst) -> None:
                    try:
                        while True:
                            data = await src.read(65536)
                            if not data:
                                break
                            dst.write(data)
                            await dst.drain()
                    except Exception:
                        pass

                asyncio.create_task(pipe(host_r, srv_w))
                asyncio.create_task(pipe(srv_r, host_w))

                # Player joins via the token.
                p_r, p_w = await asyncio.open_connection("127.0.0.1", relay_port)
                p_w.write(f"JOIN {tok}\n".encode())
                await p_w.drain()
                response = (await p_r.readline()).decode().strip()
                assert response == "OK", response
                p_w.write(b"PING-FROM-PLAYER")
                await p_w.drain()
                data = await p_r.read(1024)
                p_w.close()
                return data

            reply = loop.run_until_complete(player_join_with_token())
            assert reply == b"SERVER-HELLO"
            assert server_output and server_output[0] == b"PING-FROM-PLAYER"
            loop.close()
        finally:
            proc.terminate()
            proc.wait(timeout=5)

    def test_host_relay_client_bridges_to_server(self) -> None:
        """The host-side RelayClient pipes relay <-> local server bidirectionally."""
        from launcher.networking import RelayClient

        relay_port = _free_port()
        proc = _run_relay_server(relay_port)
        try:
            server_port = _free_port()
            received: list[bytes] = []
            server_ready = threading.Event()

            def run_server() -> None:
                with socket.socket() as sock:
                    sock.bind(("127.0.0.1", server_port))
                    sock.listen(2)
                    server_ready.set()
                    conn, _ = sock.accept()
                    conn.sendall(b"HELLO")
                    data = conn.recv(1024)
                    received.append(data)
                    conn.sendall(b"ECHO:" + data)
                    time.sleep(1)
                    conn.close()

            threading.Thread(target=run_server, daemon=True).start()
            server_ready.wait(timeout=5)

            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

            async def scenario() -> bytes:
                client = RelayClient("127.0.0.1", relay_port, server_port)
                token = await client.start()
                assert token

                pr, pw = await asyncio.open_connection("127.0.0.1", relay_port)
                pw.write(f"JOIN {token}\n".encode())
                await pw.drain()
                resp = (await pr.readline()).decode().strip()
                assert resp == "OK", resp
                pw.write(b"PING")
                await pw.drain()
                data = await asyncio.wait_for(pr.read(1024), timeout=3)
                pw.close()
                return data

            reply = loop.run_until_complete(scenario())
            assert reply == b"ECHO:PING"
            assert received == [b"PING"]
            loop.close()
        finally:
            proc.terminate()
            proc.wait(timeout=5)


class TestConnectionInfo:
    def test_detect_lan_ip(self) -> None:
        ip = detect_lan_ip()
        assert ip is not None
        # A plausible private IPv4.
        parts = ip.split(".")
        assert len(parts) == 4

    def test_build_direct_info(self) -> None:
        info = build_direct_info(25565)
        assert info is not None
        assert info.mode == "direct"
        assert info.address.endswith(":25565")

    def test_connection_info_roundtrip(self) -> None:
        info = ConnectionInfo(mode="relay", relay_token="abc", relay_host="relay.example", relay_port=9000)
        data = info.to_dict()
        restored = ConnectionInfo.from_dict(data)
        assert restored == info


class TestConnectionApi:
    def test_host_publishes_and_member_reads(self, client: TestClient) -> None:
        _register(client, "net_owner")
        token = _login(client, "net_owner")
        world = _create_world(client, token)

        # Acquire the lease (becomes current host).
        acquire = client.post(f"/worlds/{world['id']}/host/acquire", headers=_auth(token)).json()
        assert acquire["acquired"]

        # Host publishes direct connection info.
        put = client.put(
            f"/worlds/{world['id']}/connection",
            json={"mode": "direct", "address": "192.168.1.10:25565"},
            headers=_auth(token),
        )
        assert put.status_code == 200, put.text
        assert put.json()["connection"]["address"] == "192.168.1.10:25565"

        # The world status exposes connection info to members.
        status = client.get(f"/worlds/{world['id']}/status", headers=_auth(token)).json()
        assert status["connection"]["address"] == "192.168.1.10:25565"

    def test_non_host_cannot_publish(self, client: TestClient) -> None:
        _register(client, "net_owner2")
        _register(client, "net_guest")
        owner_token = _login(client, "net_owner2")
        guest_token = _login(client, "net_guest")
        world = _create_world(client, owner_token)

        guest_login = client.post(
            "/auth/login", json={"username": "net_guest", "password": "password123"}
        ).json()
        client.post(
            f"/worlds/{world['id']}/members",
            json={"user_id": str(guest_login["user"]["id"])},
            headers=_auth(owner_token),
        )

        resp = client.put(
            f"/worlds/{world['id']}/connection",
            json={"mode": "direct", "address": "10.0.0.5:25565"},
            headers=_auth(guest_token),
        )
        assert resp.status_code == 403

    def test_join_requires_hosting(self, client: TestClient) -> None:
        _register(client, "net_owner3")
        token = _login(client, "net_owner3")
        world = _create_world(client, token)

        # World is sleeping; connection should be empty.
        status = client.get(f"/worlds/{world['id']}/status", headers=_auth(token)).json()
        assert status["connection"] is None
