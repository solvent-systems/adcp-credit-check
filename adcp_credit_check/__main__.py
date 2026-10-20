"""Run the AdCP agent-buyer credit demo.

    python -m adcp_credit_check                    # step through, Enter per step
    python -m adcp_credit_check --auto             # scripted, for a recording
    python -m adcp_credit_check --without-solvent  # same flow, sellers decide alone
    python -m adcp_credit_check --scenario rogue   # an agent Acme never authorised
    python -m adcp_credit_check --serve            # just start the services

Every service runs on its own localhost port, picked at start.
"""

from __future__ import annotations

import argparse
import socket
import sys
import threading
import time

import requests
import uvicorn
from fastapi import FastAPI

from . import buyer, seller, solvent
from .scenario import OPERATOR_DOMAIN, SELLERS, trust_record


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def serve(app: FastAPI, port: int) -> uvicorn.Server:
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    threading.Thread(target=server.run, daemon=True).start()
    for _ in range(200):
        if server.started:
            return server
        time.sleep(0.02)
    raise RuntimeError(f"server on port {port} didn't start")


def operator_app() -> FastAPI:
    """Acme's web host. Serves only its trust record."""
    app = FastAPI(title="acme.example (demo)")
    record = trust_record()

    @app.get("/.well-known/trust.json")
    def trust() -> dict:
        return record

    return app


def start(with_solvent: bool = True) -> tuple[buyer.Urls, list[uvicorn.Server]]:
    servers = []
    op_port = free_port()
    servers.append(serve(operator_app(), op_port))
    operator_url = f"http://127.0.0.1:{op_port}"

    # Domain -> local host, standing in for DNS + TLS.
    hosts = {OPERATOR_DOMAIN: operator_url}

    def resolve_trust(domain: str) -> dict | None:
        base = hosts.get(domain)
        if not base:
            return None
        r = requests.get(f"{base}/.well-known/trust.json", timeout=5)
        return r.json() if r.ok else None

    solvent_url = None
    if with_solvent:
        port = free_port()
        servers.append(serve(solvent.make_app(resolve_trust), port))
        solvent_url = f"http://127.0.0.1:{port}"

    seller_urls = {}
    for cfg in SELLERS:
        port = free_port()
        servers.append(serve(seller.make_seller_app(cfg, solvent_url), port))
        seller_urls[cfg.id] = f"http://127.0.0.1:{port}"
    return buyer.Urls(sellers=seller_urls, solvent=solvent_url, operator=operator_url), servers


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m adcp_credit_check", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--auto", action="store_true", help="don't wait for Enter between steps")
    p.add_argument("--delay", type=float, default=1.5, help="seconds between steps with --auto (default 1.5)")
    p.add_argument("--brief", action="store_true", help="hide the JSON bodies")
    p.add_argument("--no-color", action="store_true")
    p.add_argument("--without-solvent", action="store_true", help="sellers decide on their own")
    p.add_argument("--scenario", choices=["main", "rogue"], default="main")
    p.add_argument("--serve", action="store_true", help="start the services and wait (for curl)")
    args = p.parse_args(argv)

    urls, _ = start(with_solvent=not args.without_solvent)

    if args.serve:
        print(f"Acme trust.json   {urls.operator}/.well-known/trust.json")
        if urls.solvent:
            print(f"Solvent           {urls.solvent}/docs")
        for cfg in SELLERS:
            print(f"{cfg.name:<18}{urls.sellers[cfg.id]}/docs")
        print("Ctrl-C to stop.")
        try:
            while True:
                time.sleep(3600)
        except KeyboardInterrupt:
            return 0

    style = buyer.Style(color=sys.stdout.isatty() and not args.no_color)
    try:
        buyer.run(urls, scenario=args.scenario, interactive=not args.auto,
                  delay=args.delay if args.auto else 0, brief=args.brief, style=style)
    except (KeyboardInterrupt, EOFError):
        print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
