"""Credential redaction and log scrubbing.

The rule this module enforces: **a secret value must never reach a log record,
a CLI output line, or an exception detail.** Two mechanisms:

* :func:`redact` masks patterns that look like credentials (URL userinfo, SigV4
  authorization, AWS-style key ids, bearer tokens, signed query params) plus any
  exact value previously passed to :func:`register_secret`.
* :func:`install_log_redaction` replaces the global log-record factory so every
  record is redacted at creation, regardless of which logger or handler emits
  it. A per-handler filter is also attached as defense in depth.

Registered exact values matter because many secrets match no pattern (a bucket
name reused as a password, a short token). ``register_secret`` is called from
``load_settings_file`` for every configured secret.
"""

from __future__ import annotations

import logging
import re

REDACTED = "***"

# Exact values to scrub wherever they appear. Populated at runtime.
_secrets: set[str] = set()

# scheme://user:secret@host  ->  scheme://user:***@host
_URL_USERINFO = re.compile(
    r"(?P<scheme>[a-zA-Z][a-zA-Z0-9+.\-]*://)(?P<user>[^/@\s:]+):(?P<secret>[^/@\s]+)@"
)
# scheme://[userinfo@]host/rest  (used to drop userinfo entirely)
_URL_PARTS = re.compile(
    r"^(?P<scheme>[a-zA-Z][a-zA-Z0-9+.\-]*://)(?:[^/@\s]*@)?(?P<host>[^/\s]+)(?P<rest>.*)$"
)
# The whole SigV4 Authorization value.
_AWS4 = re.compile(r"AWS4-HMAC-SHA256\s+[^\r\n]+")
_AUTH_HEADER = re.compile(r"(?i)\bAuthorization\s*:\s*[^\r\n]+")
_AWS_KEY_ID = re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")
_BEARER = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._\-+/=]{8,}")
# Signed-URL / credential query params.
_SENSITIVE_PARAM = re.compile(
    r"(?i)\b(X-Amz-Signature|X-Amz-Credential|X-Amz-Security-Token|"
    r"access_token|api[_-]?key|secret|password|passwd)=([^&\s\"']+)"
)


def register_secret(value: object) -> None:
    """Remember an exact secret value so it is scrubbed wherever it appears.

    Short values are ignored to avoid redacting ordinary words.
    """
    if value is None:
        return
    text = secret_value(value)
    if len(text) >= 4:
        _secrets.add(text)


def register_url_secrets(url: str | None) -> None:
    """Register the password embedded in ``scheme://user:pass@host`` URLs."""
    if not url:
        return
    match = _URL_USERINFO.search(url)
    if match is not None:
        register_secret(match.group("secret"))


def clear_secrets() -> None:
    """Forget all registered secrets (used by tests)."""
    _secrets.clear()


def secret_value(value: object) -> str:
    """Unwrap a ``SecretStr`` (or coerce any value) to its plain string."""
    getter = getattr(value, "get_secret_value", None)
    if callable(getter):
        return str(getter())
    return "" if value is None else str(value)


def redact(text: str | None) -> str:
    """Return ``text`` with anything credential-shaped masked."""
    if not text:
        return text or ""
    out = _URL_USERINFO.sub(
        lambda m: f"{m.group('scheme')}{m.group('user')}:{REDACTED}@", text
    )
    out = _AWS4.sub(f"AWS4-HMAC-SHA256 {REDACTED}", out)
    out = _AUTH_HEADER.sub(f"Authorization: {REDACTED}", out)
    out = _AWS_KEY_ID.sub(REDACTED, out)
    out = _BEARER.sub(f"Bearer {REDACTED}", out)
    out = _SENSITIVE_PARAM.sub(lambda m: f"{m.group(1)}={REDACTED}", out)
    for secret in _secrets:
        if secret and secret in out:
            out = out.replace(secret, REDACTED)
    return out


def redact_url(url: str | None) -> str:
    """Return a URL with any userinfo removed (``scheme://host/rest``).

    Used for display: an HTTPS git remote often carries a personal access token
    as ``https://user:TOKEN@host/...``; this drops it entirely.
    """
    if not url:
        return ""
    match = _URL_PARTS.match(url)
    if match is None:
        return redact(url)
    return f"{match.group('scheme')}{match.group('host')}{match.group('rest')}"


class SecretFilter(logging.Filter):
    """Handler-level filter that rewrites each record's rendered message."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:
            return True
        redacted = redact(message)
        record.msg = redacted
        record.args = ()
        return True


_factory_installed = False
_original_factory = logging.getLogRecordFactory()


def _redacting_factory(*args, **kwargs) -> logging.LogRecord:
    record = _original_factory(*args, **kwargs)
    try:
        message = record.getMessage()
    except Exception:
        return record
    record.msg = redact(message)
    record.args = ()
    return record


def install_log_redaction() -> None:
    """Scrub every future log record, wherever it is emitted.

    Filters attached to a logger only see records logged *directly* on that
    logger, and root-handler filters miss child loggers, so the only reliable
    hook is the record factory. A handler filter is added as well for any
    handlers that already exist.
    """
    global _factory_installed
    if not _factory_installed:
        logging.setLogRecordFactory(_redacting_factory)
        _factory_installed = True
    for handler in list(logging.getLogger().handlers):
        if not any(isinstance(f, SecretFilter) for f in handler.filters):
            handler.addFilter(SecretFilter())