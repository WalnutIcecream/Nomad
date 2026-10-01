"""Shared test setup.

Secrets must never touch a developer's real OS keychain during a test run, so
the keychain path is disabled for the whole suite; tests that exercise it do so
against an injected fake (see test_credentials.py).
"""

from __future__ import annotations

import os

os.environ["NOMAD_KEYCHAIN"] = "0"
