"""Create a public, synthetic bootstrap that can never authorize a BUY.

No private notification data or trained parameters are used. A separately
trained candidate must be staged privately to obtain data-dependent scores.
Run from android-fomo-controller: python -m tools.create_net2x_bootstrap
"""
from pathlib import Path
import copy
import math

from learning.core import read_payload, write_payload
from learning.notification_features import NUMERIC_FEATURES
from learning.train_2x import SCHEMA


def main():
    config = copy.deepcopy(read_payload(Path("app/src/main/assets/adaptive_hybrid_v2.json.gz"))["config"])
    config["name"] = "net2x"
    for mode in config["modes"].values():
        mode["ratio_floor"] = max(10, mode["ratio_floor"])
    features = {name: 0.0 for name in NUMERIC_FEATURES}
    features.update(log_market_cap=math.log1p(100), log_source_buy_usd=math.log1p(100),
                    log_source_to_cap=math.log1p(1))
    ensemble = dict(categoricalFeatures=[dict(name="trader", categories=["synthetic"]),
                                        dict(name="token", categories=["synthetic"])],
                    numericFeatures=NUMERIC_FEATURES, trees=[])
    payload = dict(
        version="notification_net_2x_untrained", schemaVersion=SCHEMA,
        modelId="net2x_untrained_bootstrap", parentModelId=None, bootstrapOnly=True,
        trainedThrough="1970-01-01T00:00:00+00:00",
        featureHistoryContract="notification_history_v1", featureContract=["trader", "token", *NUMERIC_FEATURES],
        matchingRule="first available later SELL with same normalized trader and ticker; proxy only",
        target=dict(name="net_return_multiple_at_first_source_sell", minimumNetMultiple=2.0,
                    copyRatio=10.0, slippagePerSide=0.0, feeDiscount=0.0, probabilityThreshold=1.0,
                    minimumPredictedNetMultiple=2.0),
        config=config, excludedTraders=[], confidenceReference=[1.0], roiUncertaintyScale=1.0,
        classifier=ensemble | dict(baseline=-100.0), regressor=ensemble | dict(baseline=-1.0),
        fixtures=[dict(trader=who, token=who, marketCap=100.0, sourceBuyUsd=100.0, features=features,
                       probability=math.exp(-100) / (1 + math.exp(-100)), predictedRoi=-1.0, confidenceRank=0.0)
                  for who in ["synthetic", "unseen"]],
    )
    write_payload(payload, Path("app/src/main/assets/notification_net_2x.json"))


if __name__ == "__main__":
    main()
