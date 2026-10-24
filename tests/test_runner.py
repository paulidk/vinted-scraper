import copy

from vinted_scraper.config import NotifyRules, SearchConfig, Config
from vinted_scraper.client import ClientSettings, ItemPage
from vinted_scraper.runner import Runner


class FakeClient:
    def __init__(self, settings, items=None, descriptions=None):
        self.settings = settings
        self.items = items or []
        self.descriptions = descriptions or {}
        self.searches = []
        self.detail_calls = []
        self.availability = {}  # item id -> True/False, missing = page doesn't say
        self.page_calls = []

    def search(self, params, pages=1):
        self.searches.append(params)
        return copy.deepcopy(self.items)

    def item_description(self, item_id):
        self.detail_calls.append(item_id)
        return self.descriptions.get(item_id)

    def item_page(self, item_id):
        self.page_calls.append(item_id)
        return ItemPage(self.descriptions.get(item_id) or "", self.availability.get(item_id))


def make_runner(tmp_path, items, descriptions=None, **search_kw):
    search = SearchConfig(
        name="pcs",
        domain="www.vinted.de",
        queries=[{"search_text": "optiplex"}, {"search_text": "mini pc"}],
        exclude=__import__("re").compile("monitor", __import__("re").I),
        scorer={"type": "computer"},
        notify=NotifyRules(min_value=1, top=10),
        **search_kw,
    )
    cfg = Config(searches=[search], client=ClientSettings(), notifiers=[], database=str(tmp_path / "db.sqlite"))
    fake = {}

    def factory(settings):
        fake["c"] = FakeClient(settings, items, descriptions)
        return fake["c"]

    runner = Runner(cfg, client_factory=factory)
    runner.notifiers = []
    return runner, fake


def test_run_ranks_filters_and_dedupes(tmp_path, catalog_items):
    runner, fake = make_runner(tmp_path, catalog_items)
    deals = runner.run_once()
    ids = [d.url.rsplit("/", 1)[1] for d in deals]
    assert len(fake["c"].searches) == 2  # both queries ran
    assert ids[0] == "101"  # best value first
    assert "103" not in ids  # defect rejected
    assert "104" not in ids  # excluded by regex
    assert len(ids) == len(set(ids))  # deduped across queries


def test_no_repeat_notifications_but_price_drops(tmp_path, catalog_items):
    runner, _ = make_runner(tmp_path, catalog_items)
    assert runner.run_once()
    assert runner.run_once() == []

    cheaper = copy.deepcopy(catalog_items)
    cheaper[0]["price"]["amount"] = "90.0"
    cheaper[0]["total_item_price"]["amount"] = "95.0"
    runner2, _ = make_runner(tmp_path, cheaper)
    deals = runner2.run_once()
    assert [d.price_drop_from for d in deals] == [120.0]


def test_fetches_details_for_vague_listings(tmp_path, catalog_items):
    runner, fake = make_runner(
        tmp_path, catalog_items, descriptions={105: "Intel i7-8700T, 32GB RAM, 1TB NVMe"}, fetch_details=5
    )
    deals = {d.url.rsplit("/", 1)[1]: d for d in runner.run_once()}
    assert 105 in fake["c"].detail_calls
    assert "i7-8700t" in deals["105"].summary
    # description is cached: no second fetch next run
    fake["c"].detail_calls.clear()
    runner.run_once()
    assert 105 not in fake["c"].detail_calls


def test_stops_querying_when_blocked(tmp_path):
    from vinted_scraper.client import BlockedError

    runner, fake = make_runner(tmp_path, [])
    calls = []

    def blocked(params, pages=1):
        calls.append(params)
        raise BlockedError("403")

    runner.client("www.vinted.de").search = blocked
    assert runner.run_once() == []
    assert len(calls) == 1


def test_scorer_bug_hitting_every_listing_is_reported_as_crash(tmp_path, catalog_items, monkeypatch, caplog):
    class Broken:
        def score(self, listing):
            raise ZeroDivisionError("bug")

    monkeypatch.setattr("vinted_scraper.runner.build_scorer", lambda cfg: Broken())
    runner, _ = make_runner(tmp_path, catalog_items)
    assert runner.run_once() == []
    assert "search crashed" in caplog.text
    assert "scoring failed for all" in caplog.text


def test_failed_rescore_keeps_fetched_description(tmp_path, catalog_items, monkeypatch):
    from vinted_scraper.scoring import build_scorer

    real = build_scorer({"type": "computer"})

    class FailsWithDescription:
        def score(self, listing):
            if listing.description:
                raise ValueError("bug in description parsing")
            return real.score(listing)

    monkeypatch.setattr("vinted_scraper.runner.build_scorer", lambda cfg: FailsWithDescription())
    runner, fake = make_runner(tmp_path, catalog_items, descriptions={105: "i7-8700T 32GB RAM"}, fetch_details=5)
    deals = runner.run_once()
    assert fake["c"].detail_calls == [105]
    assert "105" not in [d.url.rsplit("/", 1)[1] for d in deals]
    assert runner.store.previous("pcs", 105).description == "i7-8700T 32GB RAM"
    fake["c"].detail_calls.clear()
    runner.run_once()
    assert fake["c"].detail_calls == []


def test_description_excluded_listing_is_not_fetched_again(tmp_path, catalog_items):
    runner, fake = make_runner(tmp_path, catalog_items, descriptions={105: "comes with a monitor"}, fetch_details=5)
    deals = runner.run_once()
    assert "105" not in [d.url.rsplit("/", 1)[1] for d in deals]
    assert fake["c"].detail_calls == [105]
    fake["c"].detail_calls.clear()
    runner.run_once()
    assert fake["c"].detail_calls == []
