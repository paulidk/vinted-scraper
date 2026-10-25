import pytest

from vinted_scraper.models import Listing
from vinted_scraper.scoring import build_scorer
from vinted_scraper.scoring.memory import parse_memory


# Real titles from Vinted and Kleinanzeigen (2026-10-25): total GB, sticks, DDR, module.
@pytest.mark.parametrize("title, gb, sticks, ddr, module", [
    ("(2x16Gb) 32GB DDR4 sodimm", 32, 2, 4, "sodimm"),
    ("32GB (2x16) DDR4 3200 sodimm", 32, 2, 4, "sodimm"),
    ("Kit ram 8GB (2x4GB) Kingston DDR4-2400 so-dimm KVR24S17S6/4", 8, 2, 4, "sodimm"),
    ("Kit ram 8GB (2x4GB) DDR3L-1600 so-dimm Advantech + omaggio 1GB", 8, 2, 3, "sodimm"),
    ("Mémoire Sodimm DDR4 2666 MHz 24 Go", 24, 1, 4, "sodimm"),
    ("💻 Lot de 2 barrettes de RAM SK hynix 8 Go DDR4 2666 MHz", 16, 2, 4, None),
    ("RAM Kingston 8GB DDR4 so - dimm PC4-2400T per Notebook", 8, 1, 4, "sodimm"),
    ("Samsung RAM 16 GB  DDR 4 (2x8 GB Riegel)", 16, 2, 4, None),
    ("Arbeitsspeicher 16 GB DD4 2133", 16, 1, 4, None),
    ("RAM 16GB ddr4 2133MHZ (8x2)", 16, 2, 4, None),
    ("Kit de 16 Go - 2 x barrettes de RAM DDR4 8 Go 3200 Mhz so-dimm Samsung", 16, 2, 4, "sodimm"),
    ("Ram so-dimm 2x8 GB 3200", 16, 2, 4, "sodimm"),  # DDR4 from the speed
    ("2x 16GB DDR5 5600 sodimm (totale 32GB)", 32, 2, 5, "sodimm"),
    ("Lenovo 16 GB (2x 8GB SO-DIMM) DDR4 3200MHZ", 16, 2, 4, "sodimm"),
    ("2 × 4 GB SK hynix DDR4 RAM 2666 MHz SO-DIMM – 8 GB", 8, 2, 4, "sodimm"),
    ("1x SK hynix 32 GB DDR4 RAM 2666 MHz SO-DIMM – HMAA4GS6AJR8N-VK", 32, 1, 4, "sodimm"),
    ("4x 16GB DDR4 2400 MHz SO-DIMM RAM (16GB gesamt)", 16, 4, 4, "sodimm"),
    ("2x Samsung 8GB DDR4-3200MHz SODIMM Arbeitsspeicher", 16, 2, 4, "sodimm"),
    ("Sodimm RAM 1x8gb ddr4 2667 Samsung", 8, 1, 4, "sodimm"),
    ("Kingston Fury 8/16/32Gb DDR4 SO-DIMM Laptop Arbeitsspeicher", 8, 1, 4, "sodimm"),  # base option
    ("Titel: 6x DDR4 Laptop RAM SO-DIMM – 28 GB – 2666 MHz – Kingston", 28, 6, 4, "sodimm"),
    ("ram sodim 4 go pc3l 12800s hynix", 4, 1, 3, "sodimm"),
    ("Mémoire RAM Crucial 16 Go 2666MHz 288-broches DDR4 (CT16G4DFRA266)", 16, 1, 4, "dimm"),
    ("Corsair Vengeance LPX 16GB (2x8GB) RAM", 16, 2, None, "dimm"),
    ("Barrette so-dimm ddr2 Ramaxel", None, 1, 2, "sodimm"),
])
def test_parse_memory(title, gb, sticks, ddr, module):
    mem = parse_memory(title)
    assert (mem.gb, mem.sticks, mem.ddr, mem.module) == (gb, sticks, ddr, module)


def test_speed_from_bandwidth_code():
    assert parse_memory("Samsung 16GB DDR4 SO-DIMM 2Rx8 PC4-21300").mhz == 2666


@pytest.mark.parametrize("title, reject", [
    ("Crucial 16gb ddr4-3200 sodimm cl22 - ram notebook", None),
    ("Ram 4GB DDR4-2133 so-dimm Transcend", "below 16GB"),
    ("Lexar Sodimm 16GB DDR5 Ram 5600MHz", "ddr5"),
    ("Mémoire RAM Crucial 16 Go 2666MHz 288-broches DDR4", "dimm"),
    ("Samsung 16GB DDR4 3200MHz", "module unknown"),
    ("16GB RAM so-dimm", "ddr unknown"),
    ("HPE 8GB P06772-001 DDR4", "ecc"),
    ("ram 16 gb ddr4-2666 so-dimm non-ecc per nas synology e portatili", None),
    ("Notebook Lenovo ThinkPad L590 Intel Core i5-8ge. 256GB 16GB DDR4", "machine"),
    ("Acer aspire laptop i5-6300HQ, 8GB DDR4, 1TB HDD", "machine"),
    ("RAM aus ThinkPad ausgebaut 16GB DDR4 SO-DIMM", None),  # a model name alone isn't a machine
    ("4x 16GB DDR4 2400 MHz SO-DIMM RAM", "4 sticks"),
    ("16GB DDR4 SO-DIMM defekt", "defect"),
])
def test_ram_scorer_rejects(title, reject):
    scorer = build_scorer({"type": "ram", "min_gb": 16, "max_sticks": 2})
    score = scorer.score(Listing(id=1, title=title, price=40, currency="EUR", url="https://www.vinted.de/items/1"))
    assert score.reject == reject
    if reject in ("module unknown", "ddr unknown"):
        assert score.needs_details  # the runner fetches the description


def test_value_is_gb_per_100_eur_and_description_fills_gaps():
    scorer = build_scorer({"type": "ram", "shipping_estimate": 5})
    listing = Listing(id=1, title="Samsung 16GB DDR4 3200MHz", price=35, currency="EUR",
                      url="https://www.kleinanzeigen.de/s-anzeige/1", description="Aus meinem Laptop ausgebaut.")
    score = scorer.score(listing)
    assert score.reject is None and score.points == 16 and score.value == 40.0  # 16 GB for 40 € incl. shipping
    assert build_scorer({"type": "ram", "ddr": 5}).score(listing).reject == "ddr4"
