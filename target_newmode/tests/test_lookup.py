"""Unit tests for contact lookup and unified field mapping."""

from __future__ import annotations

import logging
from typing import Any

from target_newmode.contact_lookup import ContactLookupMixin, flatten_contact, group_ids
from target_newmode.sinks import EXISTING_GROUPS_KEY, ID_KEY, ContactsSink
from target_newmode.unified_mapping import (
    build_contact_payload,
    coerce_contact_value,
    contact_lookup_value,
    extract_phones,
    subscribe_status_to_opt_in,
    unified_lookup_value,
    values_match,
)


class _LookupSink(ContactLookupMixin):
    """Minimal mixin host that records the queries a lookup would issue."""

    name = "Contacts"

    def __init__(self, config: dict | None = None, results: list[list[dict]] | None = None) -> None:
        self.config = config or {}
        self.logger = logging.getLogger("test")
        self.searches: list[dict[str, Any]] = []
        self.fetched_ids: list[str] = []
        self._results = list(results or [])

    def _search_contacts(self, filters: dict[str, Any]) -> list[dict[str, Any]]:
        self.searches.append(filters)
        return self._results.pop(0) if self._results else []

    def _fetch_contact_by_id(self, contact_id: str) -> dict[str, Any] | None:
        self.fetched_ids.append(contact_id)
        return self._results.pop(0)[0] if self._results else None


def test_flatten_contact_drops_missing_group_references() -> None:
    resource = {
        "id": "uuid-1",
        "attributes": {"email": "user@example.com", "first_name": "Ada"},
        "relationships": {
            "groups": {
                "data": [
                    {"type": "contact_group--contact_group", "id": "missing"},
                    {"type": "contact_group--contact_group", "id": "group-1"},
                ],
            },
        },
    }

    contact = flatten_contact(resource)

    assert contact["id"] == "uuid-1"
    assert contact["email"] == "user@example.com"
    assert contact["groups"] == ["group-1"]
    assert group_ids(resource) == ["group-1"]


def test_extract_phones_promotes_the_only_number_to_mobile() -> None:
    record = {"phone_numbers": [{"type": "work", "number": "15551230000"}]}

    assert extract_phones(record) == ("15551230000", None)


def test_extract_phones_keeps_mobile_and_alternate_and_skips_fax() -> None:
    record = {
        "phone_numbers": [
            {"type": "fax", "number": "15550000000"},
            {"type": "home", "number": "15551111111"},
            {"type": "mobile", "number": "15552222222"},
        ],
    }

    assert extract_phones(record) == ("15552222222", "15551111111")


def test_build_contact_payload_maps_unified_fields() -> None:
    record = {
        "first_name": "Alex",
        "last_name": "Actiontaker",
        "email": "alex@example.com",
        "salutation": "Dr",
        "subscribe_status": "subscribed",
        "phone_numbers": [{"type": "mobile", "number": "15551231234"}],
        "addresses": [
            {
                "line1": "1234 Park Avenue",
                "city": "New York",
                "state": "NY",
                "postal_code": "10001",
                "country": "USA",
            },
        ],
        "custom_fields": [
            {"name": "twitter_handle", "value": "allAction"},
            {"name": "loyalty_tier", "value": "gold"},
        ],
    }

    payload, unmapped = build_contact_payload(record)

    assert payload["first_name"] == "Alex"
    assert payload["honorific"] == "Dr"
    assert payload["mobile_phone"] == "15551231234"
    assert payload["street_address"] == "1234 Park Avenue"
    assert payload["region"] == "NY"
    assert payload["postal_code"] == "10001"
    assert payload["country"] == "USA"
    assert payload["opt_in"] is True
    assert payload["twitter_handle"] == "allAction"
    assert unmapped == ["loyalty_tier"]


def test_subscribe_status_maps_to_opt_in() -> None:
    assert subscribe_status_to_opt_in("subscribed") is True
    assert subscribe_status_to_opt_in("unsubscribed") is False
    assert subscribe_status_to_opt_in(None) is None


def test_coerce_contact_value_casts_api_types() -> None:
    assert coerce_contact_value("opt_in", "true") is True
    assert coerce_contact_value("nm_marketing_opt_in", 0) is False
    assert coerce_contact_value("latitude", "49.28") == 49.28
    assert coerce_contact_value("org_id", "98") == 98
    assert coerce_contact_value("first_name", "") is None


def test_lookup_values_round_trip_between_record_and_contact() -> None:
    record = {
        "email": "User@Example.com",
        "phone_numbers": [{"type": "mobile", "number": "15551230000"}],
        "addresses": [{"line1": "9 Unified Way", "city": "Portland", "state": "OR",
                       "postal_code": "97205", "country": "US"}],
    }
    contact = {
        "email": "user@example.com",
        "mobile_phone": "15551230000",
        "street_address": "9 Unified Way",
        "city": "Portland",
        "region": "OR",
        "postal_code": "97205",
        "country": "US",
    }

    for unified_field in ("email", "mobile", "line1", "city", "state", "postal_code", "country"):
        expected = unified_lookup_value(record, unified_field)
        actual = contact_lookup_value(contact, unified_field)
        assert values_match(unified_field, expected, actual), unified_field


def test_get_lookup_fields_drops_unsupported_fields() -> None:
    sink = _LookupSink({"lookup_fields": {"Contacts": ["email", "website"]}})

    assert sink.get_lookup_fields() == ["email"]


def test_sequential_lookup_stops_at_the_first_match() -> None:
    sink = _LookupSink(
        {"lookup_fields": {"Contacts": ["email", "mobile"]}},
        results=[[{"id": "uuid-1", "email": "user@example.com"}]],
    )

    match = sink.find_matching_contact(
        {"email": "user@example.com", "phone_numbers": [{"type": "mobile", "number": "1555"}]},
    )

    assert match["id"] == "uuid-1"
    assert sink.searches == [{"email": "user@example.com"}]


def test_all_lookup_requires_every_field_and_filters_together() -> None:
    sink = _LookupSink(
        {"lookup_method": "all", "lookup_fields": {"Contacts": ["email", "postal_code"]}},
        results=[[{"id": "uuid-2"}]],
    )
    record = {
        "email": "user@example.com",
        "addresses": [{"postal_code": "97205"}],
    }

    match = sink.find_matching_contact(record)

    assert match["id"] == "uuid-2"
    assert sink.searches == [{"email": "user@example.com", "postal_code": "97205"}]

    incomplete = _LookupSink(
        {"lookup_method": "all", "lookup_fields": {"Contacts": ["email", "postal_code"]}},
    )
    assert incomplete.find_matching_contact({"email": "user@example.com"}) is None
    assert incomplete.searches == []


def test_pick_best_match_prefers_the_most_recently_changed() -> None:
    sink = _LookupSink()
    contacts = [{"id": "old", "changed": "1700000000"}, {"id": "new", "changed": "1800000000"}]

    assert sink._pick_best_match(contacts, {})["id"] == "new"
    assert sink._pick_best_match(contacts, {"id": "old"})["id"] == "old"


def test_parse_list_entries_normalizes_strings_and_objects() -> None:
    entries = ContactsSink.parse_list_entries(
        ContactsSink,
        {
            "subscribe_status": "unsubscribed",
            "lists": [
                "Volunteers",
                {"name": "Donors", "subscription_status": "subscribed"},
                {"title": "Lapsed"},
                {"name": "   "},
            ],
        },
    )

    assert entries == [
        {"title": "Volunteers", "subscription_status": "unsubscribed"},
        {"title": "Donors", "subscription_status": "subscribed"},
        {"title": "Lapsed", "subscription_status": "unsubscribed"},
    ]


def test_record_hash_ignores_lookup_results_but_not_lists() -> None:
    sink = ContactsSink.__new__(ContactsSink)
    record = {"email": "alex@example.com", "first_name": "Alex", "org_id": 5493}

    sink._pending_groups = [{"title": "Volunteers", "subscription_status": "subscribed"}]
    first_run = sink.build_record_hash(dict(record))
    replay = sink.build_record_hash(
        {**record, ID_KEY: "uuid-1", EXISTING_GROUPS_KEY: ["group-1", "group-2"]},
    )

    sink._pending_groups = [{"title": "Volunteers", "subscription_status": "unsubscribed"}]
    different_lists = sink.build_record_hash(dict(record))

    assert first_run == replay
    assert first_run != different_lists
