import pytest
from test_runner import FakeClient

from vinted_scraper import runner as runner_mod
from vinted_scraper.client import ClientSettings
from vinted_scraper.config import Config, NotifyRules, SearchConfig, _quiet_hours
from vinted_scraper.runner import Runner, _seconds_until


@pytest.mark.parametrize("raw, parsed", [
    ([1, 7], (60, 420)), (["23:30", "06:00"], (1410, 360)), ("1-7", (60, 420)), (None, None), ([3, 3], None),
])
def test_quiet_hours_config(raw, parsed):
    assert _quiet_hours(raw) == parsed


def test_quiet_hours_wrap_midnight():
    cfg = Config(searches=[], client=ClientSettings(), notifiers=[], quiet_hours=(23 * 60, 6 * 60))
    assert [h for h in range(24) if cfg.is_quiet(h * 60)] == [0, 1, 2, 3, 4, 5, 23]
    with pytest.raises(ValueError):
        _quiet_hours([1, 2, 3])


@pytest.fixture
def scheduled(tmp_path, catalog_items, monkeypatch):
    searches = [SearchConfig(name=name, domain="www.vinted.de", queries=[{"search_text": name}], scorer={"type": "computer"},
                             notify=NotifyRules(min_value=1), interval_minutes=interval)
                for name, interval in (("mini", 20), ("desk", None), ("servers", 120))]
    cfg = Config(searches=searches, client=ClientSettings(), notifiers=[], database=str(tmp_path / "db.sqlite"),
                 interval_minutes=30, jitter_minutes=4, quiet_hours=(60, 420))
    runner = Runner(cfg, client_factory=lambda settings: FakeClient(settings, catalog_items))
    runner.notifiers = []
    clock = {"now": 1_000_000.0, "minute": 12 * 60}
    monkeypatch.setattr(runner_mod.time, "time", lambda: clock["now"])
    monkeypatch.setattr(runner_mod, "_minute_of_day", lambda when: clock["minute"])
    monkeypatch.setattr(runner_mod.random, "uniform", lambda a, b: 0.0)  # no jitter

    def ran():
        return [q["search_text"] for q in runner.client("www.vinted.de").searches]

    return runner, clock, ran


def test_each_search_runs_on_its_own_interval(scheduled):
    runner, clock, ran = scheduled
    runner.run_due()
    assert ran() == ["mini", "desk", "servers"]  # never ran: all due
    runner.run_due()
    assert ran() == ["mini", "desk", "servers"]  # nothing due right after
    clock["now"] += 20 * 60
    runner.run_due()
    assert ran()[3:] == ["mini"]
    clock["now"] += 10 * 60  # 30 min after the start
    runner.run_due()
    assert ran()[4:] == ["desk"]
    clock["now"] += 90 * 60  # 120 min after the start
    runner.run_due()
    assert sorted(ran()[5:]) == ["desk", "mini", "servers"]


def test_quiet_hours_and_slack(scheduled):
    runner, clock, ran = scheduled
    clock["minute"] = 3 * 60  # 03:00
    assert runner.run_due() == [] and ran() == []
    clock["minute"] = 7 * 60
    runner.run_due()
    assert len(ran()) == 3
    clock["now"] += 17 * 60  # a timer firing 3 minutes before mini is due
    runner.run_due()
    assert ran()[3:] == []
    runner.run_due(slack=300)
    assert ran()[3:] == ["mini"]


def test_jitter_is_capped_at_a_quarter_of_the_interval(scheduled, monkeypatch):
    runner, _, _ = scheduled
    spreads = []
    monkeypatch.setattr(runner_mod.random, "uniform", lambda a, b: spreads.append(b) or b)
    mini = runner.config.searches[0]
    runner.config.jitter_minutes = 10
    runner._schedule(mini)
    assert spreads == [5 * 60] and runner._gap["mini"] == 25 * 60


def test_seconds_until_wraps_midnight():
    import time

    noon = time.mktime((2026, 10, 24, 12, 0, 0, 0, 0, -1))
    assert _seconds_until(13 * 60, noon) == 3600
    assert _seconds_until(7 * 60, noon) == 19 * 3600
