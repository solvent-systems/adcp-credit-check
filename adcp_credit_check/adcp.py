"""Minimal AdCP message shapes used by the demo.

Each builder returns a plain dict shaped like the AdCP JSON schema it names.
Only the fields the credit flow needs are filled in. Schemas were read on
20 Oct 2026 from github.com/adcontextprotocol/adcp:

- main @ 7637b371 (AdCP 3.2.x, released shapes)
- PR #8170 @ 0771886c (3.3 onboarding posture, open, experimental)
- PR #8293 @ 60e30c8d (3.3 request_account + pending_action, open, experimental)

Fields marked PROPOSED are not in any AdCP schema or open PR. They are proposed in
this repo for adcp #8118 / #8280 (requested_credit_limit on
request_account, billing_authority on the operator's trust.json agent entry).

The official Python SDK (PyPI `adcp`, adcontextprotocol/adcp-client-python)
was not used: it pulls in a2a-sdk, mcp, protobuf and cryptography, and the
3.3 shapes here are not in it yet.
"""

from __future__ import annotations

import uuid

SCHEMA_BASE = "https://github.com/adcontextprotocol/adcp/blob/main/static/schemas/source"

# Where each shape comes from. Printed by the buyer script next to each call.
SOURCES = {
    "get_adcp_capabilities": "protocol/get-adcp-capabilities-response.json (main); "
    "account.agent_onboarding from core/agent-onboarding.json (PR #8170, 3.3, open)",
    "sync_accounts": "account/sync-accounts-request.json / -response.json (main, 3.2)",
    "request_account": "account/request-account-request.json / -response.json, "
    "core/pending-action.json (PR #8293, 3.3 experimental, open)",
    "list_accounts": "account/list-accounts-response.json + core/account.json (main)",
    "create_media_buy": "media-buy/create-media-buy-request.json / -response.json (main)",
    "get_account_financials": "account/get-account-financials-response.json (main)",
    "trust.json": "trust/v1/trust.json (main); billing_authority is PROPOSED",
}


def idempotency_key() -> str:
    # request_account: pattern ^[A-Za-z0-9_.:-]{16,255}$, "Use a fresh UUID v4".
    return str(uuid.uuid4())


def money(amount: float, currency: str) -> dict:
    # core/account.json credit_limit and create_media_buy total_budget: {amount, currency}
    return {"amount": amount, "currency": currency}


def error(code: str, message: str, recovery: str | None = None, **details) -> dict:
    # core/error.json: required code, message; optional recovery, details, suggestion
    e = {"code": code, "message": message}
    if recovery:
        e["recovery"] = recovery
    if details:
        e["details"] = details
    return e


def capabilities(*, supported_billing: list[str], onboarding: dict, financials: bool,
                 experimental: list[str]) -> dict:
    """get_adcp_capabilities response, account block only (plus experimental_features)."""
    return {
        "adcp": {"major_versions": [3]},
        "supported_protocols": ["media_buy"],
        "experimental_features": experimental,
        "account": {
            "require_operator_auth": False,  # buyer-declared accounts (natural key)
            "supported_billing": supported_billing,
            "account_financials": financials,
            "sandbox": False,
            # 3.3, PR #8170 (open): seller posture per billing party
            "agent_onboarding": onboarding,
        },
    }


def account(*, account_id: str, name: str, brand: dict, operator: str, billing: str,
            billing_entity: dict, status: str, payment_terms: str | None = None,
            credit_limit: dict | None = None, setup: dict | None = None,
            currency: str = "USD") -> dict:
    """core/account.json (main). setup is present only when pending_approval."""
    a = {
        "account_id": account_id,
        "name": name,
        "status": status,
        "brand": brand,
        "operator": operator,
        "currency": currency,
        "billing": billing,
        # bank details are write-only and MUST NOT be echoed (business-entity.json)
        "billing_entity": {k: v for k, v in billing_entity.items() if k != "bank"},
    }
    if payment_terms:
        a["payment_terms"] = payment_terms
    if credit_limit:
        a["credit_limit"] = credit_limit
    if setup:
        a["setup"] = setup
    return a


def pending_action(*, waiting_on: str, kind: str, message: str, url: str | None = None) -> dict:
    """core/pending-action.json (PR #8293). url is required when waiting_on is buyer."""
    p = {"waiting_on": waiting_on, "kind": kind, "message": message}
    if url:
        p["url"] = url
    return p
