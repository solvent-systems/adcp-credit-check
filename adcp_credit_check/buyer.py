"""Acme's buying agent, walking the AdCP account flow at three sellers.

It also narrates: after each seller step it reads Solvent's check log so
the viewer can see what the seller asked Solvent and what came back. The
buyer agent itself never sees that exchange.
"""

from __future__ import annotations

import json
import sys
import time
from dataclasses import dataclass, field
from typing import Callable

import requests

from . import adcp
from .scenario import (AGENT_URL, BILLING_ENTITY, BRAND, CEILING, CURRENCY, OPERATOR_DOMAIN,
                       ROGUE_AGENT_URL, SELLERS, SellerConfig)


@dataclass
class Urls:
    sellers: dict[str, str]
    solvent: str | None
    operator: str


@dataclass
class Style:
    color: bool = True

    def _c(self, code: str, s: str) -> str:
        return f"\033[{code}m{s}\033[0m" if self.color else s

    def bold(self, s): return self._c("1", s)
    def dim(self, s): return self._c("2", s)
    def green(self, s): return self._c("32", s)
    def yellow(self, s): return self._c("33", s)
    def red(self, s): return self._c("31", s)
    def blue(self, s): return self._c("36", s)


def _fmt(amount: float) -> str:
    return f"{CURRENCY} {amount:,.0f}"


def _shorten(obj):
    """Keep printed JSON readable: collapse the billing entity's address and contacts."""
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if k in ("billing_entity",) and isinstance(v, dict):
                out[k] = {"legal_name": v.get("legal_name"),
                          "registration_number": v.get("registration_number"), "...": "address, contacts"}
            else:
                out[k] = _shorten(v)
        return out
    if isinstance(obj, list):
        return [_shorten(x) for x in obj]
    return obj


@dataclass
class Run:
    urls: Urls
    interactive: bool = True
    delay: float = 1.0
    brief: bool = False
    style: Style = field(default_factory=Style)
    out: Callable[[str], None] = print
    pause_fn: Callable[[str], str] = input
    _step: int = 0
    _seen_checks: int = 0
    results: dict = field(default_factory=dict)

    # ---- output helpers --------------------------------------------------------
    def pause(self) -> None:
        if self.interactive:
            self.pause_fn(self.style.dim("  [Enter] "))
        elif self.delay:
            time.sleep(self.delay)

    def step(self, title: str, note: str = "") -> None:
        self._step += 1
        self.out("")
        self.out(self.style.bold(f"Step {self._step}. {title}"))
        if note:
            for line in note.splitlines():
                self.out(f"  {line}")
        self.pause()

    def json(self, obj, prefix: str = "    ") -> None:
        if self.brief:
            return
        text = json.dumps(_shorten(obj), indent=2)
        for line in text.splitlines():
            self.out(self.style.dim(prefix + line))

    def call(self, seller: SellerConfig, task: str, body: dict, agent: str = AGENT_URL) -> dict:
        url = f"{self.urls.sellers[seller.id]}/{task}"
        self.out(self.style.blue(f"  -> {seller.name}: {task}") + self.style.dim(f"   ({adcp.SOURCES.get(task, '')})"))
        self.json(body)
        r = requests.post(url, json=body, headers={"Signature-Agent": agent}, timeout=10)
        resp = r.json()
        self.out(self.style.blue(f"  <- {r.status_code}"))
        self.json(resp)
        return resp

    def behind_the_scenes(self) -> list[dict]:
        if not self.urls.solvent:
            return []
        data = requests.get(f"{self.urls.solvent}/v1/checks", params={"since": self._seen_checks}, timeout=10).json()
        self._seen_checks = data["next"]
        for c in data["checks"]:
            req, res = c["request"], c["result"]
            self.out("")
            self.out(self.style.yellow(f"  Behind the scenes: {c['seller']['name']} asks Solvent before giving terms"))
            self.out(self.style.dim(f"    POST /v1/check  operator={req['operator']}  agent={req['agent_url']}  "
                                    f"terms={req['payment_terms']}  asking={_fmt(req['requested_credit_limit']['amount'])}"))
            self.json(res)
            colour = {"approve": self.style.green, "approve_lower": self.style.yellow}.get(res["decision"], self.style.red)
            self.out("    " + colour(f"Solvent: {res['decision']}, recommended limit "
                                     f"{_fmt(res['recommended_credit_limit']['amount'])}"))
            for reason in res["reasons"]:
                self.out(f"      - {reason}")
            if res["flags"]:
                self.out("    " + colour("flags: " + ", ".join(res["flags"])))
        return data["checks"]

    def human(self, message: str, url: str) -> None:
        self.out("")
        self.out(self.style.red("  HUMAN STEP. The agent can't do this part."))
        self.out(f"    {message}")
        self.out(f"    {url}")

    # ---- the flow --------------------------------------------------------------
    def intro(self) -> None:
        self.out(self.style.bold("AdCP agent-buyer credit demo"))
        self.out(f"  Buyer: Acme Ltd ({OPERATOR_DOMAIN}), buying through its agent at {AGENT_URL}.")
        self.out(f"  Sellers: {', '.join(s.name for s in SELLERS)}.")
        self.out("  Solvent: " + ("called by each seller before it gives invoiced terms."
                                  if self.urls.solvent else "switched off. Each seller decides on its own."))
        self.step("Acme says what its agent may spend",
                  f"Acme publishes its agents in https://{OPERATOR_DOMAIN}/.well-known/trust.json. The entry below\n"
                  "carries a spending ceiling (billing_authority). That field is proposed; AdCP has no\n"
                  "place for it yet.")
        trust = requests.get(f"{self.urls.operator}/.well-known/trust.json", timeout=10).json()
        self.json(trust)

    def open_account(self, s: SellerConfig, agent: str = AGENT_URL, simulate_humans: bool = True) -> dict | None:
        self.step(f"{s.name}: what do you need from a new buyer?",
                  f"AdCP {s.adcp} seller. The agent reads the capabilities before asking for an account.")
        caps = self.call(s, "get_adcp_capabilities", {})
        posture = caps["account"].get("agent_onboarding")
        if posture:
            tier = posture["billing"]["operator"]
            self.out(f"  Operator billing is '{tier['mode']}', needs: "
                     + ", ".join(r["kind"] for r in tier["requirements"]) + f". Start at {posture['url']}")
        else:
            self.out("  No onboarding posture (3.2 seller). The agent can't tell in advance what it will be asked for.")

        entry = {"brand": BRAND, "operator": OPERATOR_DOMAIN, "billing": "operator",
                 "billing_entity": BILLING_ENTITY, "payment_terms": "net_30"}
        if s.adcp == "3.3":
            self.step(f"{s.name}: ask for an account on net 30",
                      "request_account (3.3, experimental). requested_credit_limit is a proposed field\n"
                      f"(not in AdCP yet). The agent asks for {_fmt(s.requested_limit)}.")
            body = {"idempotency_key": adcp.idempotency_key(), **entry,
                    "currency": CURRENCY,
                    "requested_credit_limit": adcp.money(s.requested_limit, CURRENCY)}  # PROPOSED
            resp = self.call(s, "request_account", body, agent)
        else:
            self.step(f"{s.name}: ask for an account on net 30",
                      "sync_accounts (3.2). There's no field for how much credit the agent wants,\n"
                      "so the seller has to pick a number itself.")
            body = {"idempotency_key": adcp.idempotency_key(), "accounts": [entry]}
            resp = self.call(s, "sync_accounts", body, agent)
        self.behind_the_scenes()

        # Keep going until the account is usable or a human is needed and we stop.
        for _ in range(4):
            pending = self._pending(s, resp)
            if pending is None:
                break
            kind, message, url = pending
            self.human(message, url)
            if not simulate_humans:
                self.out("    Stopping here for this seller: nobody at Acme is going to fill this in.")
                return None
            self.out(self.style.dim("    (Demo: pretending the person has done it.)"))
            self.pause()
            acct_id = self._account_id(s, agent)
            requests.post(f"{self.urls.sellers[s.id]}/_demo/human_step",
                          json={"account_id": acct_id, "kind": kind}, timeout=10)
            self.out(f"  The agent asks again.")
            if s.adcp == "3.3":
                resp = self.call(s, "request_account", body, agent)
            else:
                resp = self.call(s, "sync_accounts", {**body, "idempotency_key": adcp.idempotency_key()}, agent)
            self.behind_the_scenes()

        accts = requests.post(f"{self.urls.sellers[s.id]}/list_accounts",
                              headers={"Signature-Agent": agent}, timeout=10).json()["accounts"]
        acct = accts[0]
        limit = (acct.get("credit_limit") or {}).get("amount", 0)
        self.out(self.style.green(f"  Account {acct['account_id']} is {acct['status']}: "
                                  f"{acct.get('payment_terms')}, credit limit {_fmt(limit)}."))
        return acct

    def _pending(self, s: SellerConfig, resp: dict):
        if s.adcp == "3.3":
            r = resp["request"]
            if r["state"] == "in_review" and r["pending_action"]["waiting_on"] == "buyer":
                pa = r["pending_action"]
                return pa["kind"], pa["message"], pa["url"]
            return None
        a = resp["accounts"][0]
        if a["status"] == "pending_approval":
            return "credit_application", a["setup"]["message"], a["setup"]["url"]
        return None

    def _account_id(self, s: SellerConfig, agent: str) -> str:
        accts = requests.post(f"{self.urls.sellers[s.id]}/list_accounts",
                              headers={"Signature-Agent": agent}, timeout=10).json()["accounts"]
        return accts[0]["account_id"]

    def place_order(self, s: SellerConfig, acct: dict) -> float:
        self.step(f"{s.name}: place the first order",
                  f"create_media_buy for {_fmt(s.order)}, invoiced on the account's terms.")
        body = {"idempotency_key": adcp.idempotency_key(), "account": {"account_id": acct["account_id"]},
                "brand": BRAND, "start_time": "2026-11-02T00:00:00Z", "end_time": "2026-11-30T23:59:59Z",
                "total_budget": adcp.money(s.order, CURRENCY), "po_number": f"ACME-{s.id.upper()}-001"}
        resp = self.call(s, "create_media_buy", body)
        if resp.get("errors"):
            err = resp["errors"][0]
            if err["code"] == "CREDIT_LIMIT_EXCEEDED":
                avail = err["details"]["available_credit"]
                self.out(self.style.yellow(f"  Over the limit. The agent cuts the order to {_fmt(avail)} and tries again."))
                self.pause()
                body = {**body, "idempotency_key": adcp.idempotency_key(), "total_budget": adcp.money(avail, CURRENCY)}
                resp = self.call(s, "create_media_buy", body)
        if resp.get("errors"):
            return 0.0
        amount = resp["total_budget"]["amount"]
        self.out(self.style.green(f"  Order {resp['media_buy_id']} accepted: {_fmt(amount)}."))
        self.behind_the_scenes()
        return amount

    def summary(self) -> None:
        self.step("Where Acme stands")
        total_limit = total_ordered = 0.0
        self.out(f"  {'Seller':<22}{'Limit':>14}{'Ordered':>14}")
        for s in SELLERS:
            lim, ordered = self.results.get(s.id, (0.0, 0.0))
            total_limit += lim
            total_ordered += ordered
            self.out(f"  {s.name:<22}{_fmt(lim):>14}{_fmt(ordered):>14}")
        self.out(f"  {'Total':<22}{_fmt(total_limit):>14}{_fmt(total_ordered):>14}")
        self.out(f"  Acme's ceiling for this agent: {_fmt(CEILING)}")
        if total_limit > CEILING or total_ordered > CEILING:
            self.out(self.style.red(
                f"  Acme's agent can now owe {_fmt(total_limit)} and has already committed {_fmt(total_ordered)}. "
                f"No seller could see that."))
        else:
            self.out(self.style.green("  Limits and orders stay inside what Acme allowed."))

    def main_flow(self) -> None:
        self.intro()
        for s in SELLERS:
            acct = self.open_account(s)
            if acct is None:
                self.results[s.id] = (0.0, 0.0)
                continue
            ordered = self.place_order(s, acct)
            limit = (acct.get("credit_limit") or {}).get("amount", 0.0)
            self.results[s.id] = (limit, ordered)
        self.summary()

    def rogue_flow(self) -> None:
        self.intro()
        self.out("")
        self.out(self.style.yellow(f"  This time the caller is {ROGUE_AGENT_URL}, which says it buys for Acme"))
        self.out(self.style.yellow("  but isn't in Acme's trust.json."))
        self.open_account(SELLERS[1], agent=ROGUE_AGENT_URL, simulate_humans=False)


def run(urls: Urls, scenario: str = "main", **kw) -> Run:
    r = Run(urls=urls, **kw)
    if scenario == "rogue":
        r.rogue_flow()
    else:
        r.main_flow()
    return r


if __name__ == "__main__":
    sys.exit("Run with: python -m adcp_credit_check")
