"""Rough CPU performance / power data for CPUs commonly found in used mini PCs.

`mark` is an approximate PassMark multi-thread CPU Mark, `tdp` is in watts.
Numbers are deliberately rounded: they only need to rank CPUs against each
other. Unknown CPUs fall back to `estimate()`, and you can add or override
entries from the config file (`scorer.cpu_overrides`).
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class CpuInfo:
    model: str
    mark: int
    tdp: int
    generation: int | None = None
    estimated: bool = False


# model (lowercase, normalised) -> (mark, tdp)
KNOWN: dict[str, tuple[int, int]] = {
    # Intel 6th-14th gen desktop "T" (35 W) parts: OptiPlex Micro, ThinkCentre Tiny, EliteDesk Mini
    "i3-6100t": (3600, 35), "i5-6500t": (5000, 35), "i5-6600t": (5400, 35), "i7-6700t": (6800, 35),
    "i3-7100t": (3900, 35), "i5-7500t": (5400, 35), "i5-7600t": (5800, 35), "i7-7700t": (7500, 35),
    "i3-8100t": (5500, 35), "i5-8400t": (8200, 35), "i5-8500t": (8700, 35), "i5-8600t": (9200, 35), "i7-8700t": (11000, 35),
    "i3-9100t": (5800, 35), "i5-9400t": (8500, 35), "i5-9500t": (9000, 35), "i5-9600t": (9700, 35), "i7-9700t": (11500, 35),
    "i3-10100t": (7000, 35), "i5-10400t": (10000, 35), "i5-10500t": (10500, 35), "i5-10600t": (11500, 35),
    "i7-10700t": (14500, 35), "i9-10900t": (16000, 35),
    "i5-11400t": (13500, 35), "i5-11500t": (14000, 35), "i7-11700t": (17000, 35),
    "i3-12100t": (11500, 35), "i5-12400t": (16500, 35), "i5-12500t": (17500, 35), "i5-12600t": (18500, 35),
    "i7-12700t": (25000, 35), "i3-13100t": (12500, 35), "i5-13400t": (22000, 35), "i5-13500t": (25000, 35),
    "i7-13700t": (30000, 35), "i5-14400t": (23000, 35), "i5-14500t": (27000, 35), "i7-14700t": (33000, 35),
    # Intel desktop 65 W parts (SFF / some Micro)
    "i3-6100": (4100, 51), "i5-6500": (5700, 65), "i7-6700": (8000, 65),
    "i3-7100": (4300, 51), "i5-7500": (6300, 65), "i7-7700": (8600, 65),
    "i3-8100": (6100, 65), "i5-8400": (9200, 65), "i5-8500": (9500, 65), "i7-8700": (13000, 65),
    "i3-9100": (6400, 65), "i5-9500": (9600, 65), "i7-9700": (13500, 65),
    "i3-10100": (8700, 65), "i5-10400": (12300, 65), "i5-10500": (12500, 65), "i7-10700": (17000, 65),
    "i5-11400": (17000, 65), "i5-11500": (17500, 65), "i7-11700": (20500, 65),
    "i3-12100": (14000, 60), "i5-12400": (19500, 65), "i5-12500": (20000, 65), "i7-12700": (31000, 65),
    "i5-13400": (25500, 65), "i5-13500": (32000, 65),
    # Intel mobile (NUCs, laptops-in-a-box)
    "i5-6200u": (3000, 15), "i7-6500u": (3300, 15), "i5-7200u": (3400, 15), "i7-7500u": (3700, 15),
    "i5-8250u": (6000, 15), "i5-8265u": (6200, 15), "i5-8350u": (6300, 15), "i7-8550u": (6200, 15),
    "i7-8650u": (6500, 15), "i5-8259u": (8000, 28), "i7-8559u": (8700, 28),
    "i5-10210u": (6500, 15), "i7-10510u": (6700, 15), "i7-10710u": (9500, 15),
    "i3-1115g4": (6200, 28), "i5-1135g7": (10000, 28), "i7-1165g7": (10500, 28),
    "i5-1235u": (13500, 15), "i7-1255u": (14500, 15), "i5-1240p": (17000, 28), "i7-1260p": (17500, 28),
    "i5-1335u": (15000, 15), "i5-1340p": (19500, 28), "i7-1360p": (20000, 28),
    "i5-12450h": (16500, 45), "i7-12700h": (26500, 45), "i9-12900h": (28500, 45),
    # Intel low power
    "n100": (5500, 6), "n95": (5400, 15), "n97": (5700, 12), "n200": (5400, 6), "n150": (5600, 6),
    "n300": (9500, 7), "n305": (10000, 15), "n355": (10500, 15),
    "n5105": (4000, 10), "n5095": (4100, 15), "n6005": (4500, 10), "n5100": (2400, 6),
    "j4125": (2900, 10), "j4105": (2600, 10), "j5005": (2600, 10), "j3455": (2200, 10), "j5040": (3000, 10),
    "n4020": (1600, 6), "n4100": (2400, 6), "n4120": (2900, 6), "n4500": (1900, 6), "n5000": (2500, 6),
    "g4400t": (2300, 35), "g4560t": (3000, 35), "g4900t": (2600, 35), "g5400t": (3300, 35),
    "g5420t": (3500, 35), "g6400t": (3800, 35), "g7400t": (5500, 35),
    # AMD desktop "GE" (35 W) parts
    "ryzen 3 2200ge": (6300, 35), "ryzen 5 2400ge": (7400, 35), "ryzen 3 3200ge": (4700, 35),
    "ryzen 5 3400ge": (8600, 35), "ryzen 3 4350ge": (10500, 35), "ryzen 5 4650ge": (16000, 35),
    "ryzen 7 4750ge": (19500, 35), "ryzen 5 5600ge": (19000, 35), "ryzen 5 5650ge": (19500, 35),
    "ryzen 7 5750ge": (23000, 35), "ryzen 5 pro 8500ge": (21000, 35), "ryzen 5 8600ge": (24000, 35),
    "a10-9700e": (2900, 35), "a6-9500e": (1500, 35), "a10-8770e": (2900, 35),
    # AMD mobile (Beelink, Minisforum, GMKtec, ...)
    "ryzen 3 3200u": (3800, 15), "ryzen 5 3500u": (7000, 15), "ryzen 5 3550h": (7900, 35),
    "ryzen 7 3750h": (8500, 35), "ryzen 5 4500u": (11000, 15), "ryzen 7 4700u": (13500, 15),
    "ryzen 5 4600h": (14500, 45), "ryzen 7 4800h": (18500, 45), "ryzen 5 5500u": (13000, 15),
    "ryzen 5 5560u": (13000, 15), "ryzen 5 5600u": (15000, 15), "ryzen 5 5625u": (15500, 15),
    "ryzen 7 5700u": (16000, 15), "ryzen 7 5800u": (18000, 15), "ryzen 7 5825u": (18000, 15),
    "ryzen 5 5600h": (17000, 45), "ryzen 7 5800h": (21000, 45), "ryzen 9 5900hx": (23000, 45),
    "ryzen 5 7430u": (16000, 15), "ryzen 7 7730u": (18500, 15), "ryzen 5 6600h": (20000, 45),
    "ryzen 7 6800h": (23500, 45), "ryzen 9 6900hx": (25000, 45), "ryzen 5 7640hs": (23000, 35),
    "ryzen 7 7735hs": (24000, 35), "ryzen 7 7840hs": (29000, 35), "ryzen 7 8845hs": (29500, 35),
    "ryzen 9 7940hs": (30500, 35), "ryzen 7 255": (29000, 45)
}

_INTEL_CORE = re.compile(r"\bi([3579])[\s-]?(\d{4,5})([a-z]\d?|[a-z]{1,2})?\b")
_INTEL_LOW = re.compile(r"\b((?:n|j)\d{3,4}|g\d{4}t?)\b")
_RYZEN = re.compile(r"\bryzen\s*(?:ai\s*)?([3579])\s*(pro\s*)?(\d{3,4})\s*([a-z]{1,2})?\b")
_AMD_A = re.compile(r"\b(a\d{1,2}-\d{4}[a-z]?)\b")
# Fujitsu/Dell/HP listings often just say "i5 8th gen" / "i5 8. Generation".
_GEN_ONLY = re.compile(r"\bi([3579])\b[^\n]{0,20}?\b(\d{1,2})\s*(?:th|st|nd|rd|\.|e|ème|eme)?\s*(?:gen|generation|génération|generacji|generazione|generación|generatie)")

_TIER_BASE = {"3": 3600, "5": 5000, "7": 6800, "9": 8500}  # 6th gen T-ish baseline
_GEN_FACTOR = {2: 0.6, 3: 0.7, 4: 0.75, 5: 0.8, 6: 1.0, 7: 1.08, 8: 1.65, 9: 1.7, 10: 2.0, 11: 2.6, 12: 3.4, 13: 4.3, 14: 4.6}


def _suffix_tdp_factor(suffix: str, generation: int | None) -> tuple[int, float]:
    if suffix in ("t", "te"):
        return 35, 0.92
    if suffix in ("u",):
        return 15, 0.62 if (generation or 0) < 8 else 0.7
    if suffix.startswith("g"):  # 1135G7 etc.
        return 28, 0.75
    if suffix in ("p",):
        return 28, 0.9
    if suffix in ("h", "hq", "hk", "hx", "hs"):
        return 45, 1.15
    if suffix in ("k", "kf", "f", ""):
        return 65, 1.1
    return 65, 1.0


def lookup(text: str, overrides: dict[str, tuple[int, int]] | None = None) -> CpuInfo | None:
    """Find the CPU mentioned in a listing and return its rough performance data."""
    t = text.lower().replace("®", "").replace("™", "")
    table = {**KNOWN, **{k.lower(): tuple(v) for k, v in (overrides or {}).items()}}

    if m := _INTEL_CORE.search(t):
        tier, number, suffix = m.group(1), m.group(2), (m.group(3) or "")
        model = f"i{tier}-{number}{suffix}"
        # 10th gen+ mobile parts use 4 digits too: i5-1135G7 is 11th gen, not 1st.
        gen = int(number[:2]) if len(number) == 5 or number[0] == "1" else int(number[0])
        if model in table:
            mark, tdp = table[model]
            return CpuInfo(model, mark, tdp, gen)
        return estimate_intel(tier, gen, suffix, model)

    if m := _RYZEN.search(t):
        tier, pro, number, suffix = m.group(1), m.group(2), m.group(3), (m.group(4) or "")
        for key in (f"ryzen {tier} {'pro ' if pro else ''}{number}{suffix}", f"ryzen {tier} {number}{suffix}"):
            if key in table:
                mark, tdp = table[key]
                return CpuInfo(key, mark, tdp, int(number[0]))
        gen = int(number[0])
        base = {"3": 5000, "5": 8000, "7": 10000, "9": 12000}[tier] * (1 + 0.35 * max(gen - 2, 0))
        tdp = 15 if suffix == "u" else 35 if suffix in ("ge", "hs") else 45 if suffix.startswith("h") else 65
        return CpuInfo(f"ryzen {tier} {number}{suffix}", int(base), tdp, gen, estimated=True)

    for rx in (_INTEL_LOW, _AMD_A):
        if m := rx.search(t):
            model = m.group(1)
            if model in table:
                mark, tdp = table[model]
                return CpuInfo(model, mark, tdp)

    if m := _GEN_ONLY.search(t):
        gen = int(m.group(2))
        if 2 <= gen <= 14:
            return estimate_intel(m.group(1), gen, "t", f"i{m.group(1)} gen{gen}")
    return None


def estimate_intel(tier: str, gen: int, suffix: str, model: str) -> CpuInfo:
    tdp, factor = _suffix_tdp_factor(suffix, gen)
    mark = _TIER_BASE.get(tier, 5000) * _GEN_FACTOR.get(gen, 1.0) * factor
    return CpuInfo(model, int(mark), tdp, gen, estimated=True)
