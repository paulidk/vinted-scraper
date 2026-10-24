"""Pull hardware specs out of free-text listings (many languages, messy formatting)."""

from __future__ import annotations

import re
from dataclasses import dataclass

from .cpu_db import CpuInfo, cpu_count, lookup
from .gpu_db import GpuInfo, find_gpus

# "to" is French for TB, but "8 to 16GB" is English: never treat it as a unit before a number.
# "2x8GB" is one 16GB size.
_SIZE = re.compile(r"(?:\b(\d)\s*[x×]\s*)?(\d{1,4}(?:[.,]\d)?)\s*(tb|to(?!\s*\d)|gb|go|g)\b")
# One alternation so "arbeitsspeicher" is read as RAM before "speicher" can be read as disk.
_KEYWORD = re.compile(
    r"\b(?:(?P<ram>ram|ddr\d?l?|memory|mémoire|memoire|arbeitsspeicher|speicher\s*ram|pamięć|pamiec|operační|"
    r"geheugen|memoria|so-?dimm|ecc|[rlu]?dimm)|(?P<disk>ssd|hdd|nvme|m\.?2|emmc|disk|disque|festplatte|stockage|dysk|schijf|"
    r"disco|storage|sata|speicher))\b"
)
_MAX_GAP = 12  # chars allowed between a size and its keyword ("8 Go de RAM", "SSD: 256 GB")
# "8/256", "16GB/512GB", "64Gb/6Gb", "512 Go + 8 Go": RAM and storage written as a pair
# not after "E5-": "E5-2680 | 128GB" is a CPU and its RAM, not RAM/storage
_PAIR = re.compile(r"(?<![\w.,-])(\d{1,4})\s*(gb|go|tb|to)?\s*[/+|]\s*(\d{1,4})\s*(gb|go|tb|to)?\b")

_RAM_SIZES = {2, 4, 6, 8, 12, 16, 20, 24, 32, 40, 48, 64, 96, 128}
_DISK_SIZES = {1, 2, 4, 32, 64, 120, 128, 240, 250, 256, 320, 480, 500, 512, 960, 1000, 1024, 2000, 2048}

# Checked in order, first match wins ("mini tower" must hit tower before "mini" hits tiny).
FORM_FACTORS = {
    # rack and tower servers: big PSUs, fans and a BMC draw far more than a desktop
    "server": r"\b(rack|rackmount|19\s*(?:zoll|inch|\")|[124]\s*u|poweredge|proliant|primergy|thinksystem|thinkserver|"
              r"supermicro|microserver)\b",
    # before tower: "Dell Precision 7550 Workstation" is a mobile workstation
    "laptop": r"\b(laptop|notebook|ordinateur\s*portable|portatile|portátil|laptopa?|latitude|thinkpad|elitebook|zbook|"
              r"probook|ideapad|macbook|chromebook|precision\s*(?:35\d0|5[56]\d0|7[5-7]\d0|m\d{4}))\b",
    "tower": r"\b(mt|tower|midi|minitower|mini[\s-]*tower|workstation)\b",
    "sff": r"\b(sff|small\s*form(?:\s*factor)?|dt)\b",
    # mini/"1 litre" machines: what you usually want for a home server
    "tiny": r"\b(micro|tiny|mini[\s-]*pc|minipc|mini|nuc|usff|1\s*l(?:itre|iter)?|thin\s*client|brix|beelink|"
            r"minisforum|gmktec|trigkey|chuwi|meerkat|m\d{2,3}q|\d{3}\s*g\d\s*dm|esprimo\s*q\d{3}|wyse)\b",
}

DEFECT = re.compile(
    r"\b(defekt|defect|defective|kaputt|broken|for\s*parts|pour\s*pi[eè]ces|ne\s*(?:fonctionne|marche)\s*pas|"
    r"uszkodzon\w*|guasto|rott[oa]|no\s*funciona|niet\s*werkend|nefunk\w*|ersatzteil\w*|bastler)\b"
    # a BIOS lock counts, "kein BIOS Passwort" / "no BIOS password" doesn't
    r"|(?<!kein )(?<!keine )(?<!no )(?<!ohne )(?<!sans )(?<!nessuna )\bbios\s*(?:lock|locked|password|passwort|mot\s*de\s*passe)\b"
)
BAREBONE = re.compile(
    r"\b(barebone|ohne\s*(?:ram|ssd|festplatte|hdd|speicher)|sans\s*(?:ram|ssd|disque|stockage|m[ée]moire)|"
    r"no\s*(?:ram|ssd|hdd|storage|drive)|without\s*(?:ram|ssd|hdd|storage)|bez\s*(?:ram|dysku|ssd)|"
    r"senza\s*(?:ram|ssd|disco)|sin\s*(?:ram|ssd|disco)|zonder\s*(?:ram|ssd|schijf))\b"
    # "Es fehlt: Ram, Netzteil, SSD", "RAM und SSD fehlen"
    r"|\bfehl(?:t|en)\s*:?[^.\n]{0,25}\b(?:ram|ssd|festplatte|arbeitsspeicher)\b|\b(?:ram|ssd|festplatte)\b[^.\n]{0,15}\bfehl(?:t|en)\b"
)
NO_PSU = re.compile(
    r"\b(ohne\s*(?:netzteil|ladeger[aä]t)|sans\s*(?:alimentation|chargeur)|no\s*(?:psu|power\s*(?:supply|adapter)|charger)|"
    r"without\s*(?:psu|power\s*(?:supply|adapter)|charger)|bez\s*zasilacza|senza\s*alimentatore|sin\s*cargador)\b"
    r"|\bfehl(?:t|en)\s*:?[^.\n]{0,25}\b(?:netzteil|ladeger[aä]t)\b|\b(?:netzteil|ladeger[aä]t)\b[^.\n]{0,15}\bfehl(?:t|en)\b"
)
LOT = re.compile(r"\b(lot\s*(?:de|of)?\s*\d|[2-9]\d*\s*(?:x\s*)?(?:pcs|pieces|pièces|stück|stk|szt))\b")
# A loose processor rather than a computer: a socket, an Intel sSpec code or "Prozessor" in the title...
CPU_PART = re.compile(
    r"\b(fc)?lga\s*-?\d{3,4}\b|\b(am[345]|sp[35]|s?tr[45])\b|\b(sockel|socket|tray|boxed)\b|"
    # Intel spec codes like SRH3Q always have a letter after SR ("Lenovo SR550" is a server)
    r"\bsr(?=[0-9a-z]{0,2}[a-z])[0-9a-z]{3}\b|"
    r"\b\d+c\s*/\s*\d+t\b|\b\d+\s*(?:cores?|kerne)\s*/?\s*\d+\s*threads?\b|vendor[\s-]*lock|\b(?:dell|hp|lenovo)[\s-]*locked\b"
)
# A graphics card on its own: "Grafikkarte MSI GTX 1060", or a card named without any CPU or machine.
GPU_FIRST = re.compile(
    r"^\W*(grafikkarte|carte\s*graphique|scheda\s*(?:video|grafica)|graphics\s*card|video\s*card|gpu|"
    r"tarjeta\s*gr[aá]fica|videokaart|karta\s*graficzna)\b"
)
_VRAM_AFTER = re.compile(r"\s*-?\s*(\d{1,2})\s*(?:gb|go|g)\b")
_VRAM = re.compile(r"\s*(?:de\s*)?(?:vram|gddr\d\w*|video)\b")
# Starting with the word is a strong sign, even with a maker in the title ("CPU Dell AMD Epyc 7402").
CPU_FIRST = re.compile(r"^\W*(cpu|processor|prozessor|processeur|processore|procesor|procesador|processador)\b")
# ...unless the title also names a whole machine: what it is, its model line, a maker or a RAM/storage size.
MACHINE_NOUN = re.compile(
    r"\b(pc|computer|rechner|ordinateur|ordenador|komputer|desktop|tower|sff|micro|tiny|mini|nuc\w*|laptop|notebook|"
    r"thin\s*client|workstation|nas|rack|[124]\s*u|microserver|barebone)\b|\b19\s*(?:\"|zoll|inch)"
)
SERVER_WORD = re.compile(r"\b(server|serveur|servidor|serwer|homeserver)\b")
MODEL_LINE = re.compile(
    r"\b(optiplex|thinkcentre|thinkstation|thinkpad|elitedesk|prodesk|elitebook|esprimo|celsius|futro|vostro|precision|"
    r"veriton|poweredge|proliant|primergy|thinksystem|thinkserver|beelink|minisforum|gmktec|chromebox|"
    r"m\d{2,3}[qsa]|z\d{3}|t\d{3,4}|r\d{3}|s[rt][56]\d0)\b"
)
MAKER_NAME = re.compile(r"\b(dell|lenovo|hp|fujitsu|asus|acer|msi|supermicro|gigabyte|intel\s*nuc)\b")
MAKER = re.compile(r"\b(dell|lenovo|hp|fujitsu|asus|acer|msi|supermicro|gigabyte|intel\s*nuc)\b|\b\d+\s*(?:gb|go|tb|to)\b")
# Boards and controllers, which often carry a maker name ("Supermicro scheda madre dual xeon").
# A board is a part even with a model line in the title ("Carte mère Dell Precision 3551").
BOARD = re.compile(
    r"\b(mainboard|motherboard|mobo|scheda\s*madre|carte\s*m[eè]re|placa\s*base|moederbord|p[łl]yta\s*g[łl][óo]wna)\b"
)
# Controllers and server boards: a part unless a machine is named ("PowerEdge R720 mit H710 RAID controller").
PART = re.compile(r"\b(hba|raid[\s-]*(?:controller|karte|card|carte)|backplane|riser|x58|x79|x99|x299|c602|c612)\b")
_BOARD = r"(x58|x79|x99|x299|c602|c612)\w*|mainboard|motherboard|mobo|carte\s*m[eè]re|scheda\s*madre"
KIT = re.compile(rf"\b(kit|bundle)\b.*\b({_BOARD})\b|\b({_BOARD})\b.*\b(kit|bundle)\b")
# "Kit GAMER - 64Go RAM - cpu Intel Xeon 10 cores": a kit of CPU and RAM is still not a machine
KIT_PARTS = re.compile(r"\b(kit|bundle)\b.*\b(cpu|processeur|prozessor|processore|ram|ddr\d)\b")
# Desktop chipsets name a board when the title has no PC word and no storage:
# "AMD Ryzen 7 5800X + MSI B550-A Pro + 32GB" (but not "MSI Cubi N (MS-B120) 8GB, 128GB SSD").
CHIPSET = re.compile(r"\b[abhqxz][1-9][0-9]0m?\b")
SIZE_IN_TITLE = re.compile(r"\b\d+\s*(?:gb|go|tb|to)\b")
DISK = re.compile(r"\b(ssd|hdd|nvme|m\.?2|disk|disque|festplatte|\d+\s*(?:tb|to))\b")
BUNDLED = re.compile(r"\+|&|\b(mit|with|avec|con|und|et|inkl\w*|incl\w*)\b")
RAM = re.compile(r"\b(ram|ddr\d|\d+\s*(?:gb|go)\b)")
# Asking to pay outside Vinted (no buyer protection): the usual bait behind a too-good price.
OFFSITE = re.compile(
    r"(?:no|not|kein\w*|nicht|pas\s*de|niente|sin)\s*(?:payment|paiement|zahlung|bezahlung|pagamento|pago)?\s*"
    r"(?:by|via|über|ueber|par|sur|tramite|con|per)?\s*vinted"
    r"|\b(paypal|pay\s*pal|whats\s*app|telegram|western\s*union|überweisung|virement|bonifico|bank\s*transfer|"
    r"f&f|friends\s*(?:and|&)\s*family)\b"
)
# One listing for several different machines ("Tiny PCs / Dell Lenovo HP / i3 i5 i7", "ab 99 €"):
# the price is only a starting price, so it's treated like a lot. Checked on the title only.
MULTI_OFFER = re.compile(
    r"\b(verschiedene|diverse|auswahl|mehrere|various|several|plusieurs|divers)\b|\bab\s*\d+\s*(?:€|eur)|"
    r"(?:\bi[3579]\b[^\n]{0,15}){3}|(?:\b(?:dell|lenovo|hp|fujitsu|acer|asus)\b[^\n]{0,12}){3}"
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
    offsite: bool = False
    cpus: int = 1  # sockets: 2 for "2x Xeon ..."
    gpu: GpuInfo | None = None  # dedicated graphics card
    wifi: bool = False

    def summary(self) -> str:
        parts = []
        if self.cpu:
            dual = "2x " if self.cpus > 1 else ""
            parts.append(f"{dual}{self.cpu.model}{'~' if self.cpu.estimated else ''} ({self.cpu.mark} pts, {self.cpu.tdp}W)")
        if self.ram_gb:
            parts.append(f"{self.ram_gb:g}GB RAM")
        if self.storage_gb:
            size = f"{self.storage_gb / 1000:g}TB" if self.storage_gb >= 1000 else f"{self.storage_gb:g}GB"
            parts.append(f"{size} {'SSD' if self.ssd else 'disk'}")
        if self.gpu:
            parts.append(f"{self.gpu.model} ({self.gpu.mark} G3D)")
        if self.form_factor:
            parts.append(self.form_factor)
        parts += [flag for flag in ("defect", "barebone", "no_psu", "lot", "cpu_only", "offsite") if getattr(self, flag)]
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


def parse_specs(text: str, cpu_overrides: dict | None = None, title: str = "", gpu_overrides: dict | None = None) -> Specs:
    t = text.lower()
    specs = Specs(cpu=lookup(t, cpu_overrides))
    gpus = find_gpus(t, gpu_overrides)
    # The first card in the title is the one fitted: "Vega 56 (wie GTX 1070)". Descriptions also name
    # older or "compatible" cards, so there the best one counts.
    in_title = find_gpus(title, gpu_overrides) if title else []
    if in_title:
        specs.gpu = in_title[0][0]
    elif gpus:
        specs.gpu = max((g for g, _, _ in gpus), key=lambda g: g.mark)
    # "GTX 1060 6GB", "RX 580 8 Go", "8GB GDDR6": video memory, neither RAM nor storage. Right after the
    # card only up to 12 GB counts: "RTX 3060 Ti 16GB 1TB" more likely lists the RAM next.
    vram_at = {m.start(1) for _, _, end in gpus if (m := _VRAM_AFTER.match(t, end)) and int(m.group(1)) <= 12}

    sizes = [
        _Size(m.start(), m.end(), _gb(number, unit) * int(count or 1), unit)
        for m in _SIZE.finditer(t)
        for count, number, unit in [m.groups()]
        # A bare "g" is also a model suffix: "Ryzen 5 5600G", "Xeon E-2176G" are not sizes.
        if unit != "g" or (_gb(number, unit) in _RAM_SIZES | _DISK_SIZES and not re.search(r"[a-z]-\s*$", t[: m.start(2)]))
        if m.start(2) not in vram_at and not _VRAM.match(t, m.end())
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
    # "i7-6700 | 16 GB | SSD": a 16 GB "SSD" without any RAM is the RAM (eMMC excepted)
    if specs.ram_gb is None and specs.storage_gb in (4, 8, 12, 16) and "emmc" not in t:
        specs.ram_gb, specs.storage_gb = specs.storage_gb, None
    specs.ssd = bool(re.search(r"\b(ssd|nvme|m\.?2)\b", t))

    # The title is more reliable than a description that may mention other machines.
    for scope in (title.lower(), t):
        specs.form_factor = next((name for name, rx in FORM_FACTORS.items() if re.search(rx, scope)), None)
        if specs.form_factor:
            break
    specs.defect = bool(DEFECT.search(t))
    specs.barebone = bool(BAREBONE.search(t))
    specs.no_psu = bool(NO_PSU.search(t))
    head = (title or text.split("\n", 1)[0]).lower()
    specs.lot = bool(LOT.search(t) or MULTI_OFFER.search(head))
    specs.offsite = bool(OFFSITE.search(t))
    specs.cpus = cpu_count(t) if specs.cpu else 1
    noun, server, line, maker = (rx.search(head) for rx in (MACHINE_NOUN, SERVER_WORD, MODEL_LINE, MAKER))
    cpu = lookup(head, cpu_overrides)
    head_gpu = find_gpus(head, gpu_overrides)
    specs.cpu_only = bool(
        # "Carte mère serveur Supermicro" is a board, "19\" Server mit Gigabyte Mainboard" a server
        ((board := BOARD.search(head)) and not noun and not (server and server.start() < board.start()))
        or ((part := PART.search(head)) and not (noun or line) and not (server and server.start() < part.start()))
        or (CHIPSET.search(head) and not (noun or server or line or DISK.search(head)))
        or (KIT.search(head) and not line)  # "Kit Serveur/Workstation X99 Xeon + 64Go RAM"
        or (KIT_PARTS.search(head) and not (noun or server or line))
        or (GPU_FIRST.search(head) and not line)
        or (head_gpu and not (cpu or noun or server or line))
        # "Processeur serveur Xeon E5-2673 v4 LGA2011" is still a processor
        or (CPU_FIRST.search(head) and not (noun or line))
        # "AMD EPYC 7402 - Socket SP3 - DELL LOCKED"; with RAM or storage sizes it's a machine though
        or (CPU_PART.search(head) and not (noun or line or SIZE_IN_TITLE.search(head)))
        # a title that is just a CPU model ("Intel Core i5 7500") is a processor for sale too
        or (not (noun or server or line or maker) and cpu)
        # CPU + RAM without storage or any machine name: "Intel Xeon E5-2660 + 64Go RAM"
        or (cpu and RAM.search(head) and BUNDLED.search(head)
            and not (DISK.search(head) or noun or server or line or MAKER_NAME.search(head)))
    )
    specs.wifi = bool(WIFI.search(t))
    return specs
