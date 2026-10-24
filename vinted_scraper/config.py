from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

import yaml

from .client import ClientSettings, params_from_url
from .kleinanzeigen import DOMAIN as KLEINANZEIGEN, is_kleinanzeigen

SITES = {"vinted", "kleinanzeigen"}

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
    domain: str
    queries: list[dict]  # API params, one dict per query
    pages: int = 5  # max result pages per query; paging stops early at listings seen before
    min_price: float | None = None
    max_price: float | None = None
    include: re.Pattern | None = None
    exclude: re.Pattern | None = None
    fetch_details: int = 0  # max descriptions fetched per run for items with missing specs
    scorer: dict = field(default_factory=dict)
    notify: NotifyRules = field(default_factory=NotifyRules)

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


def _search(raw: dict, default_domain: str) -> SearchConfig:
    site = raw.get("site", "vinted")
    if site not in SITES:
        raise ValueError(f"search {raw.get('name')!r}: unknown site {site!r}; choose from {sorted(SITES)}")
    domain = raw.get("domain") or (KLEINANZEIGEN if site == "kleinanzeigen" else default_domain)
    base = {k: v for k, v in (raw.get("params") or {}).items()}
    queries: list[dict] = []
    for url in [raw["url"]] if "url" in raw else raw.get("urls", []):
        if is_kleinanzeigen(url):
            # Kleinanzeigen keeps its filters in the path: keep it whole and only add the page.
            domain = urlparse(url).netloc or KLEINANZEIGEN
            queries.append({**base, "path": urlparse(url).path})
            continue
        url_domain, params = params_from_url(url)
        domain = url_domain or domain
        queries.append({**base, **params})
    texts = raw.get("queries") or ([raw["query"]] if "query" in raw else [])
    queries += [{**base, "search_text": text} for text in texts]
    if not queries:
        queries = [base]
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
        domain=domain,
        queries=queries,
        pages=raw.get("pages", 5),
        min_price=raw.get("min_price"),
        max_price=raw.get("max_price"),
        include=re.compile(raw["include"], re.I) if raw.get("include") else None,
        exclude=re.compile(raw["exclude"], re.I) if raw.get("exclude") else None,
        fetch_details=raw.get("fetch_details", 0),
        scorer=raw.get("scorer") or {},
        notify=NotifyRules(**(raw.get("notify") or {})),
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
    )
