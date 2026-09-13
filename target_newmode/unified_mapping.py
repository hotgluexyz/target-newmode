"""Unified CRM field names mapped to New/Mode contact API fields."""

from __future__ import annotations

from typing import Any

# Standard New/Mode contact attributes (JSON:API `contact--contact`).
NEWMODE_NATIVE_CONTACT_FIELDS = frozenset(
    {
        "org_id",
        "honorific",
        "first_name",
        "last_name",
        "name_suffix",
        "email",
        "mobile_phone",
        "alternate_phone",
        "twitter_handle",
        "street_address",
        "city",
        "region",
        "country",
        "postal_code",
        "latitude",
        "longitude",
        "opt_in",
        "nm_product_opt_in",
        "nm_marketing_opt_in",
    },
)

# Unified field name -> New/Mode contact attribute.
UNIFIED_TO_NEWMODE: dict[str, str] = {
    "salutation": "honorific",
    "title": "honorific",
    "suffix": "name_suffix",
    "twitter": "twitter_handle",
    "state": "region",
    "address": "street_address",
}

# Address sub-fields: unified address dict key -> New/Mode contact attribute.
ADDRESS_FIELD_TO_NEWMODE: dict[str, str] = {
    "line1": "street_address",
    "city": "city",
    "state": "region",
    "postal_code": "postal_code",
    "postalCode": "postal_code",
    "country": "country",
}

# Unified lookup field -> New/Mode contact attribute used in `filter[...]`.
LOOKUP_FIELD_TO_NEWMODE: dict[str, str] = {
    "id": "id",
    "email": "email",
    "first_name": "first_name",
    "last_name": "last_name",
    "mobile": "mobile_phone",
    "mobile_phone": "mobile_phone",
    "phone": "mobile_phone",
    "alternate_phone": "alternate_phone",
    "twitter_handle": "twitter_handle",
    "street_address": "street_address",
    "line1": "street_address",
    "city": "city",
    "state": "region",
    "region": "region",
    "postal_code": "postal_code",
    "postalCode": "postal_code",
    "country": "country",
}

SUPPORTED_LOOKUP_FIELDS = frozenset(LOOKUP_FIELD_TO_NEWMODE)

BOOLEAN_CONTACT_FIELDS = frozenset(
    {"opt_in", "nm_product_opt_in", "nm_marketing_opt_in"},
)
FLOAT_CONTACT_FIELDS = frozenset({"latitude", "longitude"})
INTEGER_CONTACT_FIELDS = frozenset({"org_id"})

NON_DIALABLE_PHONE_TYPES = {"fax", "pager"}
MOBILE_PHONE_TYPES = {"mobile", "cell", "cellphone"}

UNSUBSCRIBED_STATUSES = {"unsubscribed", "unsubscribe", "opt_out", "opt-out", "false"}


def newmode_field_name(unified_field: str) -> str:
    """Return the New/Mode contact attribute used for a unified field name."""
    if unified_field in LOOKUP_FIELD_TO_NEWMODE:
        return LOOKUP_FIELD_TO_NEWMODE[unified_field]
    return UNIFIED_TO_NEWMODE.get(unified_field, unified_field)


def coerce_contact_value(field: str, value: Any) -> Any:
    """Coerce a value to the type the New/Mode contact attribute expects."""
    if value is None or value == "":
        return None
    if field in BOOLEAN_CONTACT_FIELDS:
        return _coerce_boolean(value)
    if field in FLOAT_CONTACT_FIELDS:
        try:
            return float(value)
        except (TypeError, ValueError):
            return None
    if field in INTEGER_CONTACT_FIELDS:
        try:
            return int(value)
        except (TypeError, ValueError):
            return None
    return value


def _coerce_boolean(value: Any) -> Any:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    lowered = str(value).strip().lower()
    if lowered in {"true", "1", "yes", "y", "subscribed"}:
        return True
    if lowered in {"false", "0", "no", "n", "unsubscribed"}:
        return False
    return None


def extract_phones(record: dict[str, Any]) -> tuple[str | None, str | None]:
    """Map unified phone_numbers to New/Mode mobile_phone and alternate_phone values."""
    mobile_phone = None
    alternate_phone = None
    for phone in record.get("phone_numbers") or []:
        if not isinstance(phone, dict):
            continue
        number = phone.get("number")
        if not number:
            continue
        phone_type = str(phone.get("type") or "").lower()
        if phone_type in NON_DIALABLE_PHONE_TYPES:
            continue
        if phone_type in MOBILE_PHONE_TYPES and not mobile_phone:
            mobile_phone = str(number)
        elif not alternate_phone:
            alternate_phone = str(number)
    if not mobile_phone and alternate_phone:
        # New/Mode dials mobile_phone, so promote the only number available.
        mobile_phone, alternate_phone = alternate_phone, None
    return mobile_phone, alternate_phone


def _first_address(record: dict[str, Any]) -> dict[str, Any]:
    addresses = record.get("addresses") or []
    if addresses and isinstance(addresses[0], dict):
        return addresses[0]
    return {}


def subscribe_status_to_opt_in(status: Any) -> bool | None:
    """Map the unified subscribe_status to the New/Mode opt_in flag."""
    if status is None or status == "":
        return None
    return str(status).strip().lower() not in UNSUBSCRIBED_STATUSES


def build_contact_payload(record: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """Map a unified contact record to New/Mode contact attributes.

    Returns the attributes plus the names of custom fields that have no
    New/Mode equivalent, so the caller can report them.
    """
    mobile_phone, alternate_phone = extract_phones(record)
    payload: dict[str, Any] = {
        "first_name": record.get("first_name"),
        "last_name": record.get("last_name"),
        "email": record.get("email"),
        "honorific": record.get("salutation") or record.get("honorific"),
        "name_suffix": record.get("suffix") or record.get("name_suffix"),
        "twitter_handle": record.get("twitter_handle"),
        "mobile_phone": mobile_phone,
        "alternate_phone": alternate_phone,
    }

    address = _first_address(record)
    if address:
        for address_key, newmode_key in ADDRESS_FIELD_TO_NEWMODE.items():
            value = address.get(address_key)
            if value not in (None, ""):
                payload[newmode_key] = value
        for coordinate in ("latitude", "longitude"):
            payload[coordinate] = coerce_contact_value(coordinate, address.get(coordinate))

    opt_in = subscribe_status_to_opt_in(record.get("subscribe_status"))
    if opt_in is not None:
        payload["opt_in"] = opt_in

    unmapped_custom_fields: list[str] = []
    for custom_field in record.get("custom_fields") or []:
        if not isinstance(custom_field, dict):
            continue
        field_name = custom_field.get("name")
        if not field_name:
            continue
        newmode_key = newmode_field_name(str(field_name))
        if newmode_key not in NEWMODE_NATIVE_CONTACT_FIELDS:
            unmapped_custom_fields.append(str(field_name))
            continue
        payload[newmode_key] = coerce_contact_value(newmode_key, custom_field.get("value"))

    return payload, unmapped_custom_fields


def unified_lookup_value(record: dict[str, Any], unified_field: str) -> Any:
    """Read a lookup value from a unified contact record."""
    if unified_field == "id":
        return record.get("id")
    if unified_field == "email" and record.get("email"):
        return str(record["email"]).strip().lower()
    if unified_field in {"mobile", "mobile_phone", "phone"}:
        return extract_phones(record)[0]
    if unified_field == "alternate_phone":
        return extract_phones(record)[1]

    newmode_key = newmode_field_name(unified_field)
    address = _first_address(record)
    for address_key, address_newmode_key in ADDRESS_FIELD_TO_NEWMODE.items():
        if newmode_key == address_newmode_key:
            value = address.get(address_key)
            if value not in (None, ""):
                return value
    return record.get(unified_field)


def contact_lookup_value(contact: dict[str, Any], unified_field: str) -> Any:
    """Read a comparable lookup value from a flattened New/Mode contact."""
    if unified_field == "id":
        return contact.get("id")
    if unified_field == "email" and contact.get("email"):
        return str(contact["email"]).strip().lower()
    return contact.get(newmode_field_name(unified_field))


def values_match(unified_field: str, expected: Any, actual: Any) -> bool:
    """Compare lookup values from a unified record and a New/Mode contact."""
    if actual in (None, "") or expected in (None, ""):
        return False
    if unified_field == "email":
        return str(actual).strip().lower() == str(expected).strip().lower()
    return str(actual) == str(expected)
