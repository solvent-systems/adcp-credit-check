# For agents

This repo is a runnable simulation of a pre-trade credit check for AI agents buying ads over AdCP. Nothing here is a live service.

Run: `pip install -e . && python -m adcp_credit_check --serve` prints local URLs for the Solvent check service and three mock AdCP sellers (each has `/docs`).

## Solvent check API (simulation)

`POST /v1/check`: a seller asks before giving an agent buyer invoiced terms.
- Request: `seller {id, name}`, `agent_url`, `operator` (domain of the company that will be invoiced), `billing` (AdCP billing-party), `billing_entity` (optional, AdCP business-entity), `payment_terms`, `requested_credit_limit {amount, currency}`.
- Response: `check_id`, `decision` (`approve` | `approve_lower` | `refer` | `decline`), `recommended_credit_limit {amount, currency}`, `authority {status: verified | missing_ceiling | failed, source, max_outstanding, valid_until}`, `exposure {other_sellers, limits_elsewhere, requested_elsewhere, outstanding_elsewhere, headroom}`, `flags[]`, `reasons[]`, `checked_at`.

`POST /v1/report`: a seller reports an account: `seller`, `operator`, `account_id`, `status` (AdCP account-status), `credit_limit`, `outstanding`.

`GET /v1/exposure/{operator}`: the operator's own view across sellers.

Authority comes from the operator's `/.well-known/trust.json`: the agent must be listed, and the proposed `profiles.adcp.billing_authority.max_outstanding` sets its ceiling. See README.md for which fields are real AdCP and which are proposed.

## Working on the code

- Python 3.11+, FastAPI. Tests: `pip install -e ".[test]" && pytest -q`.
- `adcp_credit_check/adcp.py` builds AdCP message shapes and cites the schema for each. Keep proposed fields marked PROPOSED.
