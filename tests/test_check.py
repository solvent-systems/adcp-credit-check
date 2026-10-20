"""Tests for the AdCP agent-buyer credit demo (demo/adcp_credit)."""

import pytest
from fastapi.testclient import TestClient

from adcp_credit_check import solvent
from adcp_credit_check.scenario import AGENT_URL, OPERATOR_DOMAIN, ROGUE_AGENT_URL, trust_record

NOW = 1_792_000_000.0  # Oct 2026


def make_client(records=None):
    records = {OPERATOR_DOMAIN: trust_record()} if records is None else records
    return TestClient(solvent.make_app(records.get, clock=lambda: NOW))


def check(client, seller="northwind", amount=20_000, agent=AGENT_URL, operator=OPERATOR_DOMAIN):
    r = client.post("/v1/check", json={
        "seller": {"id": seller, "name": seller.title()},
        "agent_url": agent, "operator": operator, "billing": "operator",
        "billing_entity": {"legal_name": "Acme Ltd"}, "payment_terms": "net_30",
        "requested_credit_limit": {"amount": amount, "currency": "USD"},
    })
    assert r.status_code == 200
    return r.json()


def report(client, seller, limit, outstanding=0, status="active"):
    r = client.post("/v1/report", json={
        "seller": {"id": seller, "name": seller.title()}, "operator": OPERATOR_DOMAIN,
        "account_id": f"acc_{seller}", "status": status,
        "credit_limit": {"amount": limit, "currency": "USD"},
        "outstanding": {"amount": outstanding, "currency": "USD"},
    })
    assert r.status_code == 200


def test_authority_pass_approves_full_amount():
    res = check(make_client())
    assert res["decision"] == "approve"
    assert res["recommended_credit_limit"]["amount"] == 20_000
    assert res["authority"]["status"] == "verified"
    assert res["authority"]["max_outstanding"]["amount"] == 50_000
    assert res["flags"] == []


def test_authority_fails_for_agent_not_in_trust_json():
    res = check(make_client(), agent=ROGUE_AGENT_URL)
    assert res["decision"] == "decline"
    assert res["recommended_credit_limit"]["amount"] == 0
    assert "authority_failed" in res["flags"]


def test_authority_fails_without_trust_json():
    res = check(make_client(records={}))
    assert res["decision"] == "decline"
    assert "authority_failed" in res["flags"]


def test_authority_fails_when_expired():
    rec = trust_record(valid_until="2026-01-01T00:00:00Z")
    res = check(make_client({OPERATOR_DOMAIN: rec}))
    assert res["decision"] == "decline"
    assert any("expired" in r for r in res["reasons"])


def test_listed_agent_without_ceiling_is_referred():
    rec = trust_record()
    del rec["agents"][0]["profiles"]["adcp"]["billing_authority"]
    res = check(make_client({OPERATOR_DOMAIN: rec}))
    assert res["decision"] == "refer"
    assert "no_ceiling" in res["flags"]


def test_third_seller_is_flagged_over_the_mandate():
    c = make_client()
    assert check(c, "northwind")["decision"] == "approve"
    report(c, "northwind", 20_000, outstanding=15_000)
    assert check(c, "harbour")["decision"] == "approve"
    report(c, "harbour", 20_000, outstanding=19_000)

    res = check(c, "pixel", amount=20_000)
    assert res["decision"] == "approve_lower"
    assert res["recommended_credit_limit"]["amount"] == 10_000
    assert "exceeds_mandate" in res["flags"]
    assert "fan_out" in res["flags"]
    assert res["exposure"]["other_sellers"] == 2
    assert res["exposure"]["limits_elsewhere"]["amount"] == 40_000
    assert res["exposure"]["outstanding_elsewhere"]["amount"] == 34_000


def test_pending_request_elsewhere_counts_against_headroom():
    c = make_client()
    check(c, "northwind", amount=30_000)  # checked, not yet granted
    res = check(c, "harbour", amount=30_000)
    assert res["decision"] == "approve_lower"
    assert res["recommended_credit_limit"]["amount"] == 20_000


def test_ceiling_used_up_declines():
    c = make_client()
    report(c, "northwind", 50_000)
    res = check(c, "harbour")
    assert res["decision"] == "decline"
    assert "exceeds_mandate" in res["flags"]


def test_check_does_not_name_other_sellers():
    c = make_client()
    check(c, "northwind")
    report(c, "northwind", 20_000)
    res = check(c, "harbour")
    assert "northwind" not in str(res).lower()


@pytest.mark.parametrize("with_solvent,limits,ordered", [
    (True, 50_000, 44_000),
    (False, 60_000, 52_000),
])
def test_end_to_end_flow(with_solvent, limits, ordered):
    from adcp_credit_check import buyer
    from adcp_credit_check.__main__ import start

    urls, servers = start(with_solvent=with_solvent)
    try:
        run = buyer.run(urls, interactive=False, delay=0, out=lambda s: None,
                        style=buyer.Style(color=False))
    finally:
        for s in servers:
            s.should_exit = True
    assert sum(lim for lim, _ in run.results.values()) == limits
    assert sum(o for _, o in run.results.values()) == ordered
