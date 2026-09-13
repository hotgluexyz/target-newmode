"""Authentication helpers for New/Mode."""

from __future__ import annotations

import json
from datetime import datetime, timezone

import requests
from hotglue_etl_exceptions import InvalidCredentialsError
from hotglue_singer_sdk.target_sdk.auth import OAuthAuthenticator

TOKEN_REQUEST_TIMEOUT = 60
DEFAULT_TOKEN_LIFETIME = 300
# Refresh this many seconds before the token actually expires.
TOKEN_EXPIRY_BUFFER = 60


class NewModeAuthenticator(OAuthAuthenticator):
    """OAuth 2.0 client-credentials authenticator for the New/Mode Impact API."""

    @property
    def oauth_request_body(self) -> dict:
        """Return the client-credentials grant body for the New/Mode token endpoint."""
        return {
            "grant_type": "client_credentials",
            "client_id": self._config["client_id"],
            "client_secret": self._config["client_secret"],
        }

    @staticmethod
    def utc_now() -> int:
        """Return the current epoch seconds."""
        return round(datetime.now(timezone.utc).timestamp())

    def is_token_valid(self) -> bool:
        """Check the stored token against the expiry this class wrote.

        The SDK compares against `datetime.utcnow().timestamp()`, which is the
        local-time reading of a UTC wall clock and so is off by the machine's
        UTC offset. Both sides of the comparison are kept on a real UTC clock
        here instead.
        """
        access_token = self._config.get("access_token")
        expires_in = self._config.get("expires_in")
        if not access_token or not expires_in:
            return False
        return (int(expires_in) - self.utc_now()) > TOKEN_EXPIRY_BUFFER

    def _update_access_token_locally(self) -> None:
        """Fetch a new access token.

        New/Mode issues short-lived client-credentials tokens and returns no
        refresh token, so the SDK default (which requires one) cannot be used.
        """
        response = requests.post(
            self._auth_endpoint,
            data=self.oauth_request_body,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=TOKEN_REQUEST_TIMEOUT,
        )
        if not response.ok:
            self.state.update({"auth_error_response": self._error_body(response)})
            raise InvalidCredentialsError(
                f"Failed New/Mode OAuth login, response was '{self._error_body(response)}'.",
            )

        self.logger.info("OAuth authorization attempt was successful.")
        token_json = response.json()
        self.access_token = token_json["access_token"]
        expires_in = int(token_json.get("expires_in") or DEFAULT_TOKEN_LIFETIME)

        self._config["access_token"] = self.access_token
        self._config["expires_in"] = expires_in + self.utc_now()
        self._persist_config()

    def _persist_config(self) -> None:
        """Write the refreshed token back to the config file when one is available."""
        if not self._config_file_path:
            return
        try:
            with open(self._config_file_path, "w") as outfile:
                json.dump(self._config, outfile, indent=4)
        except OSError as ex:
            self.logger.warning("Could not persist access token to config: %s", ex)

    @staticmethod
    def _error_body(response: requests.Response) -> str:
        try:
            return json.dumps(response.json())
        except ValueError:
            return response.text or response.reason
