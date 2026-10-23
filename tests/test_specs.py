import pytest

from vinted_scraper.scoring.cpu_db import lookup
from vinted_scraper.scoring.specs import parse_specs


@pytest.mark.parametrize(
    "text, model, gen, tdp",
    [
        ("Dell OptiPlex 3060 Micro i5-8500T", "i5-8500t", 8, 35),
        ("Intel Core i7 9700T", "i7-9700t", 9, 35),
        ("i5-1135G7 NUC", "i5-1135g7", 11, 28),
        ("Core i5-10210U", "i5-10210u", 10, 15),
        ("Beelink mini S12 Pro N100", "n100", None, 6),
        ("Lenovo M75q Gen 2 AMD Ryzen 5 PRO 4650GE", "ryzen 5 4650ge", 4, 35),
        ("Minisforum UM790 Ryzen 9 7940HS", "ryzen 9 7940hs", 7, 35),
    ],
)
def test_cpu_lookup(text, model, gen, tdp):
    cpu = lookup(text)
    assert cpu is not None
    assert (cpu.model, cpu.generation, cpu.tdp) == (model, gen, tdp)
    assert not cpu.estimated


def test_cpu_generation_only_is_estimated():
    cpu = lookup("Fujitsu Esprimo Q556 Intel i5 6. Generation")
    assert cpu.estimated and cpu.generation == 6


def test_unknown_intel_model_is_estimated_by_generation():
    old, new = lookup("i5-4590T"), lookup("i5-12450T")
    assert old.estimated and new.estimated
    assert new.mark > old.mark


def test_cpu_overrides():
    assert lookup("i5-8500T", {"i5-8500T": [1234, 35]}).mark == 1234


@pytest.mark.parametrize(
    "text, ram, storage",
    [
        ("OptiPlex 7060 Micro i5-8500T 16GB RAM 256GB SSD", 16, 256),
        ("ThinkCentre M720q 8 Go de RAM, SSD 512 Go", 8, 512),
        ("EliteDesk 800 G3 mini 8/256", 8, 256),
        ("NUC 32GB DDR4 1TB NVMe", 32, 1000),
        ("Mini PC 16 GB Arbeitsspeicher, 500 GB Festplatte", 16, 500),
        ("Optiplex 8GB, upgradable 8 to 16GB", 8, None),
        ("i5 sold 12/2023 receipt", None, None),
        ("komputer 16 GB pamięć 1 TB dysk", 16, 1000),
        ("Asus CN62 Intel celeron 64Gb/6Gb", 6, 64),
        ("OptiPlex 3046 Micro – Intel i3 / 4 Go RAM / SSD 128 Go / Wi-Fi", 4, 128),
        ("M720q 512 Go SSD + 8 Go", 8, 512),
        ("Tour PC HP - 8go de RAM - 128go de SSD - 500go de HDD", 8, 500),
        ("Dell OptiPlex SFF i3 4th ssd 128Gb ddr3 16Go Win 11 pro", 16, 128),
    ],
)
def test_ram_and_storage(text, ram, storage):
    specs = parse_specs(text)
    assert specs.ram_gb == ram
    assert specs.storage_gb == storage


@pytest.mark.parametrize(
    "title, form",
    [
        ("Dell OptiPlex 3070 Micro", "tiny"),
        ("Lenovo M920q", "tiny"),
        ("HP ProDesk 600 G3 DM", "tiny"),
        ("HP ProDesk 400 G4 Mini Tower", "tower"),
        ("Dell OptiPlex 7050 SFF", "sff"),
        ("Dell OptiPlex 7010 MT", "tower"),
    ],
)
def test_form_factor(title, form):
    assert parse_specs(title, title=title).form_factor == form


def test_flags():
    assert parse_specs("OptiPlex Micro defekt").defect
    assert parse_specs("ThinkCentre ohne RAM und ohne SSD").barebone
    assert parse_specs("Mini PC sans alimentation").no_psu
    assert parse_specs("Lot de 3 Optiplex").lot
    assert not parse_specs("Optiplex 2x 8GB RAM").lot


def test_comma_decimal_size_does_not_crash():
    parse_specs("Mini PC i5-8500T 8500,8 GB")  # used to raise ValueError
