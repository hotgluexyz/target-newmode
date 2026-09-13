# target-newmode

`target-newmode` is a Singer target for [New/Mode](https://www.newmode.net), built with the [Hotglue Singer SDK](https://github.com/hotgluexyz/HotglueSingerSDK) for Singer Targets.

Contacts are written with the New/Mode Impact v2.1 JSON:API (`/jsonapi/contact/contact`).

## Installation

```bash
pip install target-newmode
```

Or from source:

```bash
pip install -e .
```

## Configuration

| Field | Required | Description |
| --- | --- | --- |
| `client_id` | Yes | New/Mode OAuth client id |
| `client_secret` | Yes | New/Mode OAuth client secret |
| `api_base_url` | No | New/Mode API base URL. Defaults to `https://base.newmode.net` |
| `org_id` | No | Numeric organization id stamped on every contact. Resolved from the account when omitted |
| `gid` | No | Organization uuid used for the contact `gid` relationship on create. Resolved from the account when omitted |
| `default_country` | No | Country used when a record has none. New/Mode rejects contacts without one |
| `only_upsert_empty_fields` | No | Only update New/Mode fields that are currently empty |
| `lookup_fields` | No | Per-stream lookup fields, e.g. `{"Contacts": ["id", "email"]}` |
| `lookup_method` | No | `sequential` (default) or `all` |

Example `config.json`:

```json
{
  "client_id": "YOUR_CLIENT_ID",
  "client_secret": "YOUR_CLIENT_SECRET",
  "api_base_url": "https://base.newmode.net",
  "default_country": "US",
  "lookup_fields": {
    "Contacts": ["id", "email"]
  },
  "lookup_method": "sequential",
  "only_upsert_empty_fields": false
}
```

### Lookup fields

Before each write, existing contacts are resolved with `GET /jsonapi/contact/contact/{id}` (when `id` is configured) or a filtered collection query (`?filter[<attribute>]=<value>`). Configure `lookup_fields` and `lookup_method` to control matching:

- `sequential` (default) — try each lookup field in order and use the first match
- `all` — every lookup field must be populated and match the same contact (filters are ANDed in one query)

When several contacts match, the most recently changed one is used and a warning is logged.

Use unified CRM field names in `lookup_fields`. The target maps them to New/Mode contact attributes both when reading incoming records and when comparing against API results:

| Unified field | New/Mode attribute |
| --- | --- |
| `id` | contact uuid |
| `email` | `email` |
| `first_name`, `last_name` | `first_name`, `last_name` |
| `mobile`, `mobile_phone`, `phone` | `mobile_phone` |
| `alternate_phone` | `alternate_phone` |
| `twitter_handle` | `twitter_handle` |
| `line1`, `street_address` | `street_address` |
| `city` | `city` |
| `state`, `region` | `region` |
| `postal_code` | `postal_code` |
| `country` | `country` |

### Contact writes

- **Create** — `POST /jsonapi/contact/contact` when no match is found. The contact is linked to the organization through the `gid` relationship
- **Update** — `PATCH /jsonapi/contact/contact/{id}` when a match is found

New/Mode requires `org_id` and `country` on every contact, and rejects a contact that has an email address without both a first and last name. `org_id` (and `gid`) are read from the config, or resolved once per job from `GET /jsonapi/group/organization`. A record without a country falls back to `default_country`; without either, the record fails with an `InvalidPayloadError`.

### Field mapping

| Unified field | New/Mode attribute |
| --- | --- |
| `first_name`, `last_name`, `email` | `first_name`, `last_name`, `email` |
| `salutation` | `honorific` |
| `suffix` | `name_suffix` |
| `phone_numbers` | `mobile_phone` (mobile-typed number) and `alternate_phone`; `fax` and `pager` are skipped |
| `addresses[0]` | `street_address`, `city`, `region`, `postal_code`, `country`, `latitude`, `longitude` |
| `subscribe_status` | `opt_in` |
| `lists` | `groups` relationship (contact groups, created by title when missing) |
| `custom_fields` | Only entries whose name matches a native contact attribute (for example `twitter_handle` or `nm_marketing_opt_in`) |

`lists` entries may be plain strings or objects with `name` and `subscription_status`. Unsubscribed entries remove the contact from that group; existing group membership is preserved otherwise.

New/Mode contacts have no tags and no user-defined custom fields, so unified `tags` and unmatched `custom_fields` are ignored with a warning.

## Source Authentication and Authorization

New/Mode uses OAuth 2.0. The target requests a token with the `client_credentials` grant from `POST /oauth/token` and sends it as `Authorization: Bearer <access_token>`. Tokens are short-lived and refreshed automatically; the client id and secret are provisioned by the New/Mode Customer Experience team.

## Supported Streams

| Stream | Description |
| --- | --- |
| `Contacts` | Unified contacts sink with contact groups, address mapping, and upsert support |

## Usage

```bash
cat sample_payload/data.singer | target-newmode --config .secrets/config.json
```

## Developer Resources

Create `.secrets/config.json` with your sandbox credentials:

```json
{
  "client_id": "YOUR_CLIENT_ID",
  "client_secret": "YOUR_CLIENT_SECRET",
  "api_base_url": "https://base.newmode.net",
  "default_country": "US"
}
```

Run lint and tests (e2e needs the config above; payloads live in `sample_payload/`):

```bash
python -m venv .venv
.venv/bin/pip install -e . ruff pytest requests
.venv/bin/ruff check .
.venv/bin/pytest target_newmode/tests/test_lookup.py -v
.venv/bin/pytest target_newmode/tests/test_e2e.py -v
```
