"""Solvent check service (mock, in memory).

A seller calls POST /v1/check before it gives a buyer agent invoiced terms.
Solvent answers two questions:

1. Authority: has the company that will be invoiced said this agent may run
   up a balance in its name, and up to how much? Read from the operator's
   trust.json (the proposed billing_authority on the agent entry).
2. Exposure: how much have this company's agents already been given, or
   asked for, at other sellers?

It returns a decision, a recommended limit and the reasons. The seller
decides and carries the credit. Solvent never holds money, never sees the
order contents, and doesn't tell one seller who the other sellers are.

Sellers tell Solvent what they granted and what is outstanding through
POST /v1/report, which mirrors get_account_financials.credit.
"""

from __future__ import annotations

import time
import uuid
from datetime import datetime, timezone
from typing import Callable

from fastapi import FastAPI
from pydantic import BaseModel

from .adcp import money

FAN_OUT_WINDOW = 24 * 3600  # seconds
FAN_OUT_SELLERS = 3

TrustResolver = Callable[[str], dict | None]


class Money(BaseModel):
    amount: float
    currency: str


class Seller(BaseModel):
    id: str
    name: str


class CheckRequest(BaseModel):
    seller: Seller
    agent_url: str
    operator: str            # domain of the party that will be invoiced
    billing: str             # billing-party enum
    billing_entity: dict | None = None
    payment_terms: str
    requested_credit_limit: Money


class ReportRequest(BaseModel):
    seller: Seller
    operator: str
    account_id: str
    status: str                  # account-status enum
    credit_limit: Money | None = None
    outstanding: Money | None = None


def money_s(amount: float, currency: str) -> str:
    return f"{currency} {amount:,.0f}"


def _parse_ts(s: str) -> float:
    return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()


def check_authority(trust: dict | None, operator: str, agent_url: str, currency: str,
                    now: float) -> tuple[str, dict, list[str]]:
    """Return (status, detail, reasons). status: verified | missing_ceiling | failed."""
    source = f"https://{operator}/.well-known/trust.json"
    if trust is None:
        return "failed", {"source": source}, [f"{operator} publishes no trust.json, so nobody has said this agent may buy for it."]
    agent = next((a for a in trust.get("agents", []) if a.get("url") == agent_url), None)
    if agent is None:
        return "failed", {"source": source}, [f"{operator}'s trust.json doesn't list {agent_url} as one of its agents."]
    auth = (agent.get("profiles", {}).get("adcp", {}) or {}).get("billing_authority")
    if not auth:
        return "missing_ceiling", {"source": source}, [
            f"{operator} lists this agent but publishes no spending ceiling for it."]
    ceiling = auth["max_outstanding"]
    detail = {"source": source, "max_outstanding": ceiling, "valid_until": auth.get("valid_until")}
    if auth.get("valid_until") and _parse_ts(auth["valid_until"]) <= now:
        return "failed", detail, [f"{operator}'s authority for this agent expired on {auth['valid_until']}."]
    if ceiling["currency"] != currency:
        return "failed", detail, [f"Ceiling is in {ceiling['currency']}, request is in {currency}; no FX in this demo."]
    return "verified", detail, [
        f"{operator} lets this agent run up to {money_s(ceiling['amount'], ceiling['currency'])} outstanding (until {auth.get('valid_until', 'no end date')})."]


def make_app(resolve_trust: TrustResolver, clock: Callable[[], float] = time.time) -> FastAPI:
    app = FastAPI(title="Solvent check (demo)", version="0.1")
    # exposure[operator][seller_id] = {...}
    exposure: dict[str, dict[str, dict]] = {}
    checks: list[dict] = []
    app.state.exposure = exposure
    app.state.checks = checks

    @app.post("/v1/check")
    def check(req: CheckRequest) -> dict:
        now = clock()
        cur = req.requested_credit_limit.currency
        requested = req.requested_credit_limit.amount
        status, auth_detail, reasons = check_authority(
            resolve_trust(req.operator), req.operator, req.agent_url, cur, now)

        book = exposure.setdefault(req.operator, {})
        others = {sid: e for sid, e in book.items() if sid != req.seller.id}
        limits = sum(e["credit_limit"] for e in others.values() if e["status"] == "active")
        pending = sum(e["requested"] for e in others.values() if e["status"] != "active")
        outstanding = sum(e["outstanding"] for e in others.values())
        committed = limits + pending
        recent = {sid for sid, e in book.items() if now - e["first_seen"] <= FAN_OUT_WINDOW}
        recent.add(req.seller.id)

        flags: list[str] = []
        exposure_out = {
            "other_sellers": len(others),
            "limits_elsewhere": money(limits, cur),
            "requested_elsewhere": money(pending, cur),
            "outstanding_elsewhere": money(outstanding, cur),
        }

        if status == "failed":
            decision, recommended = "decline", 0.0
            flags.append("authority_failed")
            reasons.append("Treat as an unknown buyer: prepay, or a credit application with a human.")
        elif status == "missing_ceiling":
            decision, recommended = "refer", 0.0
            flags.append("no_ceiling")
            reasons.append("Authority is there but has no amount. A human should ask the company.")
        else:
            ceiling = auth_detail["max_outstanding"]["amount"]
            headroom = max(ceiling - committed, 0.0)
            exposure_out["headroom"] = money(headroom, cur)
            if others:
                reasons.append(
                    f"Its agents already hold {money_s(limits, cur)} of limits"
                    + (f" and {money_s(pending, cur)} of pending requests" if pending else "")
                    + f" at {len(others)} other seller{'s' if len(others) != 1 else ''}"
                    + f" ({money_s(outstanding, cur)} drawn).")
            else:
                reasons.append("No limits or requests at other sellers.")
            if headroom >= requested:
                decision, recommended = "approve", requested
                reasons.append(f"{money_s(requested, cur)} fits within the {money_s(headroom, cur)} left under the ceiling.")
            elif headroom > 0:
                decision, recommended = "approve_lower", headroom
                flags.append("exceeds_mandate")
                reasons.append(
                    f"Asking for {money_s(requested, cur)} here would take the total to "
                    f"{money_s(committed + requested, cur)}, over the {money_s(ceiling, cur)} ceiling. "
                    f"{money_s(headroom, cur)} is left.")
            else:
                decision, recommended = "decline", 0.0
                flags.append("exceeds_mandate")
                reasons.append(f"The {money_s(ceiling, cur)} ceiling is already used up at other sellers.")

        if len(recent) >= FAN_OUT_SELLERS:
            flags.append("fan_out")
            reasons.append(f"{len(recent)} sellers have asked about this company in the last 24 hours.")

        # Record this seller's request so the next seller sees it.
        entry = book.setdefault(req.seller.id, {
            "seller_name": req.seller.name, "status": "requested", "credit_limit": 0.0,
            "outstanding": 0.0, "requested": 0.0, "first_seen": now})
        if entry["status"] != "active":
            entry["requested"] = recommended

        result = {
            "check_id": f"chk_{uuid.uuid4().hex[:10]}",
            "decision": decision,
            "recommended_credit_limit": money(recommended, cur),
            "authority": {"status": status, **auth_detail},
            "exposure": exposure_out,
            "flags": flags,
            "reasons": reasons,
            "checked_at": datetime.fromtimestamp(now, timezone.utc).isoformat(),
        }
        checks.append({"seller": req.seller.model_dump(), "request": req.model_dump(), "result": result})
        return result

    @app.post("/v1/report")
    def report(req: ReportRequest) -> dict:
        book = exposure.setdefault(req.operator, {})
        entry = book.setdefault(req.seller.id, {
            "seller_name": req.seller.name, "status": req.status, "credit_limit": 0.0,
            "outstanding": 0.0, "requested": 0.0, "first_seen": clock()})
        entry["status"] = req.status
        entry["account_id"] = req.account_id
        if req.credit_limit:
            entry["credit_limit"] = req.credit_limit.amount
        if req.outstanding:
            entry["outstanding"] = req.outstanding.amount
        return {"ok": True}

    @app.get("/v1/checks")
    def list_checks(since: int = 0) -> dict:
        return {"checks": checks[since:], "next": len(checks)}

    @app.get("/v1/exposure/{operator}")
    def get_exposure(operator: str) -> dict:
        # Operator's own view (in a real service: authenticated as the operator).
        return {"operator": operator, "sellers": exposure.get(operator, {})}

    return app
