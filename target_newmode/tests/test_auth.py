"""Unit tests for the New/Mode OAuth authenticator."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from target_newmode.auth import TOKEN_EXPIRY_BUFFER, NewModeAuthenticator


class _Target:
    name = "target-newmode"
    logger = logging.getLogger("test")

    def __init__(self, config: dict) -> None:
        self._config = config
        self._config_file_path = None


def _authenticator(config: dict) -> NewModeAuthenticator:
    return NewModeAuthenticator(_Target(config), {}, "https://base.newmode.net/oauth/token")


def _epoch_now() -> int:
    return round(datetime.now(timezone.utc).timestamp())


def test_oauth_request_body_uses_client_credentials() -> None:
    auth = _authenticator({"client_id": "id", "client_secret": "secret"})

    assert auth.oauth_request_body == {
        "grant_type": "client_credentials",
        "client_id": "id",
        "client_secret": "secret",
    }


def test_token_is_invalid_without_a_token_or_expiry() -> None:
    assert not _authenticator({}).is_token_valid()
    assert not _authenticator({"access_token": "abc"}).is_token_valid()
    assert not _authenticator({"expires_in": _epoch_now() + 300}).is_token_valid()


def test_token_validity_is_measured_against_a_real_utc_clock() -> None:
    """A local-time reading of the clock would make a stale token look valid."""
    fresh = _authenticator({"access_token": "abc", "expires_in": _epoch_now() + 300})
    stale = _authenticator({"access_token": "abc", "expires_in": _epoch_now() - 1})
    expiring = _authenticator(
        {"access_token": "abc", "expires_in": _epoch_now() + TOKEN_EXPIRY_BUFFER - 1},
    )

    assert fresh.is_token_valid()
    assert not stale.is_token_valid()
    assert not expiring.is_token_valid()
