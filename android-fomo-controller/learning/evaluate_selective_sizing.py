"""Offline selective net-win review with Android's fee gate and ratio ladder.

This is a per-opportunity sizing replay, not a wallet execution simulation:
cash, concurrent positions, adaptive modes, UI latency, and fills are unknown.
Never use this report as an automatic champion promotion decision.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

from learning.core import build_payload, load_learning_csv, read_payload, score_payload


RATIO_LADDER = (1000, 500, 250, 100, 75, 50, 40, 35, 30, 25, 20, 15, 10)
THRESHOLDS = (0.80, 0.85, 0.90, 0.95)


def fee(value: float, discount: float) -> float:
    return max(0.95 * (1 - discount), 0.005 * (1 - discount) * value) if value > 0 else 0.0


def wilson(wins: int, count: int, z: float = 1.96) -> float:
    if not count:
        return 0.0
    p = wins / count
    return (p + z * z / (2 * count) - z * math.sqrt(
        p * (1 - p) / count + z * z / (4 * count * count)
    )) / (1 + z * z / count)


def size_and_net(source: float, gross_roi: float, predicted_roi: float,
                 uncertainty: float, mode: dict, slippage: float,
                 discount: float, stage_ratio: float = 20.0) -> tuple[float, float, float] | None:
    """Mirror Android's ratio iteration and conditional fee/EV gate (NORMAL mode)."""
    effective = max(stage_ratio, mode["ratio_floor"])
    candidates = [effective] + sorted(r for r in RATIO_LADDER if r > effective)
    conservative = predicted_roi - mode["uncertainty_multiplier"] * uncertainty
    for ratio in candidates:
        principal = source / ratio
        buy_fee = fee(principal, discount)
        burden = 2 * buy_fee / principal + 2 * slippage
        if (burden > mode["fee_burden_trigger"]
                and conservative < mode["fee_margin_multiple"] * burden):
            continue
        proceeds = max(0.0, principal * (1 + gross_roi) * (1 - slippage))
        net = proceeds - fee(proceeds, discount) - principal - buy_fee
        return ratio, principal, net
    return None


def evaluate(frame: pd.DataFrame, scores: pd.DataFrame, threshold: float,
             config: dict, uncertainty: float, discount: float) -> dict:
    mode = config["modes"]["NORMAL"]
    selected = []
    ratios = []
    net_usd = []
    for row, score in zip(frame.itertuples(index=False), scores.itertuples(index=False)):
        if not math.isfinite(score.confidence) or score.confidence < max(threshold, mode["confidence_threshold"]):
            continue
        sized = size_and_net(row.source_buy_usd, row.realized_source_roi,
                             score.predicted_roi, uncertainty, mode,
                             config["slippage_per_side"], discount)
        if sized is None:
            continue
        ratios.append(sized[0])
        net_usd.append(sized[2])
        selected.append(row.lifecycle_group_id)
    count = len(selected)
    wins = sum(v > 0 for v in net_usd)
    # Each first-later-sell lifecycle is one independent outcome. A group wins
    # only when the aggregate of its selected entries covers all their fees.
    group_net: dict[str, float] = {}
    for group, net in zip(selected, net_usd):
        group_net[group] = group_net.get(group, 0.0) + net
    group_wins = sum(v > 0 for v in group_net.values())
    return {
        "selected_rows": count, "selected_lifecycles": len(group_net),
        "coverage": count / len(frame), "net_win_rate": wins / count if count else None,
        "lifecycle_net_win_rate": group_wins / len(group_net) if group_net else None,
        "lifecycle_wilson_lower_95": wilson(group_wins, len(group_net)),
        "net_usd_total_proxy": float(sum(net_usd)),
        "median_selected_ratio": float(np.median(ratios)) if ratios else None,
        "ratio_counts": {f"1:{r:g}": ratios.count(r) for r in sorted(set(ratios))},
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset", type=Path)
    parser.add_argument("--train-before", required=True)
    parser.add_argument("--validation-start", required=True)
    parser.add_argument("--test-start", required=True)
    parser.add_argument("--champion", type=Path, required=True)
    parser.add_argument("--fee-discount", type=float, choices=(0.0, 0.10), default=0.0)
    parser.add_argument("--min-validation-lifecycles", type=int, default=30)
    args = parser.parse_args()
    dates = [int(pd.Timestamp(d, tz="UTC").timestamp() * 1000)
             for d in (args.train_before, args.validation_start, args.test_start)]
    if not dates[0] <= dates[1] < dates[2] or args.min_validation_lifecycles < 1:
        parser.error("Require train-before <= validation-start < test-start and a positive minimum")
    frame = load_learning_csv(args.dataset)
    train = frame.loc[frame.exit_time.lt(dates[0])]
    validation = frame.loc[frame.entry_time.ge(dates[1]) & frame.exit_time.lt(dates[2])]
    test = frame.loc[frame.entry_time.ge(dates[2])]
    if min(map(len, (train, validation, test))) == 0:
        parser.error("Training, validation and test each need resolved rows")
    champion = read_payload(args.champion)
    if "config" not in champion:
        parser.error("Champion must contain the Android policy config")
    challenger = build_payload(train, "offline_selective_review", champion.get("modelId", "none"),
                               adapter_weight=0)
    config = champion["config"]
    val_scores = score_payload(challenger, validation)
    candidates = {str(t): evaluate(validation, val_scores, t, config,
                                    challenger["roiUncertaintyScale"], args.fee_discount)
                  for t in THRESHOLDS}
    eligible = [t for t in THRESHOLDS
                if candidates[str(t)]["selected_lifecycles"] >= args.min_validation_lifecycles]
    # Tie-break toward more selected independent outcomes, then a lower threshold.
    chosen = max(eligible, key=lambda t: (candidates[str(t)]["lifecycle_wilson_lower_95"],
                                          candidates[str(t)]["selected_lifecycles"], -t)) if eligible else None
    report = {
        "label": "first-later-sell market-cap ROI; no actual fills or partial-sale quantities",
        "scope": "per-opportunity NORMAL mode; ratio/fee gate only; no wallet, exposure, reserves, mode changes or execution latency",
        "fee_discount": args.fee_discount,
        "assumed_stage_ratio": "1:20",
        "validation_rows": len(validation), "test_rows": len(test),
        "validation_candidates": candidates, "selected_threshold": chosen,
        "threshold_selection": "maximum lifecycle Wilson lower bound on validation, subject to minimum lifecycles",
    }
    if chosen is not None:
        report["test_challenger"] = evaluate(test, score_payload(challenger, test), chosen,
                                               config, challenger["roiUncertaintyScale"], args.fee_discount)
        report["test_champion_same_threshold"] = evaluate(test, score_payload(champion, test), chosen,
                                                            config, champion["roiUncertaintyScale"], args.fee_discount)
    print(json.dumps(report, sort_keys=True, allow_nan=False))


if __name__ == "__main__":
    main()
