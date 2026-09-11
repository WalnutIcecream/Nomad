"""Minimal S3/R2 client + the world lease protocol, stdlib only.

The old architecture ran a controller (accounts, Postgres, versioned blobs,
two-phase uploads). The new one is just object storage in a Cloudflare R2
bucket: every "coordinator" decision is an atomic conditional write.

Objects per world (``worlds/<id>/…``)::

    lease.json       # {"status","holder","address","acquired_at","expires_at"}
    world.tar.gz     # the single shared version; last upload wins

The lease is a compare-and-swap on the lease object's ETag:

* acquire  -> if no lease exists, ``PUT If-None-Match: *``; if the previous
              lease is expired/released, ``PUT If-Match: <stale etag>``.
              Exactly one of two simultaneous acquirers wins; the loser's
              conditional write bounces with 412.
* renew    -> ``PUT If-Match: <own etag>`` every heartbeat interval.
* release  -> ``PUT If-Match: <own etag>`` with status "released" AFTER the
              world upload lands, so the next host can only acquire a final
              world.

SigV4 signing is implemented here (no boto3 dependency): the launcher stays a
small, self-contained desktop app.
"""

from __future__ import annotations

import datetime
import hashlib
import hmac
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

_SERVICE = "s3"
_REGION = "auto"  # R2 uses "auto" as the signing region


class CloudError(Exception):
    """Raised for non-2xx object-store responses, carrying the status code."""

    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


class LeaseError(CloudError):
    """Raised when a conditional lease write loses the race."""


# --------------------------------------------------------------------------
# SigV4 request signing (S3-compatible API)
# --------------------------------------------------------------------------

def _sign(key: bytes, msg: str) -> bytes:
    return hmac.new(key, msg.encode("utf-8"), hashlib.sha256).digest()


def _signing_key(secret: str, date_stamp: str) -> bytes:
    date_key = _sign(("AWS4" + secret).encode("utf-8"), date_stamp)
    region_key = _sign(date_key, _REGION)
    service_key = _sign(region_key, _SERVICE)
    return _sign(service_key, "aws4_request")


class S3Client:
    """Tiny S3 client speaking just the operations this app needs."""

    def __init__(self, endpoint_url: str, access_key: str, secret_key: str, bucket: str) -> None:
        self.endpoint = endpoint_url.rstrip("/")
        self.access_key = access_key
        self.secret_key = secret_key
        self.bucket = bucket

    # --- request plumbing -----------------------------------------------

    def _request(
        self,
        method: str,
        key: str,
        *,
        body: bytes | None = None,
        headers: dict[str, str] | None = None,
    ) -> tuple[int, dict[str, str], bytes]:
        headers = {k.lower(): v for k, v in (headers or {}).items()}
        payload_hash = hashlib.sha256(body or b"").hexdigest()
        host = urllib.parse.urlparse(self.endpoint).netloc

        now = datetime.datetime.now(datetime.timezone.utc)
        amz_date = now.strftime("%Y%m%dT%H%M%SZ")
        date_stamp = now.strftime("%Y%m%d")

        canonical_uri = "/" + self.bucket + "/" + key
        canonical_query = ""
        # Sign every header we send (host, content-type, conditionals, x-amz-*),
        # in sorted order, matching how botocore signs S3 requests.
        all_headers = {"host": host, **dict(headers.items())}
        sorted_names = sorted(all_headers)
        canonical_headers = (
            "".join(f"{k}:{v}\n" for k, v in sorted(all_headers.items()))
            + f"x-amz-content-sha256:{payload_hash}\n"
            + f"x-amz-date:{amz_date}\n"
        )
        signed_headers = (
            ";".join(sorted_names) + ";x-amz-content-sha256;x-amz-date"
        )

        canonical_request = "\n".join(
            [method, canonical_uri, canonical_query, canonical_headers, signed_headers, payload_hash]
        )
        scope = f"{date_stamp}/{_REGION}/{_SERVICE}/aws4_request"
        string_to_sign = "\n".join(
            ["AWS4-HMAC-SHA256", amz_date, scope, hashlib.sha256(canonical_request.encode()).hexdigest()]
        )
        signature = hmac.new(
            _signing_key(self.secret_key, date_stamp),
            string_to_sign.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

        authorization = (
            f"AWS4-HMAC-SHA256 Credential={self.access_key}/{scope}, "
            f"SignedHeaders={signed_headers}, Signature={signature}"
        )
        final_headers = {
            "Authorization": authorization,
            "x-amz-date": amz_date,
            "x-amz-content-sha256": payload_hash,
            **headers,
        }

        url = f"{self.endpoint}/{urllib.parse.quote(self.bucket, safe='')}/{key}"
        request = urllib.request.Request(url, data=body, method=method, headers=final_headers)
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                return response.status, dict(response.headers), response.read()
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise CloudError(exc.code, detail[:500]) from exc

    # --- object operations -----------------------------------------------

    def get_object(self, key: str) -> bytes:
        _, _, body = self._request("GET", key)
        return body

    def get_object_etag(self, key: str) -> tuple[bytes, str | None]:
        _, headers, body = self._request("GET", key)
        return body, headers.get("etag")

    def get_object_if_exists(self, key: str) -> bytes | None:
        try:
            return self.get_object(key)
        except CloudError as exc:
            if exc.status_code == 404:
                return None
            raise

    def put_object(
        self,
        key: str,
        body: bytes,
        *,
        if_match: str | None = None,
        if_none_match: bool = False,
        content_type: str = "application/octet-stream",
    ) -> str:
        """Write an object, optionally conditional on its current ETag.

        Returns the new ETag. Raises ``LeaseError`` (412) when the condition
        fails — the caller lost the race.
        """
        headers = {"content-type": content_type}
        if if_match is not None:
            headers["if-match"] = if_match
        if if_none_match:
            headers["if-none-match"] = "*"
        try:
            _, resp_headers, _ = self._request("PUT", key, body=body, headers=headers)
        except CloudError as exc:
            if exc.status_code == 412:
                raise LeaseError(exc.status_code, "conditional write failed (lost the race)") from exc
            raise
        return resp_headers.get("etag", "")


# --------------------------------------------------------------------------
# Lease model + protocol
# --------------------------------------------------------------------------

def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


def _iso(value: datetime.datetime) -> str:
    return value.isoformat()


@dataclass
class Lease:
    status: str  # "active" | "released"
    holder: str
    address: str | None = None
    acquired_at: str | None = None
    expires_at: str | None = None
    etag: str | None = None

    @classmethod
    def from_object(cls, raw: bytes, etag: str | None = None) -> "Lease":
        data = json.loads(raw.decode("utf-8"))
        return cls(
            status=data.get("status", "active"),
            holder=data.get("holder", "?"),
            address=data.get("address"),
            acquired_at=data.get("acquired_at"),
            expires_at=data.get("expires_at"),
            etag=etag,
        )

    @property
    def is_active(self) -> bool:
        if self.status != "active":
            return False
        if not self.expires_at:
            return False
        try:
            expiry = datetime.datetime.fromisoformat(self.expires_at)
        except ValueError:
            return False
        return expiry > _now()

    def to_bytes(self) -> bytes:
        payload = {
            "status": self.status,
            "holder": self.holder,
            "address": self.address,
            "acquired_at": self.acquired_at,
            "expires_at": self.expires_at,
        }
        return json.dumps(payload).encode("utf-8")

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "holder": self.holder,
            "address": self.address,
            "acquired_at": self.acquired_at,
            "expires_at": self.expires_at,
        }


def lease_key(world_id: str) -> str:
    return f"worlds/{world_id}/lease.json"


def world_key(world_id: str) -> str:
    # Deliberately a single, stable key per world: every upload overwrites the
    # same object (no v1/v2/... accumulation), so storage stays bounded by the
    # number of worlds, not the number of saves. Per the R2 pricing policy,
    # only retained versions grow storage cost — we never create them.
    return f"worlds/{world_id}/world.tar.gz"


def world_hash_key(world_id: str) -> str:
    """Companion object holding the SHA-256 of the world blob (hex text)."""
    return f"worlds/{world_id}/world.sha256"


# --------------------------------------------------------------------------
# Usage telemetry
# --------------------------------------------------------------------------

_USAGE_FIELDS = (
    "world_count",
    "uploaded_bytes",
    "upload_count",
    "download_count",
    "api_request_count",
)


def empty_usage() -> dict:
    return {k: 0 for k in _USAGE_FIELDS}


class UsageCounter:
    """Persisted R2 usage counters so admins can watch the free-allowance
    thresholds instead of discovering them in a bill.

    The counters are deliberately local and approximate — the authoritative
    numbers come from Cloudflare's dashboard. This exists to give the app a
    rough, always-available signal (stored worlds, bytes, upload/download
    counts, request frequency) without adding any external telemetry service.
    """

    def __init__(self, path: Path) -> None:
        self.path = path

    def load(self) -> dict:
        if not self.path.exists():
            return empty_usage()
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return empty_usage()
        return {k: data.get(k, 0) for k in _USAGE_FIELDS}

    def record(self, **delta: int) -> None:
        usage = self.load()
        for key, value in delta.items():
            usage[key] = usage.get(key, 0) + value
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(usage, indent=2), encoding="utf-8")
        tmp.replace(self.path)

    def snapshot(self) -> dict:
        return self.load()


class WorldStore:
    """R2-backed world coordinator: lease + world blob behind one object set."""

    def __init__(self, client: S3Client, player_name: str = "me", usage: UsageCounter | None = None) -> None:
        self.client = client
        self.player_name = player_name
        self.usage = usage

    def _track(self, **delta: int) -> None:
        if self.usage is not None:
            self.usage.record(**delta)

    # --- status ----------------------------------------------------------

    def status(self, world_id: str) -> dict:
        """Public status: whether the world is hosted and by whom."""
        self._track(api_request_count=1)
        raw = self.client.get_object_if_exists(lease_key(world_id))
        if raw is None:
            return {"hosted": False, "holder": None, "address": None}
        lease = Lease.from_object(raw)
        if not lease.is_active:
            return {
                "hosted": False,
                "holder": lease.holder,
                "address": lease.address,
                "expired": True,
            }
        return {
            "hosted": True,
            "holder": lease.holder,
            "address": lease.address,
            "expires_at": lease.expires_at,
        }

    # --- lease -----------------------------------------------------------

    def acquire(self, world_id: str, address: str | None = None) -> Lease:
        """Become the host, or raise ``LeaseError`` if someone already is."""
        self._track(api_request_count=1)
        now = _now()
        lease = Lease(
            status="active",
            holder=self.player_name,
            address=address,
            acquired_at=_iso(now),
            expires_at=_iso(now + datetime.timedelta(seconds=LEASE_SECONDS)),
        )
        body = lease.to_bytes()

        # Fresh world: no lease object yet — claim it unconditionally.
        try:
            etag = self.client.put_object(
                lease_key(world_id), body, if_none_match=True, content_type="application/json"
            )
            lease.etag = etag
            self._track(world_count=1)
            return lease
        except LeaseError:
            pass  # a lease already exists; fall through to stale-steal

        # A lease exists. Read it and try to take over only if it is dead.
        self._track(api_request_count=1)
        current_raw, current_etag = self.client.get_object_etag(lease_key(world_id))
        current = Lease.from_object(current_raw, etag=current_etag)
        if current.is_active:
            raise LeaseError(412, f"world is hosted by {current.holder} until {current.expires_at}")

        try:
            etag = self.client.put_object(
                lease_key(world_id),
                body,
                if_match=current.etag or "",
                content_type="application/json",
            )
        except LeaseError as exc:
            raise LeaseError(412, "someone else claimed the world first") from exc
        self._track(api_request_count=1)
        lease.etag = etag
        return lease

    def renew(self, world_id: str, lease: Lease) -> Lease:
        """Extend our lease (must still hold the etag)."""
        self._track(api_request_count=1)
        now = _now()
        updated = Lease(
            status="active",
            holder=lease.holder,
            address=lease.address,
            acquired_at=lease.acquired_at,
            expires_at=_iso(now + datetime.timedelta(seconds=LEASE_SECONDS)),
        )
        etag = self.client.put_object(
            lease_key(world_id), updated.to_bytes(), if_match=lease.etag or "", content_type="application/json"
        )
        updated.etag = etag
        return updated

    def release(self, world_id: str, lease: Lease) -> None:
        """Mark the lease released so the next acquirer can take over."""
        self._track(api_request_count=1)
        released = Lease(
            status="released",
            holder=lease.holder,
            address=lease.address,
            acquired_at=lease.acquired_at,
            expires_at=lease.expires_at,
        )
        self.client.put_object(
            lease_key(world_id), released.to_bytes(), if_match=lease.etag or "", content_type="application/json"
        )

    # --- world blob ------------------------------------------------------

    def download_world(self, world_id: str, dest: Path) -> bool:
        """Download the shared world archive into ``dest``. False if none yet.

        If a companion ``world.sha256`` object exists, the downloaded bytes are
        verified against it and a mismatch raises ``CloudError``. Verification
        is skipped for worlds uploaded before the hash object existed.
        """
        raw = self.client.get_object_if_exists(world_key(world_id))
        if raw is None:
            return False
        expected_raw = self.client.get_object_if_exists(world_hash_key(world_id))
        if expected_raw is not None:
            expected = expected_raw.decode("utf-8").strip()
            actual = hashlib.sha256(raw).hexdigest()
            if expected and actual != expected:
                raise CloudError(
                    422,
                    f"world integrity check failed for {world_id}: "
                    f"expected {expected[:12]}, got {actual[:12]}",
                )
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(raw)
        self._track(download_count=1)
        return True

    def upload_world(self, world_id: str, archive: Path) -> None:
        """Upload a new shared world archive (must hold the lease to matter).

        Overwrites the single per-world object — never accumulates versions —
        so storage stays bounded by the number of worlds, not the number of saves.
        Also writes a companion SHA-256 so pullers can verify integrity.
        """
        size = archive.stat().st_size
        digest = hashlib.sha256(archive.read_bytes()).hexdigest()
        self.client.put_object(world_key(world_id), archive.read_bytes())
        self.client.put_object(
            world_hash_key(world_id), digest.encode("utf-8"), content_type="text/plain"
        )
        self._track(upload_count=1, uploaded_bytes=size)


LEASE_SECONDS = 300
