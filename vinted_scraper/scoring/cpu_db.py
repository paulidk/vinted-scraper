"""Rough CPU performance / power data for CPUs commonly found in used mini PCs, desktops and servers.

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
    "ryzen 9 7940hs": (30500, 35), "ryzen 7 255": (29000, 45),
    # AMD desktop
    "ryzen 5 1600": (12000, 65), "ryzen 7 1700": (13700, 65), "ryzen 5 2600": (13200, 65), "ryzen 7 2700": (15500, 65),
    "ryzen 5 2400g": (8800, 65), "ryzen 3 3200g": (7200, 65), "ryzen 5 3400g": (9300, 65), "ryzen 5 3600": (17800, 65),
    "ryzen 7 3700x": (22600, 65), "ryzen 9 3900x": (32500, 105), "ryzen 5 4600g": (16300, 65), "ryzen 5 5500": (19500, 65),
    "ryzen 5 5600": (21500, 65), "ryzen 5 5600x": (22000, 65), "ryzen 5 5600g": (19800, 65), "ryzen 7 5700g": (24600, 65),
    "ryzen 7 5700x": (26500, 65), "ryzen 7 5800x": (28000, 105), "ryzen 9 5900x": (39000, 105), "ryzen 9 5950x": (46000, 105),
    "ryzen 5 7600": (27000, 65), "ryzen 7 7700x": (35000, 105),
    # Intel Xeon E3 (4 cores, Dell T/R1xx-3xx, HP MicroServer, Fujitsu TX1xx)
    "e3-1220 v2": (4500, 69), "e3-1230 v2": (6100, 69), "e3-1240 v2": (6300, 69), "e3-1270 v2": (6600, 69),
    "e3-1220 v3": (4900, 80), "e3-1225 v3": (5000, 84), "e3-1230 v3": (7000, 80), "e3-1231 v3": (7100, 80),
    "e3-1240 v3": (7200, 80), "e3-1241 v3": (7300, 80), "e3-1245 v3": (7300, 84), "e3-1271 v3": (7600, 80),
    "e3-1220 v5": (5300, 80), "e3-1225 v5": (5500, 80), "e3-1230 v5": (7600, 80), "e3-1240 v5": (7900, 80),
    "e3-1245 v5": (8000, 80), "e3-1270 v5": (8200, 80), "e3-1220 v6": (5700, 72), "e3-1225 v6": (5900, 73),
    "e3-1230 v6": (8000, 72), "e3-1240 v6": (8300, 72), "e3-1245 v6": (8400, 73), "e3-1270 v6": (8600, 72),
    # Intel Xeon E5 (PowerEdge R/T 6xx-7xx, ProLiant DL360/380, workstations)
    "e5-2620": (5800, 95), "e5-2630": (6500, 95), "e5-2640": (6200, 95), "e5-2650": (8500, 95), "e5-2660": (9000, 95),
    "e5-2670": (9600, 115), "e5-2680": (10000, 130), "e5-2690": (10600, 135), "e5-2630l": (5500, 60), "e5-2650l": (7000, 70),
    "e5-2420 v2": (6500, 80), "e5-2430 v2": (7000, 80), "e5-2450 v2": (9500, 95), "e5-2470 v2": (11000, 95),
    "e5-2620 v2": (6900, 80), "e5-2630 v2": (7600, 80), "e5-2640 v2": (8000, 95), "e5-2650 v2": (9900, 95),
    "e5-2660 v2": (11000, 95), "e5-2667 v2": (11000, 130), "e5-2670 v2": (11800, 115), "e5-2680 v2": (12300, 115),
    "e5-2690 v2": (13000, 130), "e5-2695 v2": (13500, 115), "e5-2697 v2": (14000, 130),
    "e5-1620 v2": (6300, 130), "e5-1650 v2": (9700, 130), "e5-1660 v2": (11000, 130),
    "e5-1620 v3": (7500, 140), "e5-1650 v3": (10500, 140), "e5-1660 v3": (12500, 140),
    "e5-2620 v3": (8000, 85), "e5-2630 v3": (10500, 85), "e5-2640 v3": (10800, 90), "e5-2650 v3": (12900, 105),
    "e5-2660 v3": (13800, 105), "e5-2670 v3": (15300, 120), "e5-2680 v3": (16300, 120), "e5-2690 v3": (17500, 135),
    "e5-2695 v3": (17800, 120), "e5-2697 v3": (19500, 145), "e5-2698 v3": (20000, 135), "e5-2699 v3": (21500, 145),
    "e5-1620 v4": (7800, 140), "e5-1650 v4": (11000, 140), "e5-1680 v4": (13500, 140),
    "e5-2620 v4": (9500, 85), "e5-2630 v4": (11800, 85), "e5-2640 v4": (12300, 90), "e5-2650 v4": (14800, 105),
    "e5-2660 v4": (16700, 105), "e5-2673 v4": (19000, 135), "e5-2680 v4": (18500, 120), "e5-2683 v4": (19800, 120),
    "e5-2690 v4": (19800, 135), "e5-2695 v4": (20500, 120), "e5-2697 v4": (22000, 145), "e5-2698 v4": (23000, 135),
    "e5-2699 v4": (24000, 145),
    # Intel Xeon E-2xxx (Dell T/R3x0, HP ML/DL20, Fujitsu TX1330)
    "e-2124": (6700, 71), "e-2124g": (6800, 71), "e-2126g": (9500, 80), "e-2134": (7900, 71), "e-2136": (12500, 80),
    "e-2144g": (8000, 71), "e-2146g": (12800, 80), "e-2174g": (9000, 71), "e-2176g": (13500, 80), "e-2186g": (14000, 95),
    "e-2224": (7200, 71), "e-2224g": (7300, 71), "e-2226g": (10000, 80), "e-2234": (8500, 71), "e-2236": (13500, 80),
    "e-2244g": (9000, 71), "e-2246g": (13700, 80), "e-2274g": (9700, 83), "e-2276g": (14000, 80), "e-2278g": (17000, 80),
    "e-2286g": (14500, 95), "e-2288g": (17800, 95), "e-2314": (9500, 65), "e-2324g": (10000, 65), "e-2334": (13500, 65),
    "e-2336": (18500, 65), "e-2356g": (19500, 80), "e-2378g": (22000, 80), "e-2386g": (20500, 95), "e-2388g": (23500, 95),
    # Intel Xeon W (Dell Precision, HP Z, Lenovo P workstations)
    "w-2102": (5800, 120), "w-2104": (6200, 120), "w-2123": (8500, 120), "w-2125": (9400, 120), "w-2133": (12000, 140),
    "w-2135": (13000, 140), "w-2145": (16000, 140), "w-2155": (19000, 140), "w-2175": (22000, 140), "w-2195": (26000, 140),
    "w-2223": (9000, 120), "w-2225": (9800, 105), "w-2235": (14000, 130), "w-2245": (17000, 155), "w-2255": (20500, 165),
    "w-2265": (22000, 165), "w-2275": (25500, 165), "w-2295": (30000, 165),
    # Intel Xeon Scalable
    "bronze 3104": (5500, 85), "bronze 3106": (7000, 85), "bronze 3204": (5800, 85), "silver 4108": (9500, 85),
    "silver 4110": (10500, 85), "silver 4112": (5500, 85), "silver 4114": (12500, 85), "silver 4116": (14000, 85),
    "silver 4208": (11000, 85), "silver 4210": (13500, 85), "silver 4214": (16000, 85), "silver 4215": (12500, 85),
    "silver 4216": (20000, 100), "gold 5115": (13000, 85), "gold 5118": (15500, 105), "gold 5120": (17500, 105),
    "gold 5218": (22000, 125), "gold 5220": (23500, 125), "gold 6126": (18500, 125), "gold 6130": (20500, 125),
    "gold 6132": (20500, 140), "gold 6134": (13000, 130), "gold 6138": (24000, 125), "gold 6140": (23500, 140),
    "gold 6148": (26000, 150), "gold 6152": (27000, 140), "gold 6154": (27000, 200), "gold 6230": (27000, 125),
    "gold 6242": (27000, 150), "gold 6248": (30500, 150), "gold 6252": (31500, 150), "platinum 8160": (30000, 150),
    # Intel Xeon D / older
    "d-1518": (4500, 35), "d-1521": (4800, 45), "d-1528": (6500, 35), "d-1537": (7800, 35), "d-1540": (9500, 45),
    "d-1541": (9600, 45), "x5650": (6000, 95), "x5670": (6300, 95), "x5675": (6600, 95), "x5680": (6900, 130),
    "x5690": (7100, 130), "l5640": (5500, 60), "e5645": (5200, 80), "e5649": (5300, 80),
    # AMD EPYC
    "epyc 3251": (13000, 55), "epyc 7251": (12000, 120), "epyc 7281": (19000, 155), "epyc 7301": (21000, 155),
    "epyc 7351": (22000, 155), "epyc 7401": (27000, 155), "epyc 7551": (31000, 180), "epyc 7601": (34000, 180),
    "epyc 7302": (28000, 155), "epyc 7402": (38000, 180), "epyc 7452": (45000, 155), "epyc 7502": (48000, 180),
    "epyc 7542": (52000, 225), "epyc 7702": (62000, 200),
}

# Xeon E3/E5/E7 with version: "E5-2680 v4", "e5-2680v4", "E-5 2630 v3", "E5-2673-V4"
_XEON_E = re.compile(r"\be\s*-?\s*([357])\s*-?\s*(\d{4})\s*(l|w)?(?:\s*-?\s*v\s*([1-6]))?\b")
_XEON_E2 = re.compile(r"\be\s*-\s*(2[1-4]\d{2}g?)\b|\bxeon\s*e\s*-?\s*(2[1-4]\d{2}g?)\b")
_XEON_W = re.compile(r"\bw\s*-\s*(2[12]\d{2})\b|\bxeon\s*w\s*-?\s*(2[12]\d{2})\b")
_XEON_SP = re.compile(r"\b(bronze|silver|gold|platinum)\s*([3-8]\d{3})[a-z]?\b")
_XEON_D = re.compile(r"\b(d\s*-\s*1[5-7]\d{2}|[xle]5[56]\d{2})\b")
_EPYC = re.compile(r"\bepyc\s*(\d{4})p?\b")
_CORES = re.compile(r"\b(\d{1,2})\s*[- ]?(?:cores?|kerne?|c[oœ]urs|core|nucleos|rdzeni)\b")
_DUAL = re.compile(
    r"\b(?:2\s*[x×]\s*(?:intel\s*)?(?:xeon|e\s*-?\s*5|epyc|opteron|cpus?|prozessoren|processeurs?)|"
    r"(?:dual|double|doppi[ao]|doble|dupla)[\s-]*(?:xeon|cpus?|socket|sockel|processor|prozessor|processeur|processore|"
    r"procesador|e5|epyc)|bi[\s-]?(?:processeur|xeon)|"
    r"zwei\s*(?:cpus?|prozessoren))"
)
_E5_FALLBACK = {"1": 7500, "2": 9500, "3": 13000, "4": 16000}  # unlisted E5-26xx by version
_E3_FALLBACK = {"1": 5500, "2": 6200, "3": 7000, "4": 7200, "5": 7700, "6": 8100}

# The suffix may be written apart: "i7 1165 g7", "i5 8500 t".
_INTEL_CORE = re.compile(r"\bi([3579])[\s-]?(\d{4,5})\s?([a-z]\d?|[a-z]{1,2})?\b")
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

    if server := _server_cpu(t, table):
        return server

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


def _server_cpu(t: str, table: dict) -> CpuInfo | None:
    xeon = "xeon" in t
    if m := _XEON_E.search(t):
        family, number, low, version = m.group(1), m.group(2), m.group(3) or "", m.group(4) or "1"
        model = f"e{family}-{number}{low}" + (f" v{version}" if version != "1" else "")
        if model in table:
            return CpuInfo(model, *table[model])
        if xeon or version != "1":
            if family == "3":
                return CpuInfo(model, _E3_FALLBACK.get(version, 7000), 80, estimated=True)
            return CpuInfo(model, _E5_FALLBACK.get(version, 9000), 105, estimated=True)
    for rx, prefix in ((_XEON_E2, "e-"), (_XEON_W, "w-")):
        if m := rx.search(t):
            model = prefix + (m.group(1) or m.group(2))
            if model in table:
                return CpuInfo(model, *table[model])
            return CpuInfo(model, 12000, 80 if prefix == "e-" else 140, estimated=True)
    if xeon and (m := _XEON_SP.search(t)):
        model = f"{m.group(1)} {m.group(2)}"
        if model in table:
            return CpuInfo(model, *table[model])
        return CpuInfo(model, 15000, 125, estimated=True)
    if m := _XEON_D.search(t):
        model = re.sub(r"\s", "", m.group(1))
        if model in table:
            return CpuInfo(model, *table[model])
    if m := _EPYC.search(t):
        model = f"epyc {m.group(1)}"
        return CpuInfo(model, *table[model]) if model in table else CpuInfo(model, 25000, 155, estimated=True)
    if xeon:
        # "Xeon 10 Core": no model, so judge it by its cores.
        cores = int(m.group(1)) if (m := _CORES.search(t)) else 0
        return CpuInfo(f"xeon {cores}c" if cores else "xeon", max(4000, cores * 1100) if cores else 6000, 95, estimated=True)
    return None


def cpu_count(text: str) -> int:
    """2 for dual-socket machines ("2x Xeon E5-2680 v4", "Dual CPU"), else 1."""
    return 2 if _DUAL.search(text.lower()) else 1


def estimate_intel(tier: str, gen: int, suffix: str, model: str) -> CpuInfo:
    tdp, factor = _suffix_tdp_factor(suffix, gen)
    mark = _TIER_BASE.get(tier, 5000) * _GEN_FACTOR.get(gen, 1.0) * factor
    return CpuInfo(model, int(mark), tdp, gen, estimated=True)
