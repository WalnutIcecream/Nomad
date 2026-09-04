from __future__ import annotations

from pathlib import Path

import pytest

from launcher.cloud import LeaseError, WorldStore
from launcher.tests.fake_s3 import FakeS3Client, lease_body


def make_shared_store(player: str, client: FakeS3Client) -> WorldStore:
    return WorldStore(client, player_name=player)


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
