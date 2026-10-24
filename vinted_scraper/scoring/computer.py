"""Value-for-money scorer for (mini) PCs.

points = CPU performance + RAM + storage (+ bonuses), scaled by form factor
cost   = price + buyer fees + estimated shipping + electricity over `years`

so a cheap but power-hungry tower can lose to a slightly pricier 35 W Micro.
Every weight can be changed per search in the config file.
"""

from __future__ import annotations

from ..models import Listing
from .base import Score
from .specs import parse_specs

DEFAULT_WEIGHTS = {
    "cpu": 1.0,  # points per 1000 PassMark
    "ram": 0.4,  # points per GB of RAM ...
    "ram_cap": 32,  # ... counted up to this many GB
    "storage": 0.006,  # points per GB of storage ...
    "storage_cap": 1000,  # ... counted up to this many GB
    "ssd_bonus": 1.0,
    "wifi_bonus": 0.0,
}
DEFAULT_FORM_FACTOR = {"tiny": 1.15, "sff": 1.0, "tower": 0.8, "server": 0.8, "laptop": 0.9, "unknown": 0.95}
# Idle watts on top of the CPU: PSU, fans, drive bays, a server's BMC.
DEFAULT_EXTRA_WATTS = {"server": 35, "tower": 5}
# Typical *idle* draw of the whole machine by CPU TDP class; home servers idle most of the time.
IDLE_WATTS = [(6, 6), (15, 8), (28, 10), (35, 11), (45, 14), (65, 18)]
LOAD_SHARE = 0.1  # fraction of the time spent near TDP


class ComputerScorer:
    def __init__(
        self,
        weights: dict | None = None,
        form_factor: dict | None = None,
        min_cpu_mark: int = 0,
        min_ram_gb: float = 0,
        assume_ram_gb: float = 8,
        unknown_cpu_mark: int = 3000,
        require_cpu: bool = False,
        reject: list[str] | None = None,
        barebone_factor: float = 0.85,
        no_psu_penalty: float = 15.0,
        shipping_estimate: float = 0.0,
        kwh_price: float = 0.30,
        years: float = 2.0,
        hours_per_day: float = 24.0,
        cpu_overrides: dict | None = None,
        extra_watts: dict | None = None,
    ):
        self.w = {**DEFAULT_WEIGHTS, **(weights or {})}
        self.form_factor = {**DEFAULT_FORM_FACTOR, **(form_factor or {})}
        self.min_cpu_mark = min_cpu_mark
        self.min_ram_gb = min_ram_gb
        self.assume_ram_gb = assume_ram_gb
        self.unknown_cpu_mark = unknown_cpu_mark
        self.require_cpu = require_cpu
        self.reject = set(reject if reject is not None else ["defect", "lot", "cpu_only"])
        self.barebone_factor = barebone_factor
        self.no_psu_penalty = no_psu_penalty
        self.shipping_estimate = shipping_estimate
        self.kwh_price = kwh_price
        self.years = years
        self.hours_per_day = hours_per_day
        self.cpu_overrides = cpu_overrides
        self.extra_watts = {**DEFAULT_EXTRA_WATTS, **(extra_watts or {})}

    def energy_cost(self, tdp: int, extra_watts: float = 0) -> float:
        idle = next((w for limit, w in IDLE_WATTS if tdp <= limit), IDLE_WATTS[-1][1] + (tdp - 65) * 0.2)
        idle += extra_watts
        avg_watts = idle * (1 - LOAD_SHARE) + tdp * LOAD_SHARE
        kwh = avg_watts / 1000 * self.hours_per_day * 365 * self.years
        return kwh * self.kwh_price

    def score(self, listing: Listing) -> Score:
        specs = parse_specs(listing.text, self.cpu_overrides, title=listing.title)
        w = self.w
        cost = listing.effective_price + self.shipping_estimate
        summary = specs.summary()

        for flag in ("defect", "lot", "cpu_only", "barebone", "no_psu"):
            if flag in self.reject and getattr(specs, flag):
                return Score(0, cost, summary, reject=flag)
        if self.require_cpu and not specs.cpu:
            return Score(0, cost, summary, reject="cpu unknown", needs_details=True)
        if specs.cpu and specs.cpu.mark < self.min_cpu_mark:
            return Score(0, cost, summary, reject=f"cpu below {self.min_cpu_mark}")
        if specs.ram_gb is not None and specs.ram_gb < self.min_ram_gb:
            return Score(0, cost, summary, reject=f"ram below {self.min_ram_gb:g}GB")

        # A second socket adds a bit less than double the speed, but all of the power.
        mark = specs.cpu.mark * (1.9 if specs.cpus > 1 else 1) if specs.cpu else self.unknown_cpu_mark
        tdp = specs.cpu.tdp * specs.cpus if specs.cpu else 35
        energy = self.energy_cost(tdp, self.extra_watts.get(specs.form_factor or "", 0))
        ram = specs.ram_gb if specs.ram_gb is not None else (0 if specs.barebone else self.assume_ram_gb)
        storage = specs.storage_gb or 0

        points = mark / 1000 * w["cpu"]
        points += min(ram, w["ram_cap"]) * w["ram"]
        points += min(storage, w["storage_cap"]) * w["storage"]
        points += w["ssd_bonus"] if specs.ssd else 0
        points += w["wifi_bonus"] if specs.wifi else 0
        points *= self.form_factor.get(specs.form_factor or "unknown", 1.0)
        if specs.barebone:
            points *= self.barebone_factor

        cost += energy
        if specs.no_psu:
            cost += self.no_psu_penalty
        summary += f" | energy {energy:.0f} {listing.currency}/{self.years:g}y"
        return Score(
            points=round(points, 2),
            cost=round(cost, 2),
            summary=summary,
            needs_details=specs.cpu is None or specs.ram_gb is None,
        )
