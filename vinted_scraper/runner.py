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
        self._gap: dict[str, float] = {}  # seconds until a search is due again, jitter included
        # `once --full`: page every query to the end (or `sweep_pages` deep) instead of stopping
        # at listings seen before, e.g. to catch up after a break or after widening the filters
        self.sweep = False
        self.sweep_pages = 0

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
            full = self.sweep or self.store.crawled_pages(search.name, key) < search.pages
            pages = max(search.pages, self.sweep_pages) if self.sweep else search.pages
            try:
                raw_items = client.search(params, pages=pages, until=None if full else mostly_known)
            except BlockedError as exc:
                # Retries are exhausted: hammering on with the remaining queries only digs
                # the hole deeper. Keep what we have and try again next run; other sites go on.
                log.error("[%s] blocked on %s %s, skipping the rest of this site this run: %s",
                          search.name, domain, params.get("search_text") or params, exc)
                self._blocked.add(search.name)
                blocked.add(domain)
                continue
            if full:
                self.store.set_crawled_pages(search.name, key, pages)
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
                if (score.needs_details and detail_budget > 0 and (score.reject is None or score.reject.endswith(" unknown"))
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
                    # Blocked, or the site changed its page again: keep it ranked, but say so.
                    log.warning("[%s] couldn't tell whether %s is still for sale", search.name, urls[item_id])
                    continue
                if page.available:
                    self.store.mark_seen(search.name, item_id)
                    self._complete(search, scorer, item_id, page.description, page.price)
                else:
                    log.info("[%s] no longer for sale (%s): %s", search.name, page.status or "gone", urls[item_id])
                    self.store.mark_gone(search.name, item_id)

        old = self.store.podium(search.name)
        new = [Placed(r["id"], r["price"], r["title"]) for r in rows]
        if not changed(old, new, rules.price_drop):
            return None
        self.store.save_podium(search.name, new)
        for r in rows:
            self.store.mark_notified(search.name, r["id"], r["price"])
        return build_podium(search.name, rows, old) if rows else None

    def _complete(self, search: SearchConfig, scorer, item_id: int, description: str,
                  price: float | None = None) -> None:
        """The availability check loaded the listing anyway: keep its full description and current
        price and rescore, so a podium place never rests on a search snippet ("Es ist ein i5
        verbaut. Es...") or on a price the seller has changed since."""
        listing = self.store.listing(search.name, item_id)
        if not listing:
            return
        longer = len(description) > len(listing.description)
        repriced = price is not None and price > 0 and abs(price - listing.price) > 0.01
        if longer:
            self.store.set_description(search.name, item_id, description)
        if repriced:
            log.info("[%s] price changed %g -> %g: %s", search.name, listing.price, price, listing.url)
            self.store.set_price(search.name, item_id, price)
        if longer or repriced:
            listing = self.store.listing(search.name, item_id)
            self.store.update_score(search.name, item_id, score_stored(listing, search, scorer or build_scorer(search.scorer)))

    @property
    def active(self) -> list[SearchConfig]:
        return [s for s in self.config.searches if s.enabled]

    def run_once(self, names: list[str] | None = None) -> list[Deal]:
        """Run every enabled search (or the named ones, even if disabled) now, whatever its schedule."""
        unknown = set(names or []) - {s.name for s in self.config.searches}
        if unknown:
            raise ValueError(f"no search named {', '.join(sorted(unknown))}")
        return self.run_searches([s for s in self.config.searches if s.name in names] if names else self.active)

    def run_due(self, slack: float = 0.0) -> list[Deal]:
        """Run the searches whose interval is up, unless it's quiet hours.

        `slack` lets a search run a bit early: a systemd timer that fires every 10 minutes
        would otherwise turn a 20-minute interval into 30 when it fires a few seconds early.
        """
        now = time.time()
        if self.config.is_quiet(_minute_of_day(now)):
            log.info("quiet hours, nothing to do")
            return []
        due = [s for s in self.active if self.seconds_until_due(s, now) <= slack]
        return self.run_searches(due)

    def seconds_until_due(self, search: SearchConfig, now: float) -> float:
        last = self.store.last_run(search.name)
        if last is None:
            return 0.0
        return last + self._gap.get(search.name, self.config.interval(search) * 60) - now

    def _schedule(self, search: SearchConfig) -> None:
        interval = self.config.interval(search) * 60
        # ±jitter so runs never happen at exact, bot-like intervals; at most a quarter of the interval
        spread = min(self.config.jitter_minutes * 60, interval / 4)
        self._gap[search.name] = interval + random.uniform(-spread, spread)

    def run_searches(self, searches: list[SearchConfig]) -> list[Deal]:
        all_deals: list[Deal] = []
        for search in searches:
            self.store.set_last_run(search.name, time.time())
            self._schedule(search)
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
        if not self.active:
            log.error("every search is disabled (enabled: false), nothing to run")
            return
        while True:
            now = time.time()
            if self.config.is_quiet(_minute_of_day(now)):
                wait = _seconds_until(self.config.quiet_hours[1], now)
                log.info("quiet hours, sleeping %.0f min", wait / 60)
                time.sleep(wait)
                continue
            self.run_due()
            now = time.time()
            waits = {s.name: self.seconds_until_due(s, now) for s in self.active}
            name = min(waits, key=waits.get)
            delay = max(60.0, waits[name])
            log.info("next: %s in %.1f min", name, delay / 60)
            time.sleep(delay)


def _minute_of_day(when: float) -> int:
    t = time.localtime(when)
    return t.tm_hour * 60 + t.tm_min


def _seconds_until(minute_of_day: int, now: float) -> float:
    """Seconds from `now` until the next time the local clock shows `minute_of_day`."""
    t = time.localtime(now)
    minutes = (minute_of_day - (t.tm_hour * 60 + t.tm_min)) % (24 * 60)
    return max(60.0, minutes * 60 - t.tm_sec)
