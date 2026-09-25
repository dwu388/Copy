#!/usr/bin/env python3
"""Export the sklearn Adaptive Hybrid v2 bundle for the Android runtime.

The Android app does not ship Python or sklearn. This exporter converts the two
fitted HistGradientBoosting ensembles, preprocessing constants, confidence
reference, and the selected policy configuration into a deterministic gzip JSON
asset that the Kotlin evaluator can reproduce directly.
"""
from __future__ import annotations

import argparse
import gzip
import json
import math
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pandas as pd


def export_pipeline(pipeline: Any) -> dict[str, Any]:
    prep = pipeline.named_steps["prep"]
    model = pipeline.named_steps["model"]
    categories = prep.named_transformers_["trader"].categories_[0].tolist()
    scaler = prep.named_transformers_["numeric"]

    trees: list[list[list[float | int]]] = []
    for iteration in model._predictors:  # sklearn's fitted inference representation
        predictor = iteration[0]
        nodes = []
        for node in predictor.nodes:
            nodes.append([
                float(node["value"]),
                int(node["feature_idx"]),
                float(node["num_threshold"]),
                int(node["missing_go_to_left"]),
                int(node["left"]),
                int(node["right"]),
                int(node["is_leaf"]),
            ])
        trees.append(nodes)

    return {
        "traderCategories": categories,
        "numericMean": [float(x) for x in scaler.mean_],
        "numericScale": [float(x) for x in scaler.scale_],
        "baseline": float(model._baseline_prediction[0, 0]),
        "trees": trees,
    }


def score_fixture(base: dict[str, Any], trader: str, market_cap: float, source_usd: float) -> dict[str, Any]:
    frame = pd.DataFrame([{
        "trader": trader,
        "log_market_cap": math.log1p(market_cap),
        "log_source_buy_usd": math.log1p(source_usd),
    }])
    probability = float(base["classifier"].predict_proba(frame)[0, 1])
    predicted_roi = float(base["regressor"].predict(frame)[0])
    reference = np.sort(np.asarray(base["confidence_reference"], dtype=float))
    confidence = float(np.searchsorted(reference, probability, side="right") / len(reference))
    return {
        "trader": trader,
        "marketCap": market_cap,
        "sourceBuyUsd": source_usd,
        "probability": probability,
        "predictedRoi": predicted_roi,
        "confidenceRank": confidence,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("bundle", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()

    bundle = joblib.load(args.bundle)
    if bundle.get("version") != "adaptive_hybrid_v2":
        raise ValueError(f"Unexpected bundle version: {bundle.get('version')!r}")
    base = bundle["base_hgb"]

    cases = [
        ("cryptojuggler3", 41_500.0, 498.61),
        ("pointfarmcap", 49_999.0, 999.99),
        ("SolSwizzle", 75_000.0, 500.0),
        ("DuckSoldier", 35_700.0, 299.73),
        ("unknown_trader", 25_000.0, 250.0),
        ("0xdetweiler", 1.0, 1.0),
        ("xxxfomoxxx", 32_000_000.0, 3_249.39),
    ]

    payload = {
        "version": bundle["version"],
        "trainedThrough": base["trained_through"],
        "featureContract": base["feature_contract"],
        "matchingRule": base["matching_rule"],
        "excludedTraders": base["excluded_traders"],
        "roiUncertaintyScale": float(bundle["roi_uncertainty_scale"]),
        "confidenceReference": [float(x) for x in np.sort(base["confidence_reference"])],
        "classifier": export_pipeline(base["classifier"]),
        "regressor": export_pipeline(base["regressor"]),
        "config": bundle["adaptive_hybrid_config"],
        "feeModels": bundle["fee_models"],
        "fixtures": [score_fixture(base, *case) for case in cases],
    }

    args.output.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(payload, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")
    with args.output.open("wb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, compresslevel=9, mtime=0) as out:
            out.write(encoded)


if __name__ == "__main__":
    main()
