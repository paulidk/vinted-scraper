"""Mydealz as a source: the RSS feed of a deal group (`mini-pc`, `desktop-pc`, `computer`, ...).

Deals are posted by users and link to a shop (Galaxus, AliExpress, refurbishers, ...). The feed has the
30 newest deals of a group, with title, price and shop; it can't be searched, so every deal in the group
goes to the scorer, which drops what isn't a match. A deal page says `"isExpired":true` when it's over
(checked 2026-11-04); `/deals/<id>` without the slug answers 410, so the stored URL is needed.
"""

from __future__ import annotations

import logging
import re
import xml.etree.ElementTree as ET
from typing import Any, Callable

from .client import _HTML_ACCEPT, BlockedError, ItemPage, VintedClient
from .ebay import _text

log = logging.getLogger(__name__)

DOMAIN = "www.mydealz.de"
ID_OFFSET = 3 * 10**15  # keeps thread ids apart from Vinted, Kleinanzeigen (10^15) and eBay (2·10^15)
_NS = {"pepper": "http://www.pepper.com/rss", "media": "http://search.yahoo.com/mrss/"}
_THREAD_ID = re.compile(r"-(\d+)/?$")
_EXPIRED = re.compile(r'"isExpired":\s*true')


def is_mydealz(domain_or_url: str) -> bool:
    return "mydealz." in domain_or_url


def _price(text: str | None) -> float | None:
    """ "1.099€", "269,91€", "129 €" """
    m = re.search(r"(\d{1,3}(?:\.\d{3})*|\d+)(?:,(\d{1,2}))?", text or "")
    return float(m.group(1).replace(".", "") + "." + (m.group(2) or "0")) if m else None


def parse_feed(xml: str) -> list[dict]:
    try:
        channel = ET.fromstring(xml.encode()).find("channel")
    except ET.ParseError:
        return []
    items = []
    for node in channel.findall("item") if channel is not None else []:
        link = (node.findtext("link") or "").strip()
        merchant = node.find("pepper:merchant", _NS)
        price = _price(merchant.get("price")) if merchant is not None else None
        thread = _THREAD_ID.search(link)
        if not thread or price is None:  # coupons and freebies have no price
            continue
        shop = merchant.get("name") or ""
        image = node.find("media:content", _NS)
        description = _text(node.findtext("description") or "")
        items.append({
            "id": ID_OFFSET + int(thread.group(1)),
            "title": (node.findtext("title") or "").strip(),
            "price": {"amount": price, "currency_code": "EUR"},
            "url": link,
            "photo": {"url": image.get("url") if image is not None else ""},
            # the first line repeats "185,89€ - Caseking"
            "description": "\n".join(description.splitlines()[1:]) if description.startswith(price_line(price)) else description,
            "extra": " · ".join(filter(None, [shop, "mydealz"])),
        })
    return items


def price_line(price: float) -> str:
    return f"{price:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".").removesuffix(",00")


class MydealzClient(VintedClient):
    WARMUP_COOKIE = None

    def search(self, params: dict[str, Any], pages: int = 1, per_page: int = 30,
               until: Callable[[list[dict]], bool] | None = None) -> list[dict]:
        xml = self.get_html(f"/rss/gruppe/{params['group']}")
        if not xml:
            log.warning("mydealz has no group %r", params["group"])
            return []
        low, high = _price(str(params.get("price_from", ""))), _price(str(params.get("price_to", "")))
        return [item for item in parse_feed(xml)
                if (low is None or item["price"]["amount"] >= low) and (high is None or item["price"]["amount"] <= high)]

    def item_page(self, item_id: int | str, url: str = "") -> ItemPage | None:
        if not url:
            return None
        try:
            resp = self._get(url.removeprefix(self.base_url), None, _HTML_ACCEPT, self.base_url + "/", json_body=False)
        except BlockedError as exc:
            log.info("could not fetch deal %s: %s", url, exc)
            return None
        if resp is None or resp.status_code == 410:
            return ItemPage(available=False, status="gelöscht")
        expired = bool(_EXPIRED.search(resp.text))
        return ItemPage(available=not expired, status="abgelaufen" if expired else "")

    def item_description(self, item_id: int | str) -> str | None:
        return None  # the feed's text is all there is besides the shop's own page
