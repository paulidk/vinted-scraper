"""Pull hardware specs out of free-text listings (many languages, messy formatting)."""

from __future__ import annotations

import re
from dataclasses import dataclass

from .cpu_db import CpuInfo, lookup

# "to" is French for TB, but "8 to 16GB" is English: never treat it as a unit before a number.
_SIZE = r"(\d{1,4}(?:[.,]\d)?)\s*(tb|to(?!\s*\d)|gb|go|g)\b"
_RAM_WORDS = r"(ram|ddr\d?l?|memory|mémoire|memoire|arbeitsspeicher|speicher\s*ram|pamięć|pamiec|operační|geheugen|memoria|so-?dimm)"
_DISK_WORDS = r"(ssd|hdd|nvme|m\.?2|emmc|disk|disque|festplatte|stockage|dysk|schijf|disco|storage|sata|(?<!arbeits)speicher)"



def _gap(blocked_words: str) -> str:
    """Up to 12 non-digit chars that never run across one of `blocked_words`.

    Stops "4 Go RAM / SSD 128 Go" from pairing RAM with 128.
    """
    return rf"(?:(?!{blocked_words.replace('(', '(?:', 1)})[^\d\n]){{0,12}}?"


_RAM_NEAR = [
    re.compile(_SIZE + _gap(_DISK_WORDS) + _RAM_WORDS),
    re.compile(_RAM_WORDS + _gap(_DISK_WORDS) + _SIZE),
]
_DISK_NEAR = [
    re.compile(_SIZE + _gap(_RAM_WORDS) + _DISK_WORDS),
    re.compile(_DISK_WORDS + _gap(_RAM_WORDS) + _SIZE),
]
# "8/256", "16GB/512GB", "8 + 256"
_PAIR = re.compile(r"\b(\d{1,2})\s*(?:gb|go)?\s*[/+|]\s*(\d{3,4}|[12])\s*(gb|go|tb|to)?\b")
# "64GB/6GB", "512 Go + 8 Go": both units given, either order; the smaller one is RAM
_UNIT_PAIR = re.compile(_SIZE + r"\s*[/+|]\s*" + _SIZE)

_RAM_SIZES = {2, 4, 6, 8, 12, 16, 20, 24, 32, 40, 48, 64, 96, 128}
_DISK_SIZES = {1, 2, 4, 32, 64, 120, 128, 240, 250, 256, 320, 480, 500, 512, 960, 1000, 1024, 2000, 2048}

# Checked in order, first match wins ("mini tower" must hit tower before "mini" hits tiny).
FORM_FACTORS = {
    "tower": r"\b(mt|tower|midi|minitower|mini[\s-]*tower|workstation)\b",
    "sff": r"\b(sff|small\s*form(?:\s*factor)?|dt)\b",
    # mini/"1 litre" machines: what you usually want for a home server
    "tiny": r"\b(micro|tiny|mini[\s-]*pc|minipc|mini|nuc|usff|1\s*l(?:itre|iter)?|thin\s*client|brix|beelink|"
            r"minisforum|gmktec|trigkey|chuwi|meerkat|m\d{2,3}q|\d{3}\s*g\d\s*dm|esprimo\s*q\d{3}|wyse)\b",
    "laptop": r"\b(laptop|notebook|ordinateur\s*portable|portatile|portátil|laptopa?)\b",
}

DEFECT = re.compile(
    r"\b(defekt|defect|defective|kaputt|broken|for\s*parts|pour\s*pi[eè]ces|ne\s*(?:fonctionne|marche)\s*pas|"
    r"uszkodzon\w*|guasto|rott[oa]|no\s*funciona|niet\s*werkend|nefunk\w*|"
    r"bios\s*(?:lock|locked|password|passwort|mot\s*de\s*passe)|ersatzteil\w*|bastler)\b"
)
BAREBONE = re.compile(
    r"\b(barebone|ohne\s*(?:ram|ssd|festplatte|hdd|speicher)|sans\s*(?:ram|ssd|disque|stockage|m[ée]moire)|"
    r"no\s*(?:ram|ssd|hdd|storage|drive)|without\s*(?:ram|ssd|hdd|storage)|bez\s*(?:ram|dysku|ssd)|"
    r"senza\s*(?:ram|ssd|disco)|sin\s*(?:ram|ssd|disco)|zonder\s*(?:ram|ssd|schijf))\b"
)
NO_PSU = re.compile(
    r"\b(ohne\s*(?:netzteil|ladeger[aä]t)|sans\s*(?:alimentation|chargeur)|no\s*(?:psu|power\s*(?:supply|adapter)|charger)|"
    r"without\s*(?:psu|power\s*(?:supply|adapter)|charger)|bez\s*zasilacza|senza\s*alimentatore|sin\s*cargador)\b"
)
LOT = re.compile(r"\b(lot\s*(?:de|of)?\s*\d|[2-9]\d*\s*(?:x\s*)?(?:pcs|pieces|pièces|stück|stk|szt))\b")
WIFI = re.compile(r"\b(wi-?fi|wlan|802\.11)\b")


@dataclass
class Specs:
    cpu: CpuInfo | None = None
    ram_gb: float | None = None
    storage_gb: float | None = None
    ssd: bool = False
    form_factor: str | None = None
    defect: bool = False
    barebone: bool = False
    no_psu: bool = False
    lot: bool = False
    wifi: bool = False

    def summary(self) -> str:
        parts = []
        if self.cpu:
            parts.append(f"{self.cpu.model}{'~' if self.cpu.estimated else ''} ({self.cpu.mark} pts, {self.cpu.tdp}W)")
        if self.ram_gb:
            parts.append(f"{self.ram_gb:g}GB RAM")
        if self.storage_gb:
            size = f"{self.storage_gb / 1000:g}TB" if self.storage_gb >= 1000 else f"{self.storage_gb:g}GB"
            parts.append(f"{size} {'SSD' if self.ssd else 'disk'}")
        if self.form_factor:
            parts.append(self.form_factor)
        parts += [flag for flag in ("defect", "barebone", "no_psu", "lot") if getattr(self, flag)]
        return ", ".join(parts) or "no specs found"


def _gb(number: str, unit: str) -> float:
    value = float(number.replace(",", "."))
    return value * 1000 if unit in ("tb", "to") or (unit in (None, "") and value <= 2) else value


def parse_specs(text: str, cpu_overrides: dict | None = None, title: str = "") -> Specs:
    t = text.lower()
    specs = Specs(cpu=lookup(t, cpu_overrides))

    # RAM: values next to a RAM keyword win; otherwise fall back to "8/256" style pairs
    # or a lone small GB value that is a typical RAM size.
    ram_candidates = []
    for rx in _RAM_NEAR:
        for m in rx.finditer(t):
            size, unit = (m.group(1), m.group(2)) if m.group(1)[0].isdigit() else (m.group(2), m.group(3))
            if unit in ("gb", "go", "g") and float(size.replace(",", ".")) in _RAM_SIZES:
                ram_candidates.append(float(size.replace(",", ".")))
    disk_candidates = []
    for rx in _DISK_NEAR:
        for m in rx.finditer(t):
            size, unit = (m.group(1), m.group(2)) if m.group(1)[0].isdigit() else (m.group(2), m.group(3))
            gb = _gb(size, unit)
            if gb >= 16:
                disk_candidates.append(gb)
    # A size next to both kinds of keyword ("8go de RAM - 128go de SSD", "SSD 128GB DDR3 16GB")
    # is storage if another size already explains the RAM; otherwise it stays RAM.
    if any(v not in disk_candidates for v in ram_candidates):
        ram_candidates = [v for v in ram_candidates if v not in disk_candidates]
    else:
        disk_candidates = [v for v in disk_candidates if v not in ram_candidates]
    for m in _PAIR.finditer(t):
        ram, disk, unit = int(m.group(1)), m.group(2), m.group(3)
        if ram in _RAM_SIZES and int(disk) in _DISK_SIZES:
            ram_candidates.append(float(ram))
            disk_candidates.append(_gb(disk, unit or ""))
    for m in _UNIT_PAIR.finditer(t):
        small, big = sorted((_gb(m.group(1), m.group(2)), _gb(m.group(3), m.group(4))))
        if small in _RAM_SIZES and big >= 32 and big > small:
            ram_candidates.append(small)
            disk_candidates.append(big)
    if not ram_candidates:
        loose = [float(m.group(1).replace(",", ".")) for m in re.finditer(_SIZE, t) if m.group(2) in ("gb", "go")]
        # Without a RAM keyword, the first plausible size is usually the installed amount
        # ("8GB, upgradable to 32GB").
        ram_candidates = [v for v in loose if v in _RAM_SIZES and v <= 64][:1]
        if not disk_candidates:
            disk_candidates = [v for v in loose if v >= 120]
    if not disk_candidates:
        disk_candidates = [_gb(m.group(1), m.group(2)) for m in re.finditer(_SIZE, t) if m.group(2) in ("tb", "to")]

    specs.ram_gb = max(ram_candidates) if ram_candidates else None
    specs.storage_gb = max(disk_candidates) if disk_candidates else None
    specs.ssd = bool(re.search(r"\b(ssd|nvme|m\.?2)\b", t))

    # The title is more reliable than a description that may mention other machines.
    for scope in (title.lower(), t):
        specs.form_factor = next((name for name, rx in FORM_FACTORS.items() if re.search(rx, scope)), None)
        if specs.form_factor:
            break
    specs.defect = bool(DEFECT.search(t))
    specs.barebone = bool(BAREBONE.search(t))
    specs.no_psu = bool(NO_PSU.search(t))
    specs.lot = bool(LOT.search(t))
    specs.wifi = bool(WIFI.search(t))
    return specs
