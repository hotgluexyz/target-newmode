"""New/Mode target base sink with HTTP, JSON:API helpers, and entity caches."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

import requests
from hotglue_etl_exceptions import InvalidCredentialsError, InvalidPayloadError
from hotglue_singer_sdk.exceptions import FatalAPIError, RetriableAPIError
from hotglue_singer_sdk.plugin_base import PluginBase
from hotglue_singer_sdk.target_sdk.client import HotglueSink
from hotglue_singer_sdk.target_sdk.common import HGJSONEncoder

from target_newmode.contact_lookup import ContactLookupMixin

CONTACT_TYPE = "contact--contact"
CONTACT_GROUP_TYPE = "contact_group--contact_group"
ORGANIZATION_TYPE = "group--organization"

JSON_API_MEDIA_TYPE = "application/vnd.api+json"
DEFAULT_BASE_URL = "https://base.newmode.net"
PAGE_SIZE = 50


@dataclass
class _NewModeCache:
    contact_groups_by_title: dict[str, dict[str, Any]] = field(default_factory=dict)
    contact_groups_loaded: bool = False
    org_id: int | None = None
    org_uuid: str | None = None
    org_loaded: bool = False


class NewModeSink(ContactLookupMixin, HotglueSink):
    """Base sink for New/Mode JSON:API interactions."""

    auth_state: dict = {}
    page_size = PAGE_SIZE

    def __init__(
        self,
        target: PluginBase,
        stream_name: str,
        schema: dict,
        key_properties: list[str] | None,
    ) -> None:
        super().__init__(target, stream_name, schema, key_properties)
        self._cache = _NewModeCache()

    @property
    def api_base_url(self) -> str:
        return (self.config.get("api_base_url") or DEFAULT_BASE_URL).rstrip("/")

    @property
    def base_url(self) -> str:
        return f"{self.api_base_url}/jsonapi/"

    @property
    def endpoint(self) -> str:
        return "contact/contact"

    @property
    def http_headers(self) -> dict[str, str]:
        return {"Accept": JSON_API_MEDIA_TYPE}

    @property
    def authenticator(self) -> Any:
        authenticator, auth_endpoint = self._target.access_token_support(self._target)
        return authenticator(self._target, self.auth_state, auth_endpoint)

    def _request(
        self,
        http_method: str,
        endpoint: str,
        params: dict | None = None,
        request_data: Any = None,
        headers: dict | None = None,
        verify: bool = True,
    ) -> requests.Response:
        """Issue a request with the JSON:API media type.

        The SDK forces `Content-Type: application/json`, which New/Mode rejects
        with a 415, so the request is built here instead.
        """
        request_headers = dict(headers or {})
        request_headers.update(self.default_headers)
        request_headers["Content-Type"] = JSON_API_MEDIA_TYPE
        response = requests.request(
            method=http_method,
            url=self.url(endpoint),
            params={**(params or {}), **self.params},
            headers=request_headers,
            data=json.dumps(request_data, cls=HGJSONEncoder) if request_data else None,
            verify=verify,
            timeout=self.timeout,
        )
        self.validate_response(response)
        return response

    def validate_response(self, response: requests.Response) -> None:
        """Map New/Mode HTTP status codes to hotglue exception types."""
        if response.status_code in {401, 403}:
            raise InvalidCredentialsError(self.error_message(response))
        if response.status_code in {400, 409, 422}:
            raise InvalidPayloadError(self.error_message(response))
        if response.status_code in {429} or 500 <= response.status_code < 600:
            raise RetriableAPIError(self.response_error_message(response), response)
        if 400 <= response.status_code < 500:
            raise FatalAPIError(self.error_message(response))
        super().validate_response(response)

    @staticmethod
    def error_message(response: requests.Response) -> str:
        """Summarize the JSON:API `errors` array when the API returns one."""
        try:
            errors = response.json().get("errors")
        except ValueError:
            errors = None
        if not errors:
            return response.text or response.reason
        details = []
        for error in errors:
            if not isinstance(error, dict):
                continue
            pointer = (error.get("source") or {}).get("pointer")
            detail = error.get("detail") or error.get("title") or ""
            details.append(f"{pointer}: {detail}" if pointer else detail)
        return "; ".join(detail for detail in details if detail) or response.text

    def _paginate(self, path: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        """Fetch all pages of a JSON:API collection using offset pagination."""
        resources: list[dict[str, Any]] = []
        offset = 0
        while True:
            page_params = {
                **(params or {}),
                "page[limit]": self.page_size,
                "page[offset]": offset,
            }
            response = self.request_api("GET", endpoint=path, params=page_params)
            page = response.json().get("data") or []
            if not isinstance(page, list):
                break
            resources.extend(item for item in page if isinstance(item, dict))
            if len(page) < self.page_size:
                break
            offset += self.page_size
        return resources

    def ensure_org_loaded(self) -> None:
        """Resolve the organization the API credentials belong to."""
        if self._cache.org_loaded:
            return
        self._cache.org_id = self.config.get("org_id")
        self._cache.org_uuid = self.config.get("gid")
        if self._cache.org_id is None or self._cache.org_uuid is None:
            organizations = self._paginate("group/organization")
            if organizations:
                organization = organizations[0]
                if self._cache.org_uuid is None:
                    self._cache.org_uuid = organization.get("id")
                if self._cache.org_id is None:
                    self._cache.org_id = (organization.get("attributes") or {}).get(
                        "drupal_internal__id",
                    )
                self.logger.info(
                    "Resolved New/Mode organization '%s' (org_id %s)",
                    (organization.get("attributes") or {}).get("label"),
                    self._cache.org_id,
                )
        self._cache.org_loaded = True

    @property
    def org_id(self) -> int | None:
        """Numeric organization id stamped on every contact."""
        self.ensure_org_loaded()
        return self._cache.org_id

    @property
    def org_uuid(self) -> str | None:
        """Organization uuid used for the contact `gid` relationship."""
        self.ensure_org_loaded()
        return self._cache.org_uuid

    def ensure_contact_groups_loaded(self) -> None:
        """Lazy-load contact groups when list membership is first needed."""
        if self._cache.contact_groups_loaded:
            return
        self.logger.info("Loading contact groups into cache")
        for group in self._paginate("contact_group/contact_group"):
            self._register_contact_group(group)
        self._cache.contact_groups_loaded = True

    def _register_contact_group(self, group: dict[str, Any]) -> None:
        """Store a contact group under its title and lowercase alias."""
        title = (group.get("attributes") or {}).get("title")
        if not title:
            return
        self._cache.contact_groups_by_title[str(title)] = group
        self._cache.contact_groups_by_title[str(title).lower()] = group

    def get_contact_group(self, title: str) -> dict[str, Any] | None:
        """Return a contact group by title without creating it."""
        self.ensure_contact_groups_loaded()
        trimmed = title.strip()
        return self._cache.contact_groups_by_title.get(
            trimmed,
        ) or self._cache.contact_groups_by_title.get(trimmed.lower())

    def get_or_create_contact_group(self, title: str) -> dict[str, Any]:
        """Return a contact group by title, creating it when missing."""
        existing = self.get_contact_group(title)
        if existing:
            return existing
        trimmed = title.strip()
        self.logger.info("Creating contact group '%s'", trimmed)
        response = self.request_api(
            "POST",
            endpoint="contact_group/contact_group",
            request_data={
                "data": {
                    "type": CONTACT_GROUP_TYPE,
                    "attributes": {"title": trimmed, "org_id": self.org_id},
                },
            },
        )
        group = response.json().get("data") or {}
        self._register_contact_group(group)
        return group

    @staticmethod
    def build_resource(
        attributes: dict[str, Any],
        resource_id: str | None = None,
        relationships: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Wrap contact attributes in a JSON:API document."""
        resource: dict[str, Any] = {"type": CONTACT_TYPE, "attributes": attributes}
        if resource_id:
            resource["id"] = resource_id
        if relationships:
            resource["relationships"] = relationships
        return {"data": resource}

    @staticmethod
    def build_group_relationship(group_ids: list[str]) -> dict[str, Any]:
        """Build the `groups` relationship for a contact resource."""
        return {
            "groups": {
                "data": [
                    {"type": CONTACT_GROUP_TYPE, "id": group_id} for group_id in group_ids
                ],
            },
        }

    def build_org_relationship(self) -> dict[str, Any]:
        """Build the `gid` relationship linking a new contact to the organization."""
        if not self.org_uuid:
            return {}
        return {"gid": {"data": {"type": ORGANIZATION_TYPE, "id": self.org_uuid}}}

    def clean_null_values(self, data: Any) -> Any:
        """Remove null and blank values while preserving non-empty nested structures."""
        if not isinstance(data, dict):
            return data
        cleaned: dict[str, Any] = {}
        for key, value in data.items():
            if value is None:
                continue
            if isinstance(value, dict):
                nested = self.clean_null_values(value)
                if nested:
                    cleaned[key] = nested
            elif isinstance(value, list):
                if value:
                    cleaned[key] = value
            elif value != "":
                cleaned[key] = value
        return cleaned

    def merge_empty_fields(
        self,
        existing: dict[str, Any],
        incoming: dict[str, Any],
    ) -> dict[str, Any]:
        """Keep existing non-empty values and fill only empty fields from incoming data."""
        merged = dict(incoming)
        for key in incoming:
            existing_value = existing.get(key)
            if existing_value not in (None, ""):
                merged[key] = existing_value
        return merged
