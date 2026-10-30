import copy
import re
import sqlite3

import pytest

from test_runner import make_runner

from vinted_scraper.client import BlockedError
from vinted_scraper.config import NotifyRules
from vinted_scraper.models import Listing
from vinted_scraper.notify import Deal, DiscordNotifier, Notifier, Podium
from vinted_scraper.ranking import rescore
from vinted_scraper.scoring import Score
from vinted_scraper.storage import Store


class Recorder(Notifier):
    def __init__(self):
        self.deals, self.podiums = [], []

    def send(self, deals):
        self.deals.append(deals)

    def send_podium(self, podium):
        self.podiums.append(podium)


def podium_runner(tmp_path, items, **notify):
    runner, fake = make_runner(tmp_path, items)
    runner.config.searches[0].notify = NotifyRules(min_value=1, podium=3, **notify)
    rec = Recorder()
    runner.notifiers = [rec]
    return runner, fake, rec


def ids(podium):
    return [int(d.url.rsplit("/", 1)[1]) for d in podium.deals]


def test_first_run_sends_the_whole_podium_once(tmp_path, catalog_items):
    runner, _, rec = podium_runner(tmp_path, catalog_items)
    runner.run_once()
    assert len(rec.podiums) == 1 and rec.deals == []  # no per-deal alerts in podium mode
    podium = rec.podiums[0]
    assert ids(podium) == [101, 102, 105]  # 103 defect, 104 excluded
    assert [d.rank for d in podium.deals] == [1, 2, 3]
    assert [d.change for d in podium.deals] == ["new"] * 3
    runner.run_once()
    assert len(rec.podiums) == 1  # unchanged: no second alert


def test_better_listing_entering_reorders_the_podium(tmp_path, catalog_items):
    runner, _, rec = podium_runner(tmp_path, catalog_items)
    runner.run_once()
    better = copy.deepcopy(catalog_items[0])
    better.update(id=106, url="https://www.vinted.de/items/106")
    better["price"]["amount"] = "80.0"
    better["total_item_price"]["amount"] = "84.7"
    runner2, _, rec2 = podium_runner(tmp_path, catalog_items + [better])
    runner2.run_once()
    podium = rec2.podiums[0]
    assert ids(podium) == [106, 101, 102]
    assert [d.change for d in podium.deals] == ["new", "down from #1", "down from #2"]
    assert podium.dropped == ["Mini PC sehr schnell"]


def test_item_missing_from_results_is_checked_and_sold_ones_drop_out(tmp_path, catalog_items):
    runner, _, rec = podium_runner(tmp_path, catalog_items)
    runner.run_once()
    without_best = catalog_items[1:]

    runner2, fake2, rec2 = podium_runner(tmp_path, without_best)
    runner2.run_once()
    assert fake2["c"].page_calls[0] == 101
    assert rec2.podiums == []  # still ranked (page didn't say it was sold), nothing changed

    runner3, fake3, rec3 = podium_runner(tmp_path, without_best)
    runner3.client("www.vinted.de").availability = {101: True}
    runner3.run_once()
    assert fake3["c"].page_calls[0] == 101 and rec3.podiums == []

    runner4, fake4, rec4 = podium_runner(tmp_path, without_best)
    runner4.client("www.vinted.de").availability = {101: False}
    runner4.run_once()
    assert fake4["c"].page_calls[0] == 101
    assert ids(rec4.podiums[0]) == [102, 105]
    assert rec4.podiums[0].dropped == ["Dell OptiPlex 7060 Micro i5-8500T 16GB RAM 256GB SSD"]
    assert runner4.store.top("pcs") and 101 not in [r["id"] for r in runner4.store.top("pcs")]


def test_price_drop_on_the_podium_alerts(tmp_path, catalog_items):
    runner, _, rec = podium_runner(tmp_path, catalog_items)
    runner.run_once()
    cheaper = copy.deepcopy(catalog_items)
    cheaper[1]["price"]["amount"] = "50.0"
    runner2, _, rec2 = podium_runner(tmp_path, cheaper)
    runner2.run_once()
    second = rec2.podiums[0].deals[1]
    assert second.price == 50 and second.price_drop_from == 60 and second.change == "price drop"

    cheaper[1]["price"]["amount"] = "45.0"
    runner3, _, rec3 = podium_runner(tmp_path, cheaper, price_drop=False)
    runner3.run_once()
    assert rec3.podiums == []


def test_raised_price_on_the_podium_alerts_even_without_price_drop(tmp_path, catalog_items):
    runner, _, rec = podium_runner(tmp_path, catalog_items, price_drop=False)
    runner.run_once()
    dearer = copy.deepcopy(catalog_items)
    dearer[1]["price"]["amount"] = "62.0"  # 60 -> 62: still second, but the last alert said 60
    runner2, _, rec2 = podium_runner(tmp_path, dearer, price_drop=False)
    runner2.run_once()
    second = rec2.podiums[0].deals[1]
    assert second.price == 62 and second.price_drop_from is None and second.change == "price up from 60"


def test_availability_check_picks_up_a_new_price(tmp_path, catalog_items):
    # Posted at 120, seen once, then the seller asks 150: the podium must not keep the old price.
    runner, _, rec = podium_runner(tmp_path, catalog_items)
    runner.run_once()
    runner2, fake2, rec2 = podium_runner(tmp_path, catalog_items[1:])
    client = runner2.client("www.vinted.de")
    client.availability, client.prices = {101: True}, {101: 150.0}
    runner2.run_once()
    assert fake2["c"].page_calls[0] == 101
    listing = runner2.store.listing("pcs", 101)
    assert listing.price == 150 and listing.total_price == 158.38  # Vinted's buyer fee grows with it
    sent = {d.url.rsplit("/", 1)[1]: d for d in rec2.podiums[0].deals}
    assert sent["101"].price == 150 and "price up from 120" in sent["101"].change


def test_blocked_run_keeps_the_last_podium(tmp_path, catalog_items):
    runner, _, rec = podium_runner(tmp_path, catalog_items)
    runner.run_once()
    runner2, fake2, rec2 = podium_runner(tmp_path, [])

    def blocked(params, pages=1, until=None):
        raise BlockedError("403")

    runner2.client("www.vinted.de").search = blocked
    runner2.run_once()
    assert rec2.podiums == [] and fake2["c"].page_calls == []
    assert [p.id for p in runner2.store.podium("pcs")] == [101, 102, 105]


def test_rescore_applies_config_changes_to_stored_listings(tmp_path, catalog_items):
    runner, _ = make_runner(tmp_path, catalog_items)
    runner.run_once()
    search = runner.config.searches[0]
    store = runner.store
    fresh = {r["id"]: r["value"] for r in store.top("pcs")}
    assert list(fresh) == [101, 102, 105]

    search.exclude = re.compile("thinkcentre", re.I)
    search.scorer = {"type": "computer", "require_cpu": True}
    rescore(store, search)
    assert [r["id"] for r in store.top("pcs")] == [101]
    rows = {r["id"]: r["reject"] for r in store.db.execute("SELECT id, reject FROM items")}
    assert rows[102] == "excluded" and rows[105] == "cpu unknown"

    search.exclude = None
    search.scorer = {"type": "computer"}
    rescore(store, search)
    assert [r["id"] for r in store.top("pcs")] == [101, 102, 105, 104]  # the filtered monitor is back
    assert {r["id"]: r["value"] for r in store.top("pcs") if r["id"] != 104} == fresh  # same as freshly scraped


def test_old_database_gets_new_columns(tmp_path):
    path = tmp_path / "old.db"
    db = sqlite3.connect(path)
    db.execute("CREATE TABLE items (search TEXT NOT NULL, id INTEGER NOT NULL, title TEXT, price REAL, currency TEXT, "
               "cost REAL, points REAL, value REAL, summary TEXT, url TEXT, photo TEXT, description TEXT, reject TEXT, "
               "first_seen REAL, last_seen REAL, notified_price REAL, PRIMARY KEY (search, id))")
    db.execute("INSERT INTO items (search, id, title, price, currency, last_seen) VALUES ('pcs', 1, 'M720q', 100, 'EUR', 1e12)")
    db.commit()
    db.close()
    [listing] = Store(path).listings("pcs")
    assert listing.total_price == 105.7  # buyer fee estimated for rows stored without it


def deal(rank, **kw):
    return Deal("pcs", f"PC {rank}", 100.0, "EUR", 9.5, "i5-8500T", f"https://x/{rank}", rank=rank, **kw)


def test_podium_text_and_discord_payload(monkeypatch):
    podium = Podium("pcs", [deal(1, change="new"), deal(2), deal(3, change="up from #4")], dropped=["Old PC"])
    text = podium.text()
    assert text.startswith("🏆 Top 3 changed · pcs")
    assert "🥇 9.5 pts/100EUR" in text and "(new)" in text and "🥉" in text and "Out: Old PC" in text

    sent = []
    monkeypatch.setattr("vinted_scraper.notify._post", lambda url, payload: sent.append(payload))
    DiscordNotifier("https://discord.invalid/hook").send_podium(podium)
    [payload] = sent
    assert "Top 3 changed" in payload["content"] and "Old PC" in payload["content"]
    assert [e["title"] for e in payload["embeds"]] == ["🥇 PC 1", "🥈 PC 2", "🥉 PC 3"]
    assert "up from #4" in payload["embeds"][2]["description"]


def test_ranking_for_display_works_while_a_scrape_holds_the_write_lock(tmp_path, catalog_items):
    from vinted_scraper.ranking import ranked

    runner, _ = make_runner(tmp_path, catalog_items)
    runner.run_once()
    writer = sqlite3.connect(tmp_path / "db.sqlite")
    writer.execute("BEGIN IMMEDIATE")  # what a running scrape looks like from outside
    try:
        viewer = Store(tmp_path / "db.sqlite")
        viewer.db.execute("PRAGMA busy_timeout = 100")
        search = runner.config.searches[0]
        search.scorer = {"type": "computer", "require_cpu": True}
        assert [r["id"] for r in ranked(viewer, search, limit=3)] == [101, 102]
    finally:
        writer.rollback()


def test_availability_check_completes_the_description(tmp_path, catalog_items):
    runner, _, rec = podium_runner(tmp_path, catalog_items)
    runner.run_once()
    runner2, fake2, rec2 = podium_runner(tmp_path, catalog_items[1:])
    client = runner2.client("www.vinted.de")
    client.availability = {101: True}
    client.descriptions = {101: "Dell OptiPlex 7060 Micro, i5-8500T, 16GB RAM, 256GB SSD, defekt: startet nicht"}
    runner2.run_once()
    assert "startet nicht" in runner2.store.listing("pcs", 101).description
    assert ids(rec2.podiums[0]) == [102, 105]  # the full text showed it is broken


def test_podium_candidate_with_snippet_gets_its_full_description(tmp_path, catalog_items):
    items = copy.deepcopy(catalog_items)
    best = items[0]
    best.update(description="Dell OptiPlex 7060 Micro. Es...", description_partial=True)
    runner, fake, rec = podium_runner(tmp_path, items)
    client = runner.client("www.vinted.de")
    client.availability = {101: True}
    client.descriptions = {101: "Dell OptiPlex 7060 Micro. Es fehlt: RAM, SSD und Netzteil"}
    runner.run_once()
    assert 101 in client.page_calls  # seen in this run, but only as a snippet
    assert runner.store.listing("pcs", 101).partial_description is False
    row = runner.store.db.execute("SELECT summary FROM items WHERE id=101").fetchone()
    assert "barebone" in row["summary"] and "no_psu" in row["summary"]


def test_friends_and_family_in_the_description_loses_the_podium_place(tmp_path, catalog_items):
    # Vinted search results have no description: a podium place needs the listing checked first.
    runner, _, rec = podium_runner(tmp_path, catalog_items)
    client = runner.client("www.vinted.de")
    client.availability = {101: True, 102: True, 105: True}
    client.descriptions = {101: "Bezahlung nur per PayPal Freunde und Familie"}
    runner.run_once()
    assert 101 not in ids(rec.podiums[0])
    assert runner.store.listing("pcs", 101).description.startswith("Bezahlung")
    assert [r["reject"] for r in runner.store.rows("pcs") if r["id"] == 101] == ["paypal f&f"]


def test_ignored_listing_leaves_ranking_and_undo_restores_it(tmp_path):
    store = Store(tmp_path / "db.sqlite")
    score = Score(10, 100, "ok")
    for item_id in (1, 2):
        store.upsert("pcs", Listing(id=item_id, title=f"PC {item_id}", price=100, currency="EUR", url="u"), score)
    store.commit()
    store.set_ignored(1, True)
    assert [r["id"] for r in store.top("pcs")] == [2] and [r["id"] for r in store.rows("pcs")] == [2]
    assert store.ignored() == {1}
    store.set_ignored(1, False)
    assert {r["id"] for r in store.top("pcs")} == {1, 2} and store.ignored() == set()


def test_discord_bot_sends_ignore_buttons_webhook_does_not(monkeypatch):
    sent = []
    monkeypatch.setattr("vinted_scraper.notify._post", lambda url, payload, headers=None: sent.append((url, payload, headers)))
    deals = [Deal("pcs", f"PC {i}", 100, "EUR", 9, "s", "u", rank=i, item_id=100 + i) for i in range(1, 7)]
    DiscordNotifier(bot_token="tok", channel_id=42).send_podium(Podium("pcs", deals))
    DiscordNotifier("https://discord.invalid/hook").send_podium(Podium("pcs", deals))
    (url, payload, headers), (_, hook_payload, hook_headers) = sent
    assert url.endswith("/channels/42/messages") and headers == {"Authorization": "Bot tok"}
    assert [len(row["components"]) for row in payload["components"]] == [5, 1]
    first = payload["components"][0]["components"][0]
    assert (first["label"], first["custom_id"]) == ("Ignore 🥇", "ignore:101")
    assert payload["components"][1]["components"][0]["label"] == "Ignore #6"
    assert "components" not in hook_payload and hook_headers is None
    with pytest.raises(ValueError):
        DiscordNotifier(bot_token="tok")
