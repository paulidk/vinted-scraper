import pytest

from vinted_scraper.models import Listing
from vinted_scraper.scoring import build_scorer


def L(title, price, description=""):
    return Listing(id=1, title=title, price=price, currency="EUR", url="", description=description)


def test_better_specs_same_price_scores_higher():
    s = build_scorer({"type": "computer"})
    weak = s.score(L("OptiPlex 3050 Micro i5-7500T 8GB 256GB SSD", 100))
    strong = s.score(L("OptiPlex 7070 Micro i7-9700T 16GB 512GB SSD", 100))
    assert strong.value > weak.value > 0


def test_cheaper_same_specs_scores_higher():
    s = build_scorer({"type": "computer"})
    assert s.score(L("M720q i5-8500T 8GB", 80)).value > s.score(L("M720q i5-8500T 8GB", 120)).value


def test_power_draw_matters():
    s = build_scorer({"type": "computer", "form_factor": {"tiny": 1, "tower": 1}})
    t = s.score(L("i5-8500T 8GB", 100))
    desktop = s.score(L("i5-8500 8GB", 100))
    assert t.cost < desktop.cost  # same performance class, desktop part burns more electricity
    assert s.score(L("i5-8500T 8GB", 100)).value > build_scorer(
        {"type": "computer", "kwh_price": 0.6, "form_factor": {"tiny": 1}}
    ).score(L("i5-8500T 8GB", 100)).value


def test_rejections():
    s = build_scorer({"type": "computer", "min_cpu_mark": 5000, "reject": ["defect", "barebone"]})
    assert s.score(L("Optiplex micro defekt", 20)).reject == "defect"
    assert s.score(L("Optiplex micro i5-8500T ohne RAM", 50)).reject == "barebone"
    assert s.score(L("Optiplex micro i3-6100T 8GB", 50)).reject.startswith("cpu below")
    assert s.score(L("Optiplex micro i3-6100T 8GB", 50)).value == 0


def test_missing_specs_request_details():
    s = build_scorer({"type": "computer"})
    assert s.score(L("Mini PC wie neu", 50)).needs_details
    assert not s.score(L("Mini PC i5-8500T 8GB", 50)).needs_details


def test_rules_scorer():
    s = build_scorer({
        "type": "rules",
        "base": 10,
        "rules": [
            {"match": r"\b(neu|new)\b", "points": 5},
            {"match": r"(\d+)\s*gb", "per_unit": 0.1, "max": 5},
            {"match": "damaged", "points": -20},
        ],
    })
    a = s.score(L("iPhone 13 128GB new", 300))
    assert a.points == 10 + 5 + 5
    assert s.score(L("iPhone damaged", 100)).points == 0


def test_price_scorer_is_cheapest_first():
    s = build_scorer({"type": "price"})
    assert s.score(L("x", 10)).value > s.score(L("x", 20)).value



def test_dual_cpu_server_costs_more_to_run():
    from vinted_scraper.models import Listing
    from vinted_scraper.scoring import build_scorer

    scorer = build_scorer({"type": "computer"})

    def score(title):
        return scorer.score(Listing(id=1, title=title, price=200, currency="EUR", url=""))

    single = score("Dell PowerEdge R620 Xeon E5-2650 v2 64GB")
    dual = score("Dell PowerEdge R620 2x Xeon E5-2650 v2 64GB")
    tower = score("Workstation Xeon E5-2650 v2 64GB")
    assert dual.points > single.points and dual.cost > single.cost
    assert single.cost > tower.cost  # server: 35 W extra idle draw
    assert "2x e5-2650 v2" in dual.summary


def test_graphics_card_points_and_power():
    from vinted_scraper.models import Listing
    from vinted_scraper.scoring import build_scorer

    def score(scorer, title):
        return scorer.score(Listing(id=1, title=title, price=300, currency="EUR", url=""))

    ignore, count = build_scorer({"type": "computer"}), build_scorer({"type": "computer", "weights": {"gpu": 0.5}})
    plain, with_card = "Gaming PC i5-8400 16GB 512GB SSD", "Gaming PC i5-8400 16GB 512GB SSD RTX 3060"
    assert score(ignore, with_card).points == score(ignore, plain).points  # weight 0: no points ...
    assert score(ignore, with_card).cost > score(ignore, plain).cost  # ... but the card still idles
    assert score(count, with_card).points == pytest.approx(score(count, plain).points + 17000 / 1000 * 0.5 * 0.95, abs=0.02)
    assert "rtx 3060 (17000 G3D)" in score(count, with_card).summary
    assert score(count, "No payment by vinted! Gaming PC RTX 4080 Ryzen 7").reject == "offsite"
