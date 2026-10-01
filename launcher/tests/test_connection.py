"""Connection-test behaviour: what a user sees when storage does or doesn't work."""

from __future__ import annotations

import urllib.error

from launcher.cloud import CloudError, WorldStore
from launcher.tests.fake_s3 import FakeS3Client


class _FailingClient(FakeS3Client):
    def __init__(self, exc: Exception) -> None:
        super().__init__()
        self._exc = exc

    def put_object(self, key, body, **kwargs):  # type: ignore[override]
        raise self._exc


def test_connection_succeeds_and_cleans_up() -> None:
    client = FakeS3Client()
    store = WorldStore(client, player_name="alice")

    result = store.test_connection("Cloudflare R2")

    assert result.ok is True
    assert "Cloudflare R2" in result.message
    # The probe object must not be left behind in the bucket.
    assert client.objects == {}


def test_connection_reports_missing_permission_clearly() -> None:
    store = WorldStore(_FailingClient(CloudError(403, "AccessDenied")))

    result = store.test_connection("Cloudflare R2")

    assert result.ok is False
    assert "permission to read and write objects" in result.message
    assert "AccessDenied" in result.detail


def test_connection_reports_missing_bucket_clearly() -> None:
    store = WorldStore(_FailingClient(CloudError(404, "NoSuchBucket")))

    result = store.test_connection("storage server")

    assert result.ok is False
    assert "couldn't find this bucket" in result.message


def test_connection_reports_unreachable_server_clearly() -> None:
    store = WorldStore(_FailingClient(urllib.error.URLError("connection refused")))

    result = store.test_connection("storage server")

    assert result.ok is False
    assert "couldn't reach your storage server" in result.message
    assert "connection refused" in result.detail


def test_connection_detects_corrupted_readback() -> None:
    class _WrongData(FakeS3Client):
        def get_object(self, key: str) -> bytes:
            return b"different bytes"

    result = WorldStore(_WrongData()).test_connection("Cloudflare R2")

    assert result.ok is False
    assert "wrong data" in result.message
