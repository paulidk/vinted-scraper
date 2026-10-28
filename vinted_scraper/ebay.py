"""eBay as a third source, through the official Browse API, behind the same interface as `VintedClient`.

Unlike Vinted and Kleinanzeigen this is a real API: no Cloudflare, no fingerprints, no HTML
parsing. It needs a free developer account (developer.ebay.com → "Application Keys", the
Production keyset) whose App ID and Cert ID go in `client.ebay` or in EBAY_CLIENT_ID /
EBAY_CLIENT_SECRET. An application token (OAuth client credentials) is good for two hours.
The default quota is 5000 calls a day.

Searches are `GET /buy/browse/v1/item_summary/search` with `filter=...`, newest first. Only
"Sofort-Kaufen" listings by default (Best Offer ones included): an auction's current bid isn't
its final price. Items are mapped onto the Vinted field names so `Listing.from_api` and
everything after it stay the same. The shipping cost comes with each item, so the scorers use
it instead of `shipping_estimate`. eBay.de charges buyers no fee.
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

from .client import BlockedError, ClientSettings, ItemPage, SetupError

log = logging.getLogger(__name__)

DOMAIN = "www.ebay.de"
API = "https://api.ebay.com"
# Listing ids are stored with this offset so they can't collide with Vinted ids (around 1e10) or
# Kleinanzeigen's (10^15 + ad id). eBay item numbers are 12 digits.
ID_OFFSET = 2 * 10**15
PER_PAGE = 100  # the API allows up to 200
MARKETPLACES = {"de": "EBAY_DE", "at": "EBAY_AT", "ch": "EBAY_CH", "fr": "EBAY_FR", "it": "EBAY_IT",
                "es": "EBAY_ES", "nl": "EBAY_NL", "be": "EBAY_BE", "pl": "EBAY_PL", "ie": "EBAY_IE",
                "co.uk": "EBAY_GB", "com": "EBAY_US"}
COUNTRIES = {"co.uk": "GB", "com": "US"}
CURRENCIES = {"GB": "GBP", "CH": "CHF", "PL": "PLN", "US": "USD"}  # the price filter's; EUR elsewhere
SELLERS = {"privat": "INDIVIDUAL", "private": "INDIVIDUAL", "gewerblich": "BUSINESS", "business": "BUSINESS"}
# A missing listing: 11001 / 11003 = "item (legacy) ID was not found", which is what an ended one returns.
_GONE_ERRORS = {11001, 11003}
_STRIP = re.compile(r"<(style|script)\b.*?</\1>|<!--.*?-->", re.S | re.I)
_BREAK = re.compile(r"<(?:br|/p|/div|/li|/tr|/h\d)\b[^>]*>", re.I)


def is_ebay(domain_or_url: str) -> bool:
    return "ebay." in domain_or_url


def _tld(domain: str) -> str:
    return domain.split("ebay.", 1)[-1].split("/", 1)[0]


def _amount(value) -> float | None:
    try:
        return float(value["value"]) if isinstance(value, dict) and value.get("value") not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _euro(amount: float) -> str:
    return f"{amount:.2f}".replace(".", ",") + " €"


def shipping_cost(item: dict) -> float | None:
    """The cheapest shipping option, or None when eBay doesn't say (calculated without a postcode)."""
    costs = [c for c in (_amount(o.get("shippingCost")) for o in item.get("shippingOptions") or []) if c is not None]
    return min(costs) if costs else None


def _normalize(item: dict, domain: str) -> dict:
    legacy = item.get("legacyItemId") or str(item.get("itemId") or "").split("|")[1]
    options = item.get("buyingOptions") or []
    auction = "FIXED_PRICE" not in options and "AUCTION" in options
    price = item.get("currentBidPrice") if auction else item.get("price")
    shipping = shipping_cost(item)
    place = item.get("itemLocation") or {}
    extra = [" ".join(filter(None, [place.get("postalCode"), place.get("city")])),
             "kostenloser Versand" if shipping == 0 else f"Versand {_euro(shipping)}" if shipping else ""]
    if auction:
        extra.append("Auktion")
    if "BEST_OFFER" in options:
        extra.append("Preisvorschlag")
    if (item.get("seller") or {}).get("sellerAccountType") == "BUSINESS":
        extra.append("gewerblich")
    return {
        "id": ID_OFFSET + int(legacy),
        "title": item.get("title") or "",
        "price": {"amount": _amount(price) or 0, "currency_code": (price or {}).get("currency") or "EUR"},
        "shipping": shipping,
        # The API's itemWebUrl carries tracking parameters; the plain item URL is the same page.
        "url": f"https://{domain}/itm/{legacy}",
        "status": item.get("condition") or "",
        "photo": {"url": (item.get("image") or {}).get("imageUrl") or ""},
        "user": {"login": (item.get("seller") or {}).get("username") or ""},
        # Built from the item specifics ("Marke, Prozessor, RAM"), not the seller's text.
        "description": item.get("shortDescription") or "",
        "description_partial": True,
        "extra": " · ".join(filter(None, extra)),
    }


def parse_search(data: dict, domain: str = DOMAIN) -> list[dict]:
    out = []
    for item in data.get("itemSummaries") or []:
        try:
            out.append(_normalize(item, domain))
        except (IndexError, KeyError, TypeError, ValueError):
            log.debug("skipping eBay item without an item number: %s", item.get("itemId"))
    return out


def html_text(markup: str) -> str:
    """The seller's HTML description as plain text: no CSS, no scripts, one line per block."""
    text = _BREAK.sub("\n", _STRIP.sub(" ", markup or ""))
    text = html_lib.unescape(re.sub(r"<[^>]+>", " ", text))
    lines = (re.sub(r"[ \t\xa0]+", " ", line).strip() for line in text.splitlines())
    return "\n".join(line for line in lines if line)


def parse_item(data: dict) -> ItemPage:
    """Description (item specifics first, then the seller's text), availability and price of an item."""
    aspects = []
    for aspect in data.get("localizedAspects") or []:
        name = aspect.get("name") or aspect.get("localizedName") or ""
        value = aspect.get("value") or ", ".join(aspect.get("localizedValues") or [])
        # "Maximaler Arbeitsspeicher: 64 GB" is what fits, not what's installed.
        if name and value and "max" not in name.lower():
            aspects.append(f"{name}: {value}")
    description = "\n".join(filter(None, ["\n".join(aspects), html_text(data.get("description") or "")]))
    stock = {a.get("estimatedAvailabilityStatus") for a in data.get("estimatedAvailabilities") or []}
    available = not stock or bool(stock - {"OUT_OF_STOCK"})
    status = "" if available else "ausverkauft"
    end = data.get("itemEndDate")
    if available and end:
        try:
            ended = datetime.fromisoformat(end.replace("Z", "+00:00")) < datetime.now(timezone.utc)
        except ValueError:
            ended = False
        if ended:
            available, status = False, "beendet"
    return ItemPage(description=description, available=available, status=status, price=_amount(data.get("price")))


def _bound(value) -> str:
    return "" if value is None else f"{float(value):g}"


def search_query(params: dict[str, Any], country: str) -> dict[str, str]:
    """Config params -> Browse API query (q, category_ids, filter, sort)."""
    filters = []
    low, high = params.get("price_from"), params.get("price_to")
    if low is not None or high is not None:
        filters += [f"price:[{_bound(low)}..{_bound(high)}]", f"priceCurrency:{CURRENCIES.get(country, 'EUR')}"]
    auctions = params.get("auctions") in (True, "true", "yes", 1)
    filters.append("buyingOptions:{FIXED_PRICE|AUCTION}" if auctions else "buyingOptions:{FIXED_PRICE}")
    condition = str(params.get("condition") or "").lower()
    if condition in ("new", "used"):
        filters.append(f"conditions:{{{condition.upper()}}}")
    location = params.get("country", country)
    if location:
        filters.append(f"itemLocationCountry:{location}")
    filters.append(f"deliveryCountry:{country}")  # only listings that ship to you
    if SELLERS.get(str(params.get("seller") or "").lower()):
        filters.append(f"sellerAccountTypes:{{{SELLERS[params['seller'].lower()]}}}")
    query = {"filter": ",".join(filters), "fieldgroups": "EXTENDED"}
    if params.get("search_text"):
        query["q"] = params["search_text"]
    if params.get("category"):
        query["category_ids"] = str(params["category"])
    if params.get("order") != "relevance":
        query["sort"] = "newlyListed"
    return query


def params_from_url(url: str) -> dict[str, Any]:
    """An eBay search URL copied from the browser -> config params (the filters the API has)."""
    parsed = urlparse(url)
    q = dict(parse_qsl(parsed.query))
    params: dict[str, Any] = {}
    if q.get("_nkw"):
        params["search_text"] = q["_nkw"]
    category = q.get("_sacat") or next(iter(re.findall(r"/sch/(\d+)/", parsed.path)), "")
    if category and category != "0":
        params["category"] = category
    if q.get("_udlo"):
        params["price_from"] = q["_udlo"]
    if q.get("_udhi"):
        params["price_to"] = q["_udhi"]
    if q.get("LH_Auction") == "1" and q.get("LH_BIN") != "1":
        params["auctions"] = True
    conditions = set(re.split(r"[|,]|%7C", q.get("LH_ItemCondition", "")))
    if conditions == {"1000"}:
        params["condition"] = "new"
    elif conditions and conditions <= {"2000", "2010", "2020", "2030", "2500", "3000", "4000", "5000", "6000"}:
        params["condition"] = "used"
    if q.get("LH_PrefLoc") not in (None, "1"):
        params["country"] = ""  # worldwide / EU instead of domestic only
    if q.get("_sop") == "12":
        params["order"] = "relevance"
    return params


class EbayClient:
    """Same `search` / `item_page` / `item_description` as `VintedClient`, over the Browse API."""

    def __init__(self, settings: ClientSettings | None = None):
        self.settings = settings or ClientSettings(domain=DOMAIN)
        ebay = self.settings.ebay or {}
        self.client_id = ebay.get("client_id") or os.environ.get("EBAY_CLIENT_ID", "")
        self.client_secret = ebay.get("client_secret") or os.environ.get("EBAY_CLIENT_SECRET", "")
        self.zip = str(ebay.get("zip") or "")
        tld = _tld(self.settings.domain)
        self.marketplace = MARKETPLACES.get(tld, "EBAY_DE")
        self.country = COUNTRIES.get(tld, tld.upper() if len(tld) == 2 else "DE")
        self._session: requests.Session | None = None
        self._token, self._token_expires = "", 0.0
        self.last_response = None  # kept for diagnostics (`probe`)

    @property
    def base_url(self) -> str:
        return f"https://{self.settings.domain}"

    def close(self) -> None:
        if self._session is not None:
            self._session.close()
            self._session = None

    def _http(self) -> requests.Session:
        if self._session is None:
            proxy = random.choice(self.settings.proxies) if self.settings.proxies else None
            self._session = requests.Session(timeout=self.settings.timeout,
                                             proxies={"http": proxy, "https": proxy} if proxy else None)
        return self._session

    def _access_token(self) -> str:
        if self._token and time.time() < self._token_expires:
            return self._token
        if not (self.client_id and self.client_secret):
            raise SetupError("no eBay API keys: set client.ebay.client_id / client_secret, "
                             "or EBAY_CLIENT_ID / EBAY_CLIENT_SECRET (developer.ebay.com, Production keyset)")
        basic = base64.b64encode(f"{self.client_id}:{self.client_secret}".encode()).decode()
        resp = self._send("POST", "/identity/v1/oauth2/token", headers={
            "Authorization": f"Basic {basic}", "Content-Type": "application/x-www-form-urlencoded"},
            data="grant_type=client_credentials&scope=" + quote("https://api.ebay.com/oauth/api_scope", safe=""))
        if resp.status_code != 200:
            raise SetupError(f"eBay rejected the API keys (HTTP {resp.status_code}): {resp.text[:200]}")
        body = resp.json()
        self._token = body["access_token"]
        self._token_expires = time.time() + int(body.get("expires_in", 7200)) - 120
        return self._token

    def _send(self, method: str, path: str, **kw):
        """One request with retries on network errors and 5xx; other statuses are the caller's."""
        last_error = ""
        for attempt in range(self.settings.max_retries):
            try:
                resp = self._http().request(method, API + path, **kw)
                self.last_response = resp
                log.debug("%s %s -> HTTP %s", method, path, resp.status_code)
                if resp.status_code == 429:
                    # The daily quota is used up (or calls came too fast): waiting minutes won't help.
                    raise BlockedError(f"eBay API rate limit reached: {resp.text[:200]}")
                if resp.status_code < 500:
                    return resp
                last_error = f"HTTP {resp.status_code}"
            except requests.RequestsError as exc:
                last_error = f"network error: {exc}"
                self.close()
            log.info("eBay request %s failed (%s), attempt %d/%d", path, last_error, attempt + 1, self.settings.max_retries)
            time.sleep(min(60, 5 * 2**attempt) + random.uniform(0, 2))
        raise BlockedError(f"giving up on eBay {path}: {last_error}")

    def _get(self, path: str, params: dict[str, str]):
        for renewed in (False, True):
            headers = {"Authorization": f"Bearer {self._access_token()}", "X-EBAY-C-MARKETPLACE-ID": self.marketplace,
                       "Accept-Language": "de-DE" if self.country in ("DE", "AT", "CH") else "en-US"}
            # Calculated shipping costs are only returned for a known destination.
            location = f"country={self.country}" + (f",zip={self.zip}" if self.zip else "")
            headers["X-EBAY-C-ENDUSERCTX"] = "contextualLocation=" + quote(location, safe="")
            resp = self._send("GET", path, params=params, headers=headers)
            if resp.status_code == 401 and not renewed:
                self._token = ""  # expired early: get a new one and try again
                continue
            return resp
        return resp

    @staticmethod
    def _errors(resp) -> tuple[set[int], str]:
        try:
            errors = resp.json().get("errors") or []
        except ValueError:
            return set(), resp.text[:200]
        return {e.get("errorId") for e in errors}, "; ".join(str(e.get("message") or e.get("longMessage")) for e in errors)

    def search(self, params: dict[str, Any], pages: int = 1, per_page: int = PER_PAGE,
               until: Callable[[list[dict]], bool] | None = None) -> list[dict]:
        """Listings for a search, newest first; stops before `pages` once `until(page_items)` is true."""
        query = search_query(params, self.country)
        items: list[dict] = []
        for page in range(pages):
            resp = self._get("/buy/browse/v1/item_summary/search", {**query, "limit": str(per_page), "offset": str(page * per_page)})
            if resp.status_code >= 400:
                _, message = self._errors(resp)
                # A filter or category eBay doesn't accept: a config problem, retrying won't fix it.
                raise SetupError(f"eBay search {query.get('q') or query.get('category_ids')} failed "
                                 f"(HTTP {resp.status_code}): {message}")
            data = resp.json()
            batch = parse_search(data, self.settings.domain)
            items.extend(batch)
            if not data.get("next") or len(batch) < per_page or (until and until(batch)):
                break
        return items

    def item_page(self, item_id: int | str) -> ItemPage | None:
        """Best-effort details of a listing; None when it can't be told."""
        legacy = int(item_id) % ID_OFFSET
        try:
            resp = self._get("/buy/browse/v1/item/get_item_by_legacy_id", {"legacy_item_id": str(legacy)})
        except (BlockedError, SetupError) as exc:
            log.info("could not fetch eBay item %s: %s", legacy, exc)
            return None
        if resp.status_code == 200:
            return parse_item(resp.json())
        errors, message = self._errors(resp)
        if resp.status_code == 404 or errors & _GONE_ERRORS:
            return ItemPage(available=False, status="beendet")
        # e.g. 11006 for a listing with variations, which needs the item group call instead
        log.info("eBay item %s: HTTP %s %s", legacy, resp.status_code, message)
        return None

    def item_description(self, item_id: int | str) -> str | None:
        page = self.item_page(item_id)
        return page.description or None if page else None
