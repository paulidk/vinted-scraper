"""NAS boxes: points for bays, an x86 CPU, a newer model, RAM and the drives that come with it.

The model name says most of it. Synology "DS918+" = DiskStation, 4 bays, 2018 model, "+" = x86
(Docker, Plex, VMs); "j"/"play"/no suffix are ARM. QNAP "TS-453D" = 4 bays, x53 generation
(x86 from x39 up). Asustor "AS5304T" = 4 bays. "4-Bay", "4 baies" in the text win over the table.
Included drives are parsed like the drive search ("inkl. 4x 2TB WD Red"); "ohne HDD" means none.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from ..models import Listing
from .base import Score, offsite_matters
from .drives import parse_size
from .specs import DEFECT, NO_PSU, OFFSITE

# "DS437" alone is a Shuttle PC; Synology says its name or has a suffix (DS218+, DS216j)
_SYNOLOGY_WORD = re.compile(r"\b(synology|diskstation|rackstation|dsm)\b")
_SYNOLOGY = re.compile(r"\b(ds|rs)\s?(\d{3,4})\s?(\+|xs\+?|j|play|se|air|slim|rpxs|rp\+|rp)?")
# DiskStation series (model number without the year) -> bays; the series is the max with expansion units
_DS_BAYS = {1: 1, 2: 2, 3: 3, 4: 4, 5: 5, 6: 6, 7: 2, 9: 4, 10: 5, 15: 5, 16: 6, 18: 8, 24: 12, 30: 6, 36: 12}
_RS_BAYS = {2: 2, 4: 4, 8: 4, 12: 8, 24: 12, 36: 12}
_QNAP = re.compile(r"\b(ts|tvs|hs|tbs|tss)\s?-?\s?h?(\d{3,4})")
# QNAP generation (last two digits) -> roughly the year it came out
_QNAP_YEAR = {9: 2008, 10: 2009, 12: 2010, 19: 2010, 20: 2012, 21: 2012, 28: 2018, 30: 2019, 31: 2016, 32: 2019,
              33: 2021, 35: 2022, 36: 2022, 39: 2010, 51: 2015, 53: 2017, 59: 2011, 63: 2017, 64: 2021, 69: 2012,
              70: 2012, 71: 2014, 73: 2019, 74: 2022, 77: 2018, 82: 2019}
_ASUSTOR = re.compile(r"\bas\s?-?(\d)(\d)(\d{2})(?:t|te|mt|rs)?\b")
_ASUSTOR_OLD = re.compile(r"\bas\s?-?([36])0(\d)t\b")  # AS-602T, AS-608T: 2013, Intel Atom
_TERRAMASTER = re.compile(r"\bf(\d)-(\d)(\d{2})\b")
_UGREEN = re.compile(r"\bdxp\s?(\d)(\d{3})")
_MYCLOUD = re.compile(r"\bmy\s*cloud\s*(?:ex(\d)|pr(\d)\d{3}|ex(\d)\d{3}|(mirror)|(home))")
_BAYS = re.compile(r"\b(\d{1,2})\s*-?\s*(?:bay|bays|baies?|schacht|schächte|fach|fächer)\b|\b0\s*/\s*(\d{1,2})\s*(?:hdd|bay|disk)")
_NAS_WORD = re.compile(r"\b(nas|diskstation|rackstation|netzwerkspeicher|network\s*storage|serveur\s*nas)\b")
# Expansion units, adapters, routers: no NAS of their own.
ACCESSORY = re.compile(r"\b(dx\d{3,4}|rx\d{3,4}|tr-?\d{3}|erweiterung\w*|expansion|extension|router|adapter|karte|card|"
                       r"lüfter|ventilat\w*|fans?|halterung|rack\s*kit|schienen|rails?)\b")
DISKLESS = re.compile(r"\b(ohne|sans|senza|sin|zonder|without|no|keine?)\s*(?:\w+\s*){0,2}?(hdds?|festplatten?|disques?|dischi|"
                      r"discos?|schijven|drives?|disks?|platten)\b|\bdiskless\b|\bleer\b|\b0\s*/\s*\d+\s*(?:hdd|bay|disk)"
                      # "getestet mit 2x 8TB": the drives were only used for testing
                      r"|\b(getestet|tested|testé|testato|getest)\s*(mit|with|avec|con|met)\b")
# "8GB DDR4 SODIMM RAM für Synology DS425+": made for a NAS, not one
FOR_DEVICE = re.compile(r"\b(für|fuer|for|pour|per|compatible|kompatibel\w*|passend\w*)\s+(?:\w+\s+){0,2}?"
                        r"(synology|qnap|asustor|terramaster|ugreen|ds\s?\d{3}|rs\s?\d{3}|ts-?\d{3})")
# A DAS or a bare case: no computer of its own. "NAS-Gehäuse" from a NAS brand is a diskless NAS.
DAS = re.compile(r"\b(das|jbod|usb[\s-]*gehäuse|festplattengehäuse|einschübe)\b")
CASE = re.compile(r"\b(\w*gehäuse|case|bo[iî]tier|enclosure|chassis)\b")
NAS_BRAND = re.compile(r"\b(synology|qnap|asustor|terramaster|ugreen|zyxel|netgear|readynas|buffalo|linkstation|terastation|"
                       r"thecus|wd|western\s*digital|my\s*cloud|sharespace|seagate|lenovo|iomega|d-?link|drobo|asus)\b")
_RAM = re.compile(r"(?<![\w.,])(\d{1,2})\s*(?:gb|go)\s*(?:ddr\d\w*\s*)?(?:ram|arbeitsspeicher|memory|mémoire|speicher)\b"
                  r"|\b(?:ram|arbeitsspeicher)\s*(?:auf|à|to|:)?\s*(\d{1,2})\s*(?:gb|go)\b|\bboosté\s*à\s*(\d{1,2})\s*(?:gb|go)")
_X86_CPU = re.compile(r"\b(celeron|pentium|atom|intel|xeon|ryzen|amd|core\s*i\d|i[3579]-\d{4}|j\d{4}|n\d{3,4})\b")


@dataclass
class Nas:
    model: str = ""
    bays: int | None = None
    x86: bool | None = None  # None: the model isn't known
    year: int | None = None
    tb: float = 0.0  # included drives
    ram_gb: int | None = None
    accessory: bool = False
    defect: bool = False
    no_psu: bool = False
    offsite: bool = False
    nas_word: bool = False
    made_for: bool = False  # an accessory "for Synology DS425+"
    case: bool = False  # a DAS or an empty case

    def summary(self) -> str:
        parts = [self.model or "NAS?", f"{self.bays}-bay" if self.bays else "?-bay",
                 {True: "x86", False: "ARM"}.get(self.x86, "cpu?")]
        if self.year:
            parts.append(str(self.year))
        if self.ram_gb:
            parts.append(f"{self.ram_gb}GB RAM")
        parts.append(f"{self.tb:g}TB incl." if self.tb else "no drives")
        parts += [flag for flag in ("accessory", "defect", "no_psu", "offsite") if getattr(self, flag)]
        return " ".join(parts)


def _model(t: str, nas: Nas) -> None:
    if (m := _SYNOLOGY.search(t)) and (_SYNOLOGY_WORD.search(t) or m.group(3)):
        kind, num, suffix = m.group(1), m.group(2), (m.group(3) or "").replace(" ", "")
        series, year = int(num[:-2]), int(num[-2:])
        nas.model = f"Synology {kind.upper()}{num}{suffix}"
        nas.bays = (_DS_BAYS if kind == "ds" else _RS_BAYS).get(series)
        nas.year = 2000 + year if 8 <= year <= 30 else None
        nas.x86 = suffix.startswith(("+", "xs", "rp+", "rpxs"))
    elif m := _QNAP.search(t):
        num = m.group(2)
        nas.model = f"QNAP {m.group(1).upper()}-{num}"
        nas.bays, gen = int(num[:-2]), int(num[-2:])
        nas.year = _QNAP_YEAR.get(gen)
        nas.x86 = gen >= 39
    elif m := _ASUSTOR.search(t):
        series, gen, bays = int(m.group(1)), int(m.group(2)), int(m.group(3))
        nas.model = f"Asustor AS{m.group(1)}{m.group(2)}{m.group(3)}"
        nas.bays = bays or None
        nas.x86 = series >= 3 and not (series == 3 and gen == 3)  # AS33xx are ARM
    elif m := _ASUSTOR_OLD.search(t):
        nas.model = f"Asustor AS-{m.group(1)}0{m.group(2)}T"
        nas.bays, nas.x86, nas.year = int(m.group(2)), True, 2013
    elif m := _TERRAMASTER.search(t):
        nas.model = f"TerraMaster F{m.group(1)}-{m.group(2)}{m.group(3)}"
        nas.bays = int(m.group(1))
        nas.x86 = int(m.group(3)) >= 20  # F2-210 ARM, F2-221 / F4-423 x86
    elif m := _UGREEN.search(t):
        nas.model = f"Ugreen DXP{m.group(1)}{m.group(2)}"
        nas.bays, nas.x86, nas.year = int(m.group(1)), True, 2024
    elif m := _MYCLOUD.search(t):
        ex2, pr, ex, mirror, home = m.groups()
        nas.model = "WD My Cloud"
        nas.bays = int(ex2 or pr or ex) if (ex2 or pr or ex) else 2 if mirror else 1
        nas.x86 = bool(pr)


def parse_nas(title: str, description: str = "") -> Nas:
    t, d = title.lower(), description.lower()
    nas = Nas()
    _model(t, nas)
    if m := _BAYS.search(t) or _BAYS.search(d):
        nas.bays = int(m.group(1) or m.group(2))
    if nas.x86 is None and _X86_CPU.search(t):
        nas.x86 = True
    if not DISKLESS.search(t):
        tb, _, _ = parse_size(t)
        nas.tb = tb or 0.0
    if m := _RAM.search(t) or _RAM.search(d):
        nas.ram_gb = int(next(g for g in m.groups() if g))
    nas.accessory = bool(ACCESSORY.search(t))
    nas.made_for = bool(FOR_DEVICE.search(t))
    nas.case = bool(DAS.search(t) or (CASE.search(t) and not nas.model and not NAS_BRAND.search(t)))
    nas.defect = bool(DEFECT.search(f"{t}\n{d}"))
    nas.no_psu = bool(NO_PSU.search(f"{t}\n{d}"))
    nas.offsite = bool(OFFSITE.search(f"{t}\n{d}"))
    nas.nas_word = bool(_NAS_WORD.search(t))
    return nas


DEFAULT_WEIGHTS = {
    "bay": 3.0,  # points per bay ...
    "bay_cap": 8,  # ... counted up to this many
    "x86": 4.0,  # Docker, Plex, VMs
    "per_year": 0.5,  # per model year after `base_year`
    "base_year": 2015,
    "tb": 1.0,  # per TB of included drives
    "ram": 0.25,  # per GB of RAM ...
    "ram_cap": 16,
}


class NasScorer:
    def __init__(
        self,
        weights: dict | None = None,
        min_bays: int = 2,
        min_year: int | None = None,  # skip older models (only when the model is known)
        require_x86: bool = False,
        require_model: bool = False,  # only models it knows (Synology, QNAP, Asustor, TerraMaster, Ugreen, WD)
        reject: list[str] | None = None,  # defect, offsite, accessory
        no_psu_penalty: float = 20.0,
        shipping_estimate: float = 0.0,
    ):
        self.w = {**DEFAULT_WEIGHTS, **(weights or {})}
        self.min_bays, self.min_year, self.require_x86 = min_bays, min_year, require_x86
        self.require_model = require_model
        self.reject = set(reject if reject is not None else ["defect", "offsite", "accessory"])
        self.no_psu_penalty = no_psu_penalty
        self.shipping_estimate = shipping_estimate

    def score(self, listing: Listing) -> Score:
        nas = parse_nas(listing.title, listing.description)
        cost = listing.effective_price + listing.shipping_or(self.shipping_estimate) + (self.no_psu_penalty if nas.no_psu else 0)
        cost = round(cost, 2)
        summary = nas.summary()

        def rejected(reason: str, needs_details: bool = False) -> Score:
            return Score(0, cost, summary, reject=reason, needs_details=needs_details)

        for flag in ("offsite", "defect", "accessory"):
            if flag == "offsite" and not offsite_matters(listing.url):
                continue
            if flag == "accessory" and nas.made_for and flag in self.reject:
                return rejected(flag)
            if flag in self.reject and getattr(nas, flag) and not (flag == "accessory" and nas.model and nas.bays):
                return rejected(flag)
        if nas.case:
            return rejected("case or DAS")
        if self.require_model and not nas.model:
            return rejected("model unknown", needs_details=nas.nas_word)
        if nas.bays is None:
            # "Serveur NAS", "NAS Asustor": the description may say; a router or RAM stick won't.
            return rejected("bays unknown", needs_details=nas.nas_word or bool(nas.model))
        if nas.bays < self.min_bays:
            return rejected(f"{nas.bays}-bay")
        if self.min_year and nas.year and nas.year < self.min_year:
            return rejected(f"older than {self.min_year}")
        if self.require_x86 and not nas.x86:
            return rejected("not x86")
        w = self.w
        points = min(nas.bays, w["bay_cap"]) * w["bay"]
        points += w["x86"] if nas.x86 else 0
        points += max(0, (nas.year or w["base_year"]) - w["base_year"]) * w["per_year"]
        points += nas.tb * w["tb"]
        points += min(nas.ram_gb or 0, w["ram_cap"]) * w["ram"]
        return Score(points=round(points, 2), cost=cost, summary=summary)
