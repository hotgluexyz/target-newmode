"""New/Mode target sink classes."""

from __future__ import annotations

from typing import Any

from hotglue_etl_exceptions import InvalidPayloadError

from target_newmode.client import NewModeSink
from target_newmode.contact_lookup import flatten_contact
from target_newmode.unified_mapping import (
    UNSUBSCRIBED_STATUSES,
    build_contact_payload,
)

ID_KEY = "_newmode_id"
EXISTING_GROUPS_KEY = "_newmode_existing_groups"
INTERNAL_KEYS = frozenset({ID_KEY, EXISTING_GROUPS_KEY})


class ContactsSink(NewModeSink):
    """Unified Contacts sink for New/Mode."""

    name = "Contacts"

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._pending_groups: list[dict[str, str]] = []
        self._unsupported_fields_logged: set[str] = set()

    def build_record_hash(self, record: dict) -> str:
        """Hash the incoming intent, not the lookup results.

        The SDK dedupes replays by this hash. The internal lookup keys change
        with whatever is already in New/Mode, and list membership lives on the
        sink rather than the record, so both are normalized here to keep the
        hash stable for the same input.
        """
        hashable = {key: value for key, value in record.items() if key not in INTERNAL_KEYS}
        hashable["_lists"] = self._pending_groups
        return super().build_record_hash(hashable)

    @staticmethod
    def _normalize_subscription_status(status: Any) -> str:
        if not status:
            return "subscribed"
        normalized = str(status).strip().lower()
        if normalized in UNSUBSCRIBED_STATUSES:
            return "unsubscribed"
        return "subscribed"

    def parse_list_entries(self, record: dict[str, Any]) -> list[dict[str, str]]:
        """Normalize unified lists into contact group actions with subscription status."""
        entries: list[dict[str, str]] = []
        default_status = self._normalize_subscription_status(record.get("subscribe_status"))
        for item in record.get("lists") or []:
            if isinstance(item, str):
                title = item.strip()
                if title:
                    entries.append({"title": title, "subscription_status": default_status})
            elif isinstance(item, dict):
                title = (item.get("name") or item.get("title") or item.get("list_name") or "").strip()
                if not title:
                    continue
                status = item.get("subscription_status") or item.get("subscribe_status")
                entries.append(
                    {
                        "title": title,
                        "subscription_status": self._normalize_subscription_status(
                            status or default_status,
                        ),
                    },
                )
        return entries

    def _warn_unsupported(self, field: str, message: str) -> None:
        """Warn once per job about unified fields New/Mode contacts cannot store."""
        if field in self._unsupported_fields_logged:
            return
        self.logger.warning(message)
        self._unsupported_fields_logged.add(field)

    def _resolve_country(self, payload: dict[str, Any]) -> None:
        """Apply the configured default country when a record has none."""
        if payload.get("country"):
            return
        default_country = self.config.get("default_country")
        if default_country:
            payload["country"] = default_country
            return
        raise InvalidPayloadError(
            "New/Mode requires a country. Provide addresses[].country on the record "
            "or set default_country in the target config.",
        )

    def preprocess_record(self, record: dict[str, Any], context: dict) -> dict:
        """Map, lookup, and merge a unified record before writing to New/Mode."""
        self._pending_groups = self.parse_list_entries(record)

        payload, unmapped_custom_fields = build_contact_payload(record)
        if unmapped_custom_fields:
            self._warn_unsupported(
                "custom_fields",
                "New/Mode contacts have no custom fields; ignoring "
                f"{sorted(set(unmapped_custom_fields))}",
            )
        if record.get("tags"):
            self._warn_unsupported(
                "tags",
                "New/Mode contacts have no tags; use `lists` to assign contact groups.",
            )

        matching_contact = self.find_matching_contact(record)
        if matching_contact and self.config.get("only_upsert_empty_fields"):
            payload = self.merge_empty_fields(matching_contact, payload)

        self._resolve_country(payload)
        payload["org_id"] = self.org_id
        if payload["org_id"] is None:
            raise InvalidPayloadError(
                "Could not resolve a New/Mode org_id. Set org_id in the target config.",
            )

        payload = self.clean_null_values(payload)

        if matching_contact:
            payload[ID_KEY] = matching_contact.get("id")
            payload[EXISTING_GROUPS_KEY] = matching_contact.get("groups") or []

        return payload

    def resolve_group_ids(self, existing_group_ids: list[str]) -> list[str]:
        """Merge current membership with the pending list entries."""
        group_ids = list(dict.fromkeys(str(group_id) for group_id in existing_group_ids))
        for entry in self._pending_groups:
            if entry["subscription_status"] == "unsubscribed":
                group = self.get_contact_group(entry["title"])
                group_id = str(group.get("id")) if group else None
                if group_id and group_id in group_ids:
                    group_ids.remove(group_id)
                continue
            group = self.get_or_create_contact_group(entry["title"])
            group_id = group.get("id")
            if group_id is not None and str(group_id) not in group_ids:
                group_ids.append(str(group_id))
        return group_ids

    def upsert_record(self, record: dict, context: dict):
        """Create or update a contact, including its contact group membership."""
        state_dict: dict[str, Any] = {}
        contact_id = record.pop(ID_KEY, None)
        existing_group_ids = record.pop(EXISTING_GROUPS_KEY, [])

        relationships: dict[str, Any] = {}
        if self._pending_groups:
            relationships.update(
                self.build_group_relationship(self.resolve_group_ids(existing_group_ids)),
            )

        if contact_id:
            payload = self.build_resource(record, resource_id=contact_id, relationships=relationships)
            response = self.request_api(
                "PATCH",
                endpoint=f"contact/contact/{contact_id}",
                request_data=payload,
            )
            state_dict["is_updated"] = True
        else:
            relationships.update(self.build_org_relationship())
            payload = self.build_resource(record, relationships=relationships)
            response = self.request_api("POST", endpoint="contact/contact", request_data=payload)

        contact = flatten_contact(response.json().get("data") or {})
        state_dict["success"] = response.ok
        return contact.get("id") or contact_id, response.ok, state_dict
