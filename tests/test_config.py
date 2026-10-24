from vinted_scraper.client import params_from_url
from vinted_scraper.config import load_config


def test_params_from_url():
    domain, params = params_from_url(
        "https://www.vinted.fr/catalog?search_text=optiplex%20micro&catalog[]=2994&brand_ids[]=1&brand_ids[]=2"
        "&price_to=150&order=price_low_to_high&search_id=123&time=456"
    )
    assert domain == "www.vinted.fr"
    assert params == {
        "search_text": "optiplex micro",
        "catalog_ids": "2994",
        "brand_ids": "1,2",
        "price_to": "150",
        "order": "price_low_to_high",
    }


def test_load_config(tmp_path, monkeypatch):
    monkeypatch.setenv("HOOK", "https://discord/hook")
    cfg = tmp_path / "c.yaml"
    cfg.write_text(
        """
client: {domain: www.vinted.nl}
notifiers: [{type: discord, webhook_url: "${HOOK}"}]
searches:
  - name: pcs
    queries: [optiplex, thinkcentre tiny]
    params: {catalog_ids: [1, 2]}
    max_price: 200
  - name: from-url
    url: "https://www.vinted.de/catalog?search_text=lego"
"""
    )
    c = load_config(cfg)
    assert c.notifiers[0]["webhook_url"] == "https://discord/hook"
    pcs, lego = c.searches
    assert pcs.domain == "www.vinted.nl"
    assert pcs.queries == [
        {"catalog_ids": "1,2", "search_text": "optiplex", "domain": "www.vinted.nl", "price_to": 200},
        {"catalog_ids": "1,2", "search_text": "thinkcentre tiny", "domain": "www.vinted.nl", "price_to": 200},
    ]
    assert lego.domain == "www.vinted.de" and lego.queries == [{"search_text": "lego", "domain": "www.vinted.de"}]


def test_one_search_on_both_sites(tmp_path):
    cfg = tmp_path / "c.yaml"
    cfg.write_text(
        """
searches:
  - name: mini
    sites: [vinted, kleinanzeigen]
    queries: [optiplex micro]
    params: {order: relevance}
    kleinanzeigen: {category: 228, shipping: true}
    max_price: 200
  - name: desktops
    sites: [vinted, kleinanzeigen]
    url: "https://www.vinted.de/catalog?catalog[]=3581"
    kleinanzeigen: {category: 228}
  - name: ka-only
    site: kleinanzeigen
    params: {category: 228}
  - name: ka-url
    url: "https://www.kleinanzeigen.de/s-pcs/halle/c228l2409r50"
"""
    )
    mini, desktops, ka_only, ka_url = load_config(cfg).searches
    assert mini.queries == [
        {"order": "relevance", "search_text": "optiplex micro", "domain": "www.vinted.de", "price_to": 200},
        {"category": 228, "shipping": True, "search_text": "optiplex micro", "domain": "www.kleinanzeigen.de", "price_to": 200},
    ]
    assert desktops.queries == [{"catalog_ids": "3581", "domain": "www.vinted.de"},
                                {"category": 228, "domain": "www.kleinanzeigen.de"}]
    assert ka_only.queries == [{"category": 228, "domain": "www.kleinanzeigen.de"}]
    assert ka_url.domain == "www.kleinanzeigen.de"
    assert ka_url.queries == [{"path": "/s-pcs/halle/c228l2409r50", "domain": "www.kleinanzeigen.de"}]


def test_example_config_loads():
    from pathlib import Path

    c = load_config(Path(__file__).parent.parent / "config.example.yaml")
    assert c.searches[0].scorer["type"] == "computer"
