import json

import pytest

from vinted_scraper import ebay as ebay_mod
from vinted_scraper.client import BlockedError, ClientSettings, SetupError
from vinted_scraper.config import load_config
from vinted_scraper.ebay import (ID_OFFSET, EbayClient, html_text, params_from_url, parse_item, parse_search,
                                 search_query)
from vinted_scraper.models import Listing
from vinted_scraper.scoring import build_scorer
from vinted_scraper.storage import Store


def summary(legacy, title="Dell OptiPlex 7060 Micro i5-8500T 16GB 256GB SSD", price="120.00", shipping="4.99", **kw):
    item = {
        "itemId": f"v1|{legacy}|0", "legacyItemId": str(legacy), "title": title,
        "price": {"value": price, "currency": "EUR"}, "buyingOptions": ["FIXED_PRICE"],
        "itemWebUrl": f"https://www.ebay.de/itm/{legacy}?hash=item1&amdata=enc%3A1",
        "image": {"imageUrl": f"https://i.ebayimg.com/{legacy}.jpg"}, "condition": "Gebraucht",
        "seller": {"username": "pcdealer", "sellerAccountType": "INDIVIDUAL"},
        "itemLocation": {"postalCode": "061**", "city": "Halle", "country": "DE"},
        "shortDescription": "Dell OptiPlex 7060, Intel Core i5 8. Gen.",
    }
    if shipping is not None:
        item["shippingOptions"] = [{"shippingCostType": "FIXED", "shippingCost": {"value": shipping, "currency": "EUR"}}]
    item.update(kw)
    return item


def test_parses_results_into_vinted_shape():
    items = parse_search({"itemSummaries": [
        summary(405100000001),
        summary(405100000002, price="80.00", shipping="0.00", buyingOptions=["FIXED_PRICE", "BEST_OFFER"],
                seller={"username": "shop", "sellerAccountType": "BUSINESS"}),
        summary(405100000003, shipping=None, buyingOptions=["AUCTION"], currentBidPrice={"value": "31.00", "currency": "EUR"}),
        {"title": "no item number"},
    ]})
    first, second, auction = (Listing.from_api(i) for i in items)
    assert len(items) == 3 and all(i["id"] > ID_OFFSET for i in items)
    assert (first.price, first.shipping, first.currency) == (120, 4.99, "EUR")
    assert first.url == "https://www.ebay.de/itm/405100000001"  # without the tracking parameters
    assert first.extra == "061** Halle · Versand 4,99 €" and first.partial_description
    assert first.condition == "Gebraucht" and first.photo.endswith("405100000001.jpg")
    assert second.shipping == 0 and second.extra == "061** Halle · kostenloser Versand · Preisvorschlag · gewerblich"
    assert auction.price == 31 and auction.shipping is None and "Auktion" in auction.extra


def test_scorers_use_the_real_shipping_cost():
    scorer = build_scorer({"type": "price", "shipping_estimate": 10})
    base = dict(id=1, title="x", price=50, currency="EUR", url="https://www.ebay.de/itm/1")
    assert scorer.score(Listing(**base, shipping=0)).cost == 50
    assert scorer.score(Listing(**base, shipping=5.5)).cost == 55.5
    assert scorer.score(Listing(**base)).cost == 60  # unknown: the estimate


def test_offsite_is_ignored_on_ebay():
    scorer = build_scorer({"type": "computer"})
    text = "Dell OptiPlex 7060 Micro i5-8500T 16GB 256GB SSD, Zahlung per PayPal oder Überweisung"
    assert scorer.score(Listing(1, text, 100, "EUR", "https://www.ebay.de/itm/1")).reject is None
    assert scorer.score(Listing(1, text, 100, "EUR", "https://www.vinted.de/items/1")).reject == "offsite"


@pytest.mark.parametrize("params, expected", [
    ({"search_text": "optiplex micro", "price_from": 20, "price_to": 250},
     {"q": "optiplex micro", "sort": "newlyListed", "fieldgroups": "EXTENDED",
      "filter": "price:[20..250],priceCurrency:EUR,buyingOptions:{FIXED_PRICE},itemLocationCountry:DE,deliveryCountry:DE"}),
    ({"category": 179, "price_to": 99.5, "condition": "used", "seller": "privat", "auctions": True, "order": "relevance"},
     {"category_ids": "179", "fieldgroups": "EXTENDED",
      "filter": "price:[..99.5],priceCurrency:EUR,buyingOptions:{FIXED_PRICE|AUCTION},conditions:{USED},"
                "itemLocationCountry:DE,deliveryCountry:DE,sellerAccountTypes:{INDIVIDUAL}"}),
    ({"search_text": "wd red", "country": ""},
     {"q": "wd red", "sort": "newlyListed", "fieldgroups": "EXTENDED",
      "filter": "buyingOptions:{FIXED_PRICE},deliveryCountry:DE"}),
])
def test_search_query(params, expected):
    assert search_query(params, "DE") == expected


@pytest.mark.parametrize("url, params", [
    ("https://www.ebay.de/sch/i.html?_nkw=optiplex+micro&_sacat=179&_udlo=20&_udhi=250&LH_BIN=1&LH_ItemCondition=3000",
     {"search_text": "optiplex micro", "category": "179", "price_from": "20", "price_to": "250", "condition": "used"}),
    ("https://www.ebay.de/sch/179/i.html?_nkw=nas&LH_Auction=1&LH_PrefLoc=2&_sop=12",
     {"search_text": "nas", "category": "179", "auctions": True, "country": "", "order": "relevance"}),
    ("https://www.ebay.de/sch/i.html?_nkw=ssd&_sacat=0&LH_ItemCondition=1000%7C3000", {"search_text": "ssd"}),
])
def test_params_from_url(url, params):
    assert params_from_url(url) == params


def test_item_details():
    page = parse_item({
        "price": {"value": "110.00", "currency": "EUR"},
        "localizedAspects": [{"type": "STRING", "name": "Prozessor", "value": "Intel Core i5-8500T"},
                             {"type": "STRING", "name": "Arbeitsspeichergröße", "value": "16 GB"},
                             {"type": "STRING", "name": "Maximaler Arbeitsspeicher", "value": "64 GB"}],
        "description": "<style>.x{color:red}</style><div><p>Läuft&nbsp;einwandfrei</p><p>256 GB <b>NVMe</b></p></div>",
        "estimatedAvailabilities": [{"estimatedAvailabilityStatus": "IN_STOCK"}],
    })
    assert page.description == "Prozessor: Intel Core i5-8500T\nArbeitsspeichergröße: 16 GB\nLäuft einwandfrei\n256 GB NVMe"
    assert page.available is True and page.price == 110
    sold = parse_item({"estimatedAvailabilities": [{"estimatedAvailabilityStatus": "OUT_OF_STOCK"}]})
    assert sold.available is False
    ended = parse_item({"itemEndDate": "2024-01-01T10:00:00.000Z", "buyingOptions": ["FIXED_PRICE"]})
    assert ended.available is False and ended.status == "beendet"
    assert html_text("a<br>b<script>x()</script>&amp;c") == "a\nb &c"


class Resp:
    def __init__(self, status=200, body=None):
        self.status_code, self.body = status, body or {}
        self.text = json.dumps(self.body)
        self.url = "https://api.ebay.com/x"

    def json(self):
        return self.body


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(ebay_mod.time, "sleep", lambda s: None)
    responses, log = [], []

    class Session:
        def request(self, method, url, **kw):
            log.append((method, url, kw))
            return responses.pop(0)

        def close(self):
            pass

    monkeypatch.setattr(EbayClient, "_http", lambda self: Session())
    c = EbayClient(ClientSettings(domain="www.ebay.de", ebay={"client_id": "id", "client_secret": "secret", "zip": "06112"}))
    return c, responses, log


TOKEN = Resp(body={"access_token": "t1", "expires_in": 7200})


def test_search_pages_with_one_token(client):
    c, responses, log = client
    page = [summary(405100000000 + i) for i in range(100)]
    responses += [TOKEN, Resp(body={"itemSummaries": page, "next": "x"}),
                  Resp(body={"itemSummaries": [summary(405199999999)]})]
    items = c.search({"search_text": "optiplex"}, pages=5)
    assert len(items) == 101 and len(log) == 3
    method, url, kw = log[0]
    assert (method, url) == ("POST", "https://api.ebay.com/identity/v1/oauth2/token")
    assert kw["headers"]["Authorization"] == "Basic aWQ6c2VjcmV0"
    _, url, kw = log[2]
    assert url.endswith("/buy/browse/v1/item_summary/search") and kw["params"]["offset"] == "100"
    assert kw["headers"]["Authorization"] == "Bearer t1" and kw["headers"]["X-EBAY-C-MARKETPLACE-ID"] == "EBAY_DE"
    assert kw["headers"]["X-EBAY-C-ENDUSERCTX"] == "contextualLocation=country%3DDE%2Czip%3D06112"
    responses += [Resp(body={"itemSummaries": page, "next": "x"})]
    assert len(c.search({"search_text": "optiplex"}, pages=5, until=lambda batch: True)) == 100
    assert len(log) == 4  # the token is reused


def test_expired_token_is_renewed(client):
    c, responses, log = client
    responses += [TOKEN, Resp(401), Resp(body={"access_token": "t2", "expires_in": 7200}), Resp(body={"itemSummaries": []})]
    assert c.search({"search_text": "x"}) == []
    assert log[-1][2]["headers"]["Authorization"] == "Bearer t2"


def test_setup_problems_and_rate_limits(client, monkeypatch):
    c, responses, _ = client
    responses += [TOKEN, Resp(400, {"errors": [{"errorId": 12001, "message": "Invalid category"}]})]
    with pytest.raises(SetupError, match="Invalid category"):
        c.search({"category": 1})
    responses += [Resp(429)]
    with pytest.raises(BlockedError):
        c.search({"search_text": "x"})
    monkeypatch.delenv("EBAY_CLIENT_ID", raising=False)
    monkeypatch.delenv("EBAY_CLIENT_SECRET", raising=False)
    with pytest.raises(SetupError, match="EBAY_CLIENT_ID"):
        EbayClient(ClientSettings(domain="www.ebay.de")).search({"search_text": "x"})
    monkeypatch.setenv("EBAY_CLIENT_ID", "env-id")
    assert EbayClient(ClientSettings(domain="www.ebay.at")).client_id == "env-id"
    assert EbayClient(ClientSettings(domain="www.ebay.at")).marketplace == "EBAY_AT"


def test_item_page(client):
    c, responses, log = client
    responses += [TOKEN, Resp(body={"price": {"value": "99.00"}, "description": "<p>i7-8700T</p>"}),
                  Resp(404, {"errors": [{"errorId": 11003}]}),
                  Resp(400, {"errors": [{"errorId": 11006, "message": "use the item group"}]})]
    page = c.item_page(ID_OFFSET + 405100000001)
    assert (page.available, page.price, page.description) == (True, 99, "i7-8700T")
    assert log[-1][2]["params"] == {"legacy_item_id": "405100000001"}  # eBay's own number, without the offset
    assert c.item_page(ID_OFFSET + 2).available is False
    assert c.item_page(ID_OFFSET + 3) is None  # can't tell: stays ranked


def test_config(tmp_path):
    cfg = tmp_path / "c.yaml"
    cfg.write_text("""
client:
  ebay: {client_id: "${EBAY_ID:-abc}", client_secret: s}
searches:
  - name: ram
    sites: [vinted, kleinanzeigen, ebay]
    queries: [ddr4 sodimm]
    kleinanzeigen: {shipping: true}
    ebay: {condition: used}
    max_price: 100
  - name: ebay-only
    site: ebay
    params: {category: 179}
  - name: ebay-url
    url: "https://www.ebay.de/sch/i.html?_nkw=optiplex&_udhi=150"
""")
    config = load_config(cfg)
    ram, only, url = config.searches
    assert config.client.ebay == {"client_id": "abc", "client_secret": "s"}
    assert [q["domain"] for q in ram.queries] == ["www.vinted.de", "www.kleinanzeigen.de", "www.ebay.de"]
    assert ram.queries[2] == {"condition": "used", "search_text": "ddr4 sodimm", "domain": "www.ebay.de", "price_to": 100}
    assert only.queries == [{"category": 179, "domain": "www.ebay.de"}]
    assert url.domain == "www.ebay.de"
    assert url.queries == [{"search_text": "optiplex", "price_to": "150", "domain": "www.ebay.de"}]


def test_shipping_is_stored(tmp_path):
    from vinted_scraper.scoring import Score

    store = Store(tmp_path / "db.sqlite")
    listing = Listing.from_api(parse_search({"itemSummaries": [summary(405100000001, shipping="0.00")]})[0])
    store.upsert("s", listing, Score(1, 120))
    assert store.listing("s", listing.id).shipping == 0
