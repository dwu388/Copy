"""Compare the bundled strategy with causal OFF/WARN/FILTER trader checks.

This never trains or promotes a model. Quotes and immediate fills are proxies;
Android UI queues and suspension cannot be reconstructed from these exports.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import json
import math
from pathlib import Path
import pandas as pd

from learning.core import read_payload, score_payload
from learning.strategy_replay import Wallet
from learning.trader_guard import History, HOLD_MS, QUOTE_AGE_MS


def read_raw(path):
    frame = pd.read_csv(path, keep_default_na=False, na_values=[""])
    required = {"event_id", "notification_key", "package_name", "post_time", "captured_time",
                "trader", "coin", "market_cap", "source_amount", "action"}
    if required - set(frame):
        raise ValueError(f"Missing raw columns: {sorted(required - set(frame))}")
    if frame.event_id.isna().any() or frame.event_id.duplicated().any():
        raise ValueError("Missing/duplicate event_id")
    for col in ("post_time", "captured_time", "market_cap", "source_amount"):
        frame[col] = pd.to_numeric(frame[col], errors="coerce")
    if not (frame.captured_time.map(math.isfinite) & frame.captured_time.gt(0)).all():
        raise ValueError("Missing/invalid capture timestamps")
    rows = []
    for row in frame.sort_values(["captured_time", "event_id"]).to_dict("records"):
        for key in ("trader", "coin", "notification_key"):
            row[key] = str(row[key]).strip() if pd.notna(row[key]) else ""
        row["captured_time"] = int(row["captured_time"])
        valid_time = math.isfinite(row["post_time"]) and 0 < row["post_time"] <= row["captured_time"]
        row["valid"] = bool(valid_time and row["package_name"] == "family.fomo.app"
                            and row["action"] in {"bought", "sold"} and row["notification_key"]
                            and row["trader"] and row["coin"]
                            and math.isfinite(row["market_cap"]) and row["market_cap"] > 0
                            and math.isfinite(row["source_amount"]) and row["source_amount"] > 0)
        if valid_time:
            row["post_time"] = int(row["post_time"])
        row["opportunity_id"] = f"{row['notification_key']}@{row['post_time']}"
        rows.append(row)
    return rows


class Quotes:
    def __init__(self):
        self.rows = defaultdict(list)

    def add(self, row):
        if row["package_name"] == "family.fomo.app" and row["trader"] and row["coin"]:
            self.rows[row["trader"].lower(), row["coin"].lower()].append(row)

    def get(self, trader, token, now):
        for row in reversed(self.rows[trader.lower(), token.lower()]):
            if row["captured_time"] < now - QUOTE_AGE_MS:
                break
            if row["captured_time"] <= now and now - QUOTE_AGE_MS <= row["post_time"] <= now:
                cap = row["market_cap"]
                return cap if math.isfinite(cap) and cap > 0 else None
        return None


def simulate(rows, payload, scores, start, mode, threshold, warmup=False, slippage=0, max_cap=None,
             min_source=0, strict_margin=False, loss_limit=None, use_forecast=True):
    guard, quotes = History(), Quotes()
    wallet = Wallet(payload["config"], payload["roiUncertaintyScale"], slippage,
                    strict_margin, use_forecast, loss_limit)
    seen, audit, checks = {}, [], Counter()
    end, sweep = rows[-1]["captured_time"], rows[0]["captured_time"] + 180_000

    def expire(time):
        guard.expire(time, quotes.get)
        wallet.expire(time, quotes.get)

    for row in rows:
        now = row["captured_time"]
        while sweep < now:
            expire(sweep)
            sweep += 180_000
        quotes.add(row)  # Incoming quote is durably recorded before evaluation.
        expire(now)
        if sweep == now:
            sweep += 180_000
        if not row["valid"]:
            continue
        fp = (row["action"], row["trader"].lower(), row["coin"].lower(), row["market_cap"], row["source_amount"])
        previous = seen.get(fp)
        if previous is not None and row["post_time"] - previous < 180_000:
            continue
        seen[fp] = row["post_time"]
        if now < start and not warmup:
            continue
        if row["action"] == "sold":
            guard.observe_sell(row["trader"], row["coin"], row["market_cap"], now)
            if now >= start:
                wallet.sell(row)  # No eligibility condition on any sell.
            continue
        assessment = guard.observe_buy(row["opportunity_id"], row["trader"], row["coin"],
                                       row["source_amount"], row["market_cap"], now, mode, threshold)
        if now < start or now + HOLD_MS >= end or (max_cap is not None and row["market_cap"] >= max_cap) or row["source_amount"] < min_source:
            continue
        checks[assessment.status] += 1
        audit.append(dict(opportunity_id=row["opportunity_id"], trader=row["trader"],
                          status=assessment.status, samples=assessment.samples,
                          wins=assessment.wins, allowed=assessment.allowed,
                          captured_time=now, rate=assessment.rate))
        wallet.buy(row, scores[row["event_id"]], assessment.allowed)
    expire(end)
    result = wallet.report()
    result.update(eligible_buys=len(audit), guard_status_counts=dict(checks),
                  guard_episode_count=len(guard.episodes), mode=mode, threshold=threshold,
                  guard_history="same-export warmup" if warmup else "cold start at portfolio start")
    return result, wallet.ledger, audit


def score_buys(rows, payload, start):
    end = rows[-1]["captured_time"]
    buys = [r for r in rows if r["valid"] and r["action"] == "bought"
            and start <= r["captured_time"] < end - HOLD_MS]
    if not buys:
        raise ValueError("No full-horizon buys in the selected evaluation period")
    frame = pd.DataFrame([dict(trader=r["trader"], entry_market_cap=r["market_cap"],
                               source_buy_usd=r["source_amount"]) for r in buys])
    # Raw callbacks can reuse notification key/post time with updated content.
    # Score each callback's own features; never overwrite an earlier prediction.
    return {r["event_id"]: s for r, s in zip(buys, score_payload(payload, frame).to_dict("records"))}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("history", type=Path)
    parser.add_argument("latest", type=Path)
    parser.add_argument("--older-start", default="2026-10-24", help="UTC portfolio start")
    parser.add_argument("--model", type=Path, default=Path("app/src/main/assets/adaptive_hybrid_v2.json.gz"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--max-market-cap", type=float, help="Counterfactual entry scope; not a deployed strategy setting")
    args = parser.parse_args()
    if args.max_market_cap is not None and (not math.isfinite(args.max_market_cap) or args.max_market_cap <= 0):
        parser.error("max-market-cap must be positive and finite")
    payload = read_payload(args.model)
    if "config" not in payload:
        parser.error("Use a bundled artifact containing the policy config")
    old, latest = read_raw(args.history), read_raw(args.latest)
    if not old or not latest or old[-1]["captured_time"] >= latest[0]["captured_time"]:
        parser.error("Provide separate chronological, non-overlapping exports")
    report = dict(schema="trader_guard_backtest_v1", model=payload.get("modelId", payload["version"]),
                  sha256={name: hashlib.sha256(path.read_bytes()).hexdigest()
                          for name, path in [("history", args.history), ("latest", args.latest), ("model", args.model)]},
                  assumptions=[
                      "Each portfolio starts independently at $1000; bundled model and policy unchanged.",
                      "OFF and WARN must have identical copied trades, sizing and cash.",
                      "FILTER uses 20 completed all-market-cap reference episodes and frozen 40%/50% cutoffs.",
                      "Latest starts cold, matching an update without backfill. No history crosses the unobserved export gap.",
                      "Older warmup variant uses only earlier rows within the same export.",
                      "Immediate notification-cap fills, standard fees, no UI latency/reservations/failures.",
                      "Three-minute plus notification/status sweeps approximate Android; suspension is unknown.",
                      "Missing fresh quotes are assumed write-offs, not verified losses; ticker identity is ambiguous.",
                      "Paper slippage matches main: exit haircut and two-side burden in entry gate.",
                      "Only full-24h-observed buys enter portfolios; all observed buys can update guard history.",
                      "Latest data was already inspected; retrospective comparison, not an untouched forward trial."], periods={})
    for name, rows, start in [("older", old, int(pd.Timestamp(args.older_start, tz="UTC").timestamp() * 1000)),
                              ("latest", latest, latest[0]["captured_time"])]:
        scores, period = score_buys(rows, payload, start), {}
        for warmup in ([False, True] if name == "older" else [False]):
            cases = {}
            for slip in (0.0, 0.01):
                results, ledgers = {}, {}
                for mode, threshold in [("OFF", .5), ("WARN", .5), ("FILTER", .5), ("FILTER", .4)]:
                    key = mode if mode != "FILTER" else f"FILTER_{threshold:.0%}"
                    results[key], ledgers[key], _ = simulate(rows, payload, scores, start, mode, threshold,
                                                             warmup, slip, args.max_market_cap)
                assert results["OFF"]["final_cash"] == results["WARN"]["final_cash"]
                assert ledgers["OFF"] == ledgers["WARN"], "Warnings changed trading behavior"
                cases[f"slippage_{slip:g}"] = results
            period["warm" if warmup else "cold"] = cases
        report["periods"][name] = period
        print(f"completed {name}", flush=True)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + "\n")
    print(json.dumps(report, sort_keys=True, allow_nan=False))


if __name__ == "__main__":
    main()
