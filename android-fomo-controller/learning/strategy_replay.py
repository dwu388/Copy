"""Immediate-fill Python replay of AdaptiveHybridPolicy and paper position rules.

Candidate reserve history comes from prior intended-size decisions, as in
StrategyDatabase.loadState. No UI queues or fill data exist.
"""
from __future__ import annotations

from collections import Counter, defaultdict, deque
import math
import numpy as np

from learning.trader_guard import HOLD_MS, fee

RATIO_LADDER = [1000, 500, 250, 100, 75, 50, 40, 35, 30, 25, 20, 15, 10]


class Wallet:
    def __init__(self, config: dict, uncertainty: float, slippage: float = 0):
        self.cfg, self.uncertainty, self.slippage = config, uncertainty, slippage
        self.cash = self.peak = 1000.0
        self.open = self.max_dd = 0.0
        self.stage = self.promotions = self.exits = self.streak = 0
        self.mode = "NORMAL"
        self.shadow = deque(maxlen=20)
        self.shadow_lots = []
        self.signals = []
        self.candidates = deque(maxlen=10)
        self.recent = []
        self.positions = []
        self.trader = defaultdict(float)
        self.token = defaultdict(float)
        self.ledger = []
        self.skips, self.ratios = Counter(), Counter()

    def equity(self):
        return self.cash + self.open

    def drawdown(self):
        return max(0, 1 - self.equity() / self.peak)

    def stats(self, n):
        a = list(self.shadow)[-n:]
        return len(a), sum(a) / len(a) if a else 0, sum(v > 0 for v in a) / len(a) if a else 0

    def update_mode(self):
        r, e = self.cfg["regimes"], self.equity()
        exposure, cash_fraction = (self.open / e, self.cash / e) if e > 0 else (float("inf"), 0)

        def danger(prefix):
            n, mean, wins = self.stats(r[prefix + "_shadow_n"])
            return (self.drawdown() >= r[prefix + "_drawdown"]
                    or cash_fraction < r[prefix + "_cash_fraction"]
                    or exposure > r[prefix + "_exposure"]
                    or self.streak >= r[prefix + "_losing_streak"]
                    or (n >= r[prefix + "_shadow_n"] and
                        (mean <= r[prefix + "_shadow_mean"] or wins <= r[prefix + "_shadow_win_rate"])))

        target = "DEFENSIVE" if danger("defensive") else "CAUTION" if danger("caution") else "NORMAL"
        order = {"NORMAL": 0, "CAUTION": 1, "DEFENSIVE": 2}
        if order[target] > order[self.mode]:
            self.mode, self.exits = target, 0
            return
        if target == self.mode or self.exits < r["recovery_exits"]:
            return
        if self.mode == "DEFENSIVE":
            n, mean, wins = self.stats(max(r["defensive_shadow_n"], 8))
            mode = self.cfg["modes"]["CAUTION"]
            if (self.drawdown() < r["defensive_recovery_drawdown"] and n >= r["defensive_shadow_n"]
                    and mean > r["defensive_recovery_mean"] and wins >= r["defensive_recovery_win_rate"]
                    and cash_fraction >= mode["reserve_fraction"] and exposure <= mode["max_open_exposure"]):
                self.mode, self.exits = "CAUTION", 0
        elif self.mode == "CAUTION":
            n, mean, wins = self.stats(max(r["caution_shadow_n"], 10))
            if (self.drawdown() < r["caution_recovery_drawdown"] and n >= r["caution_shadow_n"]
                    and mean > r["caution_recovery_mean"] and wins >= r["caution_recovery_win_rate"]
                    and cash_fraction >= .20 and exposure <= .70):
                self.mode, self.exits = "NORMAL", 0

    def update_stage(self, after_exit=False):
        floors = [0, 2000, 4000]
        while self.stage > 0 and self.equity() < floors[self.stage] - 1e-9:
            self.stage -= 1
            self.promotions = 0
        if self.mode != "NORMAL":
            self.promotions = 0
            return
        if self.stage < 2:
            if self.equity() >= floors[self.stage + 1] - 1e-9:
                self.promotions += int(after_exit)
                if self.promotions >= self.cfg["confirmation_exits"]:
                    self.stage += 1
                    self.promotions = 0
            else:
                self.promotions = 0

    def mark(self):
        self.peak = max(self.peak, self.equity())
        self.max_dd = max(self.max_dd, self.drawdown())

    def reserve(self, now, intended):
        cfg, m, e = self.cfg, self.cfg["modes"][self.mode], self.equity()
        reserve = max(m["reserve_floor_usd"], m["reserve_fraction"] * e)
        self.signals = [t for t in self.signals if t >= now - 3_600_000]
        excess = max(0, len(self.signals) - cfg["dynamic_burst_signal_threshold"])
        if excess:
            reserve += min(cfg["max_dynamic_burst_reserve_fraction"] * e,
                           cfg["dynamic_burst_reserve_fraction"] * e * math.ceil(excess / 3))
        if self.streak > 2:
            reserve += min(cfg["max_losing_streak_reserve_fraction"] * e,
                           cfg["losing_streak_reserve_step"] * e * (self.streak - 2))
        self.candidates.append(intended)
        reserve = max(reserve, min(cfg["recent_candidate_reserve_cap_fraction"] * e,
                                   cfg["recent_candidate_reserve_multiple"] * float(np.median(self.candidates))))
        return min(reserve, .5 * e)

    def buy(self, row: dict, score: dict, allowed: bool):
        now = row["post_time"]
        self.update_stage()
        self.update_mode()
        if not math.isfinite(score["probability"]):
            self.skips["excluded"] += 1
            return
        if score["confidence"] >= self.cfg["base_confidence_threshold"]:
            self.shadow_lots.append(row)
            self.signals.append(now)
        if not allowed:
            self.skips["trader_guard"] += 1
            return
        mode = self.cfg["modes"][self.mode]
        if score["confidence"] < mode["confidence_threshold"]:
            self.skips["confidence"] += 1
            return
        effective = max([20, 15, 10][self.stage], mode["ratio_floor"])
        reserve = self.reserve(now, row["source_amount"] / effective)
        self.recent = [(t, p) for t, p in self.recent if t >= now - 900_000]
        reasons = set()
        for ratio in [effective] + sorted(r for r in RATIO_LADDER if r > effective):
            size = row["source_amount"] / ratio
            cost = size + fee(size)
            burden = 2 * fee(size) / size + 2 * self.slippage
            conservative = score["predicted_roi"] - mode["uncertainty_multiplier"] * self.uncertainty
            if burden > mode["fee_burden_trigger"] and conservative < mode["fee_margin_multiple"] * burden:
                reasons.add("fee")
                continue
            e = self.equity() - fee(size)
            if (e <= 0 or cost > self.cash + 1e-9 or self.cash - cost < reserve - 1e-9
                    or (self.open + size) / e > mode["max_open_exposure"] + 1e-12):
                reasons.add("liquidity")
                continue
            if ((self.trader[row["trader"]] + size) / e > mode["max_trader_exposure"] + 1e-12
                    or (self.token[row["coin"]] + size) / e > mode["max_token_exposure"] + 1e-12
                    or (sum(p for _, p in self.recent) + size) / e > mode["max_15m_new_exposure"] + 1e-12):
                reasons.add("concentration")
                continue
            self.cash -= cost
            self.open += size
            self.trader[row["trader"]] += size
            self.token[row["coin"]] += size
            # StrategyDatabase.insertPosition uses the source-time plan.createdTime.
            self.positions.append(dict(row=row, principal=size, buy_fee=fee(size), opened=row["post_time"]))
            self.recent.append((row["post_time"], size))
            self.ratios[f"1:{ratio:g}"] += 1
            self.mark()
            return
        self.skips["+".join(sorted(reasons))] += 1

    def _close(self, positions, cap, now, kind):
        if not positions:
            return
        # Main's paper position path applies configured slippage at exit only.
        gross = [0.0 if cap is None else p["principal"] * cap / p["row"]["market_cap"] * (1 - self.slippage)
                 for p in positions]
        total = sum(gross)
        exit_fee = min(total, fee(total))
        self.cash += total - exit_fee
        for p, value in zip(positions, gross):
            row, size = p["row"], p["principal"]
            self.open = max(0, self.open - size)
            self.trader[row["trader"]] -= size
            self.token[row["coin"]] -= size
            self.positions.remove(p)
            self.ledger.append(dict(opportunity_id=row["opportunity_id"], trader=row["trader"],
                                    net=value - (exit_fee * value / total if total else 0) - size - p["buy_fee"],
                                    outcome=kind, entry_time=row["captured_time"], exit_time=now))
        if kind != "SOURCE_SELL":
            self.exits += 1
        self.update_mode()
        self.update_stage(after_exit=True)
        self.mark()

    def sell(self, row):
        pair = row["trader"].lower(), row["coin"].lower()
        eligible = [r for r in self.shadow_lots if (r["trader"].lower(), r["coin"].lower()) == pair
                    and r["post_time"] < row["post_time"]]
        for r in eligible:
            roi = row["market_cap"] / r["market_cap"] - 1
            self.shadow.append(roi)
            self.streak = self.streak + 1 if roi <= 0 else 0
            self.exits += 1
            self.update_mode()
            self.shadow_lots.remove(r)
        positions = [p for p in self.positions if (p["row"]["trader"].lower(), p["row"]["coin"].lower()) == pair]
        self._close(positions, row["market_cap"], row["captured_time"], "SOURCE_SELL")

    def expire(self, now, quote):
        groups = defaultdict(list)
        for p in self.positions:
            if p["opened"] + HOLD_MS < now:
                groups[p["row"]["trader"], p["row"]["coin"]].append(p)
        for (trader, token), positions in groups.items():
            cap = quote(trader, token, now)
            self._close(positions, cap, now, "TIMEOUT_MARK" if cap is not None else "TIMEOUT_ASSUMED_LOSS")

    def report(self):
        assert not self.positions and self.open < 1e-6
        assert abs(self.cash - 1000 - sum(r["net"] for r in self.ledger)) < 1e-6
        wins = sum(r["net"] > 0 for r in self.ledger)
        return dict(copied_buys=len(self.ledger), net_wins=wins,
                    net_win_rate=wins / len(self.ledger) if self.ledger else None,
                    final_cash=self.cash, net_pnl=self.cash - 1000, max_drawdown_at_cost=self.max_dd,
                    ratios=dict(self.ratios), skips=dict(self.skips),
                    outcomes=dict(Counter(r["outcome"] for r in self.ledger)))
