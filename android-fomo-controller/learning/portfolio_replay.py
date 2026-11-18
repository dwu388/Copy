"""Deterministic event replay of notified entries and policy exits.

Immediate fills at notification market caps are assumptions. This intentionally
uses fixed 1:20 sizing and NORMAL risk limits for a controlled model comparison;
it does not simulate Android UI queues, adaptive modes, or stage promotion.
"""
from __future__ import annotations

import heapq

import numpy as np
import pandas as pd

from learning.evaluate_selective_sizing import fee, size_and_net


def replay(frame: pd.DataFrame, scores: pd.DataFrame, config: dict, uncertainty: float,
           threshold: float = 0.80, initial_cash: float = 1000.0,
           discount: float = 0.0, slippage: float = 0.0) -> tuple[dict, pd.DataFrame]:
    cash = initial_cash
    peak = initial_cash
    drawdown = 0.0
    open_cost = 0.0
    by_trader: dict[str, float] = {}
    by_token: dict[str, float] = {}
    exits: list[tuple] = []
    recent: list[tuple[int, float]] = []
    trades = []
    mode = config["modes"]["NORMAL"]
    rejected = {"confidence": 0, "fee": 0, "risk": 0}

    def mark_equity():
        nonlocal peak, drawdown
        equity = cash + open_cost  # Unknown open values carried at cost.
        peak = max(peak, equity)
        drawdown = max(drawdown, (peak - equity) / peak)

    def settle(until):
        nonlocal cash, open_cost
        while exits and exits[0][0] <= until:
            _, _, trader, token, principal, proceeds = heapq.heappop(exits)
            cash += proceeds
            open_cost = max(0.0, open_cost - principal)
            by_trader[trader] -= principal
            by_token[token] -= principal
            mark_equity()

    ordered = frame.join(scores.add_prefix("score_")).sort_values(["entry_time", "opportunity_id"])
    for row in ordered.itertuples(index=False):
        settle(row.entry_time)
        if not np.isfinite(row.score_probability) or row.score_confidence < max(threshold, mode["confidence_threshold"]):
            rejected["confidence"] += 1
            continue
        sized = size_and_net(row.source_buy_usd, row.realized_source_roi, row.score_predicted_roi,
                             uncertainty, mode, slippage, discount)
        if sized is None:
            rejected["fee"] += 1
            continue
        _, size, net = sized
        entry_fee = fee(size, discount)
        equity = cash + open_cost - entry_fee
        trader, token = row.trader.lower(), row.token.lower()
        recent = [(t, p) for t, p in recent if t >= row.entry_time - 900_000]
        reserve = max(mode["reserve_floor_usd"], mode["reserve_fraction"] * (cash + open_cost))
        if (equity <= 0 or cash - size - entry_fee < reserve
                or (open_cost + size) / equity > mode["max_open_exposure"]
                or (by_trader.get(trader, 0) + size) / equity > mode["max_trader_exposure"]
                or (by_token.get(token, 0) + size) / equity > mode["max_token_exposure"]
                or (sum(p for _, p in recent) + size) / equity > mode["max_15m_new_exposure"]):
            rejected["risk"] += 1
            continue
        cash -= size + entry_fee
        open_cost += size
        by_trader[trader] = by_trader.get(trader, 0) + size
        by_token[token] = by_token.get(token, 0) + size
        recent.append((row.entry_time, size))
        proceeds = size + entry_fee + net
        heapq.heappush(exits, (row.exit_time, len(trades), trader, token, size, proceeds))
        trades.append(dict(opportunity_id=row.opportunity_id, entry_time=row.entry_time,
                           exit_time=row.exit_time, principal=size, net_usd=net,
                           lifecycle_group_id=row.lifecycle_group_id, outcome_kind=row.outcome_kind,
                           trader=row.trader))
        mark_equity()
    settle(float("inf"))
    ledger = pd.DataFrame(trades)
    return {
        "eligible_buys": len(frame), "executed_buys": len(trades),
        "net_wins": int((ledger.net_usd > 0).sum()) if len(ledger) else 0,
        "net_win_rate": float((ledger.net_usd > 0).mean()) if len(ledger) else None,
        "initial_cash": initial_cash, "final_cash": cash, "net_pnl": cash - initial_cash,
        "return_fraction": cash / initial_cash - 1, "max_drawdown_at_cost": drawdown,
        "outcomes": ledger.outcome_kind.value_counts().to_dict() if len(ledger) else {},
        "rejected": rejected,
    }, ledger


def paired_day_bootstrap(frame: pd.DataFrame, baseline: pd.DataFrame,
                         challenger: pd.DataFrame, samples: int = 5000) -> dict:
    """Include zero-trade days; paired calendar-day blocks retain intraday dependence.

    Fixed replay ledgers are resampled, not re-executed. This is descriptive
    uncertainty, not proof of an independently repeatable trading edge.
    """
    days = pd.to_datetime(frame.entry_time, unit="ms", utc=True).dt.floor("D")
    index = pd.date_range(days.min(), days.max(), freq="D")
    series = []
    for ledger in (baseline, challenger):
        if ledger.empty:
            series.append(pd.Series(0.0, index=index))
        else:
            day = pd.to_datetime(ledger.entry_time, unit="ms", utc=True).dt.floor("D")
            series.append(ledger.groupby(day).net_usd.sum().reindex(index, fill_value=0.0))
    delta = (series[1] - series[0]).to_numpy()
    rng = np.random.default_rng(388)
    draws = rng.choice(delta, size=(samples, len(delta)), replace=True).mean(axis=1)
    return {"calendar_days": len(delta), "mean_daily_pnl_delta": float(delta.mean()),
            "daily_delta_ci95": [float(x) for x in np.quantile(draws, [0.025, 0.975])],
            "positive_lower_bound": bool(np.quantile(draws, 0.025) > 0)}
