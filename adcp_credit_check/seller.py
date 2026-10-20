"""Mock AdCP seller agent, just enough of it for the account and credit flow.

Transport is simplified: one JSON POST per AdCP task at /<task_name>, where a
real seller speaks MCP or A2A. The caller's identity comes from an unsigned
Signature-Agent header; a real 3.3 seller verifies a Web Bot Auth signature
(RFC 9421) and fetches the agent's key directory first.

With a Solvent URL configured, the seller calls Solvent before it gives
invoiced terms and turns approvals into a credit_limit without a human.
Without one, every request for terms waits on a human credit application,
which is what the protocol expects today.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Callable

import requests
from fastapi import FastAPI, Header, Request
from fastapi.responses import JSONResponse

from . import adcp
from .scenario import CURRENCY, SellerConfig

Poster = Callable[[str, dict], dict]


def http_post(url: str, body: dict) -> dict:
    r = requests.post(url, json=body, timeout=10)
    r.raise_for_status()
    return r.json()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def make_seller_app(cfg: SellerConfig, solvent_url: str | None, post: Poster = http_post) -> FastAPI:
    app = FastAPI(title=f"{cfg.name} sales agent (demo)")
    accounts: dict[str, dict] = {}      # account_id -> account (+ private fields with leading _)
    by_key: dict[tuple, str] = {}       # natural key -> account_id
    reqs: dict[tuple, dict] = {}        # natural key -> account request record
    app.state.accounts = accounts
    app.state.cfg = cfg

    seller_ref = {"id": cfg.id, "name": cfg.name}

    def report(acct: dict) -> None:
        if not solvent_url:
            return
        body = {
            "seller": seller_ref, "operator": acct["operator"], "account_id": acct["account_id"],
            "status": acct["status"],
            "outstanding": adcp.money(acct["_outstanding"], CURRENCY),
        }
        if acct.get("credit_limit"):
            body["credit_limit"] = acct["credit_limit"]
        post(f"{solvent_url}/v1/report", body)

    def credit_decision(agent_url: str, entry: dict, requested: float | None) -> dict:
        """What the seller's credit desk does with a request for invoiced terms."""
        terms = entry.get("payment_terms") or "net_30"
        if terms == "prepay":
            return {"outcome": "approved", "limit": None}
        if not solvent_url:
            return {"outcome": "human"}
        check = post(f"{solvent_url}/v1/check", {
            "seller": seller_ref,
            "agent_url": agent_url,
            "operator": entry["operator"],
            "billing": entry["billing"],
            "billing_entity": entry.get("billing_entity"),
            "payment_terms": terms,
            # 3.2 sync_accounts has no amount field, so the seller asks about its own default.
            "requested_credit_limit": adcp.money(requested or cfg.default_limit, CURRENCY),
        })
        if check["decision"] in ("approve", "approve_lower"):
            limit = min(cfg.default_limit, check["recommended_credit_limit"]["amount"])
            return {"outcome": "approved", "limit": limit, "check": check}
        return {"outcome": "human", "check": check}

    def new_account(agent_url: str, entry: dict, status: str, limit: float | None,
                    setup: dict | None = None) -> dict:
        key = (entry["brand"]["domain"], entry["operator"])
        account_id = f"acc_{cfg.id}_{len(accounts) + 1:03d}"
        acct = adcp.account(
            account_id=account_id,
            name=f"{entry['billing_entity']['legal_name']} ({cfg.name})",
            brand=entry["brand"], operator=entry["operator"], billing=entry["billing"],
            billing_entity=entry["billing_entity"], status=status,
            payment_terms=entry.get("payment_terms") or "net_30",
            credit_limit=adcp.money(limit, CURRENCY) if limit else None,
            setup=setup, currency=CURRENCY,
        )
        acct.update({"_agent": agent_url, "_outstanding": 0.0, "_approved_limit": limit,
                     "_requested": None, "_terms_accepted": "contract" not in cfg.requirements,
                     "_credit_done": False})
        accounts[account_id] = acct
        by_key[key] = account_id
        return acct

    def public(acct: dict) -> dict:
        return {k: v for k, v in acct.items() if not k.startswith("_")}

    def activate(acct: dict) -> None:
        acct["status"] = "active"
        acct.pop("setup", None)
        if acct["_approved_limit"]:
            acct["credit_limit"] = adcp.money(acct["_approved_limit"], CURRENCY)
        report(acct)

    # ---- get_adcp_capabilities -------------------------------------------------
    @app.post("/get_adcp_capabilities")
    def get_adcp_capabilities() -> dict:
        onboarding = {}
        experimental = []
        if cfg.adcp == "3.3":
            experimental = ["account.requests"]
            onboarding = {
                "url": cfg.onboarding_url,
                "message": f"New advertisers on invoiced terms apply to {cfg.name}. "
                           "Have your company registration and billing contact ready.",
                "billing": {
                    "operator": {"mode": "application",
                                 "requirements": [{"kind": k} for k in cfg.requirements]},
                    "agent": {"mode": "invite_only"},
                },
            }
        caps = adcp.capabilities(supported_billing=["operator", "agent"], onboarding=onboarding,
                                 financials=True, experimental=experimental)
        if not onboarding:
            del caps["account"]["agent_onboarding"]  # a 3.2 seller has no posture to publish
        return caps

    # ---- sync_accounts (3.2) ---------------------------------------------------
    @app.post("/sync_accounts")
    def sync_accounts(body: dict, signature_agent: str = Header("")) -> dict:
        results = []
        for entry in body.get("accounts", []):
            if entry.get("billing") not in ("operator", "agent"):
                results.append({"brand": entry.get("brand"), "operator": entry.get("operator"),
                                "action": "failed", "status": "rejected",
                                "errors": [adcp.error("BILLING_NOT_SUPPORTED", "Billing party not offered.")]})
                continue
            key = (entry["brand"]["domain"], entry["operator"])
            if key in by_key:
                acct = accounts[by_key[key]]
                results.append({**public(acct), "action": "unchanged"})
                continue
            d = credit_decision(signature_agent, entry, None)
            if d["outcome"] == "approved":
                acct = new_account(signature_agent, entry, "pending_approval", d["limit"])
                activate(acct)
            else:
                expires = (datetime.now(timezone.utc) + timedelta(days=14)).isoformat(timespec="seconds")
                acct = new_account(signature_agent, entry, "pending_approval", None, setup={
                    "url": f"{cfg.onboarding_url}?ref={len(accounts) + 1}",
                    "message": f"A person at {entry['billing_entity']['legal_name']} needs to complete "
                               f"{cfg.name}'s credit application before invoiced terms are available.",
                    "expires_at": expires,
                })
            results.append({**public(acct), "action": "created"})
        return {"accounts": results}

    # ---- request_account (3.3, experimental) -----------------------------------
    def record(key: tuple, entry: dict) -> dict:
        r = reqs.get(key)
        if r is None:
            r = {"request_id": f"areq_{cfg.id}_{len(reqs) + 1:03d}", "created_at": _now(),
                 "requested": {k: entry[k] for k in ("brand", "operator", "billing", "payment_terms",
                                                     "requested_credit_limit") if k in entry}}
            reqs[key] = r
        r["updated_at"] = _now()
        return r

    def settle(r: dict, acct: dict) -> dict:
        """Move a request on as far as the account's state allows."""
        for f in ("pending_action", "account_id", "resolution"):
            r.pop(f, None)
        if not acct["_approved_limit"] and not acct["_credit_done"]:
            r["state"] = "in_review"
            r["pending_action"] = adcp.pending_action(
                waiting_on="buyer", kind="credit_application", url=f"{cfg.onboarding_url}?req={r['request_id']}",
                message=f"A person at {acct['billing_entity']['legal_name']} completes {cfg.name}'s "
                        "credit application. Usually two to five business days.")
        elif not acct["_terms_accepted"]:
            r["state"] = "in_review"
            r["pending_action"] = adcp.pending_action(
                waiting_on="buyer", kind="accept_terms", url=f"https://{cfg.domain}/terms/accept?req={r['request_id']}",
                message=f"Credit is approved. A person at {acct['billing_entity']['legal_name']} accepts "
                        f"{cfg.name}'s insertion order terms.")
        else:
            if acct["status"] != "active":
                activate(acct)
            r["state"] = "resolved"
            r["account_id"] = acct["account_id"]
            r["resolution"] = "created"
        return {"status": "completed", "request": r}

    @app.post("/request_account")
    def request_account(body: dict, signature_agent: str = Header("")):
        if cfg.adcp != "3.3":
            return JSONResponse(status_code=404, content={"status": "failed", "errors": [
                adcp.error("UNSUPPORTED_FEATURE", "request_account is not supported by this seller.")]})
        key = (body["brand"]["domain"], body["operator"])
        r = record(key, body)
        if key in by_key:  # repeat request for an open key returns the same request
            return settle(r, accounts[by_key[key]])
        requested = (body.get("requested_credit_limit") or {}).get("amount")
        d = credit_decision(signature_agent, body, requested)
        acct = new_account(signature_agent, body, "pending_approval",
                           d.get("limit") if d["outcome"] == "approved" else None)
        acct["_requested"] = requested
        return settle(r, acct)

    # ---- the human steps, simulated --------------------------------------------
    @app.post("/_demo/human_step")
    def human_step(body: dict) -> dict:
        """Stands in for a person filling in a form on the seller's website."""
        acct = accounts[body["account_id"]]
        if body["kind"] == "credit_application":
            # The seller's own credit desk approves on its own numbers, unaware of other sellers.
            acct["_credit_done"] = True
            acct["_approved_limit"] = min(cfg.default_limit, acct["_requested"] or cfg.default_limit)
            if acct["_terms_accepted"]:
                activate(acct)
        elif body["kind"] == "accept_terms":
            acct["_terms_accepted"] = True
            if acct["_approved_limit"]:
                activate(acct)
        return {"account": public(acct)}

    # ---- list_accounts ---------------------------------------------------------
    @app.post("/list_accounts")
    def list_accounts(signature_agent: str = Header("")) -> dict:
        return {"accounts": [public(a) for a in accounts.values() if a["_agent"] == signature_agent]}

    # ---- create_media_buy ------------------------------------------------------
    @app.post("/create_media_buy")
    def create_media_buy(body: dict):
        acct = accounts.get(body["account"].get("account_id", ""))
        if acct is None:
            return {"errors": [adcp.error("ACCOUNT_NOT_FOUND", "No such account.")]}
        if acct["status"] != "active":
            return {"errors": [adcp.error("ACCOUNT_SETUP_REQUIRED", "Account needs setup before use.",
                                          recovery="correctable", setup=acct.get("setup"))]}
        amount = body["total_budget"]["amount"]
        if acct.get("credit_limit"):
            available = acct["credit_limit"]["amount"] - acct["_outstanding"]
            if amount > available:
                # Not an AdCP code: nothing in error-code.json says "this order is over your credit".
                return {"errors": [adcp.error(
                    "CREDIT_LIMIT_EXCEEDED",
                    f"This order is over the account's available credit ({CURRENCY} {available:,.0f}).",
                    recovery="correctable", available_credit=available)]}
        acct["_outstanding"] += amount
        report(acct)
        return {
            "media_buy_id": f"mb_{cfg.id}_{int(acct['_outstanding'])}",
            "account": {"account_id": acct["account_id"]},
            "currency": CURRENCY,
            "total_budget": body["total_budget"],
            "confirmed_at": _now(),
            "revision": 1,
            "packages": [],
        }

    # ---- get_account_financials ------------------------------------------------
    @app.post("/get_account_financials")
    def get_account_financials(body: dict) -> dict:
        acct = accounts[body["account"]["account_id"]]
        limit = (acct.get("credit_limit") or {}).get("amount", 0.0)
        out = acct["_outstanding"]
        return {
            "account": {"account_id": acct["account_id"]},
            "currency": CURRENCY,
            "spend": {"total_spend": out, "media_buy_count": 1 if out else 0},
            "credit": {"credit_limit": limit, "available_credit": limit - out,
                       "utilization_percent": round(100 * out / limit, 1) if limit else 0},
            "payment_status": "current",
            "payment_terms": acct.get("payment_terms"),
        }

    @app.exception_handler(KeyError)
    def bad_request(_: Request, exc: KeyError):
        return JSONResponse(status_code=400, content={"errors": [
            adcp.error("INVALID_REQUEST", f"Missing field {exc}.")]})

    return app
