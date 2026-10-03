#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

from learning.core import FEATURES, fit_models, load_learning_csv


def main() -> None:
    parser = argparse.ArgumentParser(description="Create rolling-origin out-of-sample forecast predictions")
    parser.add_argument("dataset", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--folds", type=int, default=4)
    parser.add_argument("--minimum-train-groups", type=int, default=40)
    args = parser.parse_args()
    frame = load_learning_csv(args.dataset)
    groups = frame.groupby("lifecycle_group_id")["exit_time"].max().sort_values().index.to_numpy()
    if len(groups) < args.minimum_train_groups + args.folds:
        raise ValueError("Not enough lifecycle groups for rolling-origin cross-fitting")
    validation_groups = groups[args.minimum_train_groups:]
    blocks = np.array_split(validation_groups, args.folds)
    outputs = []
    for fold, block in enumerate(blocks):
        if not len(block):
            continue
        first_exit = frame.loc[frame["lifecycle_group_id"].eq(block[0]), "exit_time"].max()
        train = frame.loc[frame["exit_time"].lt(first_exit)]
        validation = frame.loc[frame["lifecycle_group_id"].isin(set(block))].copy()
        classifier, regressor = fit_models(train)
        validation["oos_probability"] = classifier.predict_proba(validation[FEATURES])[:, 1]
        validation["oos_predicted_roi"] = regressor.predict(validation[FEATURES])
        reference = np.sort(classifier.predict_proba(train[FEATURES])[:, 1])
        validation["oos_confidence"] = np.searchsorted(
            reference, validation["oos_probability"], side="right"
        ) / len(reference)
        validation["crossfit_fold"] = fold
        outputs.append(validation)
    result = pd.concat(outputs).sort_values(["exit_time", "entry_time"])
    args.output.parent.mkdir(parents=True, exist_ok=True)
    result.to_csv(args.output, index=False)
    print(f"oos_rows={len(result)} lifecycle_groups={result.lifecycle_group_id.nunique()}")


if __name__ == "__main__":
    main()
