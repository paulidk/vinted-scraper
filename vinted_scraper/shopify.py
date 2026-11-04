"""Shops on Shopify, such as the refurbisher it-versand.com: their public `products.json`.

Every Shopify shop answers `/collections/<name>/products.json` with all products of a collection,
including each configuration (variant) with its own price and stock. Each variant in stock becomes
one listing. `/products/<handle>.js` tells later whether it's still in stock (prices in cents there).
Checked on it-versand.com 2026-11-04.
"""

from __future__ import annotations

import logging
from typing import Any, Callable
from urllib.parse import parse_qs, urlparse

from .client import BlockedError, ItemPage, VintedClient
from .ebay import _text

log = logging.getLogger(__name__)

ID_OFFSET = 4 * 10**15  # variant ids are about 5·10^13
PER_PAGE = 250  # Shopify's maximum


def _items(product: dict, base_url: str) -> list[dict]:
    images = product.get("images") or []
    description = _text(product.get("body_html") or "")
    out = []
    for variant in product.get("variants") or []:
        if not variant.get("available") or not str(variant.get("id") or "").isdigit():
            continue
        name = variant.get("title") or ""
        out.append({
            "id": ID_OFFSET + int(variant["id"]),
            "title": product.get("title", "") + ("" if name in ("", "Default Title") else f" · {name}"),
            "price": {"amount": variant.get("price"), "currency_code": "EUR"},
            "url": f"{base_url}/products/{product.get('handle')}?variant={variant['id']}",
            "photo": {"url": images[0].get("src", "") if images else ""},
            "brand_title": product.get("vendor") or "",
            "description": description,
            "extra": urlparse(base_url).netloc.removeprefix("www."),
        })
    return out


class ShopifyClient(VintedClient):
    WARMUP_COOKIE = None

    def search(self, params: dict[str, Any], pages: int = 1, per_page: int = PER_PAGE,
               until: Callable[[list[dict]], bool] | None = None) -> list[dict]:
        path = params["collection"].rstrip("/") + "/products.json"
        low, high = params.get("price_from"), params.get("price_to")
        items: list[dict] = []
        for page in range(1, pages + 1):
            products = self.get_json(path, {"limit": PER_PAGE, "page": page}, referer=self.base_url + "/").get("products") or []
            batch = [item for product in products for item in _items(product, self.base_url)
                     if (low is None or float(item["price"]["amount"]) >= float(low))
                     and (high is None or float(item["price"]["amount"]) <= float(high))]
            items.extend(batch)
            if len(products) < PER_PAGE or (until and batch and until(batch)):
                break
        return items

    def item_page(self, item_id: int | str, url: str = "") -> ItemPage | None:
        parsed = urlparse(url)
        variant = (parse_qs(parsed.query).get("variant") or [""])[0]
        if not parsed.path.startswith("/products/") or not variant:
            return None
        try:
            data = self.get_json(parsed.path + ".js", referer=self.base_url + "/")
        except BlockedError as exc:
            log.info("could not fetch %s: %s", url, exc)
            return None
        match = next((v for v in data.get("variants") or [] if str(v.get("id")) == variant), None)
        if match is None:  # 404 (removed) or the configuration is gone
            return ItemPage(available=False, status="nicht mehr im Shop")
        price = match.get("price")
        return ItemPage(description=_text(data.get("description") or ""), available=bool(match.get("available")),
                        status="" if match.get("available") else "ausverkauft",
                        price=price / 100 if isinstance(price, (int, float)) else None)

    def item_description(self, item_id: int | str) -> str | None:
        return None  # products.json already carries the whole description
