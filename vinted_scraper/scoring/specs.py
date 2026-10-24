"""Pull hardware specs out of free-text listings (many languages, messy formatting)."""

from __future__ import annotations

import re
from dataclasses import dataclass

from .cpu_db import CpuInfo, lookup

# "to" is French for TB, but "8 to 16GB" is English: never treat it as a unit before a number.
# "2x8GB" is one 16GB size.
_SIZE = re.compile(r"(?:\b(\d)\s*[x×]\s*)?(\d{1,4}(?:[.,]\d)?)\s*(tb|to(?!\s*\d)|gb|go|g)\b")
# One alternation so "arbeitsspeicher" is read as RAM before "speicher" can be read as disk.
_KEYWORD = re.compile(
    r"\b(?:(?P<ram>ram|ddr\d?l?|memory|mémoire|memoire|arbeitsspeicher|speicher\s*ram|pamięć|pamiec|operační|"
    r"geheugen|memoria|so-?dimm)|(?P<disk>ssd|hdd|nvme|m\.?2|emmc|disk|disque|festplatte|stockage|dysk|schijf|"
    r"disco|storage|sata|speicher))\b"
)
_MAX_GAP = 12  # chars allowed between a size and its keyword ("8 Go de RAM", "SSD: 256 GB")
# "8/256", "16GB/512GB", "64Gb/6Gb", "512 Go + 8 Go": RAM and storage written as a pair
_PAIR = re.compile(r"(?<![\w.,])(\d{1,4})\s*(gb|go|tb|to)?\s*[/+|]\s*(\d{1,4})\s*(gb|go|tb|to)?\b")

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
# A loose processor rather than a computer: a socket, an Intel sSpec code or "Prozessor" in the title...
CPU_PART = re.compile(
    r"\b(fc)?lga\s*-?\d{3,4}\b|\bam[345]\b|\b(sockel|socket|tray|boxed)\b|\bsr[0-9a-z]{3}\b|\b\d+c\s*/\s*\d+t\b|"
    r"^\s*(cpu|processor|prozessor|processeur|processore|procesor|procesador|processador)\b"
)
# ...unless the title also names a whole machine or its RAM/storage.
MACHINE = re.compile(
    r"\b(pc|computer|rechner|ordinateur|ordenador|komputer|desktop|tower|sff|micro|tiny|mini|nuc\w*|laptop|notebook|"
    r"thin\s*client|server|workstation|dell|lenovo|hp|fujitsu|asus|acer|msi|intel\s*nuc|optiplex|thinkcentre|"
    r"thinkpad|elitedesk|prodesk|elitebook|esprimo|futro|vostro|veriton|beelink|minisforum|gmktec|chromebox|"
    r"m\d{2,3}[qsa]|\d+\s*(?:gb|go|tb|to))\b"
)
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
    cpu_only: bool = False
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
        parts += [flag for flag in ("defect", "barebone", "no_psu", "lot", "cpu_only") if getattr(self, flag)]
        return ", ".join(parts) or "no specs found"


def _gb(number: str, unit: str | None) -> float:
    value = float(number.replace(",", "."))
    return value * 1000 if unit in ("tb", "to") else value


@dataclass
class _Size:
    start: int
    end: int
    gb: float
    unit: str

    def fits(self, kind: str) -> bool:
        if kind == "ram":
            return self.unit in ("gb", "go", "g") and self.gb in _RAM_SIZES
        return self.gb >= 16


def _assign(text: str, sizes: list[_Size]) -> dict[int, str]:
    """Give each size the RAM/disk keyword it is written next to: {size index: "ram" | "disk"}.

    A size may only pair with a keyword when nothing but a few non-digit characters and no
    other keyword sit between them. Each keyword describes one size. The most constrained
    size picks first, so in "16GB RAM 256GB SSD" 16 (which can only be RAM) takes "RAM"
    before 256 can, and in "RAM 16GB SSD 256GB" 256 takes "SSD" before 16 can.
    """
    keywords = [(m.start(), m.end(), m.lastgroup) for m in _KEYWORD.finditer(text)]
    options: dict[int, list[tuple[int, int, int]]] = {}
    for i, size in enumerate(sizes):
        for j, (k_start, k_end, kind) in enumerate(keywords):
            lo, hi = (size.end, k_start) if size.start < k_start else (k_end, size.start)
            if not size.fits(kind) or hi - lo > _MAX_GAP or re.search(r"[\d\n]", text[lo:hi]):
                continue
            if any(lo <= o_start and o_end <= hi for o_start, o_end, _ in keywords):
                continue
            # (distance, prefer "16GB RAM" over "RAM 16GB" on ties, keyword)
            options.setdefault(i, []).append((hi - lo, 0 if size.start < k_start else 1, j))

    assigned: dict[int, str] = {}
    claimed: set[int] = set()
    while True:
        best = None
        for i, opts in options.items():
            live = [o for o in opts if o[2] not in claimed] if i not in assigned else []
            if live:
                key = (len(live), min(live), sizes[i].start)
                if best is None or key < best[0]:
                    best = (key, i, min(live)[2])
        if best is None:
            return assigned
        _, i, j = best
        assigned[i] = keywords[j][2]
        claimed.add(j)


def _pairs(text: str):
    """Yield (ram_gb, storage_gb) from "8/256"-style pairs."""
    for m in _PAIR.finditer(text):
        n1, u1, n2, u2 = m.groups()
        if not (u1 or u2):
            # Bare numbers need typical sizes, so dates like 12/2023 don't count.
            ram, disk = int(n1), int(n2)
            disk_gb = _gb(n2, "tb" if disk <= 2 else "gb")  # "8/1" means 1TB
            if ram in _RAM_SIZES and disk in _DISK_SIZES and disk_gb > ram:
                yield float(ram), disk_gb
            continue
        small, big = sorted((_gb(n1, u1), _gb(n2, u2)))
        if small in _RAM_SIZES and big >= 32 and big > small:
            yield small, big


def parse_specs(text: str, cpu_overrides: dict | None = None, title: str = "") -> Specs:
    t = text.lower()
    specs = Specs(cpu=lookup(t, cpu_overrides))

    sizes = [
        _Size(m.start(), m.end(), _gb(number, unit) * int(count or 1), unit)
        for m in _SIZE.finditer(t)
        for count, number, unit in [m.groups()]
    ]
    assigned = _assign(t, sizes)
    ram_candidates = [sizes[i].gb for i, kind in assigned.items() if kind == "ram"]
    disk_candidates = [sizes[i].gb for i, kind in assigned.items() if kind == "disk"]
    free = [s for i, s in enumerate(sizes) if i not in assigned]

    # Fallbacks only fill RAM when no size sits next to a RAM keyword: once one does, a later
    # "16GB/64GB" or "max 32GB" is usually the upgrade limit, not what's installed.
    if not ram_candidates:
        for ram, disk in _pairs(t):
            ram_candidates.append(ram)
            if not disk_candidates:
                disk_candidates.append(disk)
            break
    if not ram_candidates:
        # The first plausible size is usually the installed amount ("8GB, upgradable to 32GB").
        loose = next((s for s in free if s.unit in ("gb", "go") and s.gb in _RAM_SIZES and s.gb <= 64), None)
        if loose:
            ram_candidates.append(loose.gb)
            free.remove(loose)
    if not disk_candidates:
        disk_candidates = [s.gb for s in free if s.gb >= 120 or s.unit in ("tb", "to")]

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
    head = (title or text.split("\n", 1)[0]).lower()
    # A title that is just a CPU model ("Intel Core i5 7500") is a processor for sale too.
    specs.cpu_only = not MACHINE.search(head) and bool(CPU_PART.search(head) or lookup(head, cpu_overrides))
    specs.wifi = bool(WIFI.search(t))
    return specs
