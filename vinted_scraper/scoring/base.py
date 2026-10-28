from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from ..models import Listing


@dataclass
class Score:
    points: float  # how "good" the item is, independent of price
    cost: float  # what it really costs you (price, fees, running costs, ...)
    summary: str = ""
    reject: str | None = None  # reason the item should be ignored
    needs_details: bool = False  # parsing would benefit from the full description

    @property
    def value(self) -> float:
        """Points per 100 units of currency: the number everything is ranked by."""
        return 0.0 if self.reject else round(100 * self.points / max(self.cost, 1.0), 2)


def offsite_matters(url: str) -> bool:
    """Talk of PayPal or bank transfer is a scam sign only on Vinted, which has its own checkout.

    On Kleinanzeigen PayPal is the normal way to pay; on eBay payment always runs through eBay.
    """
    return not any(site in url for site in ("kleinanzeigen.", "ebay."))


class Scorer(Protocol):
    def score(self, listing: Listing) -> Score: ...


def build_scorer(config: dict | None) -> Scorer:
    config = dict(config or {})
    kind = config.pop("type", "rules")
    if kind in ("computer", "mini_pc", "pc"):
        from .computer import ComputerScorer

        return ComputerScorer(**config)
    if kind in ("rules", "price"):
        from .rules import RulesScorer

        return RulesScorer(**config)
    if kind in ("ram", "memory"):
        from .memory import MemoryScorer

        return MemoryScorer(**config)
    if kind in ("drive", "drives", "hdd"):
        from .drives import DriveScorer

        return DriveScorer(**config)
    if kind == "nas":
        from .nas import NasScorer

        return NasScorer(**config)
    raise ValueError(f"unknown scorer type {kind!r} (expected 'computer', 'ram', 'drive', 'nas' or 'rules')")
