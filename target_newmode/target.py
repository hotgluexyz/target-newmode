"""New/Mode target class."""

from typing import ClassVar

from hotglue_singer_sdk import typing as th
from hotglue_singer_sdk.helpers.capabilities import AlertingLevel
from hotglue_singer_sdk.target_sdk.target import TargetHotglue

from target_newmode.auth import NewModeAuthenticator
from target_newmode.client import DEFAULT_BASE_URL
from target_newmode.sinks import ContactsSink


class TargetNewMode(TargetHotglue):
    """Target for New/Mode unified Contacts."""

    SINK_TYPES: ClassVar = [
        ContactsSink,
    ]
    name = "target-newmode"
    alerting_level = AlertingLevel.ERROR

    config_jsonschema = th.PropertiesList(
        th.Property(
            "client_id",
            th.StringType,
            required=True,
            description="New/Mode OAuth client id.",
        ),
        th.Property(
            "client_secret",
            th.StringType,
            required=True,
            description="New/Mode OAuth client secret.",
        ),
        th.Property(
            "api_base_url",
            th.StringType,
            description=f"New/Mode API base URL. Defaults to {DEFAULT_BASE_URL}.",
        ),
        th.Property(
            "org_id",
            th.IntegerType,
            description="Numeric New/Mode organization id. Resolved from the account when omitted.",
        ),
        th.Property(
            "gid",
            th.StringType,
            description="Organization uuid used for the contact `gid` relationship on create.",
        ),
        th.Property(
            "default_country",
            th.StringType,
            description="Country used when a record has none. New/Mode requires a country.",
        ),
        th.Property(
            "only_upsert_empty_fields",
            th.BooleanType,
            description="Only update New/Mode fields that are currently empty.",
        ),
        th.Property(
            "lookup_method",
            th.StringType,
            description='Lookup strategy: "sequential" or "all". Defaults to sequential.',
        ),
        th.Property(
            "lookup_fields",
            th.ObjectType(),
            description='Per-stream lookup fields, e.g. {"Contacts": ["id", "email"]}.',
        ),
        th.Property(
            "access_token",
            th.StringType,
            description="Current access token. Populated by the target after authenticating.",
        ),
        th.Property(
            "expires_in",
            th.IntegerType,
            description="Epoch seconds when the access token expires. Managed by the target.",
        ),
    ).to_dict()

    @classmethod
    def access_token_support(cls, connector=None):
        """Return the authenticator class and the New/Mode token endpoint."""
        base_url = DEFAULT_BASE_URL
        if connector is not None:
            base_url = (connector.config.get("api_base_url") or DEFAULT_BASE_URL).rstrip("/")
        return NewModeAuthenticator, f"{base_url}/oauth/token"


if __name__ == "__main__":
    TargetNewMode.cli()
