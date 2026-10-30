"""Hard drives: points = TB, so the value is "TB per 100 €".

Every drive idles at about the same few watts whatever its size, so the cost includes the
electricity of each drive over `years`. That makes one 8 TB drive beat two 4 TB ones at the
same price, and keeps a 1 TB drive for 10 € from looking like a bargain.

Reads sets ("4 Stück 2TB", "3x 4TB (12TB)", "Coppia dischi 4TB"), per-drive prices
("(4 verfügbar)"), power-on hours, SSD vs. HDD, 3.5" vs. 2.5", external, SAS.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from ..models import Listing
from .base import Score
from .specs import _VARIANTS, DEFECT, OFFSITE, _base_variant, payment_reject

_NUM = r"(\d{1,2}\.\d{3}|\d{1,4}(?:[.,]\d{1,2})?)"
# GB below 100 is never a hard drive ("6 GB/s", "8GB RAM")
_UNIT = r"(tb|to|gb|go)\b(?!\s*/\s*s)"  # no bare "t": "DMA Kingdom 75T"
_SIZE = re.compile(rf"(?<![\w.,x×]){_NUM}\s*{_UNIT}")
_KIT = re.compile(rf"\b(\d{{1,2}})\s*[x×]\s*{_NUM}\s*{_UNIT}|(?<![\w.,]){_NUM}\s*{_UNIT}\s*[x×]\s*(\d{{1,2}})\b")
# "4x WD My Book", "4 Stück", "2 disques", "Coppia", "2er-Set"
_TOTAL = re.compile(rf"\b(?:total|totale|gesamt|insgesamt|soit|totaal|zusammen)\s*:?\s*{_NUM}\s*{_UNIT}"
                    rf"|{_NUM}\s*{_UNIT}\s*\(?(?:total|totale|gesamt|insgesamt|totaal)\b")
_COUNT = re.compile(r"(?<![\w.,])(\d{1,2})\s*[x×]\s+(?!\d)|\blot\s*(?:de|of|di)?\s*(\d{1,2})\b"
                    r"|\b(\d{1,2})\s*(?:x\s*)?(?:stück|stk|disques?|dischi|hdds?|festplatten|drives|schijven|platten)\b"
                    r"|\b(\d)er[\s-]*set\b|\b(\d)[\s-]*set\b")
_PAIR = re.compile(r"\b(paar|pair|paire|coppia|zestaw)\b")
# The price is for one drive of several: "(4 verfügbar)", "12x Vorhanden"
PER_UNIT = re.compile(r"\b(\d{1,2}\s*x?\s*)?(verfügbar|vorhanden|available|disponibles?|disponibili|auf\s*lager)\b"
                      r"|\b(preis|price|prix|prezzo)\s*(pro|per|par|je|à)\s*(stück|stk|st|piece|pièce|pezzo|unit\w*)\b|\bje\s*\d+\s*€")
_HOURS = re.compile(r"(?<![\w.,])(\d{1,3}(?:\.\d{3})?|\d+)\s*(?:h|std\.?|stunden|betriebs-?std\.?|betriebsstunden|hours|heures|ore|uur)(?!\w)")
SSD = re.compile(r"\b(ssd|nvme|m\.2|sn\d{3}\w*|qvo|evo|t7|t9|mx500|bx500)\b")
_TWO_FIVE = re.compile(r"\b2[,.]5\s*(?:\"|“|”|''|zoll|pouces?|pollici|inch|in\b)|\b2[,.]5\b(?!\s*(?:gbe|gbit|gb|g\b|tb|to))"
                       r"|\b(portable|mobile|my\s*passport|passport|slim|laptop|notebook)\b")
_THREE_FIVE = re.compile(r"\b3[,.]5\s*(?:\"|“|”|''|zoll|pouces?|pollici|inch|in\b)?|\b(desktop\s*drive|my\s*book|elements\s*desktop|"
                         r"expansion\s*desktop|backup\s*plus\s*hub)\b")
EXTERNAL = re.compile(r"\b(extern\w*|usb|my\s*book|my\s*passport|elements|expansion|backup\s*plus|portable|lacie|"
                      r"game\s*drive|ps[45]|xbox)\b")
SAS = re.compile(r"\bsas\b")
# A whole NAS or PC with drives in it belongs to another search.
DEVICE = re.compile(r"^\W*nas\b(?!\W*(?:festplatte|hdd|hard|disk|drive|disque))|\b(ds|rs)\s?\d{3,4}|\b(ts|tvs|hs)\s?-?\s?h?\d{3,4}\b|\bdiskstation|\brackstation|\bas\s?\d{4}t?\b|"
                    r"\bdxp\s?\d{4}|\bmy\s*cloud|\b\d\s*-?\s*(?:bay|bays|baies)\b|\bnas\s*(?:server|system|gehäuse)\b|"
                    r"\b(?:i[3579][\s-]?\d{3,5}\w*|ryzen|celeron|pentium|xeon)\b")
DRIVE_DEFECT = re.compile(r"\bsmart\s*[=:]?\s*(?:auffällig|bad|fail\w*|warn\w*|kritisch|schlecht)|defekte\s*sektoren|"
                          r"bad\s*sectors?|secteurs?\s*défectueux|reallocated|pending\s*sectors?|klick\w*|clicking|"
                          r"klacker\w*|fehlerhaft")
# Without one of these it isn't a drive listing at all.
DRIVE_WORD = re.compile(r"\b(hdd|hd|festplatte\w*|platte|disque|disques|disk\w*|dischi|disco|drive|drives|harde|hard|schijf|"
                        r"dysk|wd|western|seagate|toshiba|hgst|hitachi|samsung|maxtor|intenso|lacie|ironwolf|exos|barracuda|"
                        r"ultrastar|purple|red|n300|mg0\d\w*|my\s*book|elements|expansion|nas|sata|ssd|nvme)\b")
# An empty case, or something that only looks like a size: "Leergehäuse", "LTO-7 6/15to"
EMPTY = re.compile(r"\b(leergehäuse|leer|empty|vide|vuoto|diskless|ohne\s*(?:festplatten?|hdds?|platten?|inhalt)|"
                   r"sans\s*disques?|senza\s*(?:dischi|disco)|\w*gehäuse|enclosure|bo[iî]tier|caddy|docking\w*|dock)\b")
TAPE = re.compile(r"\b(lto-?\d?|tape|bande|band|cartouche|cartridge|kassette|streamer)\b")


def _tb(number: str, unit: str) -> float:
    value = float(number.replace(".", "") if re.fullmatch(r"\d{1,2}\.\d{3}", number) else number.replace(",", "."))
    return value if unit in ("tb", "to") else value / 1000


@dataclass
class Drives:
    tb: float | None = None  # total
    count: int = 1
    kind: str = "hdd"  # "hdd" or "ssd"
    form: str | None = None  # "3.5", "2.5" or None if the listing doesn't say
    external: bool = False
    sas: bool = False
    hours: int | None = None
    per_unit: bool = False  # the price is for one of several drives on offer
    device: bool = False
    defect: bool = False
    offsite: bool = False
    empty: bool = False  # a case without drives
    mixed: bool = False  # several sizes in one listing, no telling which price is which
    tape: bool = False
    drive_word: bool = True

    @property
    def tb_each(self) -> float | None:
        return self.tb / self.count if self.tb else None

    def summary(self) -> str:
        size = "?TB" if self.tb is None else f"{self.tb:g}TB"
        if self.count > 1 and self.tb:
            size += f" ({self.count}x{self.tb_each:g}TB)"
        parts = [size, self.kind.upper()]
        if self.form:
            parts.append(f'{self.form}"')
        if self.external:
            parts.append("external")
        if self.sas:
            parts.append("SAS")
        if self.hours is not None:
            parts.append(f"{self.hours}h")
        if self.per_unit:
            parts.append("price per drive")
        parts += [flag for flag in ("device", "defect", "offsite", "empty", "tape", "mixed") if getattr(self, flag)]
        return " ".join(parts)


def parse_size(title: str) -> tuple[float | None, int, bool]:
    """Total TB, number of drives and whether it's a mix of sizes, from the (lowercased) title."""
    kits = []
    for m in _KIT.finditer(title):
        n, number, unit = (m.group(1), m.group(2), m.group(3)) if m.group(1) else (m.group(6), m.group(4), m.group(5))
        tb = _tb(number, unit)
        if 1 <= int(n) <= 24 and tb >= 0.1:
            kits.append((round(int(n) * tb, 3), int(n)))
    if kits:
        return (*max(kits), False)
    sizes = sorted({tb for m in _SIZE.finditer(title) if (tb := _tb(m.group(1), m.group(2))) >= 0.1})
    if not sizes:
        return None, 1, False
    count = _COUNT.search(title)
    n = int(next(g for g in count.groups() if g)) if count else (2 if _PAIR.search(title) else 1)
    n = n if 1 <= n <= 24 else 1
    if total := _TOTAL.search(title):
        number, unit = (total.group(1), total.group(2)) if total.group(1) else (total.group(3), total.group(4))
        return _tb(number, unit), n, False  # "3 disque dur externe 3.5 total 6tb", "6× 2TB SAS – 12TB Gesamt"
    if n > 1 and len(sizes) > 1 and any(abs(sizes[-1] - n * s) < 0.01 for s in sizes[:-1]):
        return sizes[-1], n, False  # "2 disques WD 3TB soit 6 tb", "3x 4TB (12TB)"
    # "4x Festplatten 2TB 3TB 12TB 20TB": which one costs what?
    return round(sizes[-1] * n, 3), n, len(sizes) > 1


def parse_drives(title: str, description: str = "") -> Drives:
    t, d = _VARIANTS.sub(_base_variant, title.lower()), description.lower()
    per_unit = bool(PER_UNIT.search(t))
    tb, count, mixed = parse_size(t)
    if per_unit and tb:
        tb, count = tb / count, 1
    drives = Drives(tb=tb, count=count, per_unit=per_unit, mixed=mixed)
    drives.kind = "ssd" if SSD.search(t) else "hdd"
    for text in (t, d):
        two, three = bool(_TWO_FIVE.search(text)), bool(_THREE_FIVE.search(text))
        if two != three:
            drives.form = "2.5" if two else "3.5"
            break
    drives.external = bool(EXTERNAL.search(t))
    drives.sas = bool(SAS.search(t))
    hours = _HOURS.search(f"{t}\n{d}")
    drives.hours = int(hours.group(1).replace(".", "")) if hours else None
    drives.device = bool(DEVICE.search(t))
    drives.defect = bool(DEFECT.search(f"{t}\n{d}") or DRIVE_DEFECT.search(f"{t}\n{d}"))
    drives.offsite = bool(OFFSITE.search(f"{t}\n{d}"))
    drives.empty = bool(EMPTY.search(t))
    drives.tape = bool(TAPE.search(t))
    drives.drive_word = bool(DRIVE_WORD.search(t))
    return drives


class DriveScorer:
    def __init__(
        self,
        kind: str = "hdd",  # "hdd", "ssd" or "any"
        form: str = "any",  # "3.5", "2.5" or "any"
        min_tb: float = 0,  # per drive
        max_hours: int | None = None,
        tb_cap: float | None = None,  # TB counted up to this much per listing
        reject: list[str] | None = None,  # defect, offsite, sas, external, device
        watts: dict | None = None,  # idle watts per drive by form factor
        kwh_price: float = 0.30,
        years: float = 2.0,
        hours_per_day: float = 24.0,
        shipping_estimate: float = 0.0,
    ):
        self.kind, self.form = kind, form
        self.min_tb, self.max_hours, self.tb_cap = min_tb, max_hours, tb_cap
        self.reject = set(reject if reject is not None else ["defect", "offsite", "sas", "device"])
        self.watts = {"3.5": 5.0, "2.5": 1.5, **(watts or {})}
        self.kwh_price, self.years, self.hours_per_day = kwh_price, years, hours_per_day
        self.shipping_estimate = shipping_estimate

    def energy_cost(self, drives: Drives) -> float:
        watts = self.watts["2.5" if drives.form == "2.5" else "3.5"] * drives.count
        return watts / 1000 * self.hours_per_day * 365 * self.years * self.kwh_price

    def score(self, listing: Listing) -> Score:
        drives = parse_drives(listing.title, listing.description)
        energy = self.energy_cost(drives)
        cost = round(listing.effective_price + listing.shipping_or(self.shipping_estimate) + energy, 2)
        summary = f"{drives.summary()} | energy {energy:.0f} {listing.currency}/{self.years:g}y"

        def rejected(reason: str) -> Score:
            return Score(0, cost, summary, reject=reason)

        if "offsite" in self.reject and (reason := payment_reject(listing)):
            return rejected(reason)
        for flag in ("defect", "device", "sas", "external"):
            if flag in self.reject and getattr(drives, flag):
                return rejected(flag)
        if drives.empty or drives.tape:
            return rejected("empty case" if drives.empty else "tape")
        if drives.mixed:
            return rejected("mixed sizes")
        if not drives.drive_word:
            return rejected("not a drive")
        if drives.tb is None:
            return rejected("size unknown")
        if self.kind != "any" and drives.kind != self.kind:
            return rejected(drives.kind)
        if self.form != "any" and drives.form is not None and drives.form != self.form:
            return rejected(f'{drives.form}"')
        if drives.tb_each < self.min_tb:
            return rejected(f"below {self.min_tb:g}TB per drive")
        if self.max_hours is not None and drives.hours is not None and drives.hours > self.max_hours:
            return rejected(f"over {self.max_hours}h")
        tb = min(drives.tb, self.tb_cap) if self.tb_cap else drives.tb
        return Score(points=round(tb, 3), cost=cost, summary=summary)
