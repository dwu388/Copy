"""Import the older A:D notification log as causal, Android-compatible labels.

This is a separate source format from the strict K:P trade export. Source clock
times have no timezone in the workbook; treating them as UTC preserves order but
does not establish their real-world timezone.
"""
from __future__ import annotations

import re
from collections import defaultdict
from pathlib import Path

import pandas as pd

TITLE = re.compile(r"^(.*?) at \$([\d,.]+)([kKmMbB]?) MC", re.I)
TRADE = re.compile(r"@([^\s]+)\s+(bought|sold)\s+\$([\d,]+(?:\.\d+)?)", re.I)
SCALE = {"": 1.0, "k": 1e3, "m": 1e6, "b": 1e9}
COLUMNS = ["opportunity_id", "trader", "token", "entry_market_cap", "source_buy_usd",
           "entry_time", "exit_time", "realized_source_roi", "profitable",
           "lifecycle_group_id", "status"]


def import_notification_log(path: Path, sheet: str = "Notifications") -> tuple[pd.DataFrame, dict]:
    raw = pd.read_excel(path, sheet_name=sheet)
    if list(raw.columns) != ["Time", "Type", "Title", "Text"]:
        raise ValueError("Expected the legacy A:D notification log: Time, Type, Title, Text")
    raw = raw.copy()
    raw["source_row"] = range(2, len(raw) + 2)
    raw["time"] = pd.to_datetime(raw["Time"], errors="coerce")
    if raw["time"].isna().any():
        raise ValueError(f"Missing or invalid Time in {raw['time'].isna().sum()} rows")
    raw = raw.sort_values(["time", "source_row"], kind="stable")
    open_buys: dict[tuple[str, str], list[dict]] = defaultdict(list)
    last_seen: dict[tuple, int] = {}
    results: list[dict] = []
    summary = {"source_rows": len(raw), "invalid_trade_rows": 0, "duplicates_3m": 0,
               "orphan_sells": 0, "resolved_buys": 0, "open_buys": 0,
               "sell_events": 0}
    for row in raw.itertuples(index=False):
        title = TITLE.search(str(row.Title))
        trade = TRADE.search(str(row.Text))
        if not title or not trade or trade[2].lower() != str(row.Type).lower():
            summary["invalid_trade_rows"] += 1
            continue
        trader, action = trade[1], trade[2].lower()
        token = title[1].strip()
        try:
            market_cap = float(title[2].replace(",", "")) * SCALE[title[3].lower()]
            amount = float(trade[3].replace(",", ""))
        except ValueError:
            summary["invalid_trade_rows"] += 1
            continue
        if not token or not (0 < market_cap < float("inf") and 0 < amount < float("inf")):
            summary["invalid_trade_rows"] += 1
            continue
        # Same fingerprint and three-minute window used by AdaptiveHybridEngine.
        key = (trader.casefold(), token.casefold())
        fingerprint = (action, *key, market_cap, amount)
        time_ms = int(row.time.tz_localize("UTC").timestamp() * 1000)
        seen = last_seen.get(fingerprint)
        if seen is not None and time_ms - seen < 180_000:
            summary["duplicates_3m"] += 1
            continue
        last_seen[fingerprint] = time_ms
        if action == "bought":
            open_buys[key].append({
                "opportunity_id": f"legacy_row_{row.source_row}", "trader": trader,
                "token": token, "entry_market_cap": market_cap,
                "source_buy_usd": amount, "entry_time": time_ms,
                "exit_time": pd.NA, "realized_source_roi": pd.NA,
                "profitable": pd.NA, "lifecycle_group_id": pd.NA, "status": "OPEN",
            })
            continue
        summary["sell_events"] += 1
        buys = open_buys[key]
        eligible = [buy for buy in buys if buy["entry_time"] < time_ms]
        open_buys[key] = [buy for buy in buys if buy["entry_time"] >= time_ms]
        if not eligible:
            summary["orphan_sells"] += 1
            continue
        for buy in eligible:
            roi = market_cap / buy["entry_market_cap"] - 1.0
            buy.update(exit_time=time_ms, realized_source_roi=roi,
                       profitable=int(roi > 0), lifecycle_group_id=f"legacy_sell_{row.source_row}",
                       status="RESOLVED")
            results.append(buy)
        summary["resolved_buys"] += len(eligible)
    for buys in open_buys.values():
        results.extend(buys)
        summary["open_buys"] += len(buys)
    frame = pd.DataFrame(results, columns=COLUMNS)
    # The production validator must accept every resolved row we constructed.
    return frame, summary
