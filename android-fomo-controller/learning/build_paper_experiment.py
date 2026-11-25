"""Build a labeled PAPER-ONLY challenger with genuinely held-out calibration.

The trees are frozen before the calibration entry window. No later retraining
of those trees is performed after fitting the calibration map.
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from learning.core import build_payload, load_learning_csv, score_payload, read_payload, write_payload, sha256
from learning.policy_labels import build_policy_labels


def build(raw: Path, calibration_start: str, output: Path, bootstrap: Path, as_of_ms: int | None = None):
    labels, summary = build_policy_labels(raw, as_of_ms)
    # Reuse the same validated policy loader used by the maintenance tools.
    from tempfile import TemporaryDirectory
    with TemporaryDirectory() as temp:
        path = Path(temp) / 'labels.csv'
        labels.to_csv(path, index=False)
        frame = load_learning_csv(path)
    split = int(pd.Timestamp(calibration_start, tz='UTC').timestamp() * 1000)
    train = frame[frame.label_available_time < split].copy()
    calibration = frame[frame.entry_time >= split].copy()
    if len(calibration) < 100 or calibration.profitable.nunique() < 2:
        raise ValueError('Calibration needs at least 100 later mature buys and both classes')
    if train.label_available_time.max() >= calibration.entry_time.min():
        raise ValueError('Training outcomes overlap calibration decisions')
    payload = build_payload(train, 'paper_corrected_24h_calibrated_v1', 'adaptive_hybrid_v2', adapter_weight=0)
    raw_scores = score_payload(payload, calibration)
    p = np.clip(raw_scores.probability.to_numpy(), 1e-12, 1-1e-12)
    x = np.log(p/(1-p)).reshape(-1, 1)
    calibrator = LogisticRegression(C=1, max_iter=1000, random_state=388).fit(x, calibration.profitable)
    slope, intercept = float(calibrator.coef_[0,0]), float(calibrator.intercept_[0])
    if not (0 <= slope <= 10 and -20 <= intercept <= 20):
        raise ValueError('Calibration outside Android contract; do not package')
    payload['baseTrainedThrough'] = payload['trainedThrough']
    payload['trainedThrough'] = pd.to_datetime(calibration.label_available_time.max(),unit='ms',utc=True).isoformat()
    payload['probabilityCalibration'] = dict(schemaVersion='platt_oos_v1', slope=slope, intercept=intercept)
    calibrated = score_payload(payload, calibration)
    payload['confidenceReference'] = sorted(calibrated.probability.tolist())
    payload['confidenceReferenceSource'] = 'held-out chronological calibration predictions, not in-sample training ranks'
    payload['paperOnly'] = True
    payload['config'] = read_payload(bootstrap)['config']
    pool = calibration.iloc[np.linspace(0,len(calibration)-1,7,dtype=int)]
    fixture_scores = score_payload(payload,pool)
    payload['fixtures'] = [dict(trader=row.trader,marketCap=float(row.entry_market_cap),
                               sourceBuyUsd=float(row.source_buy_usd),probability=float(fixture_scores.loc[i,'probability']),
                               predictedRoi=float(fixture_scores.loc[i,'predicted_roi']),confidenceRank=float(fixture_scores.loc[i,'confidence']))
                           for i,row in pool.iterrows()]
    payload['experimentProvenance'] = dict(rawSha256=sha256(raw), trainingRows=len(train),
        calibrationRows=len(calibration), calibrationStartUtc=calibration_start, observationEndMs=summary['as_of_ms'],
        status='PAPER_ONLY_NOT_PROMOTED', scope='previously inspected data; no validated live edge',
        calibrationMethod='fixed trees plus held-out logistic calibration; calibration scores are not an untouched performance test')
    write_payload(payload,output)
    return dict(modelId=payload['modelId'],sha256=sha256(output),trainingRows=len(train),calibrationRows=len(calibration),
                baseTrainedThrough=payload['baseTrainedThrough'],trainedThrough=payload['trainedThrough'],
                calibrationSlope=slope,calibrationIntercept=intercept,labelSummary=summary)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('raw',type=Path);p.add_argument('--calibration-start',default='2026-11-01')
    p.add_argument('--output',type=Path,default=Path('app/src/main/assets/corrected_paper_v1.json.gz'))
    p.add_argument('--bootstrap',type=Path,default=Path('app/src/main/assets/adaptive_hybrid_v2.json.gz'))
    a=p.parse_args();print(json.dumps(build(a.raw,a.calibration_start,a.output,a.bootstrap),sort_keys=True))
if __name__=='__main__':main()
