from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from launcher.cloud import LeaseError, S3Client, UsageCounter, WorldStore
from launcher.tests.fake_s3 import FakeS3Client, lease_body


def make_shared_store(player: str, client: FakeS3Client, usage: UsageCounter | None = None) -> WorldStore:
    return WorldStore(client, player_name=player, usage=usage)


def test_first_acquire_wins() -> None:
    client = FakeS3Client()
    store = make_shared_store("alice", client)
    lease = store.acquire("world-1", address="203.0.113.5:25565")
    assert lease.holder == "alice"
    assert lease.etag is not None
    body = lease_body(store, "world-1")
    assert body["holder"] == "alice"
    assert body["address"] == "203.0.113.5:25565"


def test_second_acquire_denied_while_active() -> None:
    client = FakeS3Client()
    store_a = make_shared_store("alice", client)
    store_a.acquire("world-1")
    store_b = make_shared_store("bob", client)
    with pytest.raises(LeaseError):
        store_b.acquire("world-1")
    # Alice still holds it.
    assert lease_body(store_a, "world-1")["holder"] == "alice"


def test_renew_extends_expiry_and_release_frees() -> None:
    client = FakeS3Client()
    store = make_shared_store("alice", client)
    lease = store.acquire("world-1")
    first_expiry = lease_body(store, "world-1")["expires_at"]

    renewed = store.renew("world-1", lease)
    assert renewed.etag != lease.etag
    assert lease_body(store, "world-1")["expires_at"] > first_expiry

    store.release("world-1", renewed)
    assert lease_body(store, "world-1")["status"] == "released"

    # Bob can now take over an explicitly-released lease immediately.
    bob = make_shared_store("bob", client)
    bob_lease = bob.acquire("world-1")
    assert bob_lease.holder == "bob"


def test_stale_host_cannot_renew_or_release_after_loss() -> None:
    client = FakeS3Client()
    alice = make_shared_store("alice", client)
    lease = alice.acquire("world-1")
    alice.release("world-1", lease)
    bob = make_shared_store("bob", client)
    bob.acquire("world-1")

    # Alice's old etag no longer matches — her renew must fail.
    with pytest.raises(LeaseError):
        alice.renew("world-1", lease)
    # And her release must fail too (she no longer holds it).
    with pytest.raises(LeaseError):
        alice.release("world-1", lease)


def test_expired_lease_can_be_stolen() -> None:
    import datetime
    import json

    from launcher.cloud import lease_key

    client = FakeS3Client()
    alice = make_shared_store("alice", client)
    alice.acquire("world-1")

    # Manually age the lease object so alice's hold is over.
    raw, etag = client.objects[lease_key("world-1")]
    data = json.loads(raw.decode())
    data["expires_at"] = (
        datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(seconds=1)
    ).isoformat()
    client.objects[lease_key("world-1")] = (json.dumps(data).encode(), etag)

    # Bob acquires over the stale lease.
    bob = make_shared_store("bob", client)
    bob_lease = bob.acquire("world-1")
    assert bob_lease.holder == "bob"


def test_world_upload_download_round_trip(tmp_path: Path) -> None:
    client = FakeS3Client()
    store = make_shared_store("alice", client)
    archive = tmp_path / "world.tar.gz"
    archive.write_bytes(b"world-bytes")
    store.upload_world("world-1", archive)

    dest = tmp_path / "downloaded.tar.gz"
    assert store.download_world("world-1", dest) is True
    assert dest.read_bytes() == b"world-bytes"

    dest2 = tmp_path / "missing.tar.gz"
    assert store.download_world("world-2", dest2) is False
    assert not dest2.exists()


def test_status_reflects_hosting() -> None:
    client = FakeS3Client()
    store = make_shared_store("alice", client)
    assert store.status("world-1")["hosted"] is False
    store.acquire("world-1", address="1.2.3.4:25565")
    status = store.status("world-1")
    assert status["hosted"] is True
    assert status["holder"] == "alice"
    assert status["address"] == "1.2.3.4:25565"


def test_upload_overwrites_single_world_object(tmp_path: Path) -> None:
    """Re-saving a world must never accumulate version objects."""
    from launcher.cloud import world_key

    client = FakeS3Client()
    store = make_shared_store("alice", client)
    a1 = tmp_path / "a1.tar.gz"
    a1.write_bytes(b"version-one")
    store.upload_world("world-1", a1)
    a2 = tmp_path / "a2.tar.gz"
    a2.write_bytes(b"version-two")
    store.upload_world("world-1", a2)

    # Exactly one blob object, last write wins (plus one hash companion object).
    blobs = [k for k in client.objects if k.endswith("world.tar.gz")]
    assert len(blobs) == 1
    assert world_key("world-1") in client.objects
    assert client.objects[world_key("world-1")][0] == b"version-two"


def test_download_verifies_sha256(tmp_path: Path) -> None:
    import hashlib

    from launcher.cloud import CloudError, world_hash_key, world_key

    client = FakeS3Client()
    store = make_shared_store("alice", client)
    archive = tmp_path / "w.tar.gz"
    archive.write_bytes(b"good-world")
    store.upload_world("world-1", archive)

    # The companion hash matches the blob.
    stored = client.objects[world_key("world-1")][0]
    stored_hash = client.objects[world_hash_key("world-1")][0].decode("utf-8")
    assert stored_hash == hashlib.sha256(stored).hexdigest()

    # Tamper with the stored blob: download must reject it.
    blob, etag = client.objects[world_key("world-1")]
    client.objects[world_key("world-1")] = (b"tampered", etag)
    with pytest.raises(CloudError):
        store.download_world("world-1", tmp_path / "out.tar.gz")


def test_usage_counter_tracks_operations(tmp_path: Path) -> None:
    client = FakeS3Client()
    usage = UsageCounter(tmp_path / "usage.json")
    store = make_shared_store("alice", client, usage=usage)

    lease = store.acquire("world-1")        # +1 api, +1 world_count
    lease = store.renew("world-1", lease)   # +1 api
    store.release("world-1", lease)         # +1 api
    archive = tmp_path / "w.tar.gz"
    archive.write_bytes(b"data" * 1000)
    store.upload_world("world-1", archive)   # +1 upload, +4000 bytes
    store.download_world("world-1", tmp_path / "out.tar.gz")  # +1 download

    snap = usage.snapshot()
    assert snap["world_count"] == 1
    assert snap["upload_count"] == 1
    assert snap["download_count"] == 1
    assert snap["uploaded_bytes"] == 4000
    assert snap["api_request_count"] >= 3  # acquire+renew+release

    # Counters persist across instances.
    usage2 = UsageCounter(tmp_path / "usage.json")
    assert usage2.snapshot()["world_count"] == 1


def test_etag_header_casing_is_ignored() -> None:
    """HTTP response header names are case-insensitive (RFC 9110), but real
    S3 stores vary: SeaweedFS returns ``ETag`` on PUT and ``Etag`` on GET.
    Lease CAS depends on the returned etag, so a case-sensitive lookup
    (``.get("etag")`` over ``dict(response.headers)``) silently yields ``""``,
    turning every renew/release into an unconditional overwrite. This must not
    regress. Spin up a real HTTP server that sends the mixed casing."""

    class _Handler(BaseHTTPRequestHandler):
        def _respond(self) -> None:
            payload = b'{"status":"active","holder":"alice"}'
            self.send_response(200)
            # SeaweedFS's actual behaviour: differ the header casing by method.
            self.send_header("Etag" if self.command == "GET" else "ETag", '"2f8bb70a9fa97314f9141b082e24f10c"')
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def do_GET(self) -> None:
            self._respond()

        def do_PUT(self) -> None:
            length = int(self.headers.get("Content-Length") or 0)
            if length:
                self.rfile.read(length)
            self._respond()

        def log_message(self, *args) -> None:
            pass

    server = HTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        port = server.server_address[1]
        client = S3Client(f"http://127.0.0.1:{port}", "access", "secret", "bucket")

        body, etag = client.get_object_etag("worlds/1/lease.json")
        assert etag == '"2f8bb70a9fa97314f9141b082e24f10c"'

        etag = client.put_object("worlds/1/lease.json", body)
        assert etag == '"2f8bb70a9fa97314f9141b082e24f10c"'
    finally:
        server.shutdown()
        server.server_close()
