"""eBay as a third source, through eBay's official Browse API instead of scraping its pages.

The API needs a free key pair from developer.ebay.com (App ID and Cert ID, see the README),
exchanged for an application token (OAuth client credentials grant, valid two hours). Search
results carry title, price, shipping cost to Germany, seller type and location, but rarely a
description; `getItem` has the full one. Items are mapped onto the Vinted field names like the
Kleinanzeigen client does, so `Listing.from_api` and everything after it stay the same.

Search filters (`ebay:` in a search): `category`, `seller` (privat/gewerblich), `auctions`
(off by default: bids would move the price every few minutes), `country` (where the item is,
default DE; `any` for anywhere that ships to Germany) and `conditions` (eBay condition ids;
"for parts" is left out by default because those titles rarely say "defekt").
"""

from __future__ import annotations

import base64
import html as html_lib
import logging
import os
import random
import re
import time
from datetime import datetime, timezone
from typing import Any, Callable
from urllib.parse import parse_qsl, quote, urlparse

from curl_cffi import requests

from .client import BlockedError, ClientSettings, ItemPage

log = logging.getLogger(__name__)

DOMAIN = "www.ebay.de"
API = "https://api.ebay.com"
SCOPE = "https://api.ebay.com/oauth/api_scope"
# Stored ids get this offset, like Kleinanzeigen's 10^15, so one search can rank all sites together.
# eBay item ids have 12 digits.
ID_OFFSET = 2 * 10**15
MARKETPLACES = {"de": "EBAY_DE", "at": "EBAY_AT", "ch": "EBAY_CH", "fr": "EBAY_FR", "it": "EBAY_IT",
                "es": "EBAY_ES", "nl": "EBAY_NL", "be": "EBAY_BE", "pl": "EBAY_PL", "co.uk": "EBAY_GB",
                "com": "EBAY_US"}
# Every condition but 1750 "new with defects" and 7000 "for parts or not working".
DEFAULT_CONDITIONS = "1000|1500|2000|2010|2020|2030|2500|2750|2990|3000|3010|4000|5000|6000"
SELLERS = {"privat": "INDIVIDUAL", "private": "INDIVIDUAL", "individual": "INDIVIDUAL",
           "gewerblich": "BUSINESS", "commercial": "BUSINESS", "business": "BUSINESS"}


class AuthError(RuntimeError):
    """Missing or rejected API keys: a setup problem, not a block."""


def _country_code(country: str) -> str:
    return {"co.uk": "GB", "com": "US"}.get(country, country.upper())


def _amount(value) -> str:
    return "" if value in (None, "") else f"{float(value):g}"


def is_ebay(domain_or_url: str) -> bool:
    return bool(re.search(r"(?:^|[/.])ebay\.", domain_or_url))


def country_of(domain: str) -> str:
    return domain.split("ebay.", 1)[-1]


def params_from_url(url: str) -> dict[str, Any]:
    """An eBay search URL from the browser (`/sch/i.html?_nkw=optiplex&_sacat=179&_udhi=200`) -> params."""
    query = dict(parse_qsl(urlparse(url).query))
    params: dict[str, Any] = {}
    if query.get("_nkw"):
        params["search_text"] = query["_nkw"]
    category = query.get("_sacat") or (re.search(r"/sch/(\d+)/", urlparse(url).path) or [None, None])[1]
    if category and category != "0":
        params["category"] = category
    if query.get("_udlo"):
        params["price_from"] = query["_udlo"]
    if query.get("_udhi"):
        params["price_to"] = query["_udhi"]
    if query.get("LH_PrefLoc") in ("2", "3", "5", "6"):  # EU / worldwide instead of "Germany only"
        params["country"] = "any"
    if query.get("LH_Auction") == "1" or query.get("LH_All") == "1":
        params["auctions"] = True
    return params


def _yes(value) -> bool:
    return value in (True, "true", "ja", "yes", 1, "1")


def search_query(params: dict[str, Any], country: str = "de") -> dict[str, str]:
    """Config params -> Browse API `item_summary/search` query."""
    query: dict[str, str] = {"sort": "newlyListed"}
    if params.get("search_text"):
        query["q"] = str(params["search_text"])
    if params.get("category"):
        query["category_ids"] = str(params["category"])
    if "q" not in query and "category_ids" not in query:
        raise ValueError("an eBay search needs search terms or a category")
    filters = [f"deliveryCountry:{_country_code(country)}"]  # also drops pickup-only listings
    located = str(params.get("country") or _country_code(country)).upper()
    if located not in ("ANY", "ALL"):
        filters.append(f"itemLocationCountry:{located}")
    low, high = _amount(params.get("price_from")), _amount(params.get("price_to"))
    if low or high:
        filters.append(f"price:[{low}..{high}]")
        filters.append("priceCurrency:" + {"co.uk": "GBP", "com": "USD", "ch": "CHF", "pl": "PLN"}.get(country, "EUR"))
    filters.append("buyingOptions:{FIXED_PRICE|BEST_OFFER" + ("|AUCTION}" if _yes(params.get("auctions")) else "}"))
    conditions = str(params.get("conditions", DEFAULT_CONDITIONS) or "")
    if conditions and conditions != "all":
        filters.append("conditionIds:{" + conditions.replace(",", "|") + "}")
    if params.get("seller"):
        filters.append("sellerAccountTypes:{" + SELLERS.get(str(params["seller"]).lower(), str(params["seller"]).upper()) + "}")
    query["filter"] = ",".join(filters)
    return query


def _money(value) -> float | None:
    try:
        return float(value["value"]) if isinstance(value, dict) and value.get("value") not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _shipping(options: list[dict] | None) -> float | None:
    """The cheapest quoted shipping cost; None when eBay can't say (calculated shipping)."""
    costs = [c for c in (_money(o.get("shippingCost")) for o in options or [] if isinstance(o, dict)) if c is not None]
    return min(costs) if costs else None


def _euro(amount: float) -> str:
    return f"{amount:.2f} €".replace(".", ",")


def _normalize(summary: dict, base_url: str) -> dict | None:
    item_id = str(summary.get("legacyItemId") or "")
    if not item_id.isdigit():
        parts = (summary.get("itemId") or "").split("|")  # "v1|<legacy id>|<variation>"
        item_id = parts[1] if len(parts) > 1 else ""
    if not item_id.isdigit():
        return None
    buying = summary.get("buyingOptions") or []
    price = _money(summary.get("price"))
    if price is None:
        price = _money(summary.get("currentBidPrice"))
    if price is None:
        return None
    shipping = _shipping(summary.get("shippingOptions"))
    location = summary.get("itemLocation") or {}
    seller = summary.get("seller") or {}
    extra = [" ".join(filter(None, [location.get("postalCode"), location.get("city")])) or location.get("country", ""),
             "versandkostenfrei" if shipping == 0 else f"Versand {_euro(shipping)}" if shipping else "",
             "Preisvorschlag" if "BEST_OFFER" in buying else "",
             "Auktion" if "AUCTION" in buying else "",
             "gewerblich" if seller.get("sellerAccountType") == "BUSINESS" else ""]
    image = summary.get("image") or (summary.get("thumbnailImages") or [{}])[0]
    return {
        "id": ID_OFFSET + int(item_id),
        "title": summary.get("title") or "",
        "price": {"amount": price, "currency_code": (summary.get("price") or {}).get("currency") or "EUR"},
        "shipping": shipping,
        "url": f"{base_url}/itm/{item_id}",  # itemWebUrl carries tracking parameters
        "photo": {"url": image.get("imageUrl") or ""},
        "user": {"login": seller.get("username") or ""},
        "status": summary.get("condition") or "",
        "description": summary.get("shortDescription") or "",
        "description_partial": True,
        "extra": " · ".join(filter(None, extra)),
    }


def _text(description_html: str) -> str:
    """Seller descriptions are whole HTML templates; keep the readable text."""
    text = re.sub(r"<(script|style)\b.*?</\1>", " ", description_html or "", flags=re.S | re.I)
    text = re.sub(r"<br\s*/?>|</(?:p|div|li|tr|h\d)>", "\n", text, flags=re.I)
    text = html_lib.unescape(re.sub(r"<[^>]+>", " ", text))
    lines = (re.sub(r"[ \t\xa0]+", " ", line).strip() for line in text.splitlines())
    return "\n".join(line for line in lines if line)


def parse_item(data: dict) -> ItemPage:
    """A `getItem` response -> description, availability and current price."""
    available = None
    statuses = [a.get("estimatedAvailabilityStatus") for a in data.get("estimatedAvailabilities") or [] if isinstance(a, dict)]
    if statuses:
        available = any(s in ("IN_STOCK", "LIMITED_STOCK") for s in statuses)
    end = data.get("itemEndDate")
    if end:
        try:
            if datetime.fromisoformat(end.replace("Z", "+00:00")) < datetime.now(timezone.utc):
                available = False
        except ValueError:
            pass
    return ItemPage(description=_text(data.get("description") or "") or data.get("shortDescription") or "",
                    available=available, status="" if available is not False else "beendet",
                    price=_money(data.get("price")))


class EbayClient:
    """Same interface as `VintedClient` (`search`, `item_page`, `item_description`, `close`)."""

    def __init__(self, settings: ClientSettings | None = None):
        self.settings = settings or ClientSettings(domain=DOMAIN)
        self.country = country_of(self.settings.domain)
        self.marketplace = MARKETPLACES.get(self.country, "EBAY_DE")
        self._session: requests.Session | None = None
        self._token = ""
        self._token_expires = 0.0
        self._auth_error = ""  # set once the keys failed: every later call fails fast
        self._auth_logged = False
        self._last_request = 0.0
        self.last_response = None

    @property
    def base_url(self) -> str:
        return f"https://{self.settings.domain}"

    def close(self) -> None:
        if self._session is not None:
            self._session.close()
            self._session = None

    def _keys(self) -> tuple[str, str]:
        return (self.settings.ebay_client_id or os.environ.get("EBAY_CLIENT_ID", ""),
                self.settings.ebay_client_secret or os.environ.get("EBAY_CLIENT_SECRET", ""))

    def _get_token(self) -> str:
        if self._auth_error:
            raise AuthError(self._auth_error)
        if self._token and time.time() < self._token_expires - 120:
            return self._token
        client_id, secret = self._keys()
        if not client_id or not secret:
            self._auth_error = ("no eBay API keys: set client.ebay_client_id and client.ebay_client_secret "
                                "(or EBAY_CLIENT_ID / EBAY_CLIENT_SECRET), see the README")
            raise AuthError(self._auth_error)
        basic = base64.b64encode(f"{client_id}:{secret}".encode()).decode()
        resp = self._send("POST", API + "/identity/v1/oauth2/token", data={"grant_type": "client_credentials", "scope": SCOPE},
                          headers={"Authorization": "Basic " + basic, "Content-Type": "application/x-www-form-urlencoded"})
        if resp.status_code != 200:
            try:
                reason = resp.json().get("error_description") or resp.text[:200]
            except ValueError:
                reason = resp.text[:200]
            self._auth_error = f"eBay rejected the API keys (HTTP {resp.status_code}): {reason}"
            raise AuthError(self._auth_error)
        data = resp.json()
        self._token, self._token_expires = data["access_token"], time.time() + float(data.get("expires_in", 7200))
        return self._token

    def _send(self, method: str, url: str, **kwargs):
        """One request with retries on network errors and 5xx."""
        if self._session is None:
            self._session = requests.Session(timeout=self.settings.timeout)
        last_error = ""
        for attempt in range(max(1, self.settings.max_retries)):
            wait = 0.5 - (time.monotonic() - self._last_request)  # the API needs no human pacing
            if wait > 0:
                time.sleep(wait)
            self._last_request = time.monotonic()
            try:
                resp = self._session.request(method, url, **kwargs)
            except requests.RequestsError as exc:
                last_error = f"network error: {exc}"
            else:
                self.last_response = resp
                if resp.status_code < 500:
                    return resp
                last_error = f"HTTP {resp.status_code}"
            log.info("eBay request failed (%s), attempt %d/%d", last_error, attempt + 1, self.settings.max_retries)
            time.sleep(min(60, 2 * 2**attempt) + random.uniform(0, 1))
        raise BlockedError(f"giving up on eBay: {last_error}")

    def get_json(self, path: str, params: dict[str, Any] | None = None) -> dict | None:
        """GET an API path; None on 404. Raises BlockedError on the call limit, AuthError on bad keys."""
        for _ in range(2):
            headers = {"Authorization": "Bearer " + self._get_token(), "Accept": "application/json",
                       "X-EBAY-C-MARKETPLACE-ID": self.marketplace,
                       # prices and shipping costs as a buyer in that country sees them
                       "X-EBAY-C-ENDUSERCTX": "contextualLocation=" + quote(f"country={_country_code(self.country)}")}
            resp = self._send("GET", API + path, params=params, headers=headers)
            log.debug("GET %s -> HTTP %s (%d bytes)", path, resp.status_code, len(resp.content))
            if resp.status_code == 401:
                self._token = ""  # expired early: fetch a new one once
                continue
            if resp.status_code == 404:
                return None
            if resp.status_code == 429:
                raise BlockedError("eBay API call limit reached (5000 a day by default)")
            if resp.status_code >= 400:
                try:
                    errors = resp.json().get("errors") or []
                    reason = "; ".join(e.get("longMessage") or e.get("message") or "" for e in errors)
                except ValueError:
                    reason = resp.text[:200]
                raise ValueError(f"eBay API HTTP {resp.status_code}: {reason}")
            return resp.json()
        raise AuthError("eBay keeps rejecting the token (HTTP 401)")

    def search(self, params: dict[str, Any], pages: int = 1, per_page: int = 100,
               until: Callable[[list[dict]], bool] | None = None) -> list[dict]:
        items: list[dict] = []
        try:
            query = search_query(params, self.country)
            for page in range(pages):
                data = self.get_json("/buy/browse/v1/item_summary/search",
                                     {**query, "limit": per_page, "offset": page * per_page}) or {}
                batch = [item for item in (_normalize(s, self.base_url) for s in data.get("itemSummaries") or []) if item]
                items.extend(batch)
                if not data.get("next") or not batch or (until and until(batch)):
                    break
        except AuthError as exc:
            if not self._auth_logged:
                log.error("%s", exc)
                self._auth_logged = True
        except ValueError as exc:
            log.error("eBay search %s failed: %s", params.get("search_text") or params, exc)
        return items

    def item_page(self, item_id: int | str) -> ItemPage | None:
        legacy = int(item_id) % ID_OFFSET
        try:
            data = self.get_json("/buy/browse/v1/item/" + quote(f"v1|{legacy}|0", safe=""))
        except (AuthError, BlockedError, ValueError) as exc:
            # ValueError: e.g. a listing with variations, which has no single item id
            log.info("could not fetch eBay item %s: %s", legacy, exc)
            return None
        if data is None:
            return ItemPage(available=False, status="beendet")  # ended long enough ago to be gone
        return parse_item(data)

    def item_description(self, item_id: int | str) -> str | None:
        page = self.item_page(item_id)
        return page.description or None if page else None
