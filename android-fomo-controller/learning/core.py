from __future__ import annotations

import gzip
import hashlib
import json
import math
import copy
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import HistGradientBoostingClassifier, HistGradientBoostingRegressor
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler

FEATURES = ["trader", "log_market_cap", "log_source_buy_usd"]
REQUIRED = {
    "opportunity_id", "trader", "token", "entry_market_cap", "source_buy_usd",
    "entry_time", "exit_time", "realized_source_roi", "profitable",
    "lifecycle_group_id", "status",
}


def load_learning_csv(path: Path) -> pd.DataFrame:
    frame = pd.read_csv(path)
    missing = REQUIRED - set(frame.columns)
    if missing:
        raise ValueError(f"Learning export is missing columns: {sorted(missing)}")
    frame = frame.loc[frame["status"].eq("RESOLVED")].copy()
    numeric = ["entry_market_cap", "source_buy_usd", "entry_time", "exit_time", "realized_source_roi"]
    for column in numeric:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    valid = (
        frame["opportunity_id"].notna()
        & frame["lifecycle_group_id"].notna()
        & frame["trader"].notna()
        & frame["token"].notna()
        & frame["entry_market_cap"].gt(0)
        & frame["source_buy_usd"].gt(0)
        & frame["exit_time"].gt(frame["entry_time"])
        & np.isfinite(frame["realized_source_roi"])
    )
    frame = frame.loc[valid].drop_duplicates("opportunity_id", keep="last")
    frame["profitable"] = frame["realized_source_roi"].gt(0).astype(int)
    frame["log_market_cap"] = np.log1p(frame["entry_market_cap"])
    frame["log_source_buy_usd"] = np.log1p(frame["source_buy_usd"])
    return frame.sort_values(["exit_time", "entry_time", "opportunity_id"]).reset_index(drop=True)


def _preprocessor() -> ColumnTransformer:
    return ColumnTransformer(
        [
            ("trader", OneHotEncoder(handle_unknown="ignore", sparse_output=False), ["trader"]),
            ("numeric", StandardScaler(), ["log_market_cap", "log_source_buy_usd"]),
        ],
        verbose_feature_names_out=False,
    )


def fit_models(frame: pd.DataFrame, max_iter: int = 250) -> tuple[Pipeline, Pipeline]:
    if len(frame) < 50 or frame["profitable"].nunique() < 2:
        raise ValueError("Forecast fitting requires at least 50 resolved rows and both outcome classes")
    classifier = Pipeline([
        ("prep", _preprocessor()),
        ("model", HistGradientBoostingClassifier(
            max_iter=max_iter, learning_rate=0.05, max_leaf_nodes=15,
            min_samples_leaf=15, l2_regularization=1.0, random_state=388,
        )),
    ])
    regressor = Pipeline([
        ("prep", _preprocessor()),
        ("model", HistGradientBoostingRegressor(
            max_iter=max_iter, learning_rate=0.05, max_leaf_nodes=15,
            min_samples_leaf=15, l2_regularization=1.0, loss="absolute_error", random_state=388,
        )),
    ])
    classifier.fit(frame[FEATURES], frame["profitable"])
    regressor.fit(frame[FEATURES], frame["realized_source_roi"])
    return classifier, regressor


def export_pipeline(pipeline: Pipeline) -> dict[str, Any]:
    prep = pipeline.named_steps["prep"]
    model = pipeline.named_steps["model"]
    categories = prep.named_transformers_["trader"].categories_[0].tolist()
    scaler = prep.named_transformers_["numeric"]
    trees: list[list[list[float | int]]] = []
    for iteration in model._predictors:
        nodes = []
        for node in iteration[0].nodes:
            nodes.append([
                float(node["value"]), int(node["feature_idx"]), float(node["num_threshold"]),
                int(node["missing_go_to_left"]), int(node["left"]), int(node["right"]),
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


def _predict_ensemble(bundle: dict[str, Any], trader: str, market_cap: float, source_usd: float) -> float:
    categories = bundle["traderCategories"]
    category_index = {value: i for i, value in enumerate(categories)}
    active = category_index.get(trader)
    numeric = [math.log1p(market_cap), math.log1p(source_usd)]
    numeric = [(numeric[i] - bundle["numericMean"][i]) / bundle["numericScale"][i] for i in range(2)]
    raw = float(bundle["baseline"])
    for tree in bundle["trees"]:
        index = 0
        while not tree[index][6]:
            node = tree[index]
            feature = int(node[1])
            if feature < len(categories):
                value = 1.0 if feature == active else 0.0
            else:
                value = numeric[feature - len(categories)]
            index = int(node[4] if value <= node[2] else node[5])
        raw += float(tree[index][0])
    return raw


def _sigmoid(raw: float) -> float:
    if raw >= 0:
        return 1.0 / (1.0 + math.exp(-raw))
    value = math.exp(raw)
    return value / (1.0 + value)


def score_payload(payload: dict[str, Any], frame: pd.DataFrame) -> pd.DataFrame:
    reference = np.asarray(payload["confidenceReference"], dtype=float)
    excluded = {x.lower() for x in payload.get("excludedTraders", [])}
    rows = []
    for row in frame.itertuples(index=False):
        if str(row.trader).lower() in excluded:
            rows.append((np.nan, np.nan, 0.0))
            continue
        stable_logit = _predict_ensemble(payload["classifier"], row.trader, row.entry_market_cap, row.source_buy_usd)
        stable_p = _sigmoid(stable_logit)
        stable_roi = _predict_ensemble(payload["regressor"], row.trader, row.entry_market_cap, row.source_buy_usd)
        adapter = payload.get("recentAdapter")
        if adapter:
            adapter_logit = _predict_ensemble(adapter["classifier"], row.trader, row.entry_market_cap, row.source_buy_usd)
            adapter_p = _sigmoid(adapter_logit)
            probability = (1 - adapter["probabilityWeight"]) * stable_p + adapter["probabilityWeight"] * adapter_p
            adapter_roi = _predict_ensemble(adapter["regressor"], row.trader, row.entry_market_cap, row.source_buy_usd)
            roi = (1 - adapter["roiWeight"]) * stable_roi + adapter["roiWeight"] * adapter_roi
        else:
            probability, roi = stable_p, stable_roi
        confidence = float(np.searchsorted(reference, probability, side="right") / max(1, len(reference)))
        rows.append((probability, roi, confidence))
    return pd.DataFrame(rows, columns=["probability", "predicted_roi", "confidence"], index=frame.index)


def build_payload(
    train: pd.DataFrame,
    model_id: str,
    parent_model_id: str,
    adapter_days: int = 10,
    adapter_weight: float = 0.30,
) -> dict[str, Any]:
    if not 0 <= adapter_weight <= 0.35:
        raise ValueError("adapter_weight must be within the Android safety cap [0, 0.35]")
    classifier, regressor = fit_models(train)
    probabilities = classifier.predict_proba(train[FEATURES])[:, 1]
    roi_prediction = regressor.predict(train[FEATURES])
    payload: dict[str, Any] = {
        "version": "copy_recursive_forecast_v1",
        "schemaVersion": "adaptive_hybrid_hgb_v1",
        "modelId": model_id,
        "parentModelId": parent_model_id,
        "trainedThrough": datetime.fromtimestamp(float(train["exit_time"].max()) / 1000, timezone.utc).isoformat(),
        "featureContract": ["trader", "log1p(entry_market_cap)", "log1p(source_buy_usd)"],
        "matchingRule": "first later SELL with same normalized trader and token",
        "excludedTraders": [],
        "roiUncertaintyScale": float(np.median(np.abs(train["realized_source_roi"] - roi_prediction))),
        "confidenceReference": [float(x) for x in np.sort(probabilities)],
        "classifier": export_pipeline(classifier),
        "regressor": export_pipeline(regressor),
    }
    cutoff = float(train["exit_time"].max()) - adapter_days * 86_400_000
    recent = train.loc[train["exit_time"].ge(cutoff)]
    if adapter_weight > 0 and len(recent) >= 100 and recent["profitable"].nunique() == 2:
        recent_classifier, recent_regressor = fit_models(recent, max_iter=125)
        payload["recentAdapter"] = {
            "lookbackDays": adapter_days,
            "probabilityWeight": adapter_weight,
            "roiWeight": adapter_weight,
            "classifier": export_pipeline(recent_classifier),
            "regressor": export_pipeline(recent_regressor),
        }
        provisional = score_payload(payload, train)
        valid = provisional["probability"].notna()
        payload["confidenceReference"] = [
            float(x) for x in np.sort(provisional.loc[valid, "probability"])
        ]
        payload["roiUncertaintyScale"] = float(np.median(np.abs(
            train.loc[valid, "realized_source_roi"] - provisional.loc[valid, "predicted_roi"]
        )))
    fixture_pool = train.loc[~train["trader"].str.lower().isin(
        {value.lower() for value in payload.get("excludedTraders", [])}
    )]
    fixtures_source = fixture_pool.iloc[
        np.linspace(0, len(fixture_pool) - 1, min(7, len(fixture_pool)), dtype=int)
    ]
    fixtures = score_payload(payload | {"fixtures": []}, fixtures_source)
    payload["fixtures"] = [
        {
            "trader": row.trader,
            "marketCap": float(row.entry_market_cap),
            "sourceBuyUsd": float(row.source_buy_usd),
            "probability": float(fixtures.loc[index, "probability"]),
            "predictedRoi": float(fixtures.loc[index, "predicted_roi"]),
            "confidenceRank": float(fixtures.loc[index, "confidence"]),
        }
        for index, row in fixtures_source.iterrows()
    ]
    return payload


def build_adapter_payload(
    train: pd.DataFrame,
    champion: dict[str, Any],
    model_id: str,
    parent_model_id: str,
    adapter_days: int = 10,
    adapter_weight: float = 0.30,
) -> dict[str, Any]:
    """Keep the stable trees frozen and learn only a capped recent-regime adapter."""
    if not 0 < adapter_weight <= 0.35:
        raise ValueError("adapter_weight must be within the Android safety cap (0, 0.35]")
    cutoff = float(train["exit_time"].max()) - adapter_days * 86_400_000
    recent = train.loc[train["exit_time"].ge(cutoff)]
    if len(recent) < 100 or recent["profitable"].nunique() < 2:
        raise ValueError("Recent adapter requires 100 rows and both outcome classes")
    recent_classifier, recent_regressor = fit_models(recent, max_iter=125)
    payload = copy.deepcopy(champion)
    payload.pop("config", None)
    payload.update({
        "version": "copy_recursive_forecast_v1",
        "schemaVersion": "adaptive_hybrid_hgb_v1",
        "modelId": model_id,
        "parentModelId": parent_model_id,
        "trainedThrough": datetime.fromtimestamp(float(train["exit_time"].max()) / 1000, timezone.utc).isoformat(),
        "recentAdapter": {
            "lookbackDays": adapter_days,
            "probabilityWeight": adapter_weight,
            "roiWeight": adapter_weight,
            "classifier": export_pipeline(recent_classifier),
            "regressor": export_pipeline(recent_regressor),
        },
    })
    provisional = score_payload(payload, train)
    payload["confidenceReference"] = [float(x) for x in np.sort(provisional["probability"].dropna())]
    payload["roiUncertaintyScale"] = float(np.median(np.abs(
        train.loc[provisional["predicted_roi"].notna(), "realized_source_roi"]
        - provisional.loc[provisional["predicted_roi"].notna(), "predicted_roi"]
    )))
    fixture_pool = train.loc[~train["trader"].str.lower().isin(
        {value.lower() for value in payload.get("excludedTraders", [])}
    )]
    fixtures_source = fixture_pool.iloc[
        np.linspace(0, len(fixture_pool) - 1, min(7, len(fixture_pool)), dtype=int)
    ]
    fixtures = score_payload(payload, fixtures_source)
    payload["fixtures"] = [
        {
            "trader": row.trader,
            "marketCap": float(row.entry_market_cap),
            "sourceBuyUsd": float(row.source_buy_usd),
            "probability": float(fixtures.loc[index, "probability"]),
            "predictedRoi": float(fixtures.loc[index, "predicted_roi"]),
            "confidenceRank": float(fixtures.loc[index, "confidence"]),
        }
        for index, row in fixtures_source.iterrows()
    ]
    return payload


def read_payload(path: Path) -> dict[str, Any]:
    if path.suffix == ".gz":
        with gzip.open(path, "rt", encoding="utf-8") as source:
            return json.load(source)
    return json.loads(path.read_text(encoding="utf-8"))


def write_payload(payload: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(payload, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()
    if path.suffix == ".gz":
        with path.open("wb") as raw:
            with gzip.GzipFile(filename="", mode="wb", fileobj=raw, compresslevel=9, mtime=0) as target:
                target.write(encoded)
    else:
        path.write_bytes(encoded)


def metrics(frame: pd.DataFrame, scores: pd.DataFrame, threshold: float = 0.80) -> dict[str, float | None]:
    valid = scores["probability"].notna()
    y = frame.loc[valid, "profitable"].to_numpy(float)
    actual_roi = frame.loc[valid, "realized_source_roi"].to_numpy(float)
    p = np.clip(scores.loc[valid, "probability"].to_numpy(float), 1e-12, 1 - 1e-12)
    predicted_roi = scores.loc[valid, "predicted_roi"].to_numpy(float)
    selected = scores.loc[valid, "confidence"].to_numpy(float) >= threshold
    top10 = scores.loc[valid, "confidence"].to_numpy(float) >= 0.90
    if "source_buy_usd" in frame:
        assumed_size = frame.loc[valid, "source_buy_usd"].to_numpy(float) / 20.0
        net_roi = actual_roi - 2 * np.maximum(0.95, 0.005 * assumed_size) / assumed_size
    else:
        net_roi = actual_roi
    bins = np.minimum((p * 10).astype(int), 9)
    calibration_error = sum(
        np.mean(bins == index) * abs(np.mean(p[bins == index]) - np.mean(y[bins == index]))
        for index in np.unique(bins)
    )
    return {
        "brier": float(np.mean((p - y) ** 2)),
        "log_loss": float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p))),
        "roi_mae": float(np.mean(np.abs(predicted_roi - actual_roi))),
        "calibration_ece": float(calibration_error),
        "top20_net_roi": float(np.mean(net_roi[selected])) if selected.any() else None,
        "top20_win_rate": float(np.mean(y[selected])) if selected.any() else None,
        "top10_net_roi": float(np.mean(net_roi[top10])) if top10.any() else None,
        "coverage": float(np.mean(selected)),
    }


@dataclass(frozen=True)
class PromotionResult:
    promote: bool
    lower_bound: float
    mean_delta: float
    champion_metrics: dict[str, float | None]
    challenger_metrics: dict[str, float | None]
    reasons: list[str]


def compare_grouped(
    frame: pd.DataFrame,
    champion: pd.DataFrame,
    challenger: pd.DataFrame,
    bootstrap_samples: int = 2000,
    seed: int = 388,
) -> PromotionResult:
    columns = ["lifecycle_group_id", "realized_source_roi"]
    columns += [column for column in ("source_buy_usd", "exit_time", "trader") if column in frame]
    work = frame[columns].copy()
    economic_roi = work["realized_source_roi"]
    if "source_buy_usd" in work:
        assumed_size = work["source_buy_usd"] / 20.0
        economic_roi = economic_roi - 2 * np.maximum(0.95, 0.005 * assumed_size) / assumed_size
    work["champion_utility"] = np.where(champion["confidence"].ge(0.80), economic_roi, 0.0)
    work["challenger_utility"] = np.where(challenger["confidence"].ge(0.80), economic_roi, 0.0)
    work["utility_delta"] = work["challenger_utility"] - work["champion_utility"]
    grouped = work.groupby("lifecycle_group_id")[["champion_utility", "challenger_utility"]].mean()
    delta = (grouped["challenger_utility"] - grouped["champion_utility"]).to_numpy()
    if len(delta) < 20:
        raise ValueError("Promotion requires at least 20 independent lifecycle groups")
    rng = np.random.default_rng(seed)
    draws = rng.choice(delta, size=(bootstrap_samples, len(delta)), replace=True).mean(axis=1)
    lower = float(np.quantile(draws, 0.05))
    mean_delta = float(delta.mean())
    champion_metrics = metrics(frame, champion)
    challenger_metrics = metrics(frame, challenger)
    reasons = []
    if lower <= 0:
        reasons.append("paired lifecycle bootstrap lower bound is not positive")
    if mean_delta < 0.002:
        reasons.append("economic improvement is below 0.2 percentage points per lifecycle")
    if challenger_metrics["brier"] > champion_metrics["brier"] + 0.01:
        reasons.append("Brier score materially regressed")
    if challenger_metrics["calibration_ece"] > champion_metrics["calibration_ece"] + 0.03:
        reasons.append("probability calibration materially regressed")
    if challenger_metrics["roi_mae"] > champion_metrics["roi_mae"] + 0.05:
        reasons.append("ROI MAE materially regressed")
    if challenger_metrics["coverage"] < 0.10:
        reasons.append("challenger coverage fell below 10%")
    if "exit_time" in work:
        windows = np.array_split(work.sort_values("exit_time").index.to_numpy(), 4)
        window_deltas = [float(work.loc[index, "utility_delta"].mean()) for index in windows if len(index)]
        challenger_metrics["worst_time_window_delta"] = min(window_deltas)
        if min(window_deltas) < -0.02:
            reasons.append("a chronological evaluation window materially regressed")
    if "trader" in work:
        trader_delta = work.groupby("trader")["utility_delta"].agg(["mean", "size"])
        stable_traders = trader_delta.loc[trader_delta["size"].ge(5), "mean"]
        if not stable_traders.empty:
            challenger_metrics["worst_trader_delta"] = float(stable_traders.min())
            if stable_traders.min() < -0.05:
                reasons.append("a sufficiently represented trader materially regressed")
    return PromotionResult(not reasons, lower, mean_delta, champion_metrics, challenger_metrics, reasons)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
