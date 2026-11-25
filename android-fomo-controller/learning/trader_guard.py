"""Python reference for the Android trader guard; no account sizing or model fit.

Source/20 and standard per-lot fees define the historical outcome. Episodes are
observed first sells or batches of overdue same-pair buys at an expiry sweep.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
import math

HOLD_MS = 86_400_000
QUOTE_AGE_MS = 300_000
WINDOW = MIN_SAMPLES = 20
VERSION = "trader_guard_reference20_v1"


def fee(value: float) -> float:
    return max(0.95, 0.005 * value) if value > 0 else 0.0


def net_reference(source: float, entry: float, exit_cap: float | None) -> float:
    if not (math.isfinite(source) and source > 0 and math.isfinite(entry) and entry > 0):
        raise ValueError("Invalid reference buy")
    if exit_cap is not None and not (math.isfinite(exit_cap) and exit_cap > 0):
        raise ValueError("Invalid reference exit")
    principal = source / 20
    gross = principal * (exit_cap / entry) if exit_cap is not None else 0.0
    if not math.isfinite(gross):
        raise ValueError("Reference valuation overflow")
    return max(0.0, gross - fee(gross)) - principal - fee(principal)


@dataclass(frozen=True)
class Assessment:
    mode: str
    status: str
    samples: int
    wins: int
    threshold: float

    @property
    def allowed(self) -> bool:
        return self.mode != "FILTER" or self.status == "PASS"

    @property
    def rate(self) -> float | None:
        return self.wins / self.samples if self.samples else None


def assess(mode: str, outcomes: list[tuple[int, float]], now: int,
           threshold: float = 0.50) -> Assessment:
    if mode not in {"OFF", "WARN", "FILTER"} or not math.isfinite(threshold) or not 0 <= threshold <= 1:
        raise ValueError("Invalid trader guard configuration")
    known = [net for time, net in outcomes if time < now][:WINDOW]  # newest first
    if not all(math.isfinite(net) for net in known):
        raise ValueError("Non-finite outcome")
    n, wins = len(known), sum(net > 0 for net in known)
    status = ("OFF" if mode == "OFF" else "INSUFFICIENT" if n < MIN_SAMPLES
              else "LOW" if wins / n < threshold else "PASS")
    return Assessment(mode, status, n, wins, threshold)


@dataclass(frozen=True)
class Buy:
    id: str
    trader: str
    token: str
    source: float
    entry: float
    opened: int


class History:
    def __init__(self):
        self.buys: dict[str, Buy] = {}
        self.seen_buys: set[str] = set()
        self.episodes: list[dict] = []
        self.by_trader: dict[str, list[tuple[int, float]]] = defaultdict(list)

    def observe_buy(self, id: str, trader: str, token: str, source: float, cap: float,
                    now: int, mode: str, threshold: float) -> Assessment:
        trader, token = trader.strip().lower(), token.strip().lower()
        if id not in self.seen_buys:
            self.buys[id] = Buy(id, trader, token, source, cap, now)
            self.seen_buys.add(id)
        return assess(mode, list(reversed(self.by_trader[trader])), now, threshold)

    def _settle(self, lots: list[Buy], now: int, cap: float | None, kind: str):
        if not lots:
            return
        net = sum(net_reference(b.source, b.entry, cap) for b in lots)
        self.by_trader[lots[0].trader].append((now, net))
        self.episodes.append(dict(trader=lots[0].trader, token=lots[0].token,
                                  known_time=now, net_reference_usd=net,
                                  lot_count=len(lots), outcome_kind=kind))
        for b in lots:
            del self.buys[b.id]

    def observe_sell(self, trader: str, token: str, cap: float, now: int):
        pair = (trader.strip().lower(), token.strip().lower())
        lots = [b for b in self.buys.values() if (b.trader, b.token) == pair
                and b.opened < now <= b.opened + HOLD_MS]
        self._settle(lots, now, cap, "SOURCE_SELL")

    def expire(self, now: int, quote):
        groups: dict[tuple, list[Buy]] = defaultdict(list)
        for b in self.buys.values():
            if b.opened + HOLD_MS < now:
                groups[b.trader, b.token].append(b)
        for (trader, token), lots in groups.items():
            cap = quote(trader, token, now)
            self._settle(lots, now, cap, "TIMEOUT_MARK" if cap is not None else "TIMEOUT_ASSUMED_LOSS")
