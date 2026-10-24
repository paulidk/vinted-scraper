from __future__ import annotations

import dataclasses
import json
import logging
import random
import time
from urllib.parse import urlparse

from .client import BlockedError, ClientSettings, VintedClient
from .config import Config, SearchConfig
from .models import Listing
from .notify import Deal, Podium, build_notifiers
from .ranking import build_podium, changed, rescore, score_stored
from .scoring import Score, build_scorer
from .storage import Placed, Store

log = logging.getLogger(__name__)


def make_client(settings: ClientSettings) -> VintedClient:
    from .kleinanzeigen import KleinanzeigenClient, is_kleinanzeigen

    return KleinanzeigenClient(settings) if is_kleinanzeigen(settings.domain) else VintedClient(settings)


class Runner:
    def __init__(self, config: Config, client_factory=make_client):
        self.config = config
        self.store = Store(config.database)
        self.notifiers = build_notifiers(config.notifiers)
        self._client_factory = client_factory
        self._clients: dict[str, VintedClient] = {}
        self._blocked: set[str] = set()  # searches whose fetch was cut short this run

    def client(self, domain: str) -> VintedClient:
        if domain not in self._clients:
            settings = dataclasses.replace(self.config.client, domain=domain)
            self._clients[domain] = self._client_factory(settings)
        return self._clients[domain]

    def client_for(self, search: SearchConfig, url: str = "") -> VintedClient:
        """The client of the site a listing came from (one search can span several sites)."""
        return self.client(urlparse(url).netloc or search.domain)

    def fetch(self, search: SearchConfig) -> list[Listing]:
        seen: dict[int, Listing] = {}
        blocked: set[str] = set()
        self._blocked.discard(search.name)

        def mostly_known(batch: list[dict]) -> bool:
            # Results are newest first: once a page is mostly listings from earlier runs, the
            # pages after it hold nothing new.
            ids = [int(raw["id"]) for raw in batch if str(raw.get("id") or "").isdigit()]
            return self.store.known(search.name, ids) >= len(ids) / 2

        for query in search.queries:
            domain = query.get("domain") or search.domain
            if domain in blocked:
                continue
            client = self.client(domain)
            params = {k: v for k, v in query.items() if k != "domain"}
            # Same key as before searches could span sites, so existing queries aren't crawled deep again.
            key = json.dumps(params if domain == search.domain else query, sort_keys=True)
            # Each query is fetched `pages` deep once (also after `pages` was raised); after that
            # only until the listings from earlier runs start, usually a single page.
            full = self.store.crawled_pages(search.name, key) < search.pages
            try:
                raw_items = client.search(params, pages=search.pages, until=None if full else mostly_known)
            except BlockedError as exc:
                # Retries are exhausted: hammering on with the remaining queries only digs
                # the hole deeper. Keep what we have and try again next run; other sites go on.
                log.error("[%s] blocked on %s %s, skipping the rest of this site this run: %s",
                          search.name, domain, params.get("search_text") or params, exc)
                self._blocked.add(search.name)
                blocked.add(domain)
                continue
            if full:
                self.store.set_crawled_pages(search.name, key, search.pages)
            log.info("[%s] %s %s: %d items", search.name, domain.removeprefix("www."),
                     params.get("search_text") or "(no text)", len(raw_items))
            for raw in raw_items:
                try:
                    listing = Listing.from_api(raw)
                except (KeyError, TypeError, ValueError):
                    continue
                seen.setdefault(listing.id, listing)
        return list(seen.values())

    def run_search(self, search: SearchConfig) -> list[Deal] | Podium | None:
        """Returns new deals, or in podium mode the new podium (None if it didn't change)."""
        scorer = build_scorer(search.scorer)
        listings = []
        for item in self.fetch(search):
            if search.keeps(item):
                listings.append(item)
            else:
                # Stored too, so paging knows it has seen it and a looser filter later picks it up.
                self.store.upsert(search.name, item, Score(0, item.effective_price, "excluded by filters", reject="excluded"))
        scored: list[tuple[Listing, Score]] = []
        detail_budget = search.fetch_details
        failures: list[Exception] = []
        log.info("[%s] scoring %d listings (fetching up to %d descriptions)", search.name, len(listings), detail_budget)
        for listing in listings:
            prev = self.store.previous(search.name, listing.id)
            # A description fetched earlier beats the snippet some sites put in search results.
            if prev and len(prev.description) > len(listing.description):
                listing.description, listing.partial_description = prev.description, prev.partial
            try:
                score = scorer.score(listing)
                if (score.needs_details and detail_budget > 0 and score.reject in (None, "cpu unknown")
                        and (not listing.description or listing.partial_description)):
                    detail_budget -= 1
                    desc = self.client_for(search, listing.url).item_description(listing.id)
                    if desc:
                        listing.description, listing.partial_description = desc, False
                        if search.exclude and search.exclude.search(desc):
                            # Still stored, so the description isn't fetched again next run.
                            score = Score(0, listing.effective_price, "excluded by description", reject="excluded")
                        else:
                            score = scorer.score(listing)
            except Exception as exc:
                # One odd listing must not take down the whole search. It is stored as rejected
                # so a description fetched above stays cached; the next run scores it again.
                log.exception("[%s] could not score %s (%s)", search.name, listing.id, listing.title)
                failures.append(exc)
                score = Score(0, listing.effective_price, "scoring failed", reject="scoring error")
            self.store.upsert(search.name, listing, score)
            scored.append((listing, score))
        if failures and len(failures) == len(listings):
            # Every listing failed: that's a scorer bug, not odd listings. Let run_once report it.
            self.store.commit()
            raise RuntimeError(f"scoring failed for all {len(failures)} listings") from failures[-1]

        rules = search.notify
        rescored = rescore(self.store, search, rules.podium_hours, skip={lst.id for lst, _ in scored}, scorer=scorer)
        log.debug("[%s] rescored %d stored listings", search.name, rescored)
        if rules.podium > 0:
            podium = self.update_podium(search, {lst.id for lst, _ in scored}, scorer)
            self.store.commit()
            log.info("[%s] %d listings, podium %s", search.name, len(scored), "changed" if podium else "unchanged")
            return podium

        deals: list[Deal] = []
        for listing, score in sorted(scored, key=lambda pair: pair[1].value, reverse=True):
            if score.reject or score.value < rules.min_value:
                continue
            prev = self.store.previous(search.name, listing.id)
            notified = prev.notified_price if prev else None
            if notified is None or (rules.price_drop and listing.price < notified - 0.01):
                deals.append(
                    Deal(search.name, listing.title, listing.price, listing.currency, score.value,
                         score.summary, listing.url, listing.photo, price_drop_from=notified, extra=listing.extra)
                )
                self.store.mark_notified(search.name, listing.id, listing.price)
            if len(deals) >= rules.top:
                break
        self.store.commit()
        log.info("[%s] %d listings, %d new deals%s", search.name, len(scored), len(deals),
                 f", {len(failures)} could not be scored" if failures else "")
        return deals

    def update_podium(self, search: SearchConfig, seen: set[int], scorer=None) -> Podium | None:
        """Rank the best stored listings and return the podium if it differs from the last one sent."""
        rules = search.notify
        if search.name in self._blocked:
            # Can't check whether missing items were sold; keep the last podium until a clean run.
            log.info("[%s] blocked this run, podium not updated", search.name)
            return None
        # Items not in this run's results may have been sold (search is newest first, so older
        # listings simply drop off the first page). Their item page tells us.
        checks, checked = rules.podium * 2, set()
        while True:
            rows = self.store.top(search.name, rules.podium, rules.podium_hours, rules.min_value)
            # Missing from this run's results (sold?) or only known from a search snippet: look at the listing.
            unverified = [r["id"] for r in rows if (r["id"] not in seen or r["partial"]) and r["id"] not in checked]
            if not unverified or checks <= 0:
                break
            urls = {r["id"]: r["url"] for r in rows}
            for item_id in unverified[:checks]:
                checks -= 1
                checked.add(item_id)
                page = self.client_for(search, urls[item_id]).item_page(item_id)
                if page is None or page.available is None:
                    continue  # unknown: keep it ranked
                if page.available:
                    self.store.mark_seen(search.name, item_id)
                    self._complete(search, scorer, item_id, page.description)
                else:
                    log.info("[%s] %s is no longer for sale", search.name, item_id)
                    self.store.mark_gone(search.name, item_id)

        old = self.store.podium(search.name)
        new = [Placed(r["id"], r["price"], r["title"]) for r in rows]
        if not changed(old, new, rules.price_drop):
            return None
        self.store.save_podium(search.name, new)
        for r in rows:
            self.store.mark_notified(search.name, r["id"], r["price"])
        return build_podium(search.name, rows, old) if rows else None

    def _complete(self, search: SearchConfig, scorer, item_id: int, description: str) -> None:
        """The availability check loaded the listing anyway: keep its full description and rescore,
        so a podium place never rests on a search snippet ("Es ist ein i5 verbaut. Es...")."""
        listing = self.store.listing(search.name, item_id)
        if not listing or len(description) <= len(listing.description):
            return
        listing.description = description
        self.store.set_description(search.name, item_id, description)
        self.store.update_score(search.name, item_id, score_stored(listing, search, scorer or build_scorer(search.scorer)))

    def run_once(self) -> list[Deal]:
        all_deals: list[Deal] = []
        for search in self.config.searches:
            try:
                result = self.run_search(search)
            except Exception:
                log.exception("[%s] search crashed", search.name)
                continue
            if isinstance(result, Podium):
                all_deals += result.deals
                for notifier in self.notifiers:
                    notifier.send_podium(result)
            elif result:
                all_deals += result
                for notifier in self.notifiers:
                    notifier.send(result)
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
