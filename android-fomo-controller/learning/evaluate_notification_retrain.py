"""Train from scratch on earlier notifications and evaluate later held-out trades."""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.metrics import brier_score_loss, roc_auc_score

from learning.core import (
    FEATURES, build_payload, compare_grouped, fit_models, load_learning_csv,
    read_payload, score_payload, write_payload,
)


def _ms(date: str) -> int:
    return int(pd.Timestamp(date, tz="UTC").timestamp() * 1000)


def _wilson_lower(wins: int, count: int, z: float = 1.96) -> float:
    if count == 0:
        return 0.0
    rate = wins / count
    return ((rate + z * z / (2 * count)) - z * math.sqrt(
        rate * (1 - rate) / count + z * z / (4 * count * count)
    )) / (1 + z * z / count)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset", type=Path)
    parser.add_argument("--train-before", required=True, help="UTC date (exclusive)")
    parser.add_argument("--validation-start", required=True, help="UTC date (inclusive)")
    parser.add_argument("--test-start", required=True, help="UTC date (inclusive)")
    parser.add_argument("--champion", type=Path, help="Existing bundled/active forecast artifact")
    parser.add_argument("--output-model", type=Path, help="Save a train-only experimental .json.gz model")
    parser.add_argument("--model-id", default="raw_notifications_train_only")
    args = parser.parse_args()
    train_before, val_start, test_start = map(_ms, [
        args.train_before, args.validation_start, args.test_start,
    ])
    if not train_before <= val_start < test_start:
        parser.error("Expected train-before <= validation-start < test-start")
    frame = load_learning_csv(args.dataset)
    train = frame.loc[frame.exit_time.lt(train_before)]
    validation = frame.loc[frame.entry_time.ge(val_start) & frame.exit_time.lt(test_start)]
    test = frame.loc[frame.entry_time.ge(test_start)]
    if min(map(len, [train, validation, test])) == 0:
        parser.error("The chronological training, validation, and test periods must each have labels")
    classifier, _ = fit_models(train)
    validation_probabilities = classifier.predict_proba(validation[FEATURES])[:, 1]
    # This cutoff is fixed before inspecting test labels or test score ranks.
    probability_cutoff = float(np.quantile(validation_probabilities, 0.90))
    results: dict = {"training_rows": len(train), "training_lifecycles": train.lifecycle_group_id.nunique(),
                     "trained_through_utc": pd.to_datetime(train.exit_time.max(), unit="ms", utc=True).isoformat(),
                     "validation_top_decile_probability_cutoff": probability_cutoff,
                     "label": "gross sell_market_cap / buy_market_cap - 1",
                     "net_proxy": "gross ROI minus two max($0.95,0.5% of source_buy_usd/20) fees"}
    for name, part in [("validation", validation), ("test", test)]:
        probabilities = classifier.predict_proba(part[FEATURES])[:, 1]
        n = max(1, round(0.1 * len(part)))
        selected = np.argsort(-probabilities)[:n]
        gross_wins = int(part.profitable.iloc[selected].sum())
        size = part.source_buy_usd.to_numpy() / 20
        net_roi = part.realized_source_roi.to_numpy() - 2 * np.maximum(0.95, 0.005 * size) / size
        net_wins = int((net_roi[selected] > 0).sum())
        fixed = probabilities >= probability_cutoff
        fixed_count = int(fixed.sum())
        fixed_net_wins = int((net_roi[fixed] > 0).sum())
        results[name] = {
            "rows": len(part), "lifecycles": part.lifecycle_group_id.nunique(),
            "gross_base_win_rate": float(part.profitable.mean()),
            "net_proxy_base_win_rate": float((net_roi > 0).mean()),
            "auc": float(roc_auc_score(part.profitable, probabilities)),
            "brier": float(brier_score_loss(part.profitable, probabilities)),
            "top_decile_count": n, "top_decile_gross_wins": gross_wins,
            "top_decile_gross_win_rate": gross_wins / n,
            "top_decile_net_proxy_wins": net_wins,
            "top_decile_net_proxy_win_rate": net_wins / n,
            "top_decile_net_proxy_wilson_lower_95": _wilson_lower(net_wins, n),
            "fixed_cutoff_count": fixed_count,
            "fixed_cutoff_net_proxy_wins": fixed_net_wins,
            "fixed_cutoff_net_proxy_win_rate": fixed_net_wins / fixed_count if fixed_count else None,
            "fixed_cutoff_net_proxy_wilson_lower_95": _wilson_lower(fixed_net_wins, fixed_count),
        }
    if args.output_model:
        if args.output_model.suffix != ".gz" or not args.output_model.name.endswith(".json.gz"):
            parser.error("--output-model must end in .json.gz")
        experimental = build_payload(train, args.model_id, "none", adapter_weight=0)
        write_payload(experimental, args.output_model)
        results["experimental_model"] = str(args.output_model)
    if args.champion:
        champion = read_payload(args.champion)
        challenger = build_payload(train, "offline_raw_csv_trial", champion.get("modelId", "adaptive_hybrid_v2"),
                                   adapter_weight=0)
        result = compare_grouped(test, score_payload(champion, test), score_payload(challenger, test))
        results["champion_comparison"] = {
            "promote_on_this_holdout": result.promote,
            "mean_lifecycle_utility_delta": result.mean_delta,
            "bootstrap_lower_bound": result.lower_bound,
            "reasons": result.reasons,
            "champion_metrics": result.champion_metrics,
            "challenger_metrics": result.challenger_metrics,
        }
    print(json.dumps(results, sort_keys=True))


if __name__ == "__main__":
    main()
