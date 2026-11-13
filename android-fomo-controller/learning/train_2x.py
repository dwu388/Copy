"""Train the notification HGB strategy for a net >=2x first-sell return proxy.

Example: python -m learning.train_2x fomo_buy_sell_notifications.csv --output-dir local_learning_state/net2x
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd
import sklearn
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import HistGradientBoostingClassifier, HistGradientBoostingRegressor
from sklearn.metrics import average_precision_score, brier_score_loss, roc_auc_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder

from learning.core import read_payload, write_payload
from learning.notification_features import NUMERIC_FEATURES, build_dataset, net_multiple
from learning.run_maintenance import atomic_json, deploy

SCHEMA = "copy_notification_hgb_2x_v1"
FEATURES = ["trader", "token", *NUMERIC_FEATURES]


def fit(frame: pd.DataFrame, iterations: int = 200, numeric_features: list | None = None,
        categorical_features: list | None = None):
    if len(frame) < 100 or frame.target_2x.nunique() != 2:
        raise ValueError("Need 100 resolved rows and both 2x outcome classes")
    numeric_features = NUMERIC_FEATURES if numeric_features is None else numeric_features
    categorical_features = ["trader", "token"] if categorical_features is None else categorical_features
    # Bound token identity complexity. Unseen/rare tickers use all-zero encoding;
    # their causal activity/momentum features still remain available.
    categories = [sorted(frame[name].unique()) if name == "trader" else
                  sorted(frame[name].value_counts().head(64).index) for name in categorical_features]
    def pipeline(model):
        prep = ColumnTransformer([
            ("categorical", OneHotEncoder(categories=categories, handle_unknown="ignore", sparse_output=False),
             categorical_features),
            ("numeric", "passthrough", numeric_features),
        ], verbose_feature_names_out=False)
        return Pipeline([("prep", prep), ("model", model)])
    params = dict(max_iter=iterations, learning_rate=0.05, max_leaf_nodes=15,
                  min_samples_leaf=25, l2_regularization=3.0, random_state=388,
                  early_stopping=False)  # no hidden random validation split
    classifier = pipeline(HistGradientBoostingClassifier(**params))
    regressor = pipeline(HistGradientBoostingRegressor(**params, loss="absolute_error"))
    # One sell episode has total weight one, even if it resolves many entries.
    weights = 1 / frame.groupby("lifecycle_group_id").opportunity_id.transform("size").to_numpy()
    classifier.fit(frame[FEATURES], frame.target_2x, model__sample_weight=weights)
    regressor.fit(frame[FEATURES], frame.realized_source_roi, model__sample_weight=weights)
    return classifier, regressor


def export(model: Pipeline) -> dict:
    prep, fitted = model.named_steps["prep"], model.named_steps["model"]
    return dict(
        categoricalFeatures=[dict(name=name, categories=values.tolist()) for name, values in zip(
            prep.transformers_[0][2], prep.named_transformers_["categorical"].categories_)],
        numericFeatures=list(prep.transformers_[1][2]), baseline=float(fitted._baseline_prediction[0, 0]),
        trees=[[[float(n["value"]), int(n["feature_idx"]), float(n["num_threshold"]),
                 int(n["missing_go_to_left"]), int(n["left"]), int(n["right"]), int(n["is_leaf"])]
                for n in iteration[0].nodes] for iteration in fitted._predictors],
    )


def predict_tree(bundle: dict, row: dict) -> float:
    values = []
    for feature in bundle["categoricalFeatures"]:
        value = str(row[feature["name"]]).strip().lower()
        values.extend(float(value == cat) for cat in feature["categories"])
    values.extend(float(row[name]) for name in bundle["numericFeatures"])
    raw = bundle["baseline"]
    for tree in bundle["trees"]:
        index = 0
        while not tree[index][6]:
            n = tree[index]
            value = values[n[1]]
            left = bool(n[3]) if math.isnan(value) else value <= n[2]
            index = n[4] if left else n[5]
        raw += tree[index][0]
    return raw


def predict_payload(payload: dict, row: dict) -> tuple[float, float]:
    raw = predict_tree(payload["classifier"], row)
    probability = 1 / (1 + math.exp(-raw)) if raw >= 0 else math.exp(raw) / (1 + math.exp(raw))
    return probability, predict_tree(payload["regressor"], row)


def split(frame: pd.DataFrame, train_before: int, test_start: int):
    if train_before >= test_start:
        raise ValueError("train-before must precede test-start")
    train = frame.loc[frame.exit_time.lt(train_before)].copy()
    validation = frame.loc[frame.entry_time.ge(train_before) & frame.exit_time.lt(test_start)].copy()
    test = frame.loc[frame.entry_time.ge(test_start)].copy()
    if min(map(len, [train, validation, test])) == 0:
        raise ValueError("Training, validation and test periods must all have resolved labels")
    if any(set(a.lifecycle_group_id) & set(b.lifecycle_group_id)
           for a, b in [(train, validation), (train, test), (validation, test)]):
        raise ValueError("A sell lifecycle crosses chronological cohorts")
    return train, validation, test


def summary(part: pd.DataFrame, p: np.ndarray, roi: np.ndarray, cutoff: float,
            ratio: float, slippage: float) -> dict:
    y = part.target_2x.to_numpy()
    predicted_multiple = np.array([net_multiple(1 + r, amount / ratio, slippage)
                                   for r, amount in zip(roi, part.source_buy_usd)])
    probability_selected = p >= cutoff
    selected = probability_selected & (predicted_multiple >= 2)
    # Equal lifecycle weighting for rate estimates, including selected subsets.
    def rate(mask):
        rows = part.loc[mask]
        return float(rows.groupby("lifecycle_group_id").target_2x.mean().mean()) if len(rows) else None
    # Bootstrap sell lifecycles, not correlated buy rows.
    grouped = part.loc[selected].groupby("lifecycle_group_id").target_2x.mean().to_numpy()
    interval = None
    if len(grouped):
        rng = np.random.default_rng(388)
        draws = rng.choice(grouped, size=(2000, len(grouped)), replace=True).mean(axis=1)
        interval = [float(x) for x in np.quantile(draws, [.025, .975])]
    return dict(rows=len(part), lifecycle_groups=part.lifecycle_group_id.nunique(),
                base_2x_rate=float(y.mean()), lifecycle_base_2x_rate=rate(np.ones(len(part), dtype=bool)),
                average_precision=float(average_precision_score(y, p)) if y.sum() else None,
                roc_auc=float(roc_auc_score(y, p)) if len(set(y)) == 2 else None,
                brier=float(brier_score_loss(y, p)),
                probability_gate_count=int(probability_selected.sum()),
                probability_gate_2x_rate=rate(probability_selected),
                selected_count=int(selected.sum()), selected_lifecycles=len(grouped),
                selected_2x_rate=rate(selected), selected_2x_rate_bootstrap_95=interval,
                coverage=float(selected.mean()),
                selected_mean_net_multiple=float(part.net_return_multiple.loc[selected].mean()) if selected.any() else None)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("csv", type=Path)
    parser.add_argument("--output-dir", type=Path, default=Path("local_learning_state/net2x"))
    parser.add_argument("--train-before", default="2026-10-27", help="Exclusive UTC label cutoff")
    parser.add_argument("--test-start", default="2026-11-03", help="Untouched UTC entry cutoff")
    parser.add_argument("--ratio", type=float, default=10)
    parser.add_argument("--slippage", type=float, default=0.0, help="Fraction per side")
    parser.add_argument("--bootstrap-forecast", type=Path, default=Path("app/src/main/assets/adaptive_hybrid_v2.json.gz"))
    parser.add_argument("--stage", action="store_true", help="Also create a private Android staging directory; does not install it")
    args = parser.parse_args()
    frame, source_summary = build_dataset(args.csv, args.ratio, args.slippage)
    ms = lambda date: int(pd.Timestamp(date, tz="UTC").timestamp() * 1000)
    train, validation, test = split(frame, ms(args.train_before), ms(args.test_start))
    classifier, regressor = fit(train)
    vp = classifier.predict_proba(validation[FEATURES])[:, 1]
    # Fixed before test inspection. A percentile is not an absolute probability.
    cutoff = max(0.5, float(np.quantile(vp, .90)))
    digest = hashlib.sha256(args.csv.read_bytes()).hexdigest()
    training_contract = dict(schema=SCHEMA, source=digest, train_before=args.train_before,
                             test_start=args.test_start, ratio=args.ratio, slippage=args.slippage,
                             features=FEATURES, iterations=200, seed=388, sklearn=sklearn.__version__)
    model_key = hashlib.sha256(json.dumps(training_contract, sort_keys=True).encode()).hexdigest()[:12]
    payload = dict(
        schemaVersion=SCHEMA, version="notification_net_2x_v1", modelId=f"net2x_{model_key}",
        parentModelId="adaptive_hybrid_v2", sourceSha256=digest,
        trainingContract=training_contract,
        trainedThrough=pd.to_datetime(train.exit_time.max(), unit="ms", utc=True).isoformat(),
        featureContract=FEATURES, featureHistoryContract="notification_history_v1",
        matchingRule="first available later SELL with same normalized trader and ticker; proxy only",
        target=dict(name="net_return_multiple_at_first_source_sell", minimumNetMultiple=2.0,
                    copyRatio=args.ratio, slippagePerSide=args.slippage, feeDiscount=0.0,
                    probabilityThreshold=cutoff, minimumPredictedNetMultiple=2.0),
        config=copy.deepcopy(read_payload(args.bootstrap_forecast)["config"]),
        excludedTraders=[], confidenceReference=sorted(float(x) for x in vp),
        classifier=export(classifier), regressor=export(regressor),
        roiUncertaintyScale=float(np.median(np.abs(train.realized_source_roi - regressor.predict(train[FEATURES])))),
    )
    payload["config"]["name"] = "net2x"
    for mode in payload["config"]["modes"].values():
        mode["ratio_floor"] = max(args.ratio, mode["ratio_floor"])
    payload["config"]["slippage_per_side"] = args.slippage
    # Compare every serialized tree against sklearn on varied and unseen rows.
    fixtures = []
    fixture_rows = frame.iloc[np.linspace(0, len(frame) - 1, 12, dtype=int)].copy()
    unknown = fixture_rows.iloc[[0]].copy()
    unknown["trader"], unknown["token"] = "unseen-trader", "unseen-token"
    fixture_rows = pd.concat([fixture_rows, unknown], ignore_index=True)
    expected_p = classifier.predict_proba(fixture_rows[FEATURES])[:, 1]
    expected_r = regressor.predict(fixture_rows[FEATURES])
    for i, row in enumerate(fixture_rows.to_dict("records")):
        p, roi = predict_payload(payload, row)
        if not np.allclose([p, roi], [expected_p[i], expected_r[i]], atol=1e-12, rtol=0):
            raise ValueError("Serialized model differs from sklearn")
        fixtures.append(dict(trader=row["trader"], token=row["token"], marketCap=row["entry_market_cap"],
                             sourceBuyUsd=row["source_buy_usd"], features={k: row[k] for k in NUMERIC_FEATURES},
                             probability=p, predictedRoi=roi,
                             confidenceRank=float(np.searchsorted(payload["confidenceReference"], p, side="right") / len(vp))))
    payload["fixtures"] = fixtures
    results = dict(source=source_summary, source_sha256=digest,
                   model_id=payload["modelId"], training_contract=training_contract,
                   label="net proceeds / (principal + entry fee) >= 2 at first later same-trader/ticker sell",
                   limitation="Market-cap return proxy, not observed fills, quantities, or realized cash gains",
                   train_before_utc=args.train_before, test_start_utc=args.test_start,
                   training_rows=len(train), training_lifecycles=train.lifecycle_group_id.nunique(),
                   training_2x_rate=float(train.target_2x.mean()), probability_cutoff=cutoff,
                   numeric_features=NUMERIC_FEATURES, categorical_features=["trader", "token"],
                   unclosed_buys_excluded=True, refit_on_holdout=False)
    for name, part in [("validation", validation), ("test", test)]:
        results[name] = summary(part, classifier.predict_proba(part[FEATURES])[:, 1],
                                regressor.predict(part[FEATURES]), cutoff, args.ratio, args.slippage)
    # An identical train-only three-input baseline isolates the new feature value.
    baseline, baseline_reg = fit(train, numeric_features=["log_market_cap", "log_source_buy_usd"],
                                 categorical_features=["trader"])
    bp = baseline.predict_proba(validation[FEATURES])[:, 1]
    results["three_input_baseline_test"] = summary(
        test, baseline.predict_proba(test[FEATURES])[:, 1], baseline_reg.predict(test[FEATURES]),
        max(.5, float(np.quantile(bp, .9))), args.ratio, args.slippage)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    write_payload(payload, args.output_dir / "notification_net_2x.json.gz")
    if args.stage:
        policy = dict(schemaVersion="copy_policy_v1", policyModelId=f"net2x_policy_{model_key}",
                      parentPolicyModelId=None, trainedThrough=payload["trainedThrough"], config=payload["config"])
        policy_path = args.output_dir / "policy.json"
        atomic_json(policy_path, policy)
        deploy(args.output_dir, args.output_dir / "notification_net_2x.json.gz", policy_path, payload, policy)
    (args.output_dir / "evaluation.json").write_text(json.dumps(results, indent=2, allow_nan=False) + "\n")
    print(json.dumps(results, allow_nan=False))


if __name__ == "__main__":
    main()
