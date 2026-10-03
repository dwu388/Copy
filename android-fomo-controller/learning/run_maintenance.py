#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from learning.core import (
    build_adapter_payload, build_payload, compare_grouped, load_learning_csv, read_payload, score_payload,
    sha256, write_payload,
)


def atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n")
    os.replace(temporary, path)


def iso_to_ms(value: str) -> int:
    timestamp = pd.Timestamp(value)
    if timestamp.tzinfo is None:
        timestamp = timestamp.tz_localize("UTC")
    return int(timestamp.timestamp() * 1000)


def bootstrap_policy(forecast: dict) -> dict:
    if "config" not in forecast:
        raise ValueError("No existing policy champion and bootstrap forecast has no generation-0 config")
    return {
        "schemaVersion": "copy_policy_v1",
        "policyModelId": f"adaptive_hybrid_v2:{forecast['config']['name']}",
        "parentPolicyModelId": None,
        "trainedThrough": forecast["trainedThrough"],
        "config": forecast["config"],
    }


def deploy(state: Path, forecast_path: Path, policy_path: Path, forecast: dict, policy: dict) -> None:
    staging = state / "deployment" / "staging"
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)
    forecast_target = staging / "forecast.json.gz"
    policy_target = staging / "policy.json"
    shutil.copy2(forecast_path, forecast_target)
    shutil.copy2(policy_path, policy_target)
    manifest = {
        "schemaVersion": "copy_champion_manifest_v1",
        "forecast": {
            "modelId": forecast["modelId"], "file": forecast_target.name,
            "sha256": sha256(forecast_target),
        },
        "policy": {
            "policyModelId": policy["policyModelId"], "file": policy_target.name,
            "sha256": sha256(policy_target),
        },
        "trainedThrough": forecast["trainedThrough"],
    }
    atomic_json(staging / "champion_manifest.json", manifest)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run one protected Copy forecast maintenance cycle")
    parser.add_argument("input", type=Path, help="copy_learning_opportunities.csv from Android")
    parser.add_argument("--state-dir", type=Path, default=Path("local_learning_state"))
    parser.add_argument(
        "--bootstrap-forecast", type=Path,
        default=Path("app/src/main/assets/adaptive_hybrid_v2.json.gz"),
    )
    parser.add_argument("--cohort-groups", type=int, default=40)
    parser.add_argument("--min-train-rows", type=int, default=200)
    parser.add_argument("--bootstrap-samples", type=int, default=2000)
    parser.add_argument("--adapter-days", type=int, default=10)
    parser.add_argument("--adapter-weight", type=float, default=0.30)
    args = parser.parse_args()

    frame = load_learning_csv(args.input)
    state = args.state_dir
    registry_path = state / "promotion_registry.json"
    registry = json.loads(registry_path.read_text()) if registry_path.exists() else {
        "schemaVersion": 1, "forecastCohorts": [], "adapterPromotionsSinceCompaction": 0,
        "lastCompactionAt": None,
    }
    champion_path = state / "models" / "forecast" / "champion.json.gz"
    champion_source = champion_path if champion_path.exists() else args.bootstrap_forecast
    champion = read_payload(champion_source)
    champion_id = champion.get("modelId", champion.get("version", "adaptive_hybrid_v2"))
    watermark = max(
        [iso_to_ms(champion["trainedThrough"])]
        + [int(x["watermarkExitTime"]) for x in registry["forecastCohorts"]]
    )
    fresh = frame.loc[frame["exit_time"].gt(watermark)].copy()
    ordered_groups = (
        fresh.groupby("lifecycle_group_id")["exit_time"].max().sort_values().index.tolist()
    )
    if len(ordered_groups) < args.cohort_groups:
        print(f"no_cycle fresh_lifecycle_groups={len(ordered_groups)} required={args.cohort_groups}")
        return
    cohort_groups = set(ordered_groups[-args.cohort_groups:])
    cohort = fresh.loc[fresh["lifecycle_group_id"].isin(cohort_groups)].copy()
    cohort_start = float(cohort["exit_time"].min())
    train = frame.loc[frame["exit_time"].lt(cohort_start)].copy()
    if len(train) < args.min_train_rows:
        print(f"no_cycle training_rows={len(train)} required={args.min_train_rows}")
        return

    cohort_id = "promotion_" + __import__("hashlib").sha256(
        "\n".join(sorted(cohort_groups)).encode()
    ).hexdigest()[:16]
    if any(x["cohortId"] == cohort_id for x in registry["forecastCohorts"]):
        raise RuntimeError(f"Cohort {cohort_id} was already consumed")
    now = datetime.now(timezone.utc)
    model_id = f"copy_forecast_{now:%Y%m%dT%H%M%S%fZ}"
    record = {
        "cohortId": cohort_id,
        "consumed": True,
        "status": "EVALUATING",
        "consumedAt": now.isoformat(),
        "watermarkExitTime": int(fresh["exit_time"].max()),
        "lifecycleGroups": len(cohort_groups),
        "rows": len(cohort),
        "championModelId": champion_id,
        "challengerModelId": model_id,
    }
    # Reserve before fitting/evaluation. A crash may burn a cohort, but it can
    # never silently make that holdout reusable.
    registry["forecastCohorts"].append(record)
    atomic_json(registry_path, registry)

    last_compaction_ms = iso_to_ms(registry.get("lastCompactionAt") or champion["trainedThrough"])
    compaction_due = (
        registry.get("adapterPromotionsSinceCompaction", 0) >= 8
        or float(train["exit_time"].max()) - last_compaction_ms >= 14 * 86_400_000
    )
    challenger = (
        build_payload(train, model_id, champion_id, args.adapter_days, args.adapter_weight)
        if compaction_due
        else build_adapter_payload(
            train, champion, model_id, champion_id, args.adapter_days, args.adapter_weight,
        )
    )
    challenger_kind = "stable_compaction" if compaction_due else "recent_adapter"
    candidate_path = state / "models" / "forecast" / "candidates" / f"{model_id}.json.gz"
    write_payload(challenger, candidate_path)
    result = compare_grouped(
        cohort, score_payload(champion, cohort), score_payload(challenger, cohort),
        args.bootstrap_samples,
    )
    record.update({
        "status": "PROMOTED" if result.promote else "REJECTED",
        "challengerKind": challenger_kind,
        "promoted": result.promote,
        "lowerBound": result.lower_bound,
        "meanDelta": result.mean_delta,
        "reasons": result.reasons,
        "championMetrics": result.champion_metrics,
        "challengerMetrics": result.challenger_metrics,
    })

    if result.promote:
        champion_path.parent.mkdir(parents=True, exist_ok=True)
        previous = champion_path.parent / "previous.json.gz"
        if champion_path.exists():
            os.replace(champion_path, previous)
        else:
            shutil.copy2(champion_source, previous)
        os.replace(candidate_path, champion_path)
        policy_path = state / "models" / "policy" / "champion.json"
        if policy_path.exists():
            policy = json.loads(policy_path.read_text())
        else:
            policy = bootstrap_policy(read_payload(args.bootstrap_forecast))
            atomic_json(policy_path, policy)
        deploy(state, champion_path, policy_path, challenger, policy)
        if compaction_due:
            registry["adapterPromotionsSinceCompaction"] = 0
            registry["lastCompactionAt"] = challenger["trainedThrough"]
        else:
            registry["adapterPromotionsSinceCompaction"] = registry.get("adapterPromotionsSinceCompaction", 0) + 1
    else:
        rejected = state / "models" / "forecast" / "rejected" / candidate_path.name
        rejected.parent.mkdir(parents=True, exist_ok=True)
        os.replace(candidate_path, rejected)

    atomic_json(registry_path, registry)
    report = state / "reports" / f"{cohort_id}.json"
    atomic_json(report, record)
    print(json.dumps({"cohort": cohort_id, "promoted": result.promote, "report": str(report)}))


if __name__ == "__main__":
    main()
