from __future__ import annotations

import logging

import pytest

from launcher import secrets


@pytest.fixture(autouse=True)
def _clean_secrets():
    secrets.clear_secrets()
    yield
    secrets.clear_secrets()


def test_redacts_url_userinfo() -> None:
    text = "clone https://alice:ghp_SUPERSECRETTOKEN@github.com/x/y.git failed"
    out = secrets.redact(text)
    assert "ghp_SUPERSECRETTOKEN" not in out
    assert "alice:***@" in out


def test_redacts_aws_key_id_and_sigv4() -> None:
    out = secrets.redact("key AKIAIOSFODNN7EXAMPLE used")
    assert "AKIAIOSFODNN7EXAMPLE" not in out

    header = "Authorization: AWS4-HMAC-SHA256 Credential=AKIAIOSFODNN7EXAMPLE/20260101, Signature=deadbeef"
    out = secrets.redact(header)
    assert "deadbeef" not in out
    assert "AKIAIOSFODNN7EXAMPLE" not in out


def test_redacts_bearer_and_sensitive_params() -> None:
    assert "abc123token" not in secrets.redact("sent Bearer abc123token")
    out = secrets.redact("GET /x?X-Amz-Signature=deadbeefdeadbeef&X-Amz-Credential=AKIA")
    assert "deadbeefdeadbeef" not in out
    assert "AKIA" not in out


def test_registered_exact_value_is_scrubbed() -> None:
    secrets.register_secret("bucketnameusedaspass")
    assert "bucketnameusedaspass" not in secrets.redact("login bucketnameusedaspass rejected")


def test_register_url_secrets_extracts_password() -> None:
    secrets.register_url_secrets("https://user:hunter2token@example.com/repo.git")
    assert "hunter2token" not in secrets.redact("url hunter2token leaked")


def test_redact_url_drops_userinfo_entirely() -> None:
    out = secrets.redact_url("https://user:ghp_TOKEN@github.com/owner/repo.git")
    assert out == "https://github.com/owner/repo.git"
    assert "ghp_TOKEN" not in out


def test_secret_value_unwraps_secretstr_and_plain() -> None:
    from pydantic import SecretStr

    assert secrets.secret_value(SecretStr("abc")) == "abc"
    assert secrets.secret_value("abc") == "abc"
    assert secrets.secret_value(None) == ""


def test_log_records_are_scrubbed(caplog) -> None:
    secrets.install_log_redaction()
    secrets.register_secret("supersecret123")
    logger = logging.getLogger("nomad.tests.redaction")
    with caplog.at_level(logging.INFO):
        logger.info("failed with %s in the message", "supersecret123")
    assert "supersecret123" not in caplog.text
    assert "***" in caplog.text


def test_log_scrubbing_leaves_ordinary_messages_intact(caplog) -> None:
    secrets.install_log_redaction()
    logger = logging.getLogger("nomad.tests.redaction")
    with caplog.at_level(logging.INFO):
        logger.info("world %s uploaded (%d bytes)", "w1", 1234)
    assert "world w1 uploaded (1234 bytes)" in caplog.text