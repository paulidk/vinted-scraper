"""HTTP client for Vinted's web frontend.

Vinted retired the public `/api/v2/catalog/items` endpoint; search results
are now server-rendered into the catalog page as a Next.js RSC payload, which
we fetch as HTML and parse (see `parse_catalog_page`).

Vinted sits behind Cloudflare, which mostly blocks clients by TLS/HTTP2
fingerprint and request behaviour rather than by IP. To look like a real
browser we:

* use curl_cffi, which reproduces the TLS + HTTP/2 fingerprint of real
  browsers (a plain `requests` call is blocked almost immediately);
* never override the User-Agent, so it always matches the impersonated
  fingerprint;
* visit the homepage first to collect the session cookies a browser would
  have (`access_token_web`, Cloudflare cookies, ...);
* pace requests with random delays;
* on a 401 refresh the session, and on 403/429/challenge back off
  exponentially, then switch to a different browser fingerprint (and proxy,
  if configured) with a fresh session.

Nothing can guarantee you will never be blocked, but at one search every
half hour this stays far below anything Cloudflare reacts to.
"""

from __future__ import annotations

import json
import logging
import random
import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable
from urllib.parse import parse_qsl, urlparse

from curl_cffi import requests

log = logging.getLogger(__name__)

# Recent desktop browsers only: mobile fingerprints with desktop cookies look odd.
DEFAULT_IMPERSONATE = ["chrome", "chrome131", "chrome124", "edge101", "safari17_0"]

ACCEPT_LANGUAGE = {
    "de": "de-DE,de;q=0.9,en;q=0.8",
    "at": "de-AT,de;q=0.9,en;q=0.8",
    "fr": "fr-FR,fr;q=0.9,en;q=0.8",
    "be": "fr-BE,fr;q=0.9,nl;q=0.8,en;q=0.7",
    "nl": "nl-NL,nl;q=0.9,en;q=0.8",
    "pl": "pl-PL,pl;q=0.9,en;q=0.8",
    "it": "it-IT,it;q=0.9,en;q=0.8",
    "es": "es-ES,es;q=0.9,en;q=0.8",
    "pt": "pt-PT,pt;q=0.9,en;q=0.8",
    "lt": "lt-LT,lt;q=0.9,en;q=0.8",
    "cz": "cs-CZ,cs;q=0.9,en;q=0.8",
    "sk": "sk-SK,sk;q=0.9,en;q=0.8",
    "co.uk": "en-GB,en;q=0.9",
    "com": "en-US,en;q=0.9",
}

# Vinted web search URL params -> API params.
_URL_PARAM_MAP = {
    "catalog[]": "catalog_ids",
    "brand_ids[]": "brand_ids",
    "status_ids[]": "status_ids",
    "size_ids[]": "size_ids",
    "color_ids[]": "color_ids",
    "material_ids[]": "material_ids",
    "video_game_platform_ids[]": "video_game_platform_ids",
}


_API_TO_URL_PARAM = {v: k for k, v in _URL_PARAM_MAP.items()}
_API_TO_URL_PARAM["catalog_ids"] = "catalog[]"

_RSC_CHUNK = re.compile(r'self\.__next_f\.push\(\[1,(".*?")\]\)</script>', re.S)
_ITEMS_BLOCK = re.compile(r'"items":\s*(?=\{\s*"items":\s*\[)')
_LD_JSON = re.compile(r'<script type="application/ld\+json">(.*?)</script>', re.S)
_HTML_ACCEPT = "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"


class BlockedError(RuntimeError):
    """Raised when Vinted/Cloudflare keeps refusing requests."""


class SetupError(RuntimeError):
    """A site can't be searched as configured (no API key, a filter it rejects). Unlike a block,
    the run goes on without that site and the podium still updates."""


def params_from_url(url: str) -> tuple[str, dict[str, str]]:
    """Turn a Vinted search URL copied from the browser into (domain, API params)."""
    parsed = urlparse(url)
    multi: dict[str, list[str]] = {}
    for key, value in parse_qsl(parsed.query):
        multi.setdefault(_URL_PARAM_MAP.get(key, key.removesuffix("[]")), []).append(value)
    params = {k: ",".join(v) for k, v in multi.items()}
    params.pop("search_id", None)
    params.pop("time", None)
    return parsed.netloc, params


def url_params(params: dict[str, Any]) -> list[tuple[str, str]]:
    """Inverse of `params_from_url`: API-style params -> catalog page query."""
    query = []
    for key, value in params.items():
        if value is None:
            continue
        url_key = _API_TO_URL_PARAM.get(key)
        if url_key is None and key.endswith("_ids"):
            url_key = key + "[]"
        if url_key:
            query += [(url_key, v) for v in str(value).split(",") if v]
        else:
            query.append((key, str(value)))
    return query


def _rsc_payload(html: str) -> str:
    return "".join(json.loads(chunk) for chunk in _RSC_CHUNK.findall(html))


def _normalize_item(entry: dict, base_url: str) -> dict:
    """Map a catalog page item onto the field names of the old JSON API."""
    item = entry.get("productItem") or entry
    box = item.get("itemBox") or {}
    url = item.get("url") or ""
    if url.startswith("/"):
        url = base_url + url

    def money(value):
        if not isinstance(value, dict):
            return value
        return {"amount": value.get("amount"), "currency_code": value.get("currencyCode") or value.get("currency_code")}

    photos = item.get("photos") or []
    photo = photos[0].get("url") if photos and isinstance(photos[0], dict) else item.get("thumbnailUrl")
    return {
        "id": item.get("id") or entry.get("id"),
        "title": item.get("title") or "",
        "price": money(item.get("priceWithDiscount") or item.get("price")),
        "total_item_price": money(item.get("totalItemPrice")),
        "url": url,
        "brand_title": box.get("firstLine") or "",
        "status": box.get("secondLine") or "",
        "photo": {"url": photo or ""},
        "user": item.get("user") or {},
        "favourite_count": item.get("favouriteCount") or 0,
    }


def parse_catalog_page(html: str, base_url: str = "") -> dict | None:
    """Extract {"items": [...], "pagination": {...}} from a catalog page, or None if absent."""
    payload = _rsc_payload(html)
    match = _ITEMS_BLOCK.search(payload)
    if match is None:
        return None
    try:
        block, _ = json.JSONDecoder().raw_decode(payload, match.end())
    except ValueError:
        return None
    return {
        "items": [_normalize_item(e, base_url) for e in block.get("items") or [] if isinstance(e, dict)],
        "pagination": block.get("pagination") or {},
    }


@dataclass
class ItemPage:
    description: str = ""
    available: bool | None = None  # None: the page didn't say
    status: str = ""  # what the site shows instead of a buy button: "Verkauft", "Reserviert", ...
    price: float | None = None  # the current asking price, if the page shows one


# Sold, reserved or hidden listings carry a status block for buyers ({"title": "Verkauft", ...,
# "name": "buyer_item_status"}) and can't be bought. Their JSON-LD product data is gone entirely,
# so JSON-LD alone can't tell a sold listing from a page it failed to read (checked 2026-10-25).
_BUYER_STATUS = re.compile(r'"title":"([^"]+)"\}[^{}]{0,120}"name":"buyer_item_status"')
_CAN_BUY = re.compile(r'"can_buy":(true|false)')


def parse_item_page(html: str) -> ItemPage | None:
    """Description and availability of a listing page."""
    page = None
    for blob in _LD_JSON.findall(html):
        try:
            data = json.loads(blob)
        except ValueError:
            continue
        if not isinstance(data, dict) or data.get("@type") not in (None, "Product"):
            continue
        offers = data.get("offers")
        offers = offers if isinstance(offers, dict) else {}
        availability = str(offers.get("availability") or "")
        try:
            price = float(offers["price"]) if offers.get("price") not in (None, "") else None
        except (TypeError, ValueError):
            price = None
        page = ItemPage(
            description=data.get("description") or "",
            # schema.org values, sometimes as a full URL: InStock, SoldOut, OutOfStock, ...
            available=availability.rsplit("/", 1)[-1] == "InStock" if availability else None,
            price=price,
        )
        break
    payload = _rsc_payload(html) if "self.__next_f" in html else ""
    status = _BUYER_STATUS.search(payload)
    can_buy = _CAN_BUY.search(payload)
    if status or (can_buy and can_buy.group(1) == "false"):
        page = page or ItemPage()
        page.available, page.status = False, status.group(1) if status else "can't be bought"
    elif can_buy and page and page.available is None:
        page.available = True
    elif can_buy and not page:
        page = ItemPage(available=True)
    return page


def parse_item_description(html: str) -> str | None:
    page = parse_item_page(html)
    return page.description or None if page else None


@dataclass
class ClientSettings:
    domain: str = "www.vinted.de"
    impersonate: list[str] = field(default_factory=lambda: list(DEFAULT_IMPERSONATE))
    proxies: list[str] = field(default_factory=list)
    min_delay: float = 2.0
    max_delay: float = 6.0
    max_retries: int = 5
    timeout: float = 30.0
    ebay: dict = field(default_factory=dict)  # Browse API keys: client_id, client_secret; zip for shipping costs


class VintedClient:
    WARMUP_COOKIE: str | None = "access_token_web"  # set by the homepage visit; its absence predicts 401s

    def __init__(self, settings: ClientSettings | None = None):
        self.settings = settings or ClientSettings()
        self._session: requests.Session | None = None
        self._fingerprint_idx = random.randrange(len(self.settings.impersonate))
        self._proxy_idx = random.randrange(len(self.settings.proxies)) if self.settings.proxies else 0
        self._last_request = 0.0
        self.last_response = None  # kept for diagnostics (`probe`)

    @property
    def base_url(self) -> str:
        return f"https://{self.settings.domain}"

    def _accept_language(self) -> str:
        tld = self.settings.domain.split("vinted.", 1)[-1]
        return ACCEPT_LANGUAGE.get(tld, "en-US,en;q=0.9")

    def _pace(self) -> None:
        wait = random.uniform(self.settings.min_delay, self.settings.max_delay)
        elapsed = time.monotonic() - self._last_request
        if elapsed < wait:
            time.sleep(wait - elapsed)
        self._last_request = time.monotonic()

    def _rotate(self) -> None:
        """Switch to the next fingerprint/proxy and drop the current session."""
        self._fingerprint_idx = (self._fingerprint_idx + 1) % len(self.settings.impersonate)
        if self.settings.proxies:
            self._proxy_idx = (self._proxy_idx + 1) % len(self.settings.proxies)
        self.close()

    def _new_session(self) -> requests.Session:
        target = self.settings.impersonate[self._fingerprint_idx]
        proxy = self.settings.proxies[self._proxy_idx] if self.settings.proxies else None
        log.debug("new session: impersonate=%s proxy=%s", target, bool(proxy))
        session = requests.Session(
            impersonate=target,
            timeout=self.settings.timeout,
            proxies={"http": proxy, "https": proxy} if proxy else None,
        )
        session.headers.update({"Accept-Language": self._accept_language()})
        self._pace()
        resp = session.get(self.base_url + "/", headers={"Accept": _HTML_ACCEPT})
        if _is_blocked(resp):
            session.close()
            raise _Retryable(f"homepage blocked (HTTP {resp.status_code})")
        if self.WARMUP_COOKIE and self.WARMUP_COOKIE not in session.cookies:
            log.warning("no %s cookie after warm-up; API calls may return 401", self.WARMUP_COOKIE)
        return session

    def close(self) -> None:
        if self._session is not None:
            self._session.close()
            self._session = None

    def _get(self, path: str, params: Any, accept: str, referer: str | None, json_body: bool):
        """GET with retries; returns the response, or None on 404."""
        last_error = ""
        for attempt in range(self.settings.max_retries):
            try:
                if self._session is None:
                    self._session = self._new_session()
                self._pace()
                headers = {"Accept": accept}
                if referer:
                    headers["Referer"] = referer
                resp = self._session.get(self.base_url + path, params=params, headers=headers)
                if resp.status_code == 401:
                    # Session cookie expired: a fresh warm-up fixes it.
                    raise _Retryable("401 unauthorized", rotate=False, backoff=False)
                self.last_response = resp
                log.debug("GET %s -> HTTP %s (%s, %d bytes)", path, resp.status_code,
                          resp.headers.get("content-type", "?"), len(resp.content))
                if resp.status_code == 404:
                    log.warning("GET %s -> 404 not found", path)
                    return None
                if _is_blocked(resp) or resp.status_code >= 500:
                    raise _Retryable(f"HTTP {resp.status_code}")
                resp.raise_for_status()
                if json_body:
                    try:
                        resp.json()
                    except ValueError:
                        raise _Retryable(f"non-JSON response ({resp.headers.get('content-type', '?')})") from None
                return resp
            except _Retryable as exc:
                last_error = str(exc)
                rotate, backoff = exc.rotate, exc.backoff
            except requests.RequestsError as exc:
                last_error = f"network error: {exc}"
                rotate, backoff = True, True
            log.info("request to %s failed (%s), attempt %d/%d", path, last_error, attempt + 1, self.settings.max_retries)
            if rotate:
                self._rotate()
            else:
                self.close()
            if backoff:
                time.sleep(min(300, 10 * 2**attempt) + random.uniform(0, 5))
        raise BlockedError(f"giving up on {path}: {last_error}")

    def get_json(self, path: str, params: dict[str, Any] | None = None, referer: str | None = None) -> dict:
        resp = self._get(path, params, "application/json, text/plain, */*",
                         referer or self.base_url + "/catalog", json_body=True)
        return resp.json() if resp is not None else {}

    def get_html(self, path: str, params: Any = None, referer: str | None = None) -> str:
        resp = self._get(path, params, _HTML_ACCEPT, referer or self.base_url + "/", json_body=False)
        return resp.text if resp is not None else ""

    def search(self, params: dict[str, Any], pages: int = 1, per_page: int = 96,
               until: Callable[[list[dict]], bool] | None = None) -> list[dict]:
        """Return catalog items for a search, newest first by default.

        The catalog page always renders 96 items; `per_page` only trims the result.
        Stops before `pages` once `until(page_items)` is true.
        """
        items: list[dict] = []
        query = {"order": "newest_first", **params}
        query.pop("per_page", None)
        for page in range(1, pages + 1):
            html = self.get_html("/catalog", url_params({**query, "page": page}))
            data = parse_catalog_page(html, self.base_url)
            if data is None:
                log.warning("catalog page has no item data; Vinted may have changed its site")
                break
            batch = data["items"]
            items.extend(batch)
            total_pages = data["pagination"].get("total_pages") or page
            if not batch or page >= total_pages or (until and until(batch)):
                break
        return items[: per_page * pages]

    def item_page(self, item_id: int | str) -> ItemPage | None:
        """Best-effort fetch of a listing's page; None when blocked or unreadable."""
        try:
            html = self.get_html(f"/items/{item_id}", referer=self.base_url + "/catalog")
        except BlockedError as exc:
            log.info("could not fetch details for %s: %s", item_id, exc)
            return None
        if not html:  # 404: the listing was deleted
            return ItemPage(available=False)
        return parse_item_page(html)

    def item_description(self, item_id: int | str) -> str | None:
        """Best-effort fetch of a listing's description (not included in search results)."""
        page = self.item_page(item_id)
        return page.description or None if page else None


class _Retryable(Exception):
    def __init__(self, msg: str, rotate: bool = True, backoff: bool = True):
        super().__init__(msg)
        self.rotate = rotate
        self.backoff = backoff


def _is_blocked(resp) -> bool:
    if resp.status_code in (403, 429, 503):
        return True
    if resp.headers.get("cf-mitigated") == "challenge":
        return True
    ctype = resp.headers.get("content-type", "")
    return "text/html" in ctype and "Just a moment" in resp.text[:2000]
