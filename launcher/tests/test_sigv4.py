"""SigV4 signer is byte-compatible with botocore's S3 signer (R2 accepts it)."""

from __future__ import annotations

import sys
import urllib.request
from pathlib import Path
from unittest.mock import patch

import pytest

botocore = pytest.importorskip("botocore")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from botocore.auth import S3SigV4Auth  # noqa: E402
from botocore.awsrequest import AWSRequest  # noqa: E402
from botocore.credentials import Credentials  # noqa: E402

from launcher.cloud import S3Client, _REGION  # noqa: E402

ENDPOINT = "https://00000000000000000000000000000000.r2.cloudflarestorage.com"
ACCESS = "test-access"
SECRET = "test-secret"
BUCKET = "nomad-worlds"
KEY = "worlds/abc-123/lease.json"
BODY = b'{"status":"active"}'


def client_auth(method: str, body: bytes, headers: dict[str, str]) -> str:
    captured = {}

    class _FakeResponse:
        status = 200
        headers = {}

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self):
            return b""

    def _fake_urlopen(request, timeout=None):
        captured["auth"] = request.get_header("Authorization") or ""
        return _FakeResponse()

    client = S3Client(ENDPOINT, ACCESS, SECRET, BUCKET)
    with patch.object(urllib.request, "urlopen", _fake_urlopen):
        client._request(method, KEY, body=body, headers=headers)
    return captured["auth"]


def botocore_auth(method: str, body: bytes, headers: dict[str, str]) -> str:
    creds = Credentials(ACCESS, SECRET)
    signer = S3SigV4Auth(creds, "s3", _REGION)
    url = f"{ENDPOINT}/{BUCKET}/{KEY}"
    request = AWSRequest(method=method, url=url, data=body, headers=headers)
    signer.add_auth(request)
    return request.headers["Authorization"]


@pytest.mark.parametrize(
    ("method", "body", "headers"),
    [
        ("PUT", BODY, {"content-type": "application/json", "if-none-match": "*"}),
        ("PUT", BODY, {"content-type": "application/json", "if-match": '"deadbeef"'}),
        ("GET", b"", {"content-type": "application/json"}),
    ],
)
def test_sigv4_matches_botocore(method: str, body: bytes, headers: dict[str, str]) -> None:
    ours = " ".join(client_auth(method, body, headers).split())
    theirs = " ".join(botocore_auth(method, body, headers).split())
    assert ours == theirs
