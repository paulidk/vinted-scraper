"""Generic scorer for any kind of search: regex rules award or deduct points.

With no rules at all every item gets the same points, so ranking by value
simply means "cheapest first".
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from ..models import Listing
from .base import Score


@dataclass
class Rule:
    match: re.Pattern
    points: float = 0.0
    per_unit: float = 0.0  # multiplied by the first number captured by `match`
    max: float | None = None
    field: str = "text"  # "title", "brand", "condition" or "text" (everything)

    def apply(self, listing: Listing) -> float:
        haystack = getattr(listing, self.field) if self.field != "text" else f"{listing.text}\n{listing.condition}"
        m = self.match.search(haystack or "")
        if not m:
            return 0.0
        total = self.points
        if self.per_unit and m.groups():
            try:
                total += self.per_unit * float(m.group(1).replace(",", "."))
            except (TypeError, ValueError):
                pass
        return min(total, self.max) if self.max is not None else total


class RulesScorer:
    def __init__(self, base: float = 10.0, rules: list[dict] | None = None, shipping_estimate: float = 0.0):
        self.base = base
        self.shipping_estimate = shipping_estimate
        self.rules = [
            Rule(
                match=re.compile(r["match"], re.I),
                points=r.get("points", 0.0),
                per_unit=r.get("per_unit", 0.0),
                max=r.get("max"),
                field=r.get("field", "text"),
            )
            for r in rules or []
        ]

    def score(self, listing: Listing) -> Score:
        hits = [(rule.match.pattern, pts) for rule in self.rules if (pts := rule.apply(listing))]
        points = max(self.base + sum(p for _, p in hits), 0.0)
        summary = ", ".join(f"{pat} {p:+g}" for pat, p in hits) or "no rule matched"
        return Score(points=points, cost=listing.effective_price + self.shipping_estimate, summary=summary)
