"""Causal 24-hour notification-policy outcomes, distinct from observed source sells.

This is a scheduled-deadline replay, not a reconstruction of Android sweep times
or actual fills. Missing fresh quotes are conservative assumed write-offs.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from learning.raw_notification_csv import import_raw_notifications

HOLD_MS = 86_400_000
QUOTE_AGE_MS = 300_000
LABEL_CONTRACT = "notification_24h_net_reference_v1"


def order_fee(value):
    value = np.asarray(value, dtype=float)
    return np.where(value > 0, np.maximum(0.95, 0.005 * value), 0.0)


def reference_net_roi(frame: pd.DataFrame, ratio: float = 20.0) -> np.ndarray:
    """Gross return minus separate buy/sell fees at a documented reference size."""
    size = frame.source_buy_usd.to_numpy(float) / ratio
    gross = np.maximum(0, size * (1 + frame.realized_source_roi.to_numpy(float)))
    return (gross - order_fee(gross) - size - order_fee(size)) / size


def build_policy_labels(path: Path, as_of_ms: int | None = None) -> tuple[pd.DataFrame, dict]:
    raw = pd.read_csv(path, keep_default_na=False, na_values=[""])
    if "captured_time" not in raw:
        raise ValueError("24-hour labels require captured_time; post time alone cannot establish availability")
    for c in ("post_time", "captured_time", "market_cap"):
        raw[c] = pd.to_numeric(raw[c], errors="coerce")
    if raw.empty or raw.captured_time.isna().any() or raw.captured_time.le(0).any():
        raise ValueError("Missing/invalid capture timestamps")
    snapshot = int(raw.captured_time.max()) if as_of_ms is None else int(as_of_ms)
    if snapshot > raw.captured_time.max():
        raise ValueError("as-of exceeds observed export end; cannot assume future feed coverage")
    # The importer uses availability order for dedupe and rejects future-dated posts.
    opportunities, summary = import_raw_notifications(path, as_of_ms=snapshot, availability_order=True)
    available = raw.loc[raw.captured_time.le(snapshot) & raw.post_time.le(raw.captured_time)].copy()
    # Match the raw quote query: choose latest capture/event, then validate cap.
    quotes = available.loc[available.package_name.eq("family.fomo.app")].copy()
    quotes["pair"] = list(zip(quotes.trader.fillna("").str.strip().str.lower(),
                              quotes.coin.fillna("").str.strip().str.lower()))
    by_pair = {k: g.sort_values(["captured_time", "event_id"])
               for k, g in quotes.groupby("pair", sort=False)}
    records = []
    for row in opportunities.to_dict("records"):
        # Start at decision availability; real paper opening can be seconds later.
        start = int(row["entry_time"])
        deadline = start + HOLD_MS + 1  # Android expires strictly after 24h.
        row["entry_time"] = start
        row["label_contract"] = LABEL_CONTRACT
        row["observation_end_time"] = snapshot
        row["deadline_time"] = deadline
        row["source_exit_time"] = row["exit_time"]
        row["source_realized_roi"] = row["realized_source_roi"]
        row["source_exit_notification_key"] = row["exit_notification_key"]
        # Require the whole horizon even for fast sells, avoiding duration bias
        # at the right edge of every train/validation/test observation window.
        if deadline > snapshot:
            row.update(status="PENDING", outcome_kind="PENDING", label_available_time=np.nan,
                       exit_time=np.nan, realized_source_roi=np.nan, profitable=np.nan,
                       lifecycle_group_id=pd.NA)
            records.append(row)
            continue
        sell_capture = row["exit_time"] if row["status"] == "RESOLVED" else np.inf
        if row["status"] == "RESOLVED" and start < sell_capture <= deadline:
            row.update(outcome_kind="SOURCE_SELL", exit_time=int(sell_capture))
        else:
            pair = (str(row["trader"]).strip().lower(), str(row["token"]).strip().lower())
            candidates = by_pair.get(pair, pd.DataFrame())
            mark = None
            if not candidates.empty:
                q = candidates.loc[candidates.captured_time.between(deadline - QUOTE_AGE_MS, deadline)
                                   & candidates.post_time.between(deadline - QUOTE_AGE_MS, deadline)]
                if not q.empty:
                    value = q.iloc[-1].market_cap
                    if np.isfinite(value) and value > 0:
                        mark = float(value)
            row.update(exit_time=deadline, exit_market_cap=mark if mark is not None else 0.0,
                       realized_source_roi=mark / row["entry_market_cap"] - 1 if mark is not None else -1.0,
                       exit_notification_key=pd.NA,
                       outcome_kind="TIMEOUT_MARK" if mark is not None else "TIMEOUT_ASSUMED_LOSS",
                       # Correlated pair/day write-offs are one bootstrap cluster.
                       lifecycle_group_id=f"timeout|{pair[0]}|{pair[1]}|{deadline // HOLD_MS}")
        row.update(status="RESOLVED", label_available_time=deadline)
        records.append(row)
    result = pd.DataFrame(records)
    if result.empty:
        raise ValueError("No valid buy opportunities")
    mature = result.status.eq("RESOLVED")
    result.loc[mature, "profitable"] = (reference_net_roi(result.loc[mature]) > 0).astype(int)
    summary.update(label_contract=LABEL_CONTRACT, as_of_ms=snapshot,
                   outcomes=result.outcome_kind.value_counts().to_dict(),
                   assumption="scheduled 24h deadline; missing fresh notification quote = assumed full loss")
    return result, summary
