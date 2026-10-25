import pytest

from vinted_scraper.models import Listing
from vinted_scraper.scoring import build_scorer
from vinted_scraper.scoring.nas import parse_nas


# Real titles from Vinted and Kleinanzeigen (2026-10-25): bays, x86, year, included TB.
@pytest.mark.parametrize("title, bays, x86, year, tb", [
    ("Synology DS918+ 4 baies + 4×4 To WD Red – 16 To", 4, True, 2018, 16),
    ("NAS Synology DS218 - 2 Bay - mit 2 HDDs je 2 TB von WD exklusiv", 2, False, 2018, 4),
    ("NAS Synology DS216se + 2 HDD 2to", 2, False, 2016, 4),
    ("NAS Synology DS218+ Boosté à 10 Go RAM - Excellent état", 2, True, 2018, 0),
    ("Synology DS 720+", 2, True, 2020, 0),
    ("NAS Synology DS1019+ 5 baies 500 €", 5, True, 2019, 0),
    ("Synology Ds 218 play", 2, False, 2018, 0),
    ("Synology DiskStation DS220+ NAS Server ohne HDD", 2, True, 2020, 0),
    ("Synology DS224+ mit 28TB NAS System 2-Bay", 2, True, 2024, 28),
    ("Synology RackStation RS820RP+ 4-Bay", 4, True, 2020, 0),
    ("NAS Synology RackStation RS822rp+ 32GB Memory und 4 x 2TB HD", 4, True, 2022, 8),
    ("QNAP TS-451D2 4GB NAS Intel 4-Bay HDMI inkl 4x 2TB WD Red HDDs", 4, True, 2015, 8),
    ("QNAP TS-231K NAS mit 2x 2TB WD Red Festplatten", 2, False, 2016, 4),
    ("QNAP TS-473 NAS 4 baie AMD Quad-Core, 2x M.2, slot PCIe 10GbE", 4, True, 2019, 0),
    ("24TB QNAP TS-251D 2x12TB HDD 2-Bay NAS Intel Celeron J4025 2.0GHz", 2, True, 2015, 24),
    ("QNAP TS-453D NAS 4-Bay 16 TB Toshiba N300 8 GB RAM 2,5 GbE", 4, True, 2017, 16),
    ("Asustor AS3202T NAS + 2x 2 TB WD Red, Quad-Core, Fernbedienung", 2, True, None, 4),
    ("UGREEN NASync DXP2800 NAS mit 2x 6TB (8GB RAM)", 2, True, 2024, 12),
    ("WD My Cloud EX2 Ultra NAS 8 TB (2 × 4 TB WD Red) guter Zustand", 2, False, None, 8),
    ("ZyXEL NSA325 v2 2-Bay NAS-Gehäuse ohne Festplatten", 2, None, None, 0),
    ("Synology DS215j NAS 2-Bay – getestet mit 2x 8TB, m. Netzteil", 2, False, 2015, 0),
])
def test_parse_nas(title, bays, x86, year, tb):
    nas = parse_nas(title)
    assert (nas.bays, nas.x86, nas.year, nas.tb) == (bays, x86, year, pytest.approx(tb))


@pytest.mark.parametrize("title, reject", [
    ("Synology DS918+ 4 baies", None),
    ("Synology RackStation RS819 NAS, 1HE, neue Lüfter, OVP", None),  # a model with bays isn't an accessory
    ("Synology DS214 NAS-Server", "older than 2015"),
    ("Synology DiskStation DS120j – NAS 1 Bay", "1-bay"),
    ("Synology DiskStation DS411j NAS - Defekt für Bastler", "defect"),
    ("Router Synology RT2600ac", "accessory"),
    ("Lot de 2 ventilateurs NAS Synology", "accessory"),
    ("QNAP TR-004 Erweiterungsgehäuse 4-Bay", "accessory"),
    ("Lot de 2 RAM Synology D4ER01-16G 16 Go DDR4 ECC 2666 MHz", "bays unknown"),
    ("Synology DS218j", "not x86"),
    ("8GB DDR4 SODIMM RAM für Synology DS425+ DS225+ u andere", "accessory"),
    ("TerraMaster D5-300 DAS Gehäuse - ohne Einschübe", "case or DAS"),
    ("4-Bay NAS Gehäuse schwarz", "case or DAS"),
    ("Mini computer Shuttle DS437 - Desktop / Server / NAS / Router", "accessory"),  # not a Synology
    ("Asustor NAS 3GB AS-602T 2Bay Intel Dual core 2.13GHz", "older than 2015"),  # not 602 TB of drives
])
def test_nas_scorer_rejects(title, reject):
    scorer = build_scorer({"type": "nas", "min_year": 2015, "require_x86": "j" in title.lower()})
    score = scorer.score(Listing(id=1, title=title, price=100, currency="EUR", url="https://www.vinted.de/items/1"))
    assert score.reject == reject


def test_require_model_skips_unknown_boxes():
    scorer = build_scorer({"type": "nas", "require_model": True})
    for title, reject in [("Icybox 4 bay", "model unknown"), ("Netgear ReadyNAS 314 • 4-Bay NAS", "model unknown"),
                          ("QNAP TS-453A, ohne HD", None)]:
        assert scorer.score(Listing(id=1, title=title, price=75, currency="EUR", url="https://www.vinted.de/items/1")).reject == reject


def test_nas_points_and_missing_psu():
    scorer = build_scorer({"type": "nas"})
    listing = Listing(id=1, title="Synology DS918+ 4 baies + 4×4 To WD Red", price=500, currency="EUR",
                      url="https://www.vinted.de/items/1", description="Vendu sans alimentation")
    score = scorer.score(listing)
    # 4 bays × 3 + x86 4 + (2018 - 2015) × 0.5 + 16 TB × 1
    assert score.points == 4 * 3 + 4 + 1.5 + 16
    assert score.cost == 520  # + 20 for a power supply
    unknown = scorer.score(Listing(id=2, title="Serveur NAS", price=50, currency="EUR", url="https://www.vinted.de/items/2"))
    assert unknown.reject == "bays unknown" and unknown.needs_details
