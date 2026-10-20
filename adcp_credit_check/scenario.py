"""The demo's cast: one buying company, its agent, three sellers.

All names and domains are made up and use the reserved .example TLD.
"""

from __future__ import annotations

from dataclasses import dataclass, field

CURRENCY = "USD"

# The company that will be invoiced, and the agent it runs.
OPERATOR_DOMAIN = "acme.example"
AGENT_URL = "https://buy.acme.example/adcp"
# An agent that claims to buy for Acme but isn't in Acme's trust.json.
ROGUE_AGENT_URL = "https://agents.cheapreach.example/acme"

BRAND = {"domain": OPERATOR_DOMAIN}

# core/business-entity.json
BILLING_ENTITY = {
    "legal_name": "Acme Ltd",
    "registration_number": "GB-12345678",
    "vat_id": "GB123456789",
    "address": {"street": "1 Example Street", "city": "London",
                "postal_code": "EC1A 1AA", "country": "GB"},
    "contacts": [{"role": "billing", "name": "Accounts payable",
                  "email": "ap@acme.example"}],
}

# What Acme publishes about its agent. billing_authority is PROPOSED (see adcp.py).
CEILING = 50_000
MANDATE_VALID_UNTIL = "2027-06-30T00:00:00Z"


def trust_record(ceiling: float = CEILING, valid_until: str = MANDATE_VALID_UNTIL) -> dict:
    """Acme's /.well-known/trust.json (trust/v1/trust.json shape)."""
    return {
        "version": 1,
        "agents": [
            {
                "url": AGENT_URL,
                "roles": ["adcp:buying"],  # role token guessed; schema only shows adcp:sales
                "description": "Acme's media buying agent",
                "profiles": {
                    "adcp": {
                        # PROPOSED: a ceiling published by the party that gets invoiced
                        "billing_authority": {
                            "max_outstanding": {"amount": ceiling, "currency": CURRENCY},
                            "valid_until": valid_until,
                        }
                    }
                },
            }
        ],
        "grants": [],
        "acknowledgements": [],
    }


@dataclass
class SellerConfig:
    id: str
    name: str
    domain: str
    # "3.2": sync_accounts only. "3.3": also request_account and agent_onboarding.
    adcp: str
    # Most the seller's credit desk gives a new net_30 account on its own.
    default_limit: float
    requirements: list[str] = field(default_factory=lambda: ["billing_entity", "credit_application"])
    # What the buyer asks for (request_account only; 3.2 has no field for it).
    requested_limit: float | None = None
    # What the buyer agent wants to spend in its first order here.
    order: float = 0

    @property
    def onboarding_url(self) -> str:
        return f"https://{self.domain}/advertise/apply"


SELLERS = [
    SellerConfig(id="northwind", name="Northwind Streaming", domain="northwind-tv.example",
                 adcp="3.2", default_limit=20_000, order=15_000),
    SellerConfig(id="harbour", name="Harbour News", domain="harbournews.example",
                 adcp="3.3", default_limit=30_000, requested_limit=20_000, order=19_000),
    SellerConfig(id="pixel", name="Pixel Square Audio", domain="pixelsquare.example",
                 adcp="3.3", default_limit=25_000, requested_limit=20_000, order=18_000,
                 requirements=["billing_entity", "contract", "credit_application"]),
]
