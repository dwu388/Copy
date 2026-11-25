#!/usr/bin/env python3
from __future__ import annotations

import argparse
import copy
import json
import os
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from learning.core import causal_training, read_payload
from learning.policy_labels import LABEL_CONTRACT, reference_net_roi


def fee(value: np.ndarray) -> np.ndarray:
    return np.maximum(0.95, 0.005 * value)


def utility(frame: pd.DataFrame, threshold: float, ratio: float) -> pd.Series:
    selected = frame["oos_confidence"].ge(threshold).to_numpy()
    size = frame["source_buy_usd"].to_numpy(float) / ratio
    net = size * reference_net_roi(frame, ratio)
    return pd.Series(np.where(selected, net / 1_000.0, 0.0), index=frame.index)


def grouped_delta(frame: pd.DataFrame, candidate: pd.Series, baseline: pd.Series, samples: int) -> tuple[float, float]:
    work = pd.DataFrame({
        "group": frame["lifecycle_group_id"], "delta": candidate - baseline,
    }).groupby("group")["delta"].mean().to_numpy()
    rng = np.random.default_rng(388)
    draws = rng.choice(work, size=(samples, len(work)), replace=True).mean(axis=1)
    return float(work.mean()), float(np.quantile(draws, 0.05))


def atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n")
    os.replace(temporary, path)


def main() -> None:
    parser = argparse.ArgumentParser(description="Train and independently promote a policy from OOS predictions")
    parser.add_argument("oos_predictions", type=Path)
    parser.add_argument("--state-dir", type=Path, default=Path("local_learning_state"))
    parser.add_argument("--bootstrap-forecast", type=Path, default=Path("app/src/main/assets/adaptive_hybrid_v2.json.gz"))
    parser.add_argument("--holdout-groups", type=int, default=30)
    parser.add_argument("--bootstrap-samples", type=int, default=2000)
    args = parser.parse_args()
    frame = pd.read_csv(args.oos_predictions).sort_values(["exit_time", "entry_time"])
    if "label_contract" not in frame or not frame.label_contract.eq(LABEL_CONTRACT).all():
        raise ValueError("Policy training requires 24-hour-policy cross-fit predictions")
    required = {"oos_confidence", "oos_predicted_roi", "realized_source_roi", "source_buy_usd", "lifecycle_group_id", "entry_time", "label_available_time"}
    if missing := required - set(frame.columns):
        raise ValueError(f"Cross-fit data is missing {sorted(missing)}")

    policy_path = args.state_dir / "models" / "policy" / "champion.json"
    if policy_path.exists():
        champion = json.loads(policy_path.read_text())
    else:
        forecast = read_payload(args.bootstrap_forecast)
        champion = {
            "schemaVersion": "copy_policy_v1",
            "policyModelId": f"adaptive_hybrid_v2:{forecast['config']['name']}",
            "parentPolicyModelId": None,
            "trainedThrough": forecast["trainedThrough"],
            "config": forecast["config"],
        }
    groups = frame.groupby("lifecycle_group_id")["entry_time"].min().sort_values().index.tolist()
    if len(groups) <= args.holdout_groups + 20:
        raise ValueError("Policy promotion needs training groups plus an untouched holdout")
    holdout_groups = set(groups[-args.holdout_groups:])
    holdout = frame.loc[frame["lifecycle_group_id"].isin(holdout_groups)]
    training = causal_training(frame, holdout)
    if training.lifecycle_group_id.nunique() < 20:
        raise ValueError("Not enough policy groups whose labels predate holdout buys")
    normal = champion["config"]["modes"]["NORMAL"]
    base_threshold = float(normal["confidence_threshold"])
    base_ratio = max(20.0, float(normal["ratio_floor"]))
    candidates = []
    for threshold in sorted({base_threshold, 0.80, 0.83, 0.85, 0.88, 0.90}):
        for ratio in sorted({base_ratio, 20.0, 25.0, 30.0, 40.0}):
            score = utility(training, threshold, ratio).mean()
            candidates.append((score, threshold, ratio))
    _, threshold, ratio = max(candidates)

    now = datetime.now(timezone.utc)
    policy_id = f"copy_policy_{now:%Y%m%dT%H%M%S%fZ}"
    cohort_id = "policy_" + __import__("hashlib").sha256("\n".join(sorted(holdout_groups)).encode()).hexdigest()[:16]
    registry_path = args.state_dir / "policy_promotion_registry.json"
    registry = json.loads(registry_path.read_text()) if registry_path.exists() else {"schemaVersion": 1, "policyCohorts": []}
    if any(item["cohortId"] == cohort_id for item in registry["policyCohorts"]):
        raise RuntimeError(f"Policy cohort {cohort_id} was already consumed")
    record = {
        "cohortId": cohort_id, "consumed": True, "status": "EVALUATING",
        "consumedAt": now.isoformat(), "championPolicyId": champion["policyModelId"],
        "challengerPolicyId": policy_id, "lifecycleGroups": len(holdout_groups),
        "selectedConfidence": threshold, "selectedRatio": ratio,
    }
    registry["policyCohorts"].append(record)
    atomic_json(registry_path, registry)

    candidate_holdout = utility(holdout, threshold, ratio)
    baseline_holdout = utility(holdout, base_threshold, base_ratio)
    mean_delta, lower = grouped_delta(holdout, candidate_holdout, baseline_holdout, args.bootstrap_samples)
    # Beating a losing baseline is insufficient: require positive absolute utility.
    absolute_mean, absolute_lower = grouped_delta(
        holdout, candidate_holdout, pd.Series(0.0, index=holdout.index), args.bootstrap_samples)
    promote = lower > 0 and mean_delta >= 0.001 and absolute_lower > 0

    challenger = copy.deepcopy(champion)
    challenger.update({
        "schemaVersion": "copy_policy_v1", "policyModelId": policy_id,
        "parentPolicyModelId": champion["policyModelId"],
        "trainedThrough": datetime.fromtimestamp(float(training["label_available_time"].max()) / 1000, timezone.utc).isoformat(),
    })
    scale = ratio / base_ratio
    for mode in challenger["config"]["modes"].values():
        mode["confidence_threshold"] = min(0.98, max(float(mode["confidence_threshold"]), threshold))
        mode["ratio_floor"] = max(float(mode["ratio_floor"]), float(mode["ratio_floor"]) * scale)
    challenger["config"]["name"] = f"recursive-{policy_id}"

    record.update({
        "status": "PROMOTED" if promote else "REJECTED", "promoted": promote,
        "meanDeltaPer1000": mean_delta, "lowerBoundPer1000": lower,
        "absoluteMeanPer1000": absolute_mean, "absoluteLowerBoundPer1000": absolute_lower,
    })
    if promote:
        previous = policy_path.parent / "previous.json"
        if policy_path.exists():
            os.replace(policy_path, previous)
        else:
            atomic_json(previous, champion)
        atomic_json(policy_path, challenger)
    else:
        atomic_json(args.state_dir / "models" / "policy" / "rejected" / f"{policy_id}.json", challenger)
    atomic_json(registry_path, registry)
    atomic_json(args.state_dir / "reports" / f"{cohort_id}.json", record)
    print(json.dumps(record))


if __name__ == "__main__":
    main()
