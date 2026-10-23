from __future__ import annotations

import dataclasses
import logging
import random
import time

from .client import BlockedError, VintedClient
from .config import Config, SearchConfig
from .models import Listing
from .notify import Deal, build_notifiers
from .scoring import Score, build_scorer
from .storage import Store

log = logging.getLogger(__name__)


class Runner:
    def __init__(self, config: Config, client_factory=VintedClient):
        self.config = config
        self.store = Store(config.database)
        self.notifiers = build_notifiers(config.notifiers)
        self._client_factory = client_factory
        self._clients: dict[str, VintedClient] = {}

    def client(self, domain: str) -> VintedClient:
        if domain not in self._clients:
            settings = dataclasses.replace(self.config.client, domain=domain)
            self._clients[domain] = self._client_factory(settings)
        return self._clients[domain]

    def fetch(self, search: SearchConfig) -> list[Listing]:
        client = self.client(search.domain)
        seen: dict[int, Listing] = {}
        for params in search.queries:
            try:
                raw_items = client.search(params, pages=search.pages)
            except BlockedError as exc:
                # Retries are exhausted: hammering on with the remaining queries only digs
                # the hole deeper. Keep what we have and try again next run.
                log.error("[%s] blocked on %s, skipping the rest of this run: %s",
                          search.name, params.get("search_text", params), exc)
                break
            for raw in raw_items:
                try:
                    listing = Listing.from_api(raw)
                except (KeyError, TypeError, ValueError):
                    continue
                seen.setdefault(listing.id, listing)
        return list(seen.values())

    def _keep(self, search: SearchConfig, listing: Listing) -> bool:
        if search.min_price is not None and listing.price < search.min_price:
            return False
        if search.max_price is not None and listing.price > search.max_price:
            return False
        if search.include and not search.include.search(listing.title):
            return False
        if search.exclude and search.exclude.search(f"{listing.title}\n{listing.description}"):
            return False
        return True

    def run_search(self, search: SearchConfig) -> list[Deal]:
        scorer = build_scorer(search.scorer)
        listings = [item for item in self.fetch(search) if self._keep(search, item)]
        scored: list[tuple[Listing, Score]] = []
        detail_budget = search.fetch_details
        for listing in listings:
            prev = self.store.previous(search.name, listing.id)
            if prev and prev.description and not listing.description:
                listing.description = prev.description
            score = scorer.score(listing)
            if score.needs_details and detail_budget > 0 and not listing.description and score.reject in (None, "cpu unknown"):
                detail_budget -= 1
                desc = self.client(search.domain).item_description(listing.id)
                if desc:
                    listing.description = desc
                    if search.exclude and search.exclude.search(desc):
                        continue
                    score = scorer.score(listing)
            self.store.upsert(search.name, listing, score)
            scored.append((listing, score))

        deals: list[Deal] = []
        rules = search.notify
        for listing, score in sorted(scored, key=lambda pair: pair[1].value, reverse=True):
            if score.reject or score.value < rules.min_value:
                continue
            prev = self.store.previous(search.name, listing.id)
            notified = prev.notified_price if prev else None
            if notified is None or (rules.price_drop and listing.price < notified - 0.01):
                deals.append(
                    Deal(search.name, listing.title, listing.price, listing.currency, score.value,
                         score.summary, listing.url, listing.photo, price_drop_from=notified)
                )
                self.store.mark_notified(search.name, listing.id, listing.price)
            if len(deals) >= rules.top:
                break
        self.store.commit()
        log.info("[%s] %d listings, %d new deals", search.name, len(scored), len(deals))
        return deals

    def run_once(self) -> list[Deal]:
        all_deals: list[Deal] = []
        for search in self.config.searches:
            try:
                deals = self.run_search(search)
            except Exception:
                log.exception("[%s] search crashed", search.name)
                continue
            all_deals += deals
            if deals:
                for notifier in self.notifiers:
                    notifier.send(deals)
        return all_deals

    def run_forever(self) -> None:
        while True:
            started = time.monotonic()
            self.run_once()
            interval = self.config.interval_minutes * 60
            jitter = random.uniform(-1, 1) * self.config.jitter_minutes * 60
            delay = max(60.0, interval + jitter - (time.monotonic() - started))
            log.info("next run in %.1f min", delay / 60)
            time.sleep(delay)
