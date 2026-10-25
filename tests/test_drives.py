import pytest

from vinted_scraper.models import Listing
from vinted_scraper.scoring import build_scorer
from vinted_scraper.scoring.drives import parse_drives


# Real titles from Vinted and Kleinanzeigen (2026-10-25): total TB, drives, kind, form.
@pytest.mark.parametrize("title, tb, count, kind, form", [
    ("Seagate Barracuda 3000GB HDD 3TB Festplatte", 3, 1, "hdd", None),
    ("Western Digital Red Festplatte mit 6 TB", 6, 1, "hdd", None),
    ("Seagate 1,5TB Barracuda Festplatte", 1.5, 1, "hdd", None),
    ("Intenso, kleine 4tb externe Festplatte NEU 4.000 Gb.", 4, 1, "hdd", None),
    ("Hd 6.0 tb western digital wd red", 6, 1, "hdd", None),
    ("Seagate 4To IronWolf Disque Dur Interne Nas | Sata 6 GB/s (ST4000VNZ06)", 4, 1, "hdd", None),
    ("2x 8TB WD Red NAS-Festplatten (WD80EFAX) Set – 100 % fehlerfrei", 16, 2, "hdd", None),
    ("3x 4TB (12TB) WD Red NASware Festplatten/ HDD, Neuwertig, 0h", 12, 3, "hdd", None),
    ("4 x Disque dur WD Red - Western Digital 2 To - 2To - 2TB - 2 TB", 8, 4, "hdd", None),
    ("4 Stück 2TB WD-RED HDD 3.5\"  NAS Interne Festplatten SATA 6GB/s", 8, 4, "hdd", "3.5"),
    ("Coppia dischi WD RED 4TB", 8, 2, "hdd", None),
    ("2 disques WD 3TB soit 6 tb", 6, 2, "hdd", None),
    ("2× Seagate Enterprise HDD 4 TB | NEU & OVP | 8 TB | NAS", 8, 2, "hdd", None),
    ("Seagate BarrCuda 8TB - HDD (4 verfügbar)", 8, 1, "hdd", None),  # the price is per drive
    ("Dell Festplatte Enterprise 1,8TB  HDD 12x Vorhanden", 1.8, 1, "hdd", None),
    ("2x Synology HAT3300-4T 4TB HDD Festplatten", 8, 2, "hdd", None),
    ("Maxtor M3 Portable 4TB Externe Festplatte (2.5 Zoll, USB 3.0)", 4, 1, "hdd", "2.5"),
    ("Seagate Expansion Desktop Drive 3TB Externe Festplatte (USB 3.0, Modell SRD00F2)", 3, 1, "hdd", "3.5"),
    ("Samsung 860 QVO 4TB SSD SATA Festplatte", 4, 1, "ssd", None),
    ("Inateck & Wintech externe Festplatten, USB 3.0, 150 / 252 / 460 / 465 GB", 0.15, 1, "hdd", None),
    ("3 disque dur externe 3.5 total 6tb", 6, 3, "hdd", "3.5"),
    ("6× Seagate Enterprise Capacity V5 – 2TB SAS HDD – 12TB Gesamt", 12, 6, "hdd", None),
    ("5x WD120EDAZ 12to/tb nas hdd 3.5 sata", 60, 5, "hdd", "3.5"),
])
def test_parse_drives(title, tb, count, kind, form):
    drives = parse_drives(title)
    assert (drives.tb, drives.count, drives.kind, drives.form) == (pytest.approx(tb), count, kind, form)


@pytest.mark.parametrize("title, hours", [
    ("Hard disk 4tb red 6800h", 6800),
    ("WD RED 6TB Festplatte  40715 Std.", 40715),
    ("6TB Western Digital WD Red NAS-HDD WD60EFAX nur 2045 Betriebs-Std", 2045),
    ("WD red plus 8tb nas hard drive WD80EFZZ cmr, 0 hours", 0),
    ("Seagate IronWolf 12TB NAS HDD 7200 RPM", None),
])
def test_power_on_hours(title, hours):
    assert parse_drives(title).hours == hours


@pytest.mark.parametrize("title, reject", [
    ("Seagate Ironwolf 4TB NAS Festplatte ST4000VN008", None),
    ("WD Elements 4 TB externe Festplatte, getestet (SMART: Gut)", None),  # shuckable, and SMART is fine
    ("Seagate BarraCuda 8TB HDD - Festplatte SMART=auffällig", "defect"),
    ("4Tb HDD sas hp", "sas"),
    ("Samsung 860 QVO 4TB SSD SATA Festplatte", "ssd"),
    ("Western Digital WD My Passport 4 TB externe Festplatte 2,5 Zoll", '2.5"'),
    ("Seagate Barracuda 3000GB HDD 3TB Festplatte", "below 4TB per drive"),
    ("QNAP TS-451D2 4GB NAS Intel 4-Bay HDMI inkl 4x 2TB WD Red HDDs", "device"),
    ("Synology DS418 4-Bay NAS mit 8 TB WD Red Plus Festplatten", "device"),
    ("WD RED 6TB Festplatte  40715 Std.", "over 35000h"),
    ("externe Festplatte", "size unknown"),
    ("DMA Kingdom 75T + Makcu", "not a drive"),
    ("WD My Book Duo 28TB Leergehäuse USB-C RAID – neuwertig", "empty case"),
    ("WD My Book Gehäuse HDD Netzteil USB 3.0 3,5 8TB", "empty case"),
    ("Cartouche bande quantum ultrium lto-7 6/15to sauvegarde data cartridge", "tape"),
    ("4x 3,5 Zoll Festplatten HDD WD Seagate 2TB 3TB 12TB 20TB", "mixed sizes"),
    ("Nas 419p2 avec 4 disque ( total 5tb )", "device"),
])
def test_drive_scorer_rejects(title, reject):
    scorer = build_scorer({"type": "drive", "form": "3.5", "min_tb": 4, "max_hours": 35000})
    score = scorer.score(Listing(id=1, title=title, price=80, currency="EUR", url="https://www.vinted.de/items/1"))
    assert score.reject == reject


def test_bare_t_is_not_terabytes():
    assert parse_drives("HDD 75T").tb is None and parse_drives("Hard disk 2tb").tb == 2


def test_power_cost_favours_big_drives():
    scorer = build_scorer({"type": "drive", "kwh_price": 0.32, "years": 2})
    one = scorer.score(Listing(id=1, title="WD Red 8TB", price=150, currency="EUR", url="https://www.vinted.de/items/1"))
    two = scorer.score(Listing(id=2, title="2x WD Red 4TB", price=150, currency="EUR", url="https://www.vinted.de/items/2"))
    assert one.points == two.points == 8
    assert one.value > two.value  # the second drive draws another 5 W for two years (~28 €)
    assert one.cost == pytest.approx(150 + 5 / 1000 * 24 * 365 * 2 * 0.32, abs=0.01)
