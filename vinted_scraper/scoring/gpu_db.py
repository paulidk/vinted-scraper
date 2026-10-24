"""Rough performance data for dedicated graphics cards found in used desktops.

`mark` is an approximate PassMark G3D Mark. Like the CPU table, the numbers only
need to rank cards against each other; add or correct entries with
`scorer.gpu_overrides`. Only cards in the table are recognised, so "RX 100"
(a Fujitsu server) or "Vega 11" (integrated graphics) don't count as a GPU.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class GpuInfo:
    model: str
    mark: int

    @property
    def idle_watts(self) -> float:
        """A dedicated card idles at roughly 6-20 W, more for the fast ones."""
        return round(6 + self.mark / 2500, 1)


KNOWN: dict[str, int] = {
    # NVIDIA GeForce
    "gt 710": 800, "gt 730": 1000, "gt 1030": 2700, "gtx 750": 3300, "gtx 750 ti": 3900, "gtx 950": 5300,
    "gtx 960": 6000, "gtx 970": 9600, "gtx 980": 11000, "gtx 980 ti": 13500, "gtx 1050": 4800, "gtx 1050 ti": 6000,
    "gtx 1060": 10000, "gtx 1060 3gb": 9000, "gtx 1060 6gb": 10000, "gtx 1070": 13500, "gtx 1070 ti": 14500,
    "gtx 1080": 15500, "gtx 1080 ti": 18500, "gtx 1630": 4500, "gtx 1650": 7800, "gtx 1650 super": 10000,
    "gtx 1660": 11500, "gtx 1660 super": 12700, "gtx 1660 ti": 12800,
    "rtx 2060": 14000, "rtx 2060 super": 16000, "rtx 2070": 16500, "rtx 2070 super": 18000, "rtx 2080": 19000,
    "rtx 2080 super": 20000, "rtx 2080 ti": 21800, "rtx 3050": 12800, "rtx 3060": 17000, "rtx 3060 ti": 20300,
    "rtx 3070": 22000, "rtx 3070 ti": 23500, "rtx 3080": 25000, "rtx 3080 ti": 27000, "rtx 3090": 27000,
    "rtx 3090 ti": 29000, "rtx 4060": 19500, "rtx 4060 ti": 22500, "rtx 4070": 26500, "rtx 4070 super": 29500,
    "rtx 4070 ti": 31500, "rtx 4070 ti super": 32000, "rtx 4080": 34500, "rtx 4080 super": 35000, "rtx 4090": 38500,
    "rtx 5060": 22000, "rtx 5060 ti": 24500, "rtx 5070": 30000, "rtx 5070 ti": 34000, "rtx 5080": 37000,
    # NVIDIA workstation
    "quadro k620": 1600, "quadro k2200": 2600, "quadro m2000": 3500, "quadro m4000": 6200, "quadro p400": 1300,
    "quadro p600": 2000, "quadro p620": 2300, "quadro p1000": 3800, "quadro p2000": 6600, "quadro p2200": 7400,
    "quadro p4000": 10000, "quadro p5000": 12500, "quadro t400": 2300, "quadro t600": 4500, "quadro t1000": 5900,
    "rtx a2000": 13000, "rtx a4000": 19000, "rtx a5000": 22500, "quadro rtx 4000": 15500, "quadro rtx 5000": 18000,
    # AMD Radeon
    "r9 280x": 7000, "r9 290": 8800, "r9 290x": 9300, "r9 380": 6800, "r9 390": 9500,
    "rx 460": 4000, "rx 470": 7700, "rx 480": 8600, "rx 550": 2900, "rx 560": 3800, "rx 570": 7800, "rx 580": 8900,
    "rx 590": 9300, "rx vega 56": 13000, "rx vega 64": 14500, "rx 5500 xt": 9500, "rx 5600 xt": 13300,
    "rx 5700": 14000, "rx 5700 xt": 16000, "rx 6400": 7000, "rx 6500 xt": 7600, "rx 6600": 15000,
    "rx 6600 xt": 16200, "rx 6650 xt": 16700, "rx 6700 xt": 19500, "rx 6750 xt": 20000, "rx 6800": 22500,
    "rx 6800 xt": 26000, "rx 6900 xt": 27000, "rx 6950 xt": 28000, "rx 7600": 17000, "rx 7600 xt": 17500,
    "rx 7700 xt": 23000, "rx 7800 xt": 25500, "rx 7900 gre": 27000, "rx 7900 xt": 29500, "rx 7900 xtx": 31000,
    "rx 9060 xt": 26000, "rx 9070": 31000, "rx 9070 xt": 33000,
    # Intel Arc
    "arc a380": 4800, "arc a580": 12000, "arc a750": 13500, "arc a770": 14500, "arc b570": 14000, "arc b580": 15000,
}

_PATTERNS = [
    # GeForce: "GTX 1060 6GB", "rtx3060ti", "RTX 2070 Super", "GeForce GT 1030"
    (re.compile(r"\b(gtx|rtx|gt)\s*-?\s*(\d{3,4})\s*(ti|super)?\s*(super)?\b"),
     lambda m: " ".join(filter(None, [m.group(1), m.group(2), m.group(3), m.group(4)]))),
    # Workstation: "Quadro P2000", "Quadro RTX 4000", "RTX A2000", "NVIDIA T1000"
    (re.compile(r"\bquadro\s*(rtx\s*)?([kmpt]?\d{3,4})\b"),
     lambda m: f"quadro {'rtx ' if m.group(1) else ''}{m.group(2)}"),
    (re.compile(r"\brtx\s*(a\d{4})\b"), lambda m: f"rtx {m.group(1)}"),
    (re.compile(r"\bnvidia\s*(t\d{3,4})\b"), lambda m: f"quadro {m.group(1)}"),
    # Radeon: "RX 580 8GB", "rx6600xt", "RX 7900 XTX", "RX Vega 56", "R9 290X"
    (re.compile(r"\b(?:radeon\s*)?rx\s*-?\s*(vega\s*\d{2}|\d{3,4})\s*(xtx|xt|gre)?\b"),
     lambda m: " ".join(filter(None, ["rx", re.sub(r"\s+", " ", m.group(1)), m.group(2)]))),
    (re.compile(r"\bvega\s*(56|64)\b"), lambda m: f"rx vega {m.group(1)}"),
    (re.compile(r"\br9\s*-?\s*(\d{3}x?)\b"), lambda m: f"r9 {m.group(1)}"),
    # Intel Arc: "Arc A770"
    (re.compile(r"\barc\s*([ab]\d{3})\b"), lambda m: f"arc {m.group(1)}"),
]
_VRAM_AFTER = re.compile(r"\s*-?\s*(\d{1,2})\s*(?:gb|go|g)\b")


def find_gpus(text: str, overrides: dict[str, int] | None = None) -> list[tuple[GpuInfo, int, int]]:
    """Every known card mentioned, with where it sits in the (lowercased) text."""
    t = text.lower()
    table = {**KNOWN, **{k.lower(): int(v) for k, v in (overrides or {}).items()}}
    found = []
    for rx, name in _PATTERNS:
        for m in rx.finditer(t):
            model = name(m)
            vram = _VRAM_AFTER.match(t, m.end())
            variant = f"{model} {vram.group(1)}gb" if vram else ""
            if variant in table:
                found.append((GpuInfo(variant, table[variant]), m.start(), m.end()))
            elif model in table:
                found.append((GpuInfo(model, table[model]), m.start(), m.end()))
    return sorted(found, key=lambda f: f[1])


def lookup_gpu(text: str, overrides: dict[str, int] | None = None) -> GpuInfo | None:
    """The best card mentioned: listings sometimes name an old card next to the one fitted."""
    found = find_gpus(text, overrides)
    return max((gpu for gpu, _, _ in found), key=lambda g: g.mark) if found else None
