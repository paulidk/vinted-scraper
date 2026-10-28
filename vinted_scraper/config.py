from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

import yaml

from .client import ClientSettings, params_from_url
from .ebay import DOMAIN as EBAY, is_ebay, params_from_url as ebay_params
from .kleinanzeigen import DOMAIN as KLEINANZEIGEN, is_kleinanzeigen

SITES = {"vinted", "kleinanzeigen", "ebay"}
ORDER = ["vinted", "kleinanzeigen", "ebay"]


def site_of(url: str) -> str:
    return "kleinanzeigen" if is_kleinanzeigen(url) else "ebay" if is_ebay(url) else "vinted"

_ENV = re.compile(r"\$\{(\w+)(?::-([^}]*))?\}")


def _expand_env(value):
    """Replace ${VAR} / ${VAR:-default} so secrets can stay out of the config file."""
    if isinstance(value, str):
        return _ENV.sub(lambda m: os.environ.get(m.group(1), m.group(2) or ""), value)
    if isinstance(value, list):
        return [_expand_env(v) for v in value]
    if isinstance(value, dict):
        return {k: _expand_env(v) for k, v in value.items()}
    return value


@dataclass
class NotifyRules:
    min_value: float = 0.0  # only notify items scoring at least this
    top: int = 5  # at most this many notifications per search per run
    price_drop: bool = True  # notify again when a known item gets cheaper
    # >0: keep a ranking of the best N listings and alert only when it changes (replaces per-deal alerts)
    podium: int = 0
    podium_hours: float = 72  # an item missing from search results stays ranked this long (if still for sale)


@dataclass
class SearchConfig:
    name: str
    domain: str  # default site for queries without their own "domain"
    queries: list[dict]  # params, one dict per query; "domain" picks the site (Vinted country or Kleinanzeigen)
    pages: int = 5  # max result pages per query; paging stops early at listings seen before
    min_price: float | None = None
    max_price: float | None = None
    include: re.Pattern | None = None
    exclude: re.Pattern | None = None
    fetch_details: int = 0  # max descriptions fetched per run for items with missing specs
    scorer: dict = field(default_factory=dict)
    notify: NotifyRules = field(default_factory=NotifyRules)
    interval_minutes: float | None = None  # how often this search runs; None = the global interval
    enabled: bool = True  # off: kept in the config, but `run` and `once` skip it unless it's named

    def keeps(self, listing) -> bool:
        if self.min_price is not None and listing.price < self.min_price:
            return False
        if self.max_price is not None and listing.price > self.max_price:
            return False
        if self.include and not self.include.search(listing.title):
            return False
        if self.exclude and self.exclude.search(f"{listing.title}\n{listing.description}"):
            return False
        return True


@dataclass
class Config:
    searches: list[SearchConfig]
    client: ClientSettings
    notifiers: list[dict]
    database: str = "data/vinted.db"
    interval_minutes: float = 30
    jitter_minutes: float = 4
    quiet_hours: tuple[int, int] | None = None  # (start, end) in minutes after midnight, local time; may wrap

    def interval(self, search: SearchConfig) -> float:
        return search.interval_minutes or self.interval_minutes

    def is_quiet(self, minute_of_day: int) -> bool:
        if not self.quiet_hours:
            return False
        start, end = self.quiet_hours
        return start <= minute_of_day < end if start < end else minute_of_day >= start or minute_of_day < end


def _clock(value) -> int:
    """1, "1", "01:30" -> minutes after midnight."""
    text = str(value).strip()
    hours, _, minutes = text.partition(":")
    total = int(hours) * 60 + int(minutes or 0)
    if not 0 <= total <= 24 * 60:
        raise ValueError(f"quiet_hours: {value!r} is not a time of day")
    return total % (24 * 60)


def _quiet_hours(raw) -> tuple[int, int] | None:
    """`quiet_hours: [1, 7]` or `["23:30", "06:00"]`: no runs in between."""
    if not raw:
        return None
    if isinstance(raw, str):
        raw = raw.replace("–", "-").split("-")
    if len(raw) != 2:
        raise ValueError("quiet_hours: expected [start, end], e.g. [1, 7]")
    start, end = _clock(raw[0]), _clock(raw[1])
    return (start, end) if start != end else None


def _search(raw: dict, default_domain: str) -> SearchConfig:
    """Search terms run on every site in `sites`, so one search ranks Vinted, Kleinanzeigen and eBay together.

    `params` are Vinted's (`catalog_ids`, `order`, ...), `kleinanzeigen` holds Kleinanzeigen's
    (`category`, `shipping`, `seller`, ...) and `ebay` eBay's (`category`, `condition`, `seller`, ...).
    A search on Kleinanzeigen or eBay alone may put them in `params`.
    """
    urls = [raw["url"]] if "url" in raw else raw.get("urls", [])
    url_sites = {site_of(u) for u in urls}
    if "sites" in raw or "site" in raw:
        sites = list(raw.get("sites") or [raw["site"]])
    else:
        sites = [s for s in ORDER if s in url_sites] or ["vinted"]
    for site in sites:
        if site not in SITES:
            raise ValueError(f"search {raw.get('name')!r}: unknown site {site!r}; choose from {sorted(SITES)}")
    vinted_domain = raw.get("domain") or default_domain
    only = sites[0] if len(sites) == 1 else None

    def own(site: str) -> dict:
        return dict(raw.get(site) or (raw.get("params") if only == site else None) or {})

    bases = {
        "vinted": ({} if only and only != "vinted" else dict(raw.get("params") or {}), vinted_domain),
        "kleinanzeigen": (own("kleinanzeigen"), KLEINANZEIGEN),
        "ebay": (own("ebay"), EBAY),
    }
    queries: list[dict] = []
    for url in urls:
        if is_kleinanzeigen(url):
            # Kleinanzeigen keeps its filters in the path: keep it whole and only add the page.
            base, _ = bases["kleinanzeigen"]
            queries.append({**base, "path": urlparse(url).path, "domain": urlparse(url).netloc or KLEINANZEIGEN})
            continue
        if is_ebay(url):
            queries.append({**bases["ebay"][0], **ebay_params(url), "domain": urlparse(url).netloc or EBAY})
            continue
        url_domain, params = params_from_url(url)
        queries.append({**bases["vinted"][0], **params, "domain": url_domain or vinted_domain})
    texts = raw.get("queries") or ([raw["query"]] if "query" in raw else [])
    for site in sites:  # in the order of `sites`
        base, domain = bases[site]
        if texts:
            queries += [{**base, "search_text": text, "domain": domain} for text in texts]
        elif site not in url_sites:
            queries.append({**base, "domain": domain})  # no terms: the whole (category) feed
    if raw.get("max_price") is not None:
        for q in queries:
            q.setdefault("price_to", raw["max_price"])
    if raw.get("min_price") is not None:
        for q in queries:
            q.setdefault("price_from", raw["min_price"])
    for q in queries:
        for k, v in list(q.items()):
            if isinstance(v, list):
                q[k] = ",".join(map(str, v))
    return SearchConfig(
        name=raw["name"],
        domain=queries[0]["domain"] if queries else bases[sites[0]][1],
        queries=queries,
        pages=raw.get("pages", 5),
        min_price=raw.get("min_price"),
        max_price=raw.get("max_price"),
        include=re.compile(raw["include"], re.I) if raw.get("include") else None,
        exclude=re.compile(raw["exclude"], re.I) if raw.get("exclude") else None,
        fetch_details=raw.get("fetch_details", 0),
        scorer=raw.get("scorer") or {},
        notify=NotifyRules(**(raw.get("notify") or {})),
        interval_minutes=raw.get("interval_minutes"),
        enabled=bool(raw.get("enabled", True)),
    )


def load_config(path: str | Path) -> Config:
    raw = _expand_env(yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {})
    client_raw = raw.get("client") or {}
    client = ClientSettings(**client_raw)
    searches = [_search(s, client.domain) for s in raw.get("searches") or []]
    if not searches:
        raise ValueError(f"{path}: no searches configured")
    names = [s.name for s in searches]
    if len(set(names)) != len(names):
        raise ValueError(f"{path}: search names must be unique")
    return Config(
        searches=searches,
        client=client,
        notifiers=raw.get("notifiers") or [{"type": "console"}],
        database=raw.get("database", "data/vinted.db"),
        interval_minutes=raw.get("interval_minutes", 30),
        jitter_minutes=raw.get("jitter_minutes", 4),
        quiet_hours=_quiet_hours(raw.get("quiet_hours")),
    )
