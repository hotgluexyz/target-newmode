"""Unit tests for the New/Mode OAuth authenticator."""

from __future__ import annotations

import json
import logging

from target_newmode import auth
from target_newmode.auth import NewModeAuthenticator


class _Target:
    name = "target-newmode"
    logger = logging.getLogger("test")

    def __init__(self, config: dict, config_file_path: str) -> None:
        self._config = config
        self._config_file_path = config_file_path


class _TokenResponse:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self._payload


def _authenticator(config: dict, tmp_path) -> NewModeAuthenticator:
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps(config))
    return NewModeAuthenticator(
        _Target(config, str(config_path)),
        {},
        "https://base.newmode.net/oauth/token",
    )


def test_oauth_request_body_uses_client_credentials(tmp_path) -> None:
    authenticator = _authenticator({"client_id": "id", "client_secret": "secret"}, tmp_path)

    assert authenticator.oauth_request_body == {
        "grant_type": "client_credentials",
        "client_id": "id",
        "client_secret": "secret",
    }


def test_token_refresh_without_refresh_token_is_valid_for_the_sdk(tmp_path, monkeypatch) -> None:
    """New/Mode returns no refresh_token; the stored expiry must satisfy the SDK's own check."""
    monkeypatch.setattr(
        auth.requests,
        "post",
        lambda *args, **kwargs: _TokenResponse(
            {"token_type": "Bearer", "expires_in": 300, "access_token": "token-1"},
        ),
    )
    authenticator = _authenticator({"client_id": "id", "client_secret": "secret"}, tmp_path)

    authenticator.update_access_token()

    assert authenticator.is_token_valid()
    assert authenticator.auth_headers == {"Authorization": "Bearer token-1"}
    written = json.loads((tmp_path / "config.json").read_text())
    assert written["access_token"] == "token-1"
    assert "refresh_token" not in written
