"""Reconstruct Android learning opportunities from its raw notification export.

The raw recorder retains every callback. This importer applies the strategy's
three-minute fingerprint deduplication and first-later-sell price-return rule.
It does not infer realized copied fills from notification dollar amounts.
"""
from __future__ import annotations

from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd


REQUIRED = {
    "event_id", "notification_key", "package_name", "post_time", "action",
    "trader", "coin", "market_cap", "source_amount",
}
COLUMNS = [
    "opportunity_id", "buy_notification_key", "trader", "token",
    "entry_market_cap", "source_buy_usd", "entry_time", "exit_notification_key",
    "exit_market_cap", "exit_time", "realized_source_roi", "profitable",
    "lifecycle_group_id", "status",
]


def import_raw_notifications(path: Path) -> tuple[pd.DataFrame, dict[str, int]]:
    raw = pd.read_csv(path, keep_default_na=False, na_values=[""])
    missing = REQUIRED - set(raw.columns)
    if missing:
        raise ValueError(f"Raw notification export is missing columns: {sorted(missing)}")
    if raw.event_id.isna().any() or raw.event_id.duplicated().any():
        raise ValueError("Raw notification export has missing or duplicate event_id values")
    for column in ("post_time", "market_cap", "source_amount"):
        raw[column] = pd.to_numeric(raw[column], errors="coerce")
    raw = raw.sort_values(["post_time", "event_id"], kind="stable")
    summary = {"source_rows": len(raw), "invalid_trade_rows": 0, "duplicates_3m": 0,
               "resolved_buys": 0, "open_buys": 0, "orphan_sells": 0}
    seen: dict[tuple, float] = {}
    open_buys: dict[tuple[str, str], list[dict]] = defaultdict(list)
    records: list[dict] = []

    for row in raw.itertuples(index=False):
        if (row.package_name != "family.fomo.app" or row.action not in ("bought", "sold")
                or pd.isna(row.trader) or pd.isna(row.coin)
                or pd.isna(row.notification_key)
                or not isinstance(row.trader, str) or not row.trader.strip()
                or not isinstance(row.coin, str) or not row.coin.strip()
                or not np.isfinite(row.post_time) or row.post_time <= 0
                or not np.isfinite(row.market_cap) or row.market_cap <= 0
                or not np.isfinite(row.source_amount) or row.source_amount <= 0):
            summary["invalid_trade_rows"] += 1
            continue
        trader, token = row.trader.strip(), row.coin.strip()
        key = (trader.casefold(), token.casefold())
        fingerprint = (row.action, *key, float(row.market_cap), float(row.source_amount))
        previous = seen.get(fingerprint)
        if previous is not None and row.post_time - previous < 180_000:
            summary["duplicates_3m"] += 1
            continue
        seen[fingerprint] = row.post_time
        time = int(row.post_time)
        if row.action == "bought":
            open_buys[key].append({
                "opportunity_id": f"{row.notification_key}@{time}",
                "buy_notification_key": row.notification_key,
                "trader": trader, "token": token,
                "entry_market_cap": float(row.market_cap),
                "source_buy_usd": float(row.source_amount), "entry_time": time,
                "exit_notification_key": pd.NA, "exit_market_cap": pd.NA,
                "exit_time": pd.NA, "realized_source_roi": pd.NA,
                "profitable": pd.NA, "lifecycle_group_id": pd.NA, "status": "OPEN",
            })
            continue
        buys = open_buys[key]
        eligible = [buy for buy in buys if buy["entry_time"] < time]
        open_buys[key] = [buy for buy in buys if buy["entry_time"] >= time]
        if not eligible:
            summary["orphan_sells"] += 1
            continue
        lifecycle = f"{key[0]}|{key[1]}|{row.notification_key}@{time}"
        for buy in eligible:
            roi = float(row.market_cap) / buy["entry_market_cap"] - 1
            buy.update(exit_notification_key=row.notification_key,
                       exit_market_cap=float(row.market_cap), exit_time=time,
                       realized_source_roi=roi, profitable=int(roi > 0),
                       lifecycle_group_id=lifecycle, status="RESOLVED")
            records.append(buy)
        summary["resolved_buys"] += len(eligible)
    for buys in open_buys.values():
        records.extend(buys)
        summary["open_buys"] += len(buys)
    return pd.DataFrame(records, columns=COLUMNS), summary
