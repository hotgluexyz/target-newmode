"""Authentication helpers for New/Mode."""

from __future__ import annotations

import json
from datetime import datetime

import requests
from hotglue_singer_sdk.target_sdk.auth import OAuthAuthenticator


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

    def _update_access_token_locally(self) -> None:
        """Fetch a token the way the SDK does, without reading a refresh token.

        New/Mode's client-credentials grant returns no `refresh_token`, and the
        SDK implementation reads one unconditionally.
        """
        token_response = requests.post(
            self._auth_endpoint,
            data=self.oauth_request_body,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=300,
        )

        try:
            token_response.raise_for_status()
            self.logger.info("OAuth authorization attempt was successful.")
        except requests.HTTPError as ex:
            self.state.update({"auth_error_response": token_response.json()})
            raise RuntimeError(
                f"Failed OAuth login, response was '{token_response.json()}'. {ex}",
            )

        token_json = token_response.json()
        self.access_token = token_json["access_token"]

        self._config["access_token"] = token_json["access_token"]
        # Same clock as OAuthAuthenticator.is_token_valid, so the stored expiry compares correctly.
        now = round(datetime.utcnow().timestamp())  # noqa: DTZ003
        self._config["expires_in"] = int(token_json["expires_in"]) + now

        with open(self._config_file_path, "w") as outfile:
            json.dump(self._config, outfile, indent=4)
