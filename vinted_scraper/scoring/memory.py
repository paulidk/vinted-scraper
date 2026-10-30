"""RAM sticks: points = GB, so the value is "GB per 100 €".

Reads the total size of a stick or kit ("32GB (2x16GB)", "2 × 8 GB", "Lot de 2 barrettes 8 Go"),
the DDR generation, SO-DIMM vs. desktop DIMM and the speed, and rejects what doesn't fit the
machine: the wrong generation or module type, too few GB, more sticks than free slots.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from ..models import Listing
from .base import Score
from .specs import _VARIANTS, DEFECT, OFFSITE, _base_variant, payment_reject

_UNIT = r"(?:gb|go|g)\b"
_STICK_SIZES = (1, 2, 4, 8, 12, 16, 24, 32, 48, 64, 128)
# "2x16GB", "2 × 8 GB", "4x 16GB"; "(2x16)" and "(8x2)" without a unit, next to a size elsewhere
_KIT = re.compile(rf"\b(\d)\s*[x×]\s*(\d{{1,3}})\s*{_UNIT}|\b(\d{{1,3}})\s*{_UNIT}\s*[x×]\s*(\d)\b"
                  r"|\((\d{1,2})\s*[x×]\s*(\d{1,2})\)|\b(\d)\s*[x×]\s*(\d{1,2})\b(?!\s*(?:mhz|mt|pin))")
_SIZE = re.compile(rf"(?<![\d.,x×])(\d{{1,3}})\s*{_UNIT}")
_TOTAL = re.compile(rf"(\d{{1,3}})\s*{_UNIT}\s*\(?(?:gesamt|insgesamt|total|totale|in\s*total)"
                    rf"|(?:gesamt|insgesamt|total|totale)\s*:?\s*(\d{{1,3}})\s*{_UNIT}")
# "2x Samsung 8GB", "Lot de 2 barrettes", "2 Riegel", "Paar"
_COUNT = re.compile(r"(?<![\w.,])(\d)\s*[x×]\s+(?!\d)|\blot\s*(?:de|of|di)?\s*(\d)\b"
                    r"|\b(\d)\s*(?:x\s*)?(?:barrettes?|riegel|module?s?|sticks?|stück|stk|pcs|pezzi|moduli|memorie|memoria|kości)\b")
# The title names the price of the whole set, so the listing price is for one stick
_PRICE_FOR_SET = re.compile(r"\d+\s*(?:€|eur\w*)\s*(?:für|fuer|for|pour|per)\s*(?:alle\s*|all\s*|les\s*)?\d")
_PAIR = re.compile(r"\b(paar|pair|paire|coppia|par)\b")
# "DDR4", "DDR4L", "dd4" (typo), "PC4-2666V", "PC3L-12800"
_DDR = re.compile(r"\bddr\s*(\d)l?\b|\bddr(\d)l?-|\bdd(\d)\b|\bpc(\d)l?[\s-]*\d{4,5}|\bpc([345])l?\b")
SPEEDS = (1066, 1333, 1600, 1866, 2133, 2400, 2666, 2933, 3200, 3600, 4800, 5200, 5600, 6000, 6400)
# "2666 MHz", "DDR4-3200", "PC4-2400T", or a bare standard speed ("so-dimm 2x8 GB 3200")
_MHZ = re.compile(r"\b(\d{4})\s*(?:mhz|mt/?s)|\bddr\d\w?-(\d{4})\b|\bpc\dl?[\s-]*(\d{4,5})"
                  rf"|\b({'|'.join(map(str, SPEEDS))})\b(?!\s*(?:gb|go|€|eur))")
SODIMM = re.compile(r"\b(so-?\s*dimm|sodim+|sodim|so\s*-\s*dimm|260[\s-]*(?:pin|polig|broches)|laptop\w*|notebook\w*|"
                    r"portable|portatile|portátil|portatil|macbook|nuc|mini[\s-]?pc)\b")
# A bare "DIMM" is a desktop stick, but not the "dimm" of "so-dimm" / "so dimm"
DIMM = re.compile(r"\b(u-?dimm|desktop|288[\s-]*(?:pin|polig|broches)|lpx|fury\s*beast|ripjaws|trident|"
                  r"dominator|aegis|flare|sniper|spectrix|gammix|vulcan|t-?force|tower|rgb|pc\s*fisso|de\s*bureau|(?<!so-)(?<!so )(?<!so - )dimm)\b")
ECC = re.compile(r"(?<!non-)(?<!non )(?<!kein )(?<!ohne )\b(ecc|registered|reg|r-?dimm|lr-?dimm|rdimm|lrdimm|server|serveur|[12]rx4|hpe)\b")
# A whole laptop or PC that merely lists its RAM.
# Model names don't count: sticks are often sold as "aus ThinkPad ausgebaut".
MACHINE = re.compile(r"\b(?:i[3579](?:[\s-]?\d{3,5}\w*)?|intel\s*core|core\s*i\d|ryzen|celeron|pentium|ssd|hdd|nvme|"
                     r"windows|win\s*1[01])\b"
                     r"|\b\d{2}(?:[.,]\d)?\s*(?:zoll|pouces|inch\b|\")")


@dataclass
class Memory:
    gb: float | None = None
    sticks: int = 1
    ddr: int | None = None
    module: str | None = None  # "sodimm", "dimm" or None if the listing doesn't say
    mhz: int | None = None
    ecc: bool = False
    machine: bool = False
    defect: bool = False
    offsite: bool = False

    def summary(self) -> str:
        size = "?GB" if self.gb is None else f"{self.gb:g}GB"
        if self.sticks > 1 and self.gb and self.gb % self.sticks == 0:
            size += f" ({self.sticks}x{self.gb / self.sticks:g}GB)"
        parts = [size, f"DDR{self.ddr}" if self.ddr else "DDR?", {"sodimm": "SO-DIMM", "dimm": "DIMM"}.get(self.module or "", "module?")]
        if self.mhz:
            parts.append(f"{self.mhz}MHz")
        parts += [flag for flag in ("ecc", "machine", "defect", "offsite") if getattr(self, flag)]
        return " ".join(parts)


def _size(title: str) -> tuple[float | None, int]:
    """Total GB and number of sticks, from the title."""
    total = _TOTAL.search(title)
    kits = []
    for m in _KIT.finditer(title):
        g = m.groups()
        if g[0]:
            n, size = int(g[0]), int(g[1])
        elif g[2]:
            size, n = int(g[2]), int(g[3])  # "16GB x2"
        else:
            n, size = sorted(map(int, g[4:6] if g[4] else g[6:8]))  # "(2x16)", "(8x2)": the smaller is the count
        if 1 <= n <= 8 and size in _STICK_SIZES:  # "1x8gb" too
            kits.append((n * size, n))
    if total:
        gb = int(total.group(1) or total.group(2))
        sticks = next((n for t, n in kits if t == gb), kits[0][1] if kits else 1)
        return gb, sticks
    if kits:
        return max(kits)
    # Above 128 GB it's the disk of a laptop that slipped past the machine check.
    sizes = sorted({gb for m in _SIZE.finditer(title) if 0 < (gb := int(m.group(1))) <= 128})
    if not sizes:
        return None, 1
    if _PRICE_FOR_SET.search(title):
        return sizes[-1], 1  # "8gb DDR4 200€ für 4 Stück im Set" listed at 65 €: one stick
    count = _COUNT.search(title)
    n = int(next(g for g in count.groups() if g)) if count else (2 if _PAIR.search(title) else 1)
    # Only "2x Samsung 8GB" clearly means n sticks of that size. "Lot de 2 barrettes 16 Go – Samsung +
    # G.Skill" for 18 € is 2x8 as often as 2x16, so the size counts as the total: a missed deal is
    # better than a false #1.
    leading = bool(count and count.group(1))
    if n > 1 and len(sizes) > 1 and sizes[-1] in (n * s for s in sizes[:-1]):
        return sizes[-1], n  # "Kit de 16 Go - 2 x barrettes 8 Go"
    if len(sizes) > 1:
        # "16gb ddr4 laptop ram 8gb", "hp stream laptop 4gb 64 GB": no telling, so the smaller
        return sizes[0] * (n if leading else 1), n
    if not leading or sizes[-1] not in _STICK_SIZES or n * sizes[-1] > 64:
        # "6x DDR4 SO-DIMM – 28 GB": no stick has 28 GB; "4x G.Skill Aegis 32GB" for 150 €: 4x8, not 4x32
        return sizes[-1], n
    return sizes[-1] * n, n


def _first(pattern: re.Pattern, *texts: str) -> int | None:
    for text in texts:
        m = pattern.search(text)
        if m:
            return int(next(g for g in m.groups() if g))
    return None


def parse_memory(title: str, description: str = "") -> Memory:
    # "Kingston Fury 8/16/32Gb": the price is for the smallest option
    t, d = _VARIANTS.sub(_base_variant, title.lower()), description.lower()
    gb, sticks = _size(t)
    mem = Memory(gb=gb, sticks=sticks, ddr=_first(_DDR, t, d))
    mhz = _first(_MHZ, t, d)
    if mhz and mhz >= 8000:  # "PC4-21300" is bandwidth in MB/s: 2666 MT/s
        mhz = min(SPEEDS, key=lambda s: abs(s - mhz / 8))
    mem.mhz = mhz
    if mem.ddr is None and mhz:
        # "Ram so-dimm 2x8 GB 3200": each generation has its own speeds
        mem.ddr = 3 if mhz <= 1866 else 4 if 2133 <= mhz <= 3600 else 5 if mhz >= 4800 else None
    for text in (t, d):
        so, dimm = bool(SODIMM.search(text)), bool(DIMM.search(text))
        if so != dimm:
            mem.module = "sodimm" if so else "dimm"
            break
    mem.ecc = bool(ECC.search(t))
    mem.machine = bool(MACHINE.search(t))
    mem.defect = bool(DEFECT.search(f"{t}\n{d}"))
    mem.offsite = bool(OFFSITE.search(f"{t}\n{d}"))
    return mem


class MemoryScorer:
    def __init__(
        self,
        ddr: int | list[int] | None = 4,
        module: str = "sodimm",  # "sodimm", "dimm" or "any"
        min_gb: float = 0,
        max_gb: float | None = None,
        min_stick_gb: float = 0,  # per stick: 16 = a single 16GB stick or a 2x16GB kit, not 2x8GB
        gb_cap: float = 64,  # GB counted up to this much
        max_sticks: int | None = None,  # free slots in the machine
        min_mhz: int = 0,
        require_known: bool = True,  # reject listings that don't say the generation or module type
        reject: list[str] | None = None,
        shipping_estimate: float = 0.0,
    ):
        self.ddr = {ddr} if isinstance(ddr, int) else set(ddr or [])
        self.module = module
        self.min_gb, self.max_gb, self.gb_cap = min_gb, max_gb, gb_cap
        self.min_stick_gb = min_stick_gb
        self.max_sticks, self.min_mhz = max_sticks, min_mhz
        self.require_known = require_known
        self.reject = set(reject if reject is not None else ["defect", "offsite", "ecc", "machine"])
        self.shipping_estimate = shipping_estimate

    def score(self, listing: Listing) -> Score:
        mem = parse_memory(listing.title, listing.description)
        cost = round(listing.effective_price + listing.shipping_or(self.shipping_estimate), 2)
        summary = mem.summary()

        def rejected(reason: str, needs_details: bool = False) -> Score:
            return Score(0, cost, summary, reject=reason, needs_details=needs_details)

        if "offsite" in self.reject and (reason := payment_reject(listing)):
            return rejected(reason)
        for flag in ("defect", "machine", "ecc"):
            if flag in self.reject and getattr(mem, flag):
                return rejected(flag)
        if mem.gb is None:
            return rejected("size unknown")
        if self.ddr and mem.ddr is not None and mem.ddr not in self.ddr:
            return rejected(f"ddr{mem.ddr}")
        if self.module != "any" and mem.module is not None and mem.module != self.module:
            return rejected(mem.module)
        if self.require_known and self.ddr and mem.ddr is None:
            return rejected("ddr unknown", needs_details=True)
        if self.require_known and self.module != "any" and mem.module is None:
            return rejected("module unknown", needs_details=True)
        if mem.gb < self.min_gb:
            return rejected(f"below {self.min_gb:g}GB")
        if self.max_gb is not None and mem.gb > self.max_gb:
            return rejected(f"above {self.max_gb:g}GB")
        if self.min_stick_gb and mem.gb / mem.sticks < self.min_stick_gb:
            return rejected(f"sticks below {self.min_stick_gb:g}GB")
        if self.max_sticks and mem.sticks > self.max_sticks:
            return rejected(f"{mem.sticks} sticks")
        if self.min_mhz and mem.mhz and mem.mhz < self.min_mhz:
            return rejected(f"below {self.min_mhz}MHz")
        return Score(points=min(mem.gb, self.gb_cap), cost=cost, summary=summary)
