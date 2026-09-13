"""End-to-end tests against the New/Mode sandbox API.

Requires a base config at .secrets/config.json with client_id and client_secret.
Test-specific lookup and feature flags are merged in at runtime.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
import requests

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = REPO_ROOT / ".secrets" / "config.json"
SAMPLE_PAYLOAD_DIR = REPO_ROOT / "sample_payload"
DEFAULT_BASE_URL = "https://base.newmode.net"
JSON_API_MEDIA_TYPE = "application/vnd.api+json"

CREATE_EMAIL = "hotglue-e2e-create-newmode@example.com"
UPSERT_EMAIL = "hotglue-e2e-upsert-newmode@example.com"
LOOKUP_EMAIL = "hotglue-e2e-lookup-newmode@example.com"
UNIFIED_LOOKUP_EMAIL = "hotglue-e2e-unified-lookup-newmode@example.com"
UNIFIED_LOOKUP_UPDATED_EMAIL = "hotglue-e2e-unified-lookup-different@example.com"
EMPTY_FIELDS_EMAIL = "hotglue-e2e-empty-fields-newmode@example.com"
CREATE_PHONE = "15551000001"
UPSERT_PHONE = "15551000002"
UNIFIED_LOOKUP_PHONE = "15551000005"
E2E_GROUP_TITLE = "hg-e2e-group-newmode"

TEST_EMAILS = (
    CREATE_EMAIL,
    UPSERT_EMAIL,
    LOOKUP_EMAIL,
    UNIFIED_LOOKUP_EMAIL,
    UNIFIED_LOOKUP_UPDATED_EMAIL,
    EMPTY_FIELDS_EMAIL,
)


@dataclass(frozen=True)
class TargetRun:
    result: subprocess.CompletedProcess
    log_path: Path


def _load_config() -> dict:
    if not CONFIG_PATH.exists():
        pytest.skip(
            f"Missing sandbox config at {CONFIG_PATH}. "
            "Create it with client_id and client_secret to run e2e tests.",
        )
    return json.loads(CONFIG_PATH.read_text())


def _write_config(base_config: dict, path: Path, **overrides: Any) -> Path:
    path.write_text(json.dumps({**base_config, **overrides}, indent=2))
    return path


def _api_base(config: dict) -> str:
    return (config.get("api_base_url") or DEFAULT_BASE_URL).rstrip("/")


def _access_token(config: dict) -> str:
    response = requests.post(
        f"{_api_base(config)}/oauth/token",
        data={
            "grant_type": "client_credentials",
            "client_id": config["client_id"],
            "client_secret": config["client_secret"],
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=60,
    )
    response.raise_for_status()
    return response.json()["access_token"]


def _auth_headers(config: dict) -> dict:
    return {
        "Authorization": f"Bearer {_access_token(config)}",
        "Accept": JSON_API_MEDIA_TYPE,
        "Content-Type": JSON_API_MEDIA_TYPE,
    }


def _find_contacts_by_email(config: dict, email: str) -> list[dict]:
    response = requests.get(
        f"{_api_base(config)}/jsonapi/contact/contact",
        headers=_auth_headers(config),
        params={"filter[email]": email, "page[limit]": 50},
        timeout=120,
    )
    response.raise_for_status()
    return [item for item in response.json().get("data") or [] if isinstance(item, dict)]


def _get_contact(config: dict, contact_id: str) -> dict:
    response = requests.get(
        f"{_api_base(config)}/jsonapi/contact/contact/{contact_id}",
        headers=_auth_headers(config),
        timeout=120,
    )
    response.raise_for_status()
    return response.json()["data"]


def _attributes(config: dict, contact_id: str) -> dict:
    return _get_contact(config, contact_id).get("attributes") or {}


def _group_ids(contact: dict) -> set[str]:
    data = ((contact.get("relationships") or {}).get("groups") or {}).get("data") or []
    return {
        str(reference["id"])
        for reference in data
        if isinstance(reference, dict) and reference.get("id") not in (None, "missing")
    }


def _delete_contact(config: dict, contact_id: str) -> None:
    response = requests.delete(
        f"{_api_base(config)}/jsonapi/contact/contact/{contact_id}",
        headers=_auth_headers(config),
        timeout=120,
    )
    if response.status_code not in {200, 202, 204, 404}:
        response.raise_for_status()


def _cleanup_test_contacts(config: dict) -> None:
    for email in TEST_EMAILS:
        for contact in _find_contacts_by_email(config, email):
            contact_id = contact.get("id")
            if contact_id:
                _delete_contact(config, str(contact_id))


def _run_target(config_path: Path, payload_path: Path, log_path: Path) -> TargetRun:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(REPO_ROOT)
    with payload_path.open("rb") as payload, log_path.open("wb") as log:
        result = subprocess.run(
            [sys.executable, "-m", "target_newmode.target", "--config", str(config_path)],
            stdin=payload,
            stdout=subprocess.PIPE,
            stderr=log,
            cwd=REPO_ROOT,
            env=env,
            check=False,
        )
    return TargetRun(result=result, log_path=log_path)


@pytest.fixture(scope="module")
def config() -> dict:
    return _load_config()


@pytest.fixture(scope="module")
def e2e_tmp(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return tmp_path_factory.mktemp("target-newmode-e2e")


@pytest.fixture(scope="module", autouse=True)
def cleanup_contacts(config: dict):
    _cleanup_test_contacts(config)
    yield
    _cleanup_test_contacts(config)


@pytest.fixture(scope="module")
def target_run(config: dict, e2e_tmp: Path) -> TargetRun:
    return _run_target(
        CONFIG_PATH,
        SAMPLE_PAYLOAD_DIR / "data.singer",
        e2e_tmp / "main-output.log",
    )


@pytest.fixture(scope="module")
def lookup_target_run(config: dict, e2e_tmp: Path) -> TargetRun:
    config_path = _write_config(
        config,
        e2e_tmp / "lookup-config.json",
        lookup_fields={"Contacts": ["email"]},
        lookup_method="sequential",
    )
    return _run_target(
        config_path,
        SAMPLE_PAYLOAD_DIR / "lookup-e2e.singer",
        e2e_tmp / "lookup-output.log",
    )


@pytest.fixture(scope="module")
def unified_lookup_target_run(config: dict, e2e_tmp: Path) -> TargetRun:
    config_path = _write_config(
        config,
        e2e_tmp / "unified-lookup-config.json",
        lookup_fields={"Contacts": ["mobile"]},
        lookup_method="sequential",
    )
    return _run_target(
        config_path,
        SAMPLE_PAYLOAD_DIR / "unified-lookup-e2e.singer",
        e2e_tmp / "unified-lookup-output.log",
    )


@pytest.fixture(scope="module")
def empty_fields_target_run(config: dict, e2e_tmp: Path) -> TargetRun:
    config_path = _write_config(
        config,
        e2e_tmp / "empty-fields-config.json",
        only_upsert_empty_fields=True,
    )
    return _run_target(
        config_path,
        SAMPLE_PAYLOAD_DIR / "empty-fields-e2e.singer",
        e2e_tmp / "empty-fields-output.log",
    )


def test_target_cli_exits_cleanly(target_run: TargetRun) -> None:
    log_text = target_run.log_path.read_text()
    assert target_run.result.returncode == 0, log_text
    assert "Upsert record error" not in log_text
    assert "Preprocess record error" not in log_text


def test_create_contact_written(config: dict, target_run: TargetRun) -> None:
    matches = _find_contacts_by_email(config, CREATE_EMAIL)
    assert matches, "Expected created contact to exist"
    contact = _get_contact(config, str(matches[0]["id"]))
    attributes = contact.get("attributes") or {}
    assert attributes["first_name"] == "Hotglue"
    assert attributes["last_name"] == "Create"
    assert attributes["mobile_phone"] == CREATE_PHONE
    assert attributes["street_address"] == "123 Test Street"
    assert attributes["region"] == "OR"
    assert attributes["postal_code"] == "97201"
    assert attributes["country"] == "US"
    assert attributes["twitter_handle"] == "hotglueE2E"
    assert attributes["opt_in"] is True
    assert attributes["org_id"] is not None
    assert _group_ids(contact), "Expected contact to belong to at least one contact group"


def test_upsert_contact_updated(config: dict, target_run: TargetRun) -> None:
    matches = _find_contacts_by_email(config, UPSERT_EMAIL)
    assert matches, "Expected upserted contact to exist"
    if len(matches) > 1:
        pytest.fail(f"Expected one upsert contact, found duplicates: {[m['id'] for m in matches]}")
    attributes = _attributes(config, str(matches[0]["id"]))
    assert attributes["first_name"] == "HotglueUpdated"
    assert attributes["last_name"] == "Upsert"
    assert attributes["mobile_phone"] == UPSERT_PHONE
    assert attributes["street_address"] == "2 Second Ave"
    assert attributes["postal_code"] == "K1Z 7K2"


def test_contact_group_membership_is_preserved_on_update(
    config: dict,
    target_run: TargetRun,
) -> None:
    matches = _find_contacts_by_email(config, UPSERT_EMAIL)
    contact = _get_contact(config, str(matches[0]["id"]))
    group_ids = _group_ids(contact)
    assert len(group_ids) == 1, f"Expected exactly one contact group, got {group_ids}"


def test_lookup_fields_email_upserts_without_duplicates(
    config: dict,
    lookup_target_run: TargetRun,
) -> None:
    log_text = lookup_target_run.log_path.read_text()
    assert lookup_target_run.result.returncode == 0, log_text
    matches = _find_contacts_by_email(config, LOOKUP_EMAIL)
    assert len(matches) == 1, f"Expected one contact, found ids: {[m['id'] for m in matches]}"
    assert _attributes(config, str(matches[0]["id"]))["first_name"] == "LookupUpdated"


def test_lookup_fields_support_unified_field_names(
    config: dict,
    unified_lookup_target_run: TargetRun,
) -> None:
    log_text = unified_lookup_target_run.log_path.read_text()
    assert unified_lookup_target_run.result.returncode == 0, log_text
    seeded = _find_contacts_by_email(config, UNIFIED_LOOKUP_EMAIL)
    updated = _find_contacts_by_email(config, UNIFIED_LOOKUP_UPDATED_EMAIL)
    assert not seeded, "Expected the mobile lookup to update the seeded contact, not keep it"
    assert len(updated) == 1, f"Expected one contact, found ids: {[m['id'] for m in updated]}"
    attributes = _attributes(config, str(updated[0]["id"]))
    assert attributes["first_name"] == "UnifiedLookupUpdated"
    assert attributes["mobile_phone"] == UNIFIED_LOOKUP_PHONE


def test_only_upsert_empty_fields_preserves_existing_values(
    config: dict,
    empty_fields_target_run: TargetRun,
) -> None:
    log_text = empty_fields_target_run.log_path.read_text()
    assert empty_fields_target_run.result.returncode == 0, log_text
    matches = _find_contacts_by_email(config, EMPTY_FIELDS_EMAIL)
    assert len(matches) == 1
    attributes = _attributes(config, str(matches[0]["id"]))
    assert attributes["first_name"] == "KeepMe"
    assert attributes["postal_code"] == "97201"
