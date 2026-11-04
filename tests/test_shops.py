import pytest

from vinted_scraper.client import ClientSettings
from vinted_scraper.config import _search, load_config
from vinted_scraper.models import Listing
from vinted_scraper.mydealz import ID_OFFSET as MD_OFFSET, MydealzClient, parse_feed
from vinted_scraper.runner import make_client
from vinted_scraper.scoring import build_scorer
from vinted_scraper.scoring.specs import payment_reject, pickup_only
from vinted_scraper.shopify import ID_OFFSET as SHOP_OFFSET, ShopifyClient

FEED = """<?xml version="1.0" encoding="UTF-8"?>
<rss xmlns:pepper="http://www.pepper.com/rss" xmlns:media="http://search.yahoo.com/mrss/" version="2.0"><channel>
<item><pepper:merchant name="AliExpress" price="1.273,24€"/><media:content medium="image" url="https://static.mydealz.de/1.jpg"/>
<title><![CDATA[Firebat Mini PC F1: AMD Ryzen 7 H255, 16 GB, 512 GB SSD]]></title>
<description><![CDATA[<strong>1.273,24€ - AliExpress</strong><br /><p>Mit Gutschein. Nur PayPal Freunde? Nein.</p>]]></description>
<link>https://www.mydealz.de/deals/firebat-mini-pc-f1-2862449</link></item>
<item><pepper:merchant name="Shop" price="129€"/><title><![CDATA[HP ProDesk 400 G4 Mini]]></title>
<description><![CDATA[x]]></description><link>https://www.mydealz.de/deals/hp-prodesk-2863132</link></item>
<item><title><![CDATA[10% Gutschein]]></title><link>https://www.mydealz.de/gutscheine/x-1</link></item>
</channel></rss>"""


def test_mydealz_feed():
    first, second = parse_feed(FEED)
    assert first["id"] == MD_OFFSET + 2862449 and first["price"]["amount"] == 1273.24
    assert first["extra"] == "AliExpress · mydealz" and first["photo"]["url"].endswith("1.jpg")
    assert first["description"] == "Mit Gutschein. Nur PayPal Freunde? Nein."  # the price line is dropped
    assert second["price"]["amount"] == 129 and parse_feed("not xml") == []


def test_mydealz_search_filters_price_and_checks_expiry(monkeypatch):
    client = MydealzClient(ClientSettings(domain="www.mydealz.de"))
    monkeypatch.setattr(client, "get_html", lambda path, *a, **k: FEED if path == "/rss/gruppe/mini-pc" else "")
    assert [i["price"]["amount"] for i in client.search({"group": "mini-pc", "price_to": 500})] == [129]
    assert client.search({"group": "nope"}) == []

    class Resp:
        status_code, text = 200, '{"isExpired":true}'

    monkeypatch.setattr(client, "_get", lambda *a, **k: Resp())
    assert client.item_page(1, "https://www.mydealz.de/deals/x-1").available is False
    Resp.text = '{"isExpired":false}'
    assert client.item_page(1, "https://www.mydealz.de/deals/x-1").available is True
    assert client.item_page(1) is None  # no URL, no way to find the deal
    monkeypatch.setattr(client, "_get", lambda *a, **k: None)
    assert client.item_page(1, "https://www.mydealz.de/deals/x-1").available is False


def product(handle, variants, title="Lenovo ThinkCentre M720q Tiny, i5-8500T"):
    return {"title": title, "handle": handle, "vendor": "Lenovo", "body_html": "<p>Refurbished, 24 Monate Garantie</p>",
            "images": [{"src": "https://cdn.shopify.com/a.png"}], "variants": variants}


def test_shopify_search_one_listing_per_variant_in_stock(monkeypatch):
    client = ShopifyClient(ClientSettings(domain="it-versand.com"))
    pages = {1: [product("m720q", [{"id": 11, "title": "16GB / 256GB", "price": "229.00", "available": True},
                                   {"id": 12, "title": "32GB / 512GB", "price": "289.00", "available": False}])] * 250,
             2: [product("m70q", [{"id": 21, "title": "Default Title", "price": "349.00", "available": True}])]}
    calls = []

    def get_json(path, params=None, **kw):
        calls.append((path, params))
        return {"products": pages.get(params["page"], [])}

    monkeypatch.setattr(client, "get_json", get_json)
    items = client.search({"collection": "/collections/thinkcentre/"}, pages=5)
    assert calls[0] == ("/collections/thinkcentre/products.json", {"limit": 250, "page": 1}) and len(calls) == 2
    first, last = Listing.from_api(items[0]), Listing.from_api(items[-1])
    assert first.id == SHOP_OFFSET + 11 and first.price == 229 and first.title.endswith("· 16GB / 256GB")
    assert first.url == "https://it-versand.com/products/m720q?variant=11" and first.extra == "it-versand.com"
    assert last.title == "Lenovo ThinkCentre M720q Tiny, i5-8500T" and last.price == 349
    assert client.search({"collection": "/collections/x", "price_to": 300}, pages=1)[0]["id"] == SHOP_OFFSET + 11

    js = {"description": "<p>Neu aufbereitet</p>", "variants": [{"id": 11, "price": 21900, "available": True},
                                                                {"id": 12, "price": 28900, "available": False}]}
    monkeypatch.setattr(client, "get_json", lambda path, *a, **k: js if path == "/products/m720q.js" else {})
    page = client.item_page(SHOP_OFFSET + 11, "https://it-versand.com/products/m720q?variant=11")
    assert (page.available, page.price, page.description) == (True, 219, "Neu aufbereitet")
    assert client.item_page(SHOP_OFFSET + 12, "https://it-versand.com/products/m720q?variant=12").available is False
    assert client.item_page(1, "https://it-versand.com/products/gone?variant=1").available is False
    assert client.item_page(1) is None


def test_config_feeds_and_client_choice(tmp_path):
    s = _search({"name": "mini", "sites": ["vinted", "mydealz", "shopify"], "queries": ["optiplex", "thinkcentre"],
                 "mydealz": {"groups": ["mini-pc", "/gruppe/desktop-pc"]}, "max_price": 300,
                 "shopify": {"collections": ["https://it-versand.com/collections/lenovo-thinkcentre-refurbished"]}},
                "www.vinted.de")
    feeds = [q for q in s.queries if q["domain"] != "www.vinted.de"]
    assert feeds == [{"group": "mini-pc", "domain": "www.mydealz.de", "price_to": 300},
                     {"group": "desktop-pc", "domain": "www.mydealz.de", "price_to": 300},
                     {"collection": "/collections/lenovo-thinkcentre-refurbished", "domain": "it-versand.com", "price_to": 300}]
    for bad in ({"mydealz": {}}, {"shopify": {"collections": ["https://it-versand.com/products/x"]}}):
        with pytest.raises(ValueError):
            _search({"name": "x", "sites": list(bad), **bad}, "www.vinted.de")

    path = tmp_path / "config.yaml"
    path.write_text("searches: [{name: m, sites: [shopify, mydealz], mydealz: {groups: [mini-pc]},"
                    " shopify: {collections: ['https://it-versand.com/collections/x']}}]")
    config = load_config(path)
    assert config.client.shop_domains == ["it-versand.com"]
    import dataclasses
    assert isinstance(make_client(dataclasses.replace(config.client, domain="it-versand.com")), ShopifyClient)
    assert isinstance(make_client(dataclasses.replace(config.client, domain="www.mydealz.de")), MydealzClient)


@pytest.mark.parametrize("url", ["https://www.mydealz.de/deals/x-1", "https://it-versand.com/products/m720q?variant=11"])
def test_shops_skip_payment_and_pickup_checks(url):
    listing = Listing(id=1, title="Dell OptiPlex 3060 Micro i5-8500T 16GB 256GB", price=100, currency="EUR", url=url,
                      description="Zahlung per PayPal Freunde, nur Abholung im Markt möglich")
    assert payment_reject(listing) is None and not pickup_only(listing)
    assert build_scorer({"type": "computer"}).score(listing).reject is None
