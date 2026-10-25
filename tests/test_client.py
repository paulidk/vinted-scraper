import json

import pytest

from vinted_scraper import client as client_mod
from vinted_scraper.client import (BlockedError, ClientSettings, VintedClient, parse_catalog_page,
                                   parse_item_description, parse_item_page, params_from_url, url_params)


def catalog_html(items, total_pages=1):
    """A catalog page the way Vinted renders it: items inside a Next.js RSC chunk."""
    block = {"items": [{"id": it["id"], "productItem": it} for it in items],
             "pagination": {"current_page": 1, "total_pages": total_pages}}
    rsc = f'5:["$","div",null,{{"items":{json.dumps(block)}}}]\n'
    return f"<html><body><script>self.__next_f.push([1,{json.dumps(rsc)}])</script></body></html>"


def page(items, total_pages=1):
    return Resp(200, headers={"content-type": "text/html"}, text=catalog_html(items, total_pages))


class Resp:
    def __init__(self, status, json_data=None, headers=None, text=""):
        self.status_code = status
        self._json = json_data or {}
        self.headers = headers or {"content-type": "application/json"}
        self.text = text
        self.content = text.encode()

    def json(self):
        return self._json

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(self.status_code)


class FakeSession:
    def __init__(self, responses, log):
        self.responses = responses
        self.log = log
        self.cookies = {"access_token_web": "x"}
        self.headers = {}

    def get(self, url, **kw):
        self.log.append(url)
        return self.responses.pop(0)

    def close(self):
        pass


@pytest.fixture
def no_sleep(monkeypatch):
    monkeypatch.setattr(client_mod.time, "sleep", lambda s: None)


def make_client(monkeypatch, api_responses):
    log, sessions = [], []

    def new_session(self):
        sessions.append(self.settings.impersonate[self._fingerprint_idx])
        return FakeSession(api_responses, log)

    monkeypatch.setattr(VintedClient, "_new_session", new_session)
    c = VintedClient(ClientSettings(min_delay=0, max_delay=0, max_retries=3))
    return c, log, sessions


def test_recovers_from_cloudflare_challenge_with_new_fingerprint(monkeypatch, no_sleep):
    challenge = Resp(403, headers={"content-type": "text/html", "cf-mitigated": "challenge"}, text="Just a moment...")
    c, log, sessions = make_client(monkeypatch, [challenge, page([{"id": 1}])])
    assert [it["id"] for it in c.search({"search_text": "x"})] == [1]
    assert len(sessions) == 2 and sessions[0] != sessions[1]


def test_refreshes_session_on_401(monkeypatch, no_sleep):
    c, log, sessions = make_client(monkeypatch, [Resp(401), page([])])
    c.search({})
    assert len(sessions) == 2 and sessions[0] == sessions[1]  # same fingerprint, fresh cookies


def test_gives_up_after_max_retries(monkeypatch, no_sleep):
    c, _, _ = make_client(monkeypatch, [Resp(429)] * 3)
    with pytest.raises(BlockedError):
        c.search({})


def test_paginates(monkeypatch, no_sleep):
    def p(n):
        return page([{"id": i} for i in range(n)], total_pages=3)

    c, log, _ = make_client(monkeypatch, [p(2), p(2), p(1)])
    assert len(c.search({}, pages=5)) == 5
    assert len(log) == 3


def test_paging_stops_when_until_says_so(monkeypatch, no_sleep):
    pages = [page([{"id": i}, {"id": i + 100}], total_pages=9) for i in range(3)]
    c, log, _ = make_client(monkeypatch, pages)
    items = c.search({}, pages=9, until=lambda batch: batch[0]["id"] == 1)
    assert [i["id"] for i in items] == [0, 100, 1, 101]
    assert len(log) == 2


def test_parses_catalog_item_into_api_shape():
    item = {
        "id": 7, "title": "Dell OptiPlex 7060", "url": "/items/7-dell", "favouriteCount": 3,
        "priceWithDiscount": None, "price": {"amount": "175.00", "currencyCode": "EUR"},
        "totalItemPrice": {"amount": "184.45", "currencyCode": "EUR"},
        "photos": [{"url": "https://img/7.webp"}], "user": {"id": 1},
        "itemBox": {"firstLine": "DELL", "secondLine": "Sehr gut"},
    }
    data = parse_catalog_page(catalog_html([item], total_pages=4), "https://www.vinted.de")
    assert data["pagination"]["total_pages"] == 4
    assert data["items"][0] == {
        "id": 7, "title": "Dell OptiPlex 7060", "url": "https://www.vinted.de/items/7-dell",
        "price": {"amount": "175.00", "currency_code": "EUR"},
        "total_item_price": {"amount": "184.45", "currency_code": "EUR"},
        "brand_title": "DELL", "status": "Sehr gut", "photo": {"url": "https://img/7.webp"},
        "user": {"id": 1}, "favourite_count": 3,
    }


def test_catalog_page_without_items_is_none():
    assert parse_catalog_page("<html>nope</html>") is None


def test_url_params_round_trip():
    _, params = params_from_url("https://www.vinted.de/catalog?search_text=pc&catalog[]=1&catalog[]=2&price_to=80")
    assert url_params(params) == [("search_text", "pc"), ("catalog[]", "1"), ("catalog[]", "2"), ("price_to", "80")]


def test_item_description_from_json_ld():
    html = '<script type="application/ld+json">{"@type":"Product","description":"i5-8500T, 16GB"}</script>'
    assert parse_item_description(html) == "i5-8500T, 16GB"


@pytest.mark.parametrize("availability, expected", [
    ("InStock", True), ("https://schema.org/InStock", True), ("SoldOut", False), ("OutOfStock", False), (None, None),
])
def test_item_page_availability(availability, expected):
    offers = {"@type": "Offer", "price": 20} | ({"availability": availability} if availability else {})
    html = f'<script type="application/ld+json">{json.dumps({"@type": "Product", "description": "x", "offers": offers})}</script>'
    assert parse_item_page(html).available is expected


def item_html(payload: str, ld: dict | None = None) -> str:
    """An item page as Vinted renders it in 2026-10: an RSC chunk, JSON-LD only while it's for sale."""
    script = f'<script type="application/ld+json">{json.dumps(ld)}</script>' if ld else ""
    return f"<html>{script}<script>self.__next_f.push([1,{json.dumps(payload)}])</script></html>"


def test_sold_listing_has_a_status_block_and_no_json_ld():
    sold = item_html('{"data":{"item_id":"1","seller_id":"2","theme":"SUCCESS","title":"Verkauft"},"exposures":[],'
                     '"name":"buyer_item_status","section":"sidebar"},{"can_buy":false,"is_reserved":false}')
    page = parse_item_page(sold)
    assert page.available is False and page.status == "Verkauft"


def test_available_and_unbuyable_listings():
    ld = {"@type": "Product", "description": "i5-8500T", "offers": {"@type": "Offer"}}
    available = parse_item_page(item_html('{"can_buy":true,"is_reserved":false}', ld))
    assert available.available is True and available.description == "i5-8500T"
    assert parse_item_page(item_html('{"can_buy":false}')).available is False
    assert parse_item_page("<html>nothing we know</html>") is None
