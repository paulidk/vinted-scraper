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
        # a RAM keyword wins over later "upgrade" pairs
        ("Optiplex i5 8GB RAM upgradable 16GB/64GB", 8, None),
        ("Mini PC 8GB RAM, max 8GB/32GB", 8, None),
        # same value for RAM and storage
        ("Wyse 32GB RAM 32GB eMMC", 32, 32),
        # keyword before size, or mixed orders
        ("RAM 16GB SSD 256GB", 16, 256),
        ("SSD 512GB, 16GB RAM", 16, 512),
        ("16GB RAM, 8GB/256GB", 16, 256),
        ("Mini 8/256GB SSD", 8, 256),
        ("ThinkCentre 8GB RAM 256GB", 8, 256),
        ("NUC 8GB (2x4GB) DDR4", 8, None),
        ("Optiplex 2x8GB DDR4 256GB SSD", 16, 256),
        ("i3 / 4 Go RAM", 4, None),
        ("EliteDesk 8/1", 8, 1000),
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


@pytest.mark.parametrize("title, cpu_only", [
    ("AMD Ryzen 3 PRO 5350GE (4c/8t - 35w - am4)", True),
    ("Intel Core i3-10100T 3.00GHz LGA 1200 - Basso consumo 35W (SRH3Q)", True),
    ("Processore Intel Core i5-14500T (LGA 1700)", True),
    ("CPU intel i5-6500T", True),
    ("Intel Core i5 7500", True),
    ("Kit carte mere + i5 6500", True),
    ("NUC7i5BNK i5-7260U", False),
    ("Lenovo M720q i5-8500T", False),
    ("i5-8500T 16GB 256GB SSD", False),
    ("Mini pc idéal retrogaming i7 11 ème gen", False),
    ("Dell OptiPlex 7040 + Lecteur DVD", False),
    ("PC Intel i5-6500 Windows 11", False),
    ("Supermicro scheda madre RAM dual xeon", True),
    ("Processeur serveur Xeon E5-2673-V4 - 20 cores 40 threads - pour socket LGA2011-3", True),
    ("2x Intel Xeon Gold 6138 - 20 Core / 40 Threads", True),
    ("Dell RAID Controller (PERC) H710", True),
    ("Dell PowerEdge R520 – 2×Xeon – ohne RAM/HDD", False),
    ("Dell OptiPlex 7050 i5 mit Netzteil", False),
    ("Intel Xeon 16 GB RAM", False),
    ("i5-8500T 8GB", False),
    ("CPU Dell AMD Epyc 7402 serveur AMD 24 Cœur 48thread SP3", True),
    ("Intel Xeon E5-2660 + 64Go RAM", True),
    ("Kit Serveur X99 Intel Xeon 18 cores 36 threads x 3.0ghz - 64Go RAM", True),
    ("Carte mere serveur Supermicro X9SBAA-F", True),
    ("Kit Serveur/Workstation X99 Intel Xeon 12 cores 24 threads - 64Go RAM", True),
    ("Gaming PC X99 Xeon E5-2680 v4 32GB SSD", False),
    ("Bundle Xeon X5690 + Gigabyte X58A UD7 + 48 GB ddr3", True),
    ("19\" Server mit Gigabyte MJ11 Mainboard & Amd Epyc / 40GB Ram", False),
    ("Bayern Kit 2008/2009", False),
    ("AMD Ryzen 7 5800X + MSI B550-A Pro + 32GB Corsair DDR4 RAM", True),
    ("i5 6500  Fujitsu H110", True),
    ("Dell Raid H330", True),
    ("ASRock Jupiter X600 Barebone – sehr wenig benutzt", False),
    ("MSi Cubi N (MS-B120) 8GB, 128GB SSD, Win11", False),
    ("Carte mère Dell Precision 3551 Intel i7-10875H Nvidia P620", True),
    ("AMD EPYC 7402 - 24 Cores - Socket SP3 - DELL LOCKED", True),
    ("Dell PowerEdge R720 mit H710 RAID controller", False),
    ("Dell i7 Sockel 1151 16GB 512GB SSD", False),
    ("Server Lenovo SR550", False),
    ("AMD Epyc 7302p 16 Cores 32 Threads PCIe 4.0 DELL vendor lock", True),
    ("Gaming PC Ryzen 9 16 Cores 32 Threads 64GB RTX 3080", False),
    ("Supermicro AS-E301-9D-8CN4 | EPYC 8C/16T | 16GB Ram ECC", False),
    ("Intel Core i3-10100T 3.00GHz LGA 1200 (SRH3Q)", True),
    ("Kit GAMER Turbo Unlock - 64Go RAM - cpu Intel Xeon 10 cores 20 threads", True),
    ("Bundle server HP ProLiant DL380p Gen8 - doppia cpu Intel Xeon + 128gb ram DDR3 ECC", False),
    ("Dell PowerEdge R320 Server Xeon E5-2470 v2 10-Core 96GB DDR3 ECC RAM", False),
])
def test_loose_cpu(title, cpu_only):
    assert parse_specs(title, title=title).cpu_only is cpu_only
    assert parse_specs("Lot de 3 Optiplex").lot
    assert not parse_specs("Optiplex 2x 8GB RAM").lot


def test_comma_decimal_size_does_not_crash():
    parse_specs("Mini PC i5-8500T 8500,8 GB")  # used to raise ValueError


@pytest.mark.parametrize("text, model, cpus", [
    ("Dell PowerEdge R320 Server Xeon E5-2470 v2 10-Core 96GB DDR3 ECC", "e5-2470 v2", 1),
    ("Dell R620 2x E5-2650 v2 64GB", "e5-2650 v2", 2),
    ("2x Xeon E-5 2630 v3", "e5-2630 v3", 2),
    ("HP ML30 Gen10 Xeon E-2224 16GB", "e-2224", 1),
    ("Dell Precision 5820 W-2135 32GB", "w-2135", 1),
    ("Supermicro EPYC 7302P 128GB", "epyc 7302", 1),
    ("Dual Xeon Gold 6148 workstation", "gold 6148", 2),
    ("HP ProLiant DL380p Gen8 - doppia cpu Intel Xeon E5-2670 v2 + 128gb ram", "e5-2670 v2", 2),
    ("PC Gaming Desktop / Studio - Xeon 10 Core, RX 580", "xeon 10c", 1),
    ("Gaming PC Ryzen 5 5600X RTX 3060", "ryzen 5 5600x", 1),
    ("2x8GB RAM i5-6500", "i5-6500", 1),  # 2x8GB is RAM, not two CPUs
])
def test_server_and_desktop_cpus(text, model, cpus):
    specs = parse_specs(text, title=text)
    assert specs.cpu.model == model and specs.cpus == cpus


def test_server_form_factor():
    assert parse_specs("Dell PowerEdge T310 Tower Server", title="Dell PowerEdge T310 Tower Server").form_factor == "server"
    assert parse_specs("HP 1U rack server").form_factor == "server"
    assert parse_specs("Mini PC Server N100").form_factor == "tiny"  # a home server, not a rack box


@pytest.mark.parametrize("text, ram, storage", [
    ("Ryzen 5 5600G 16GB", 16, None),
    ("Intel Xeon E-2176G Server Homelab", None, None),
    ("Mini PC i5 16g ram 512g ssd", 16, 512),
])
def test_model_suffix_g_is_not_a_size(text, ram, storage):
    specs = parse_specs(text)
    assert (specs.ram_gb, specs.storage_gb) == (ram, storage)


@pytest.mark.parametrize("title, gpu, ram, storage", [
    ("Gaming PC GTX 1060 6GB i5-8400 16GB 512GB SSD", "gtx 1060 6gb", 16, 512),
    ("Gaming PC mit GTX 980 4GB, Ryzen 5 3400G, 16GB RAM", "gtx 980", 16, None),
    ("PC Gamer i7 6700 16 Go DDR4 stockage 500 Go GTX 970", "gtx 970", 16, 500),
    ("Gaming PC Ryzen 5 5600 RTX 3060 Ti 16GB 1TB", "rtx 3060 ti", 16, 1000),
    ("HP Z240 Workstation Xeon 32 GB RAM Quadro P2000 2 TB HDD", "quadro p2000", 32, 2000),
    ("PC i5 RX 580 8GB 1TB", "rx 580", None, 1000),
    ("PC i7 RTX 2070 Super 8GB GDDR6 32GB RAM", "rtx 2070 super", 32, None),
    ("rx6600xt gaming pc i5-12400", "rx 6600 xt", None, None),
    ("Fujitsu Primergy RX 100 S3", None, None, None),  # a server, not a Radeon
    ("Mini PC Ryzen 5 5600G Vega 7", None, None, None),  # integrated graphics
])
def test_graphics_card_and_vram(title, gpu, ram, storage):
    specs = parse_specs(title, title=title)
    assert (specs.gpu.model if specs.gpu else None, specs.ram_gb, specs.storage_gb) == (gpu, ram, storage)


def test_title_card_wins_over_description():
    specs = parse_specs("Gaming PC RTX 3060\n\nAlte GTX 1080 Ti war vorher drin", title="Gaming PC RTX 3060")
    assert specs.gpu.model == "rtx 3060"


@pytest.mark.parametrize("title, part", [
    ("MSI GTX 1060 6GB", True),
    ("Grafikkarte GTX 1070 für Gaming PC", True),
    ("Gaming PC GTX 1070 i7-8700 16GB", False),
])
def test_graphics_card_alone_is_a_part(title, part):
    assert parse_specs(title, title=title).cpu_only is part


@pytest.mark.parametrize("text, offsite", [
    ("No payment by vinted !Gaming PC RTX 4080 SUPER Ryzen 5 7600X", True),
    ("Gaming PC, Zahlung gerne per PayPal", True),
    ("Kontakt über WhatsApp", True),
    ("Gaming PC, Versand über Vinted", False),
])
def test_offsite_payment(text, offsite):
    assert parse_specs(text).offsite is offsite


@pytest.mark.parametrize("title, lot", [
    ("Tiny PCs / Dell Lenovo HP Fujitsu / Intel i3 i5 i7 Ryzen 3 5 Pro", True),
    ("Mini PCs ab 79 € - große Auswahl", True),
    ("Lenovo ThinkCentre M90q i5-10500T", False),
    ("Dell OptiPlex 7060 Micro, mit diversem Zubehör", False),
])
def test_multi_offer_counts_as_lot(title, lot):
    assert parse_specs(f"{title}\n\nVerschiedene Kabel dabei", title=title).lot is lot


@pytest.mark.parametrize("text, defect, barebone, no_psu", [
    ("Optisch Gebrauchsspuren.\nEs fehlt: Ram, Netzteil, SSD\n\nKein Bios Passwort", False, True, True),
    ("ThinkCentre M720q, BIOS Passwort gesetzt", True, False, False),
    ("Mini PC, no BIOS password, ohne Netzteil", False, False, True),
    ("Mini PC i5, RAM und SSD fehlen", False, True, False),
    ("Mini PC i5 16GB, alles dabei, nichts fehlt", False, False, False),
])
def test_missing_parts_and_negated_bios_lock(text, defect, barebone, no_psu):
    specs = parse_specs(text)
    assert (specs.defect, specs.barebone, specs.no_psu) == (defect, barebone, no_psu)


@pytest.mark.parametrize("title, form_factor", [
    ("Dell Precision 3550 ( i5 10 Generation )", "laptop"),
    ("Dell Precision 7550 Workstation i7 32GB", "laptop"),
    ("HP ZBook 15 G5", "laptop"),
    ("Lenovo ThinkPad T480", "laptop"),
    ("Dell Precision 3650 Tower i7", "tower"),
    ("Dell Precision T5810 Xeon", None),
])
def test_laptops(title, form_factor):
    assert parse_specs(title, title=title).form_factor == form_factor


def test_cpu_suffix_written_apart_and_cpu_model_not_a_size_pair():
    assert parse_specs("Mini PC Intel core i7 1165 g7 16GB").cpu.model == "i7-1165g7"
    specs = parse_specs("HP Z820 Workstation | 2x Xeon E5-2680 | 128GB ecc | gtx 1080 8GB")
    assert (specs.ram_gb, specs.storage_gb, specs.cpus) == (128, None, 2)


def test_first_card_in_title_wins():
    title = "Gaming PC Intel Core i5-12400F, Vega 56 (wie GTX 1070), 16GB DDR5"
    assert parse_specs(title, title=title).gpu.model == "rx vega 56"


@pytest.mark.parametrize("text, ram, storage", [
    ("Lenovo ThinkStation P310 | Intel Core i7-6700 | 16 GB | SSD", 16, None),
    ("HP Z4 G4 | Xeon W-2223 | 64GB ECC | 512GB | Quadro T1000", 64, 512),
    ("Dell OptiPlex 5040 Intel i3-6100 / 128 GB SSD", None, 128),
    ("Mini PC Atom 4GB RAM 16GB eMMC", 4, 16),
])
def test_model_numbers_and_small_disks(text, ram, storage):
    specs = parse_specs(text)
    assert (specs.ram_gb, specs.storage_gb) == (ram, storage)
