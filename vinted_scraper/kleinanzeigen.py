"""Kleinanzeigen.de as a second source, behind the same interface as `VintedClient`.

Search pages are server-rendered (Astro). Every result sits as structured data in the
props of the `astro-island` that holds `resultAds`, in Astro's `[type, value]` encoding,
which is far more stable than the generated CSS classes of the cards. Items are mapped
onto the Vinted field names so `Listing.from_api` and everything after it stay the same.

Search URLs are paths: `/s-<filters>/<query>/k0c<category>`, e.g.
`/s-anzeige:angebote/preis:30:500/versand:ja/seite:2/optiplex/k0c228`. Results are newest
first by default. A listing is `/s-anzeige/<id>`; a deleted one redirects to the homepage.
"""

from __future__ import annotations

import html as html_lib
import json
import logging
import re
from typing import Any, Callable
from urllib.parse import quote, urlparse

from .client import BlockedError, ItemPage, VintedClient, _HTML_ACCEPT

log = logging.getLogger(__name__)

DOMAIN = "www.kleinanzeigen.de"
# Listing ids are stored with this offset so they can't collide with Vinted ids when one search
# ranks both sites together. Vinted ids are around 1e10, Kleinanzeigen ids around 3.5e9.
ID_OFFSET = 10**15
# Common categories: 228 PCs, 225 PC-Zubehör & Software, 278 Notebooks, 161 Elektronik
_ISLAND = re.compile(r'<astro-island[^>]*\sprops="([^"]*resultAds[^"]*)"')
_PRICE = re.compile(r"(\d[\d.]*(?:,\d+)?)\s*€")
_DESCRIPTION = re.compile(r'id="viewad-description-text"[^>]*>(.*?)</p>', re.S)
_RESERVED = re.compile(r"pvap-reserved-title|<h1[^>]*>\s*(?:<[^>]+>\s*)*Reserviert\b", re.I)


def _unwrap(value):
    """Decode Astro's serialised props: every value is `[type, value]`, type 1 being an array."""
    if isinstance(value, list) and len(value) == 2 and isinstance(value[0], int):
        kind, inner = value
        return [_unwrap(v) for v in inner] if kind == 1 else _unwrap(inner)
    if isinstance(value, dict):
        return {k: _unwrap(v) for k, v in value.items()}
    return value


def parse_price(text: str) -> float | None:
    """"1.299 € VB" -> 1299.0; "VB" or "Zu verschenken" -> None."""
    m = _PRICE.search(text or "")
    return float(m.group(1).replace(".", "").replace(",", ".")) if m else None


def _normalize(ad: dict, base_url: str) -> dict:
    price_text = ad.get("price") or ""
    images = ad.get("imageList") or []
    photo = next((img.get("xLargeUrl") or img.get("adTableThumbnailLargeUrl") for img in images if isinstance(img, dict)), "")
    place = " ".join(filter(None, [ad.get("locationName"), ad.get("parentLocationName")]))
    extra = [place, ad.get("shippingAvailableValue") or "nur Abholung"]
    if "VB" in price_text:
        extra.append("VB")
    if ad.get("posterType") == "COMMERCIAL":
        extra.append("gewerblich")
    return {
        "id": ID_OFFSET + int(ad["id"]),
        "title": ad.get("title") or "",
        "price": {"amount": parse_price(price_text) or 0, "currency_code": "EUR"},
        "url": base_url + (ad.get("seoLink") or f"/s-anzeige/{ad['id']}"),
        "photo": {"url": photo or ""},
        "user": {"login": str(ad.get("userId") or "")},
        # Only the start of the description; the runner fetches the rest when specs are missing.
        "description": ad.get("description") or "",
        "description_partial": True,
        "extra": " · ".join(filter(None, extra)),
    }


def parse_search_page(html: str, base_url: str = "https://" + DOMAIN) -> list[dict] | None:
    """The listings of a search page, or None if the page has no result data."""
    m = _ISLAND.search(html)
    if m is None:
        return None
    try:
        props = _unwrap(json.loads(html_lib.unescape(m.group(1))))
    except ValueError:
        return None
    out = []
    for entry in props.get("resultAds") or []:
        ad = entry.get("organicAdPreview") if isinstance(entry, dict) else None
        if not ad or not ad.get("id"):
            continue  # sponsored slots carry an external ad instead
        if (ad.get("title") or "").lower().startswith("reserviert"):
            continue
        out.append(_normalize(ad, base_url))
    return out


def parse_listing_page(html: str) -> ItemPage:
    m = _DESCRIPTION.search(html)
    description = ""
    if m:
        text = re.sub(r"<br\s*/?>", "\n", m.group(1))
        description = html_lib.unescape(re.sub(r"<[^>]+>", "", text)).strip()
    return ItemPage(description=description, available=not _RESERVED.search(html))


def search_path(params: dict[str, Any], page: int = 1) -> str:
    """Build a search path from config params, or page through a path pasted from the browser."""
    if params.get("path"):
        parts = re.sub(r"seite:\d+/", "", params["path"].removeprefix("/s-")).strip("/").split("/")
        if page > 1:
            # like the site's own links: right before "<query>/k0..." or the bare "c228..."
            at = len(parts) - (2 if parts[-1].startswith("k0") and len(parts) > 1 else 1)
            parts.insert(at, f"seite:{page}")
        return "/s-" + "/".join(parts)
    filters = ["anzeige:angebote"]  # offers only, no "Gesuche"
    low, high = params.get("price_from"), params.get("price_to")
    if low is not None or high is not None:
        filters.append(f"preis:{'' if low is None else int(float(low))}:{'' if high is None else int(float(high))}")
    if params.get("shipping") in (True, "true", "ja", "yes", 1):
        filters.append("versand:ja")
    if params.get("seller"):
        filters.append(f"anbieter:{params['seller']}")  # privat / gewerblich
    if page > 1:
        filters.append(f"seite:{page}")
    category = f"c{params['category']}" if params.get("category") else ""
    location = f"l{params['location']}r{params.get('radius', 50)}" if params.get("location") else ""
    slug = quote(re.sub(r"\s+", "-", (params.get("search_text") or "").strip().lower()))
    tail = f"{slug}/k0{category}{location}" if slug else (category + location or "k0")
    return "/s-" + "/".join(filters) + "/" + tail


class KleinanzeigenClient(VintedClient):
    WARMUP_COOKIE = None

    def _accept_language(self) -> str:
        return "de-DE,de;q=0.9,en;q=0.8"

    def search(self, params: dict[str, Any], pages: int = 1, per_page: int = 25,
               until: Callable[[list[dict]], bool] | None = None) -> list[dict]:
        items: list[dict] = []
        for page in range(1, pages + 1):
            batch = parse_search_page(self.get_html(search_path(params, page)), self.base_url)
            if batch is None:
                log.warning("Kleinanzeigen page has no result data; the site may have changed")
                break
            items.extend(batch)
            # A full page holds 25 listings plus top ads; fewer means it was the last one.
            if len(batch) < 20 or (until and until(batch)):
                break
        return items

    def item_page(self, item_id: int | str) -> ItemPage | None:
        ad_id = int(item_id) % ID_OFFSET
        try:
            resp = self._get(f"/s-anzeige/{ad_id}", None, _HTML_ACCEPT, self.base_url + "/", json_body=False)
        except BlockedError as exc:
            log.info("could not fetch details for %s: %s", ad_id, exc)
            return None
        if resp is None or "/s-anzeige/" not in urlparse(str(resp.url)).path:
            return ItemPage(available=False)  # deleted: redirected to the homepage
        return parse_listing_page(resp.text)


def is_kleinanzeigen(domain_or_url: str) -> bool:
    return "kleinanzeigen." in domain_or_url
