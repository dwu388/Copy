"""Causal notification features shared by training and the Android replay contract.

No source amounts are interpreted as token quantities. Returns remain market-cap
proxies. Availability is max(post_time, captured_time), not the source post time.
"""
from __future__ import annotations

from collections import defaultdict
import math
from pathlib import Path

import numpy as np
import pandas as pd

NUMERIC_FEATURES = [
    "log_market_cap", "log_source_buy_usd", "log_source_to_cap",
    "log_capture_delay_seconds", "hour_sin", "hour_cos", "weekday_sin", "weekday_cos",
    "token_prior_buys_log", "token_prior_sells_log", "token_traders_log", "token_age_hours_log",
    "token_return_previous", "token_return_1h", "token_buys_1h_log", "token_sells_1h_log",
    "token_net_flow_1h_to_cap", "trader_prior_buys_log", "trader_prior_sells_log",
    "trader_mean_buy_usd_log", "source_vs_trader_mean_log", "pair_prior_buys_log",
    "pair_prior_sells_log", "trader_resolved_log", "trader_prior_2x_rate", "trader_prior_mean_roi",
]


def fee(usd: float, discount: float = 0.0) -> float:
    return max(0.95, 0.005 * usd) * (1.0 - discount)


def net_multiple(gross_multiple: float, principal: float, slippage: float = 0.0,
                 discount: float = 0.0) -> float:
    proceeds = max(0.0, principal * gross_multiple * (1 - slippage) ** 2)
    return max(0.0, proceeds - fee(proceeds, discount)) / (principal + fee(principal, discount))


class FeatureHistory:
    def __init__(self, ratio: float = 10, slippage: float = 0.0):
        self.ratio, self.slippage = ratio, slippage
        self.tokens: dict[str, list[dict]] = defaultdict(list)
        self.traders: dict[str, list[dict]] = defaultdict(list)
        self.pairs: dict[tuple, list[dict]] = defaultdict(list)
        self.outcomes: dict[str, list[dict]] = defaultdict(list)
        self.open: dict[tuple, list[dict]] = defaultdict(list)
        self.seen: dict[tuple, float] = {}

    def features(self, row: dict) -> dict[str, float]:
        now, mc, amount = row["available_time"], row["market_cap"], row["source_amount"]
        # Equal-time callbacks cannot inform one another, even if IDs differ.
        token = [r for r in self.tokens[row["coin"]] if r["available_time"] < now]
        trader = [r for r in self.traders[row["trader"]] if r["available_time"] < now]
        pair = [r for r in self.pairs[row["trader"], row["coin"]] if r["available_time"] < now]
        outcomes = [r for r in self.outcomes[row["trader"]] if r["time"] < now]
        recent = [r for r in token if r["available_time"] >= now - 3_600_000]
        buys = [r for r in trader if r["action"] == "bought"]
        mean_buy = sum(r["source_amount"] for r in buys) / len(buys) if buys else amount
        hour = (now % 86_400_000) / 3_600_000
        weekday = (now // 86_400_000 + 3) % 7  # Monday=0, UTC
        count = lambda rows, action: math.log1p(sum(r["action"] == action for r in rows))
        return dict(zip(NUMERIC_FEATURES, [
            math.log1p(mc), math.log1p(amount), math.log1p(amount / mc),
            math.log1p(max(0, row["captured_time"] - row["post_time"]) / 1000),
            math.sin(2 * math.pi * hour / 24), math.cos(2 * math.pi * hour / 24),
            math.sin(2 * math.pi * weekday / 7), math.cos(2 * math.pi * weekday / 7),
            count(token, "bought"), count(token, "sold"),
            math.log1p(len({r["trader"] for r in token})),
            math.log1p((now - token[0]["available_time"]) / 3_600_000) if token else 0,
            mc / token[-1]["market_cap"] - 1 if token else 0,
            mc / recent[0]["market_cap"] - 1 if recent else 0,
            count(recent, "bought"), count(recent, "sold"),
            sum(r["source_amount"] * (1 if r["action"] == "bought" else -1) for r in recent) / mc,
            count(trader, "bought"), count(trader, "sold"), math.log1p(mean_buy),
            math.log(amount / mean_buy), count(pair, "bought"), count(pair, "sold"),
            math.log1p(len(outcomes)),
            (sum(r["hit"] for r in outcomes) + 1) / (len(outcomes) + 2),
            sum(r["roi"] for r in outcomes) / len(outcomes) if outcomes else 0,
        ]))

    def accept(self, row: dict) -> bool:
        fingerprint = (row["action"], row["trader"], row["coin"], row["market_cap"], row["source_amount"])
        last = self.seen.get(fingerprint)
        if last is not None and row["available_time"] - last < 180_000:
            return False
        self.seen[fingerprint] = row["available_time"]
        return True

    def observe(self, row: dict) -> list[dict]:
        key = row["trader"], row["coin"]
        resolved = []
        if row["action"] == "bought":
            self.open[key].append(row)
        else:
            remaining = []
            for buy in self.open[key]:
                if buy["post_time"] >= row["post_time"] or buy["available_time"] >= row["available_time"]:
                    remaining.append(buy)
                    continue
                roi = row["market_cap"] / buy["market_cap"] - 1
                multiple = net_multiple(1 + roi, buy["source_amount"] / self.ratio, self.slippage)
                self.outcomes[row["trader"]].append(dict(time=row["available_time"], hit=int(multiple >= 2),
                                                         roi=min(10, max(-1, roi))))
                resolved.append(dict(
                    **buy["features"], opportunity_id=buy["opportunity_id"], trader=buy["trader"],
                    token=buy["coin"], entry_market_cap=buy["market_cap"], source_buy_usd=buy["source_amount"],
                    entry_time=buy["available_time"], exit_time=row["available_time"],
                    realized_source_roi=roi, net_return_multiple=multiple, target_2x=int(multiple >= 2),
                    lifecycle_group_id=f'{row["trader"]}|{row["coin"]}|{row["opportunity_id"]}', status="RESOLVED",
                ))
            self.open[key] = remaining
        self.tokens[row["coin"]].append(row)
        self.traders[row["trader"]].append(row)
        self.pairs[key].append(row)
        return resolved


def load_notifications(path: Path) -> tuple[pd.DataFrame, dict]:
    raw = pd.read_csv(path, keep_default_na=False)
    required = {"event_id", "notification_key", "package_name", "post_time", "captured_time",
                "action", "trader", "coin", "market_cap", "source_amount"}
    if missing := required - set(raw):
        raise ValueError(f"Raw notifications missing {sorted(missing)}")
    for name in ["event_id", "post_time", "captured_time", "market_cap", "source_amount"]:
        raw[name] = pd.to_numeric(raw[name], errors="coerce")
    if raw.event_id.isna().any() or raw.event_id.duplicated().any():
        raise ValueError("event_id must be present and unique")
    valid = raw.package_name.eq("family.fomo.app") & raw.action.isin(["bought", "sold"])
    for name in ["trader", "coin", "notification_key"]:
        valid &= raw[name].str.strip().ne("")
    for name in ["post_time", "captured_time", "market_cap", "source_amount"]:
        valid &= np.isfinite(raw[name]) & raw[name].gt(0)
    clean = raw.loc[valid].copy()
    clean["available_time"] = clean[["post_time", "captured_time"]].max(axis=1).astype("int64")
    clean["trader"] = clean.trader.str.strip().str.lower()
    clean["coin"] = clean.coin.str.strip().str.lower()
    clean["opportunity_id"] = clean.notification_key + "@" + clean.post_time.astype("int64").astype(str)
    clean = clean.sort_values(["available_time", "event_id"], kind="stable")
    return clean, dict(source_rows=len(raw), invalid_rows=int((~valid).sum()))


def build_dataset(path: Path, ratio: float = 10, slippage: float = 0.0) -> tuple[pd.DataFrame, dict]:
    if not math.isfinite(ratio) or ratio <= 0 or not 0 <= slippage <= 0.25:
        raise ValueError("ratio must be positive; slippage must be in [0, .25]")
    clean, summary = load_notifications(path)
    history = FeatureHistory(ratio, slippage)
    records, duplicates, orphan_sells = [], 0, 0
    for row in clean.to_dict("records"):
        if not history.accept(row):
            duplicates += 1
            continue
        row["features"] = history.features(row) if row["action"] == "bought" else {}
        resolved = history.observe(row)
        orphan_sells += int(row["action"] == "sold" and not resolved)
        records.extend(resolved)
    frame = pd.DataFrame(records)
    if frame.empty:
        raise ValueError("No resolved buy/sell opportunities")
    summary.update(duplicates_3m=duplicates, orphan_sells=orphan_sells, resolved_buys=len(frame),
                   lifecycle_groups=frame.lifecycle_group_id.nunique(),
                   open_buys=sum(len(rows) for rows in history.open.values()),
                   target_2x_buys=int(frame.target_2x.sum()),
                   first_available_time=int(clean.available_time.min()),
                   last_available_time=int(clean.available_time.max()))
    return frame.sort_values(["entry_time", "opportunity_id"]).reset_index(drop=True), summary
