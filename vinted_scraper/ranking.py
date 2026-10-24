"""Re-scoring stored listings and keeping each search's podium (its best N listings)."""

from __future__ import annotations

import logging

from .config import SearchConfig
from .notify import Deal, Podium
from .scoring import Score, Scorer, build_scorer
from .storage import Placed, Store, listing_from_row

log = logging.getLogger(__name__)


def score_stored(listing, search: SearchConfig, scorer: Scorer) -> Score:
    if not search.keeps(listing):
        return Score(0, listing.effective_price, "excluded by filters", reject="excluded")
    try:
        return scorer.score(listing)
    except Exception:
        log.exception("[%s] could not rescore %s (%s)", search.name, listing.id, listing.title)
        return Score(0, listing.effective_price, "scoring failed", reject="scoring error")


def rescore(store: Store, search: SearchConfig, max_age_hours: float | None = None,
            skip: set[int] = frozenset(), scorer: Scorer | None = None) -> int:
    """Score stored listings again with the current config, so config changes and parser
    fixes also apply to items that haven't shown up in search results since."""
    scorer = scorer or build_scorer(search.scorer)
    count = 0
    for listing in store.listings(search.name, max_age_hours):
        if listing.id not in skip:
            store.update_score(search.name, listing.id, score_stored(listing, search, scorer))
            count += 1
    return count


def ranked(store: Store, search: SearchConfig, max_age_hours: float | None = None,
           limit: int | None = None, min_value: float | None = None) -> list[dict]:
    """The best stored listings scored with the current config, without writing anything:
    a scrape in progress holds the database's write lock for minutes."""
    scorer = build_scorer(search.scorer)
    out = []
    for row in store.rows(search.name, max_age_hours):
        score = score_stored(listing_from_row(row), search, scorer)
        if score.reject or (min_value is not None and score.value < min_value):
            continue
        out.append({**dict(row), "cost": score.cost, "points": score.points, "value": score.value,
                    "summary": score.summary, "reject": None})
    out.sort(key=lambda r: (-r["value"], r["price"]))
    return out[:limit] if limit else out


def changed(old: list[Placed], new: list[Placed], price_drop: bool) -> bool:
    if [p.id for p in old] != [p.id for p in new]:
        return True
    return price_drop and any(n.price < o.price - 0.01 for o, n in zip(old, new, strict=True))


def build_podium(search: str, rows, old: list[Placed]) -> Podium:
    before = {p.id: (i, p) for i, p in enumerate(old)}
    deals = []
    for i, row in enumerate(rows):
        change, drop_from = "", None
        if row["id"] not in before:
            change = "new"
        else:
            j, prev = before[row["id"]]
            if j > i:
                change = f"up from #{j + 1}"
            elif j < i:
                change = f"down from #{j + 1}"
            if row["price"] < prev.price - 0.01:
                drop_from = prev.price
                change = ", ".join(filter(None, [change, "price drop"]))
        deals.append(Deal(search, row["title"], row["price"], row["currency"], row["value"], row["summary"],
                          row["url"], row["photo"] or "", price_drop_from=drop_from, rank=i + 1, change=change,
                          extra=row["extra"] or ""))
    ids = {row["id"] for row in rows}
    return Podium(search, deals, dropped=[p.title for p in old if p.id not in ids])
