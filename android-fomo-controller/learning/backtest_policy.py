"""Compare bundled, fresh sell-only, and 24h-policy forecasts on equal outcomes.

No thresholds/hyperparameters are selected on these evaluation periods. The
forward CSV was previously inspected, so this is a retrospective diagnostic,
not an untouched prospective trial or an automatic deployment decision.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd
import numpy as np

from learning.core import build_payload, causal_training, metrics, read_payload, score_payload, sha256
from learning.policy_labels import build_policy_labels, reference_net_roi
from learning.portfolio_replay import paired_day_bootstrap, replay
from learning.raw_notification_csv import import_raw_notifications


def legacy_frame(path: Path) -> pd.DataFrame:
    frame, _ = import_raw_notifications(path, availability_order=True)
    frame = frame.loc[frame.status.eq("RESOLVED")].copy()
    for col in ("entry_time", "exit_time", "realized_source_roi", "profitable"):
        frame[col] = pd.to_numeric(frame[col])
    frame["label_available_time"] = frame.exit_time
    return frame


def evaluate_period(training: pd.DataFrame, legacy: pd.DataFrame, test: pd.DataFrame,
                    bundled: dict, samples: int) -> dict:
    training = causal_training(training, test)
    legacy = causal_training(legacy, test)
    models = {"bundled": bundled,
              "fresh_sell_only": build_payload(legacy, "diagnostic_sell_only", "none", adapter_weight=0),
              "policy_24h": build_payload(training, "diagnostic_policy_24h", "none", adapter_weight=0)}
    scores = {name: score_payload(payload, test) for name, payload in models.items()}
    report = {
        "training_policy_rows": len(training), "training_sell_only_rows": len(legacy),
        "latest_policy_label_available_utc": pd.to_datetime(training.label_available_time.max(), unit="ms", utc=True).isoformat(),
        "earliest_test_buy_utc": pd.to_datetime(test.entry_time.min(), unit="ms", utc=True).isoformat(),
        "test_buys": len(test), "test_outcomes": test.outcome_kind.value_counts().to_dict(),
        "gross_roi_diagnostics": {"median": float(test.realized_source_roi.median()),
                                  "maximum": float(test.realized_source_roi.max()),
                                  "above_100x_gain_rows": int(test.realized_source_roi.gt(100).sum())},
        "base_net_win_rate": float((reference_net_roi(test) > 0).mean()),
        "forecasts": {name: metrics(test, score) for name, score in scores.items()},
        "replays": {},
    }
    for slippage in (0.0, 0.01):
        ledgers = {}
        replay_reports = {}
        for name, payload in models.items():
            replay_reports[name], ledgers[name] = replay(
                test, scores[name], bundled["config"], payload["roiUncertaintyScale"], slippage=slippage)
        replay_reports["policy_vs_bundled"] = paired_day_bootstrap(test, ledgers["bundled"], ledgers["policy_24h"], samples)
        replay_reports["policy_vs_fresh_sell_only"] = paired_day_bootstrap(test, ledgers["fresh_sell_only"], ledgers["policy_24h"], samples)
        report["replays"][f"slippage_per_side_{slippage:g}"] = replay_reports
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("history", type=Path)
    parser.add_argument("forward", type=Path)
    parser.add_argument("--holdout-start", required=True, help="Frozen UTC entry-date split")
    parser.add_argument("--champion", type=Path, default=Path("app/src/main/assets/adaptive_hybrid_v2.json.gz"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--bootstrap-samples", type=int, default=5000)
    args = parser.parse_args()
    history, history_summary = build_policy_labels(args.history)
    forward, forward_summary = build_policy_labels(args.forward)
    legacy = legacy_frame(args.history)
    for frame in (history, forward, legacy):
        frame["log_market_cap"] = np.log1p(frame.entry_market_cap.astype(float))
        frame["log_source_buy_usd"] = np.log1p(frame.source_buy_usd.astype(float))
    cutoff = int(pd.Timestamp(args.holdout_start, tz="UTC").timestamp() * 1000)
    mature = history.loc[history.status.eq("RESOLVED")].copy()
    forward_mature = forward.loc[forward.status.eq("RESOLVED")].copy()
    # Explicit cutoff as well as causal_training: no horizon outcomes from after
    # the declared model-freeze time, even if the first test buy comes later.
    historical_train = mature.loc[mature.label_available_time.lt(cutoff)]
    historical_legacy = legacy.loc[legacy.label_available_time.lt(cutoff)]
    historical_test = mature.loc[mature.entry_time.ge(cutoff)]
    if historical_test.empty or forward_mature.empty:
        parser.error("Both evaluation periods require fully observed buys")
    if history.observation_end_time.max() >= forward.entry_time.min():
        parser.error("History and forward periods overlap; provide separate chronological exports")
    bundled = read_payload(args.champion)
    report = {
        "schema": "copy_causal_24h_backtest_v1",
        "inputs_sha256": {"history": sha256(args.history), "forward": sha256(args.forward), "champion": sha256(args.champion)},
        "holdout_start_utc": args.holdout_start,
        "history_labels": history_summary, "forward_labels": forward_summary,
        "assumptions": [
            "All buys must have a complete 24h observation window, including early sells.",
            "First source sell by deadline; otherwise latest same trader/ticker quote with capture and post age <=5m.",
            "No fresh quote means assumed zero proceeds, not proof of zero market value.",
            "Deadline starts at buy capture; exact Android execution/sweep times unavailable.",
            "Fixed 1:20, NORMAL fee gate/exposure/reserve/burst limits, $1000 initial cash; unknown open values at cost.",
            "Immediate notification-cap fills; no UI failures, adaptive modes, stage promotion or dynamic reserves.",
            "Sell fees charged per simulated lot; Android may aggregate lots, so fees can differ.",
            "Ticker-only identity can match unrelated assets; extreme source returns are retained and reported, not treated as verified fills.",
            "No parameter selection on evaluated periods; latest forward file was already inspected.",
            "Bootstrap resamples calendar-day P&L blocks from fixed ledgers, not whole re-executed paths.",
            "This diagnostic never promotes or replaces a deployed model.",
        ],
        "historical_holdout": evaluate_period(historical_train, historical_legacy, historical_test, bundled, args.bootstrap_samples),
        "latest_forward": evaluate_period(mature, legacy, forward_mature, bundled, args.bootstrap_samples),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + "\n")
    print(json.dumps(report, sort_keys=True, allow_nan=False))


if __name__ == "__main__":
    main()
