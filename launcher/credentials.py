"""Storage credential storage, using the OS keychain when one is available.

Secrets (S3 access/secret keys) are the one thing Nomad must keep but must
never leak. Preference order:

1. The platform keychain (macOS Keychain, Windows Credential Manager, or
   Secret Service on Linux), reached through the optional ``keyring`` package.
2. The ``0600`` settings file, as a fallback when no keychain is usable
   (headless servers and CI commonly have none).

Keychain use is best-effort and never fatal: any error degrades to the file
rather than blocking startup. Set ``NOMAD_KEYCHAIN=0`` to force file storage.
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

_SERVICE = "nomad"

# Field names in LauncherSettings that hold secrets, in settings-file key form.
SECRET_FIELDS: tuple[str, ...] = (
    "r2_access_key",
    "r2_secret_key",
    "server_access_key",
    "server_secret_key",
)


def keychain_enabled() -> bool:
    """Whether the user allows keychain storage (NOMAD_KEYCHAIN, default on)."""
    return os.environ.get("NOMAD_KEYCHAIN", "1").strip().lower() not in ("0", "false", "no", "off")


def _keyring():
    """Return the keyring module when it can actually store secrets, else None."""
    try:
        import keyring
        from keyring.backends import fail, null
    except Exception:  # not installed, or a broken platform backend
        return None
    try:
        backend = keyring.get_keyring()
    except Exception:
        return None
    # keyring falls back to fail/null backends when no real store exists; those
    # would silently lose every secret, so treat them as "no keychain".
    if isinstance(backend, (fail.Keyring, null.Keyring)):
        return None
    return keyring


def keychain_available() -> bool:
    """True when a real, usable keychain backend is present."""
    return _keyring() is not None


def get_secret(name: str) -> str | None:
    """Read a secret from the keychain, or None when unavailable/absent."""
    module = _keyring()
    if module is None:
        return None
    try:
        return module.get_password(_SERVICE, name) or None
    except Exception as exc:
        logger.debug("keychain read failed for %s: %s", name, exc)
        return None


def set_secret(name: str, value: str) -> bool:
    """Store a secret in the keychain. False when it could not be stored."""
    module = _keyring()
    if module is None:
        return False
    try:
        module.set_password(_SERVICE, name, value)
        return True
    except Exception as exc:
        logger.debug("keychain write failed for %s: %s", name, exc)
        return False


def delete_secret(name: str) -> None:
    """Best-effort removal of a secret from the keychain."""
    module = _keyring()
    if module is None:
        return
    try:
        module.delete_password(_SERVICE, name)
    except Exception:
        pass  # absent already, or no backend; nothing to clean up
