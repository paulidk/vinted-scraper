"""HTTP client for Vinted's internal JSON API.

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

import logging
import random
import time
from dataclasses import dataclass, field
from typing import Any
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


class BlockedError(RuntimeError):
    """Raised when Vinted/Cloudflare keeps refusing requests."""


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


@dataclass
class ClientSettings:
    domain: str = "www.vinted.de"
    impersonate: list[str] = field(default_factory=lambda: list(DEFAULT_IMPERSONATE))
    proxies: list[str] = field(default_factory=list)
    min_delay: float = 2.0
    max_delay: float = 6.0
    max_retries: int = 5
    timeout: float = 30.0


class VintedClient:
    def __init__(self, settings: ClientSettings | None = None):
        self.settings = settings or ClientSettings()
        self._session: requests.Session | None = None
        self._fingerprint_idx = random.randrange(len(self.settings.impersonate))
        self._proxy_idx = random.randrange(len(self.settings.proxies)) if self.settings.proxies else 0
        self._last_request = 0.0

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
        resp = session.get(
            self.base_url + "/",
            headers={"Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"},
        )
        if _is_blocked(resp):
            session.close()
            raise _Retryable(f"homepage blocked (HTTP {resp.status_code})")
        if "access_token_web" not in session.cookies:
            log.warning("no access_token_web cookie after warm-up; API calls may return 401")
        return session

    def close(self) -> None:
        if self._session is not None:
            self._session.close()
            self._session = None

    def get_json(self, path: str, params: dict[str, Any] | None = None, referer: str | None = None) -> dict:
        last_error = ""
        for attempt in range(self.settings.max_retries):
            try:
                if self._session is None:
                    self._session = self._new_session()
                self._pace()
                resp = self._session.get(
                    self.base_url + path,
                    params=params,
                    headers={
                        "Accept": "application/json, text/plain, */*",
                        "Referer": referer or self.base_url + "/catalog",
                    },
                )
                if resp.status_code == 401:
                    # Session cookie expired: a fresh warm-up fixes it.
                    raise _Retryable("401 unauthorized", rotate=False, backoff=False)
                if resp.status_code == 404:
                    return {}
                if _is_blocked(resp) or resp.status_code >= 500:
                    raise _Retryable(f"HTTP {resp.status_code}")
                resp.raise_for_status()
                return resp.json()
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

    def search(self, params: dict[str, Any], pages: int = 1, per_page: int = 96) -> list[dict]:
        """Return raw catalog items for a search, newest first by default."""
        items: list[dict] = []
        query = {"order": "newest_first", **params, "per_page": per_page}
        for page in range(1, pages + 1):
            data = self.get_json("/api/v2/catalog/items", {**query, "page": page})
            batch = data.get("items") or []
            items.extend(batch)
            total_pages = (data.get("pagination") or {}).get("total_pages", page)
            if len(batch) < per_page or page >= total_pages:
                break
        return items

    def item_description(self, item_id: int | str) -> str | None:
        """Best-effort fetch of a listing's description (not included in search results)."""
        referer = f"{self.base_url}/items/{item_id}"
        for path in (f"/api/v2/items/{item_id}/details", f"/api/v2/items/{item_id}"):
            try:
                data = self.get_json(path, referer=referer)
            except BlockedError as exc:
                log.info("could not fetch details for %s: %s", item_id, exc)
                return None
            item = data.get("item") or data
            if isinstance(item, dict) and item.get("description"):
                return item["description"]
        return None


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
