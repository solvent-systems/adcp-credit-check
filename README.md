# adcp-credit-check

A pre-trade credit check for AI agents buying ads over [AdCP](https://github.com/adcontextprotocol/adcp), as a runnable simulation.

AI buying agents can now find ad sellers and place orders in seconds. Getting invoiced terms still takes a person filling in a credit application, often for days. AdCP leaves that step to each seller, and no seller can see what a buyer's agents have already been given by the others. This repo shows the check a seller could call instead: is this agent allowed to commit its company, and how much does the company already owe elsewhere?

It's a simulation. The sellers are mocks, the AdCP messages follow the real schemas (released 3.2 and the open 3.3 PRs), and the fields AdCP doesn't have yet are marked as proposed. Built by [Solvent](https://solvent.systems).

```
pip install -e .
python -m adcp_credit_check --brief
```

## What it shows

Acme Ltd (`acme.example`) runs a buying agent at `https://buy.acme.example/adcp`. Acme publishes that agent in its `trust.json`, with a ceiling: the agent may leave Acme owing at most USD 50,000.

The agent finds three sellers and asks each for an account on net 30:

| Seller | AdCP | Own limit for a new account | Agent asks for | First order |
|---|---|---|---|---|
| Northwind Streaming | 3.2 (`sync_accounts`) | 20,000 | (no field for it) | 15,000 |
| Harbour News | 3.3 (`request_account`) | 30,000 | 20,000 | 19,000 |
| Pixel Square Audio | 3.3, also wants a signed contract | 25,000 | 20,000 | 18,000 |

Each seller on its own would say yes. Before it does, it calls Solvent's `POST /v1/check`, which looks at two things:

- **Authority.** Does Acme's `trust.json` list this agent, and how much may it commit Acme to?
- **Exposure.** How much have Acme's agents already been given, or asked for, at other sellers?

Solvent answers with a decision (`approve`, `approve_lower`, `refer`, `decline`), a recommended limit and the reasons. Northwind and Harbour get approved. At Pixel, Acme already holds 40,000 of limits elsewhere, so Solvent recommends 10,000 and flags `exceeds_mandate` and `fan_out`. Pixel opens the account at 10,000, the agent's 18,000 order bounces, and it retries at 10,000.

Run it with `--without-solvent` to compare: the same three sellers each approve on their own numbers, the agent ends up with 60,000 of limits and 52,000 of orders against a 50,000 ceiling, and no seller can see it. That run also shows every human step the protocol expects today.

Solvent never holds money and doesn't score the agent's creditworthiness. The seller makes the decision and carries the credit. Sellers learn totals from Solvent, never which other sellers are involved.

## Run it

```
python -m adcp_credit_check                    # step through, Enter per step
python -m adcp_credit_check --auto             # scripted, 1.5 s per step (--delay N)
python -m adcp_credit_check --auto --brief     # no JSON bodies, for a short recording
python -m adcp_credit_check --without-solvent  # sellers decide alone
python -m adcp_credit_check --scenario rogue   # an agent Acme never listed
python -m adcp_credit_check --serve            # start everything and print the URLs, for curl
```

Each service (Acme's web host, Solvent, three sellers) runs on its own free localhost port. FastAPI's `/docs` page works on each one under `--serve`.

Tests: `pip install -e ".[test]" && pytest -q`.

## The check API

`POST /v1/check`, called by a seller before it gives an agent buyer invoiced terms:

```json
{
  "seller": {"id": "pixel", "name": "Pixel Square Audio"},
  "agent_url": "https://buy.acme.example/adcp",
  "operator": "acme.example",
  "billing": "operator",
  "payment_terms": "net_30",
  "requested_credit_limit": {"amount": 20000, "currency": "USD"}
}
```

Answer: `decision` (`approve`, `approve_lower`, `refer`, `decline`), `recommended_credit_limit`, `authority`, `exposure` (totals at other sellers, never their names), `flags` (`authority_failed`, `no_ceiling`, `exceeds_mandate`, `fan_out`) and plain-language `reasons`. Sellers keep Solvent's view current with `POST /v1/report`, which mirrors AdCP's `get_account_financials.credit`. See `AGENTS.md` for the full shapes.

## Files

- `adcp_credit_check/scenario.py`: the cast (Acme, its agent, the three sellers, the amounts).
- `adcp.py`: builders for the AdCP JSON shapes, with the schema each one follows.
- `seller.py`: a mock seller agent: `get_adcp_capabilities`, `sync_accounts`, `request_account`, `list_accounts`, `create_media_buy`, `get_account_financials`, and `/_demo/human_step`, which stands in for a person filling in a form on the seller's site.
- `solvent.py`: the check service: `POST /v1/check`, `POST /v1/report`, `GET /v1/checks`, `GET /v1/exposure/{operator}`. In memory.
- `buyer.py`: Acme's agent walking the flow, plus the narration (it reads Solvent's check log so you can see the call the seller made).
- `__main__.py`: starts everything and runs the buyer.

## What's real AdCP and what isn't

Schemas read on 20 Oct 2026 from [adcontextprotocol/adcp](https://github.com/adcontextprotocol/adcp): `main` at `7637b371`, PR #8170 at `0771886c`, PR #8293 at `60e30c8d`.

| Field or task | Status | Source |
|---|---|---|
| `get_adcp_capabilities` → `account.supported_billing`, `require_operator_auth`, `account_financials` | Released (3.2) | `protocol/get-adcp-capabilities-response.json` |
| `account.agent_onboarding.billing.{party}.{mode, url, requirements[]}`, kinds `billing_entity`, `contract`, `credit_application` | 3.3, PR #8170, open, experimental | `core/agent-onboarding.json`, `enums/agent-onboarding-requirement-kind.json` |
| `sync_accounts` with `billing`, `billing_entity`, `payment_terms`; result `status: pending_approval` + `setup.{url, message, expires_at}` | Released (3.2) | `account/sync-accounts-request.json`, `-response.json` |
| `request_account`, record `state` (`in_review`, `resolved`), `pending_action.{waiting_on, kind, url, message}` with `kind: credit_application` / `accept_terms` | 3.3, PR #8293 for RFC #8280, open, experimental. May slip to 3.4 | `account/request-account-*.json`, `core/pending-action.json` |
| Account `payment_terms`, `credit_limit {amount, currency}` | Released | `core/account.json` |
| `create_media_buy` with `account`, `brand`, `total_budget`, `po_number` | Released | `media-buy/create-media-buy-request.json` |
| `get_account_financials` → `credit.{credit_limit, available_credit, utilization_percent}` | Released | `account/get-account-financials-response.json` |
| `trust.json` `agents[]` entry | On main (3.3 identity work, merged 13 Oct) | `trust/v1/trust.json` |
| **`requested_credit_limit`** on `request_account` | **Proposed.** Proposed here for #8118 / #8280 | none |
| **`profiles.adcp.billing_authority.{max_outstanding, valid_until}`** on the trust.json agent entry | **Proposed** here, same issues | none |
| **`CREDIT_LIMIT_EXCEEDED`** error on `create_media_buy` | **Made up.** The error-code list is open, but has no code for this | none |
| Solvent's `/v1/check` and `/v1/report` | Not AdCP. Seller-to-Solvent API | this demo |

Simplifications: every AdCP task is a plain JSON POST to `/<task>` (real agents speak MCP or A2A). The caller is identified by an unsigned `Signature-Agent` header, where a real 3.3 seller verifies a Web Bot Auth signature. `acme.example` resolves to a local port instead of DNS and TLS. The official Python SDK (PyPI `adcp` 8.1.3) wasn't used: it pulls in `a2a-sdk`, `mcp`, `protobuf` and `cryptography`, and doesn't have the 3.3 shapes yet. No new dependencies were added.

## Friction found

Every place where the protocol forced a human step, lacked a field, or left the answer open. Numbers refer to adcontextprotocol/adcp issues and PRs.

**Human steps**

1. **Credit is a human step at every seller.** Both routes end at a web form: `sync_accounts` returns `pending_approval` with `setup.url`, and `request_account` returns `pending_action {waiting_on: buyer, kind: credit_application, url}`. Without Solvent the demo needs four of them for three sellers. The spec says so on purpose ("a human stays involved wherever the seller takes on new risk").
2. **Accepting a contract stays human even when credit is automatic.** Pixel's `contract` requirement turns into `pending_action.kind: accept_terms` after credit clears. #8118 D10 keeps "agents accepting contracts on an operator's behalf" out of scope, so no amount of automation removes it.
3. **The seller's posture can't say that credit is decided straight away.** `agent_onboarding` lists `credit_application` as a requirement with a URL. There's no way to say "automatic up to 25,000" or "decided by a check, no form", so the agent can't tell whether it's waiting minutes or a week. #7700's `credit_application: required_above` + threshold would cover it; that's backlog.
4. **A 3.2 seller publishes no posture at all.** Northwind's requirements only show up as free text in `setup.message` after the agent has already asked.

**Missing fields**

5. **No way to ask for an amount.** `sync_accounts` (3.2) and `request_account` (#8293) carry `payment_terms` but no credit amount; `credit_limit` exists only on the answer. Northwind had to ask Solvent about its own default instead of what the agent wanted. This repo adds `requested_credit_limit`.
6. **Nowhere for the billed company to say how much the agent may commit it to.** Grants say an agent may act for a brand; the trust.json agent entry says who runs it. Neither has an amount or billing authority. This repo adds `billing_authority.max_outstanding`.
7. **Nothing ties the operator domain to the legal entity.** `billing_entity` (legal name, registration number) is whatever the agent sends. trust.json speaks for `acme.example`, not for "Acme Ltd, GB-12345678". Solvent keys exposure on the operator domain, so one company under two domains would split its exposure, and a buyer could put someone else's registration number in `billing_entity`.
8. **No error for "this order is over your credit".** `ACCOUNT_PAYMENT_REQUIRED` is terminal and means a balance is owed; `BUDGET_EXCEEDED` is about the media buy's own budget. The demo invents `CREDIT_LIMIT_EXCEEDED` with `details.available_credit` so the agent can cut the order and retry.
9. **No way to pass a third-party check along.** A seller can't attach Solvent's result to the account, and a buyer can't present one. #7700's `evidence[]` is the closest slot: unsigned, identity-only vocabulary, and sellers must confirm with the verifier themselves.
10. **Exposure has no shared source.** `get_account_financials` is per account, per seller, and the 3.3 work keeps credit data off the wire (#8113). Sellers report to Solvent out of band (`/v1/report`); nothing in AdCP prompts them to.

**Ambiguous**

11. **Can a seller grant less than was asked?** `request_account` says terms are "accepted or rejected, never silently remapped" (`billing`, `billing_entity`, `payment_terms`). If a requested amount became a term, Pixel would have to reject 20,000 instead of offering 10,000. The demo offers less and the agent only finds out by reading `list_accounts` or bouncing an order. A counter-offer shape would help.
12. **The resolved request doesn't say what was granted.** A `resolved` record carries `account_id` and nothing about terms or limit, so the agent needs a second call (`list_accounts`) to learn its limit.
13. **Which `billing` value fits a company running its own agent?** The demo uses `operator` with Acme as both brand and operator ("brand buying direct"). `agent` billing is meant for an intermediary consolidating across brands (the Apostra case in #8280). Under 3.3, `open` operator billing still needs an existing billing relationship, so a new company always lands on `application`.
14. **The trust.json role token for a buying agent.** The schema shows namespaced roles like `adcp:sales`. The demo guesses `adcp:buying`.
15. **One ceiling, several currencies.** Sellers bind accounts to a currency; a ceiling in USD against a GBP seller needs FX that nobody owns. The demo uses USD throughout.

## How a real seller would call Solvent

The Prebid salesagent is the reference AdCP seller. Today it stores `credit_limit` on the account (`src/core/database/models.py`) and never reads it when an order is placed, and it applies whatever `payment_terms` the buyer sends in `sync_accounts` (`src/core/tools/accounts.py`, provisioning path). salesagent #1772 says why: "we have no commercial-relationship model". The two places a check would go:

1. **Account provisioning** (`sync_accounts`, later `request_account`): before applying `payment_terms` other than `prepay`, call `/v1/check` and set `credit_limit` from the answer, or fall back to `pending_approval` with the credit form. This is `credit_decision()` in `seller.py`.
2. **`create_media_buy`**: before the manual-approval branch, compare `total_budget` with available credit and send the buy to approval (or reject it) when it doesn't fit. That needs salesagent #1722 (`get_account_financials`) first. Then report the new outstanding balance.

salesagent has no plug-in point for a commercial policy, so either hook would need a small interface, off by default, agreed with the maintainers on #1772 first.

## Guesses about the spec

- PR #8170 and PR #8293 are open; field names may change before the 26 Oct 2026 scope freeze, and `request_account` may move to 3.4.
- Re-sending `request_account` for the same natural key to poll the request (the response schema says "an existing request for the same key was returned"). A real buyer might use `get_account_requests` or a webhook instead.
- The `requested` echo on the request record includes the proposed `requested_credit_limit`.
- `get_adcp_capabilities` returns more required fields than the demo fills in (versions, protocol lists); only the account block is faithful.

## Feedback

Issues and pull requests are welcome, especially from people running AdCP seller or buyer agents: what your credit desk actually checks, and where this flow is wrong. Contact: hello@solvent.systems.
