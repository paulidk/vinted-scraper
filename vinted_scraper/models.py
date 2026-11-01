from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


def _money(value: Any) -> float | None:
    """Vinted returns prices either as {"amount": "12.5", ...} or a plain string."""
    if value is None:
        return None
    if isinstance(value, dict):
        value = value.get("amount")
    try:
        return float(str(value).replace(",", "."))
    except ValueError:
        return None


@dataclass
class Listing:
    id: int
    title: str
    price: float
    currency: str
    url: str
    total_price: float | None = None
    brand: str = ""
    condition: str = ""
    photo: str = ""
    seller: str = ""
    favourites: int = 0
    description: str = ""
    partial_description: bool = False  # only the start, as in Kleinanzeigen search results
    extra: str = ""  # site-specific notes for alerts: "06112 Halle · nur Abholung · VB"
    shipping: float | None = None  # shipping cost the site quotes (eBay); None: scorers use their estimate
    raw: dict = field(default_factory=dict, repr=False)

    @property
    def effective_price(self) -> float:
        """Price incl. buyer protection fee when Vinted tells us, never below 1."""
        return max(self.total_price or self.price, 1.0)

    def shipping_or(self, estimate: float) -> float:
        return estimate if self.shipping is None else self.shipping

    @property
    def text(self) -> str:
        return f"{self.title}\n{self.brand}\n{self.description}"

    @classmethod
    def from_api(cls, item: dict) -> "Listing":
        price = item.get("price")
        currency = (price.get("currency_code") if isinstance(price, dict) else None) or item.get("currency", "EUR")
        photo = item.get("photo") or {}
        return cls(
            id=int(item["id"]),
            title=item.get("title") or "",
            price=_money(price) or 0.0,
            currency=currency,
            url=item.get("url") or "",
            total_price=_money(item.get("total_item_price")),
            brand=item.get("brand_title") or "",
            condition=item.get("status") or "",
            photo=photo.get("url") or "" if isinstance(photo, dict) else "",
            seller=(item.get("user") or {}).get("login") or "",
            favourites=int(item.get("favourite_count") or 0),
            description=item.get("description") or "",
            partial_description=bool(item.get("description_partial")),
            extra=item.get("extra") or "",
            shipping=_money(item.get("shipping")),
            raw=item,
        )
