import html
import json

import pytest

from vinted_scraper import client as client_mod
from vinted_scraper.client import ClientSettings
from vinted_scraper.kleinanzeigen import (ID_OFFSET, KleinanzeigenClient, parse_listing_page, parse_price,
                                          parse_search_page, search_path)
from vinted_scraper.models import Listing


def astro(value):
    """Encode like Astro's island props: every value is [type, value], arrays are type 1."""
    if isinstance(value, dict):
        return [0, {k: astro(v) for k, v in value.items()}]
    if isinstance(value, list):
        return [1, [astro(v) for v in value]]
    return [0, value]


def ad(ad_id, title="Dell OptiPlex 7060 Micro i5-8500T 16GB 256GB", price="120 € VB", **kw):
    return {"organicAdPreview": {
        "id": ad_id, "title": title, "price": price, "description": "Läuft einwandfrei...",
        "seoLink": f"/s-anzeige/x/{ad_id}-228-1", "locationName": "06112", "parentLocationName": "Halle",
        "shippingAvailableValue": "Versand möglich", "posterType": "PRIVATE", "userId": 7,
        "imageList": [{"xLargeUrl": f"https://img/{ad_id}"}], **kw}}


def search_html(ads):
    props = html.escape(json.dumps({"resultAds": astro(ads)}))
    return f'<html><astro-island uid="x" props="{props}"></astro-island></html>'


def test_parses_results_into_vinted_shape():
    ads = [ad(1), {"sponsoredAdPresent": True, "sponsoredAd": {}},
           ad(2, "Gaming PC", "1.299 €", shippingAvailableValue="", posterType="COMMERCIAL"),
           ad(3, "Reserviert • Mini PC")]
    items = parse_search_page(search_html(ads))
    assert [i["id"] % ID_OFFSET for i in items] == [1, 2]  # no sponsored slot, no reserved listing
    assert all(i["id"] > ID_OFFSET for i in items)  # never the same id as a Vinted listing
    first, second = (Listing.from_api(i) for i in items)
    assert (first.price, first.currency, first.url) == (120, "EUR", "https://www.kleinanzeigen.de/s-anzeige/x/1-228-1")
    assert first.extra == "06112 Halle · Versand möglich · VB" and first.partial_description
    assert first.photo == "https://img/1"
    assert second.price == 1299 and second.extra == "06112 Halle · nur Abholung · gewerblich"
    assert parse_search_page("<html>changed</html>") is None


@pytest.mark.parametrize("text, price", [("120 € VB", 120), ("1.299 €", 1299), ("49,99 €", 49.99), ("VB", None),
                                         ("Zu verschenken", None)])
def test_price(text, price):
    assert parse_price(text) == price


@pytest.mark.parametrize("params, page, path", [
    ({"search_text": "rack server", "category": 228, "price_from": 30, "price_to": 500, "shipping": True}, 2,
     "/s-anzeige:angebote/preis:30:500/versand:ja/seite:2/rack-server/k0c228"),
    ({"category": 228, "seller": "privat"}, 1, "/s-anzeige:angebote/anbieter:privat/c228"),
    ({"search_text": "optiplex", "category": 228, "location": 2409, "radius": 30}, 1,
     "/s-anzeige:angebote/optiplex/k0c228l2409r30"),
    ({"path": "/s-pcs/halle/optiplex/k0c228l2409r50"}, 3, "/s-pcs/halle/seite:3/optiplex/k0c228l2409r50"),
    ({"path": "/s-seite:4/optiplex/k0"}, 1, "/s-optiplex/k0"),
    ({"path": "/s-pcs/anzeige:angebote/c228"}, 2, "/s-pcs/anzeige:angebote/seite:2/c228"),
])
def test_search_path(params, page, path):
    assert search_path(params, page) == path


def test_listing_page():
    page = '<h1 id="viewad-title">Mini PC</h1><p id="viewad-description-text" class="x">i5-8500T<br/>16 GB &amp; 256 GB</p>'
    assert parse_listing_page(page).description == "i5-8500T\n16 GB & 256 GB"
    assert parse_listing_page(page).available is True
    priced = '<h2 class="boxedarticle--price" id="viewad-price">\n   150 € VB</h2>'
    assert parse_listing_page(priced).price == 150
    assert parse_listing_page(page).price is None
    reserved = '<h1 id="viewad-title"><span class="pvap-reserved-title">Reserviert • </span>Mini PC</h1>'
    assert parse_listing_page(reserved).available is False


class Resp:
    def __init__(self, text, url="https://www.kleinanzeigen.de/s-anzeige/1", status=200):
        self.status_code, self.text, self.url = status, text, url
        self.headers = {"content-type": "text/html"}
        self.content = text.encode()

    def raise_for_status(self):
        pass


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(client_mod.time, "sleep", lambda s: None)
    responses, log = [], []

    class Session:
        cookies, headers = {}, {}

        def get(self, url, **kw):
            log.append(url)
            return responses.pop(0)

        def close(self):
            pass

    monkeypatch.setattr(KleinanzeigenClient, "_new_session", lambda self: Session())
    c = KleinanzeigenClient(ClientSettings(domain="www.kleinanzeigen.de", min_delay=0, max_delay=0))
    return c, responses, log


def test_pages_until_short_page_or_known(client):
    c, responses, log = client
    full = search_html([ad(i) for i in range(1, 26)])
    responses += [Resp(full), Resp(full), Resp(search_html([ad(99)]))]
    assert len(c.search({"category": 228}, pages=5)) == 51 and len(log) == 3
    responses += [Resp(full), Resp(full)]
    assert len(c.search({"category": 228}, pages=5, until=lambda batch: True)) == 25


def test_deleted_listing_redirects_home(client):
    c, responses, log = client
    responses += [Resp("<html>home</html>", url="https://www.kleinanzeigen.de/")]
    assert c.item_page(1).available is False
    responses += [Resp('<p id="viewad-description-text">i7-8700T</p>')]
    page = c.item_page(ID_OFFSET + 2)
    assert page.available is True and page.description == "i7-8700T"
    assert log[-1].endswith("/s-anzeige/2")  # the site's own id, without the offset
