from __future__ import annotations

import argparse
import json
import logging
import sys

from .client import ClientSettings, VintedClient, params_from_url
from .config import load_config
from .models import Listing
from .runner import Runner
from .scoring import build_scorer
from .storage import Store


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="vinted-scraper", description="Watch Vinted searches and rank deals by value.")
    parser.add_argument("-c", "--config", default="config.yaml")
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("run", help="scrape every interval_minutes, forever")
    sub.add_parser("once", help="scrape once and exit (for cron / systemd timers)")
    top = sub.add_parser("top", help="show the best items seen recently")
    top.add_argument("search", nargs="?")
    top.add_argument("-n", type=int, default=15)
    top.add_argument("--hours", type=float, default=72)
    score = sub.add_parser("score", help="score a single title/description without scraping")
    score.add_argument("text")
    score.add_argument("--price", type=float, required=True)
    score.add_argument("--search", help="use the scorer of this configured search")
    ui = sub.add_parser("ui", help="web page for editing the config and browsing deals")
    ui.add_argument("--host", default="127.0.0.1", help="use 0.0.0.0 to reach it from other devices (exposes your tokens)")
    ui.add_argument("--port", type=int, default=8765)
    probe = sub.add_parser("probe", help="fetch one search URL and print raw results (connectivity check)")
    probe.add_argument("url")

    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    if args.command == "probe":
        domain, params = params_from_url(args.url)
        client = VintedClient(ClientSettings(domain=domain or "www.vinted.de", min_delay=1, max_delay=2))
        items = client.search(params, per_page=20)
        for item in items:
            listing = Listing.from_api(item)
            print(f"{listing.price:>8g} {listing.currency}  {listing.title}  {listing.url}")
        print(f"{len(items)} items", file=sys.stderr)
        if not items:
            _diagnose(client)
        return 0

    if args.command == "ui":
        from .webui import serve

        serve(args.config, args.host, args.port)
        return 0

    config = load_config(args.config)
    if args.command == "score":
        search = next((s for s in config.searches if s.name == args.search), config.searches[0])
        listing = Listing(id=0, title=args.text, price=args.price, currency="EUR", url="")
        result = build_scorer(search.scorer).score(listing)
        print(json.dumps({**result.__dict__, "value": result.value}, indent=2))
        return 0
    if args.command == "top":
        for row in Store(config.database).top(args.search, args.n, args.hours):
            print(f"{row['value']:7.1f}  {row['price']:>7g} {row['currency']}  [{row['search']}] {row['title']}")
            print(f"         {row['summary']}\n         {row['url']}")
        return 0

    runner = Runner(config)
    if args.command == "once":
        runner.run_once()
    else:
        runner.run_forever()
    return 0


def _diagnose(client: VintedClient) -> None:
    """Print what Vinted actually answered, to debug empty results."""
    resp = client.last_response
    session = client._session
    print("\n--- diagnostics (paste this when reporting a problem) ---", file=sys.stderr)
    print(f"curl_cffi impersonating: {client.settings.impersonate[client._fingerprint_idx]}", file=sys.stderr)
    if session is not None:
        print(f"cookies: {sorted(session.cookies.keys())}", file=sys.stderr)
    if resp is None:
        print("no API response recorded", file=sys.stderr)
        return
    print(f"last request: {resp.url}", file=sys.stderr)
    print(f"status: {resp.status_code}  content-type: {resp.headers.get('content-type')}", file=sys.stderr)
    print(f"body: {resp.text[:800]}", file=sys.stderr)


if __name__ == "__main__":
    sys.exit(main())
