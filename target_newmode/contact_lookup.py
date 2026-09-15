"""Contact lookup for New/Mode upserts, backed by JSON:API filters."""

from __future__ import annotations

from typing import Any

from hotglue_singer_sdk.exceptions import FatalAPIError

from target_newmode.unified_mapping import (
    LOOKUP_FIELD_TO_NEWMODE,
    SUPPORTED_LOOKUP_FIELDS,
    contact_lookup_value,
    unified_lookup_value,
    values_match,
)

DEFAULT_LOOKUP_FIELDS = ["id", "email"]
SEARCH_RESULT_LIMIT = 50


def flatten_contact(resource: dict[str, Any]) -> dict[str, Any]:
    """Flatten a JSON:API contact resource into a plain attribute dict."""
    if not isinstance(resource, dict):
        return {}
    flattened: dict[str, Any] = {"id": resource.get("id")}
    flattened.update(resource.get("attributes") or {})
    flattened["groups"] = group_ids(resource)
    return flattened


def group_ids(resource: dict[str, Any]) -> list[str]:
    """Return the contact group ids referenced by a JSON:API contact resource."""
    relationships = resource.get("relationships") or {}
    data = (relationships.get("groups") or {}).get("data") or []
    ids: list[str] = []
    for reference in data:
        if not isinstance(reference, dict):
            continue
        group_id = reference.get("id")
        # Drupal returns the sentinel id "missing" for deleted references.
        if group_id and group_id != "missing":
            ids.append(str(group_id))
    return ids


class ContactLookupMixin:
    """Resolve create vs update matches with JSON:API `filter` queries."""

    @property
    def lookup_method(self) -> str:
        method = (self.config.get("lookup_method") or "sequential").lower()
        return method if method in {"sequential", "all"} else "sequential"

    def get_lookup_fields(self) -> list[str]:
        """Return configured lookup fields for this stream, defaulting to id then email."""
        lookup_config = self.config.get("lookup_fields") or {}
        fields = lookup_config.get(self.name) or lookup_config.get("Contacts")
        if fields is None:
            fields = DEFAULT_LOOKUP_FIELDS
        if isinstance(fields, str):
            fields = [fields]
        supported = [field for field in fields if field in SUPPORTED_LOOKUP_FIELDS]
        unsupported = [field for field in fields if field not in SUPPORTED_LOOKUP_FIELDS]
        if unsupported:
            self.logger.warning(
                "Ignoring unsupported lookup fields: %s. Supported: %s",
                unsupported,
                ", ".join(sorted(SUPPORTED_LOOKUP_FIELDS)),
            )
        return supported

    def _record_field_value(self, record: dict[str, Any], field: str) -> Any:
        return unified_lookup_value(record, field)

    def _contact_matches_value(
        self,
        contact: dict[str, Any],
        field: str,
        expected: Any,
    ) -> bool:
        """Return whether a New/Mode contact matches a unified lookup field value."""
        return values_match(field, expected, contact_lookup_value(contact, field))

    def _lookup_fields_with_values(
        self,
        record: dict[str, Any],
        fields: list[str],
    ) -> list[tuple[str, Any]]:
        populated: list[tuple[str, Any]] = []
        for field in fields:
            value = self._record_field_value(record, field)
            if value not in (None, ""):
                populated.append((field, value))
        return populated

    def _search_contacts(self, filters: dict[str, Any]) -> list[dict[str, Any]]:
        """Run a filtered contact query and return flattened contacts."""
        params: dict[str, Any] = {"page[limit]": SEARCH_RESULT_LIMIT}
        for attribute, value in filters.items():
            params[f"filter[{attribute}]"] = value
        response = self.request_api("GET", endpoint=self.endpoint, params=params)
        resources = response.json().get("data") or []
        if not isinstance(resources, list):
            return []
        return [flatten_contact(resource) for resource in resources if isinstance(resource, dict)]

    def _pick_best_match(
        self,
        contacts: list[dict[str, Any]],
        record: dict[str, Any],
    ) -> dict[str, Any]:
        """Choose one contact when several match the same lookup values."""
        record_id = record.get("id")
        if record_id is not None:
            for contact in contacts:
                if str(contact.get("id")) == str(record_id):
                    return contact
        return max(contacts, key=lambda contact: str(contact.get("changed") or ""))

    def _warn_duplicates(
        self,
        fields: list[str],
        contacts: list[dict[str, Any]],
        chosen: dict[str, Any],
    ) -> None:
        self.logger.warning(
            "Lookup on %s matched multiple contacts; using contact id %s. Other ids: %s",
            fields,
            chosen.get("id"),
            [contact.get("id") for contact in contacts if contact is not chosen],
        )

    def _lookup_by_field(
        self,
        record: dict[str, Any],
        field: str,
    ) -> dict[str, Any] | None:
        """Find a contact using a single lookup field."""
        value = self._record_field_value(record, field)
        if value in (None, ""):
            return None
        if field == "id":
            return self._fetch_contact_by_id(str(value))

        matches = self._search_contacts({LOOKUP_FIELD_TO_NEWMODE[field]: value})
        if not matches:
            return None
        chosen = self._pick_best_match(matches, record)
        if len(matches) > 1:
            self._warn_duplicates([field], matches, chosen)
        return chosen

    def _lookup_by_all_fields(
        self,
        record: dict[str, Any],
        fields: list[str],
    ) -> dict[str, Any] | None:
        """Find a contact that matches every configured lookup field."""
        populated = self._lookup_fields_with_values(record, fields)
        if len(populated) != len(fields):
            return None

        contact_id = next((value for field, value in populated if field == "id"), None)
        if contact_id is not None:
            contact = self._fetch_contact_by_id(str(contact_id))
            if contact is None:
                return None
            for field, expected in populated:
                if field != "id" and not self._contact_matches_value(contact, field, expected):
                    return None
            return contact

        filters = {LOOKUP_FIELD_TO_NEWMODE[field]: value for field, value in populated}
        matches = self._search_contacts(filters)
        if not matches:
            return None
        chosen = self._pick_best_match(matches, record)
        if len(matches) > 1:
            self._warn_duplicates(fields, matches, chosen)
        return chosen

    def find_matching_contact(self, record: dict[str, Any]) -> dict[str, Any] | None:
        """Resolve an existing contact using the configured lookup fields."""
        fields = self.get_lookup_fields()
        if not fields:
            return None

        if self.lookup_method == "all":
            match = self._lookup_by_all_fields(record, fields)
            if match:
                self.logger.info(
                    "Found existing contact id %s matching all lookup fields %s",
                    match.get("id"),
                    fields,
                )
            return match

        for field in fields:
            match = self._lookup_by_field(record, field)
            if match:
                self.logger.info(
                    "Found existing contact id %s via lookup field '%s'",
                    match.get("id"),
                    field,
                )
                return match
        return None

    def _fetch_contact_by_id(self, contact_id: str) -> dict[str, Any] | None:
        """Fetch a contact by its New/Mode uuid."""
        try:
            response = self.request_api("GET", endpoint=f"{self.endpoint}/{contact_id}")
        except FatalAPIError:
            return None
        resource = response.json().get("data")
        if isinstance(resource, dict):
            return flatten_contact(resource)
        return None
