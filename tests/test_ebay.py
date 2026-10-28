import json
import logging

import pytest

from vinted_scraper import ebay
from vinted_scraper.client import ClientSettings
from vinted_scraper.config import _search
from vinted_scraper.ebay import ID_OFFSET, EbayClient, parse_item, params_from_url, search_query
from vinted_scraper.models import Listing
from vinted_scraper.scoring import build_scorer
from vinted_scraper.storage import Store


class Resp:
    def __init__(self, status=200, body=None):
        self.status_code, self._body = status, body or {}
        self.text = json.dumps(self._body)
        self.content = self.text.encode()

    def json(self):
        return self._body


class FakeSession:
    """Answers the token call, then the queued API responses in order."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        if url.endswith("/oauth2/token"):
            return Resp(200, {"access_token": f"t{len(self.calls)}", "expires_in": 7200})
        return self.responses.pop(0)

    def close(self):
        pass


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr(ebay.time, "sleep", lambda s: None)


def make_client(*responses, keys=True):
    client = EbayClient(ClientSettings(domain="www.ebay.de", ebay_client_id="id" if keys else "",
                                       ebay_client_secret="secret" if keys else ""))
    client._session = FakeSession(*responses)
    return client


def summary(item_id, title="Dell OptiPlex 3060 Micro i5-8500T 16GB 256GB", price="99.00", **kw):
    return {"itemId": f"v1|{item_id}|0", "legacyItemId": str(item_id), "title": title,
            "price": {"value": price, "currency": "EUR"}, "buyingOptions": ["FIXED_PRICE"],
            "shippingOptions": [{"shippingCostType": "FIXED", "shippingCost": {"value": "5.99", "currency": "EUR"}}],
            "itemLocation": {"postalCode": "06***", "city": "Halle", "country": "DE"},
            "seller": {"username": "pcshop", "sellerAccountType": "INDIVIDUAL"},
            "image": {"imageUrl": f"https://i.ebayimg.com/{item_id}.jpg"}, "condition": "Gebraucht",
            "itemWebUrl": f"https://www.ebay.de/itm/{item_id}?hash=item123:g:abc&amdata=enc", **kw}


def test_search_query_filters():
    q = search_query({"search_text": "optiplex", "category": 179, "price_from": 30, "price_to": 200, "seller": "privat"})
    assert q["q"] == "optiplex" and q["category_ids"] == "179" and q["sort"] == "newlyListed"
    filters = q["filter"].split(",")
    assert "deliveryCountry:DE" in filters and "itemLocationCountry:DE" in filters
    assert "price:[30..200]" in filters and "priceCurrency:EUR" in filters
    assert "buyingOptions:{FIXED_PRICE|BEST_OFFER}" in filters  # no auctions unless asked
    assert "sellerAccountTypes:{INDIVIDUAL}" in filters
    assert any(f.startswith("conditionIds:") and "7000" not in f for f in filters)  # no "for parts"
    q = search_query({"category": 179, "auctions": True, "country": "any", "conditions": "all", "price_to": 99.5})
    assert "q" not in q and "AUCTION" in q["filter"] and "itemLocationCountry" not in q["filter"]
    assert "conditionIds" not in q["filter"] and "price:[..99.5]" in q["filter"]
    with pytest.raises(ValueError):
        search_query({"seller": "privat"})  # neither terms nor category


def test_params_from_browser_url():
    assert params_from_url("https://www.ebay.de/sch/i.html?_nkw=thinkcentre+tiny&_sacat=179&_udlo=40&_udhi=150") == {
        "search_text": "thinkcentre tiny", "category": "179", "price_from": "40", "price_to": "150"}
    assert params_from_url("https://www.ebay.de/sch/170083/i.html?_nkw=ddr4&LH_PrefLoc=3") == {
        "search_text": "ddr4", "category": "170083", "country": "any"}


def test_search_maps_results_and_pages_until_known():
    page1 = {"itemSummaries": [summary(111111111111), summary(222222222222, "Lenovo M720q", "120.00",
                                                                 buyingOptions=["FIXED_PRICE", "BEST_OFFER"],
                                                                 shippingOptions=[{"shippingCost": {"value": "0.00"}}],
                                                                 seller={"username": "x", "sellerAccountType": "BUSINESS"})],
             "next": "https://api.ebay.com/...offset=100"}
    page2 = {"itemSummaries": [summary(333333333333)], "next": "more"}
    client = make_client(Resp(200, page1), Resp(200, page2))
    items = client.search({"search_text": "optiplex"}, pages=5, until=lambda batch: len(batch) == 1)
    assert [i["id"] - ID_OFFSET for i in items] == [111111111111, 222222222222, 333333333333]  # stopped after page 2
    first, second = (Listing.from_api(i) for i in items[:2])
    assert (first.price, first.shipping, first.url) == (99.0, 5.99, "https://www.ebay.de/itm/111111111111")
    assert first.extra == "06*** Halle · Versand 5,99 €" and first.condition == "Gebraucht"
    assert second.shipping == 0 and second.extra == "06*** Halle · versandkostenfrei · Preisvorschlag · gewerblich"
    calls = [c for c in client._session.calls if "item_summary" in c[1]]
    assert [c[2]["params"]["offset"] for c in calls] == [0, 100]
    headers = calls[0][2]["headers"]
    assert headers["X-EBAY-C-MARKETPLACE-ID"] == "EBAY_DE" and headers["Authorization"] == "Bearer t1"


def test_missing_keys_log_once_and_skip(caplog):
    client = make_client(keys=False)
    with caplog.at_level(logging.ERROR):
        assert client.search({"search_text": "optiplex"}) == []
        assert client.search({"search_text": "thinkcentre"}) == []
    assert [r.message for r in caplog.records].count(client._auth_error) == 1 and "README" in client._auth_error
    assert client._session.calls == []
    assert client.item_page(ID_OFFSET + 1) is None  # unknown, not "sold"


def test_expired_token_is_renewed():
    client = make_client(Resp(401), Resp(200, {"itemSummaries": [summary(111111111111)]}))
    assert len(client.search({"search_text": "optiplex"})) == 1
    assert [c[1].rsplit("/", 1)[-1] for c in client._session.calls] == ["token", "search", "token", "search"]


def test_item_page():
    item = {"price": {"value": "129.00", "currency": "EUR"}, "shortDescription": "kurz",
            "description": "<style>.x{}</style><div><p>Dell OptiPlex 3060<br>16 GB RAM &amp; 256 GB SSD</p></div>",
            "estimatedAvailabilities": [{"estimatedAvailabilityStatus": "IN_STOCK"}]}
    client = make_client(Resp(200, item), Resp(404))
    page = client.item_page(ID_OFFSET + 111111111111)
    assert page.description == "Dell OptiPlex 3060\n16 GB RAM & 256 GB SSD"
    assert (page.available, page.price) == (True, 129.0)
    assert client._session.calls[-1][1].endswith("/item/v1%7C111111111111%7C0")
    assert client.item_page(ID_OFFSET + 1).available is False  # ended and gone
    assert parse_item({"estimatedAvailabilities": [{"estimatedAvailabilityStatus": "OUT_OF_STOCK"}]}).available is False
    assert parse_item({"itemEndDate": "2020-01-01T10:00:00.000Z"}).available is False


def test_quoted_shipping_replaces_estimate_and_paypal_is_fine():
    scorer = build_scorer({"type": "ram", "shipping_estimate": 6})
    listing = Listing(id=1, title="Crucial 16GB DDR4 SO-DIMM 3200", price=30, currency="EUR",
                      url="https://www.ebay.de/itm/1", shipping=2, description="Zahlung per PayPal")
    score = scorer.score(listing)
    assert score.reject is None and score.cost == 32
    listing.shipping = None
    assert scorer.score(listing).cost == 36


def test_config_sites_and_store_round_trip(tmp_path):
    s = _search({"name": "mini", "sites": ["vinted", "kleinanzeigen", "ebay"], "queries": ["optiplex"],
                 "kleinanzeigen": {"category": 228}, "ebay": {"category": 179}, "max_price": 150}, "www.vinted.de")
    assert [(q["domain"], q.get("category")) for q in s.queries] == [
        ("www.vinted.de", None), ("www.kleinanzeigen.de", 228), ("www.ebay.de", 179)]
    assert s.domain == "www.vinted.de" and s.queries[2]["price_to"] == 150
    s = _search({"name": "x", "urls": ["https://www.ebay.de/sch/i.html?_nkw=nuc&_sacat=179"], "params": {"seller": "privat"}},
                "www.vinted.de")
    assert s.queries == [{"seller": "privat", "search_text": "nuc", "category": "179", "domain": "www.ebay.de"}]
    with pytest.raises(ValueError, match="eBay needs"):
        _search({"name": "d", "sites": ["ebay"]}, "www.vinted.de")

    store = Store(tmp_path / "db.sqlite")
    item = make_client(Resp(200, {"itemSummaries": [summary(111111111111)]})).search({"search_text": "optiplex"})[0]
    listing = Listing.from_api(item)
    store.upsert("mini", listing, build_scorer({"type": "price"}).score(listing))
    assert store.listing("mini", listing.id).shipping == 5.99
