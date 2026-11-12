import tempfile
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

from learning.notification_features import FeatureHistory, NUMERIC_FEATURES, build_dataset, net_multiple
from learning.train_2x import export, fit, predict_payload, split


def event(i, action="bought", post=None, captured=None, mc=100, amount=1000, trader="A", token="NA"):
    return dict(event_id=i, notification_key=f"k{i}", package_name="family.fomo.app",
                action=action, post_time=post or i * 200_000, captured_time=captured or post or i * 200_000,
                market_cap=mc, source_amount=amount, trader=trader, coin=token)


class Notification2xTest(unittest.TestCase):
    def dataset(self, rows):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "raw.csv"
            pd.DataFrame(rows).to_csv(path, index=False)
            return build_dataset(path)

    def test_net_2x_is_not_positive_return_or_gross_double(self):
        data, stats = self.dataset([
            event(1), event(2, "sold", mc=200), event(3), event(4, "sold", mc=220), event(5)])
        self.assertEqual([0, 1], data.target_2x.tolist())
        self.assertEqual(1, stats["open_buys"])
        self.assertLess(data.net_return_multiple.iloc[0], 2)
        self.assertGreater(data.net_return_multiple.iloc[1], 2)
        self.assertEqual("na", data.token.iloc[0])

    def test_notification_dollars_do_not_define_profit(self):
        data, _ = self.dataset([event(1), event(2, "sold", mc=220, amount=1)])
        self.assertEqual(1, data.target_2x.iloc[0])

    def test_partial_sells_close_first_sell_proxy_once(self):
        data, stats = self.dataset([event(1), event(2, mc=110),
                                    event(3, "sold", mc=150), event(4, "sold", mc=900)])
        self.assertEqual(2, len(data))
        self.assertEqual(1, data.lifecycle_group_id.nunique())
        self.assertEqual(1, stats["orphan_sells"])
        self.assertFalse(data.target_2x.any())

    def test_capture_order_and_strict_future_sell(self):
        # The earlier-posted SELL was unavailable at BUY time, and still cannot
        # resolve a BUY whose source post was later than that SELL.
        data, _ = self.dataset([event(1, post=2000, captured=2000),
                                event(2, "sold", post=1000, captured=3000, mc=900),
                                event(3, "sold", post=4000, captured=4000, mc=220)])
        self.assertEqual(4000, data.exit_time.iloc[0])
        self.assertEqual(0, data.token_prior_sells_log.iloc[0])

    def test_future_changes_do_not_change_entry_features(self):
        before = [event(1), event(2), event(3, "sold", mc=220), event(4)]
        a, _ = self.dataset(before + [event(5, "sold", mc=400)])
        b, _ = self.dataset(before + [event(5, "sold", mc=10)])
        np.testing.assert_array_equal(a[NUMERIC_FEATURES], b[NUMERIC_FEATURES])
        self.assertGreater(a.trader_resolved_log.iloc[2], 0)
        self.assertEqual(0, a.trader_resolved_log.iloc[1])

    def test_equal_time_events_cannot_inform_each_other(self):
        data, _ = self.dataset([event(1, post=1000), event(2, post=1000, mc=105),
                                event(3, "sold", post=2000, mc=220)])
        self.assertEqual([0, 0], data.token_prior_buys_log.tolist())

    def test_duplicates_invalid_and_open_are_not_negative_labels(self):
        data, stats = self.dataset([event(1), event(2, post=200001), event(3, amount=0),
                                    event(4, "sold", mc=220), event(5)])
        self.assertEqual(1, len(data))
        self.assertEqual(1, stats["duplicates_3m"])
        self.assertEqual(1, stats["invalid_rows"])
        self.assertEqual(1, stats["open_buys"])

    def test_fees_slippage_and_size_affect_net_target(self):
        self.assertLess(net_multiple(2, 50), 2)
        self.assertGreater(net_multiple(2.2, 50), 2)
        self.assertLess(net_multiple(2.2, 1), 2)
        self.assertLess(net_multiple(2.2, 50, .1), 2)
        self.assertGreater(net_multiple(2, 50, discount=1), 1.99)

    def test_purged_temporal_split(self):
        f = pd.DataFrame(dict(entry_time=[1, 2, 11, 15, 21], exit_time=[5, 12, 18, 25, 30],
                              lifecycle_group_id=["a", "b", "c", "d", "e"]))
        train, validation, test = split(f, 10, 20)
        self.assertEqual([0], train.index.tolist())
        self.assertEqual([2], validation.index.tolist())
        self.assertEqual([4], test.index.tolist())

    def test_serialized_trees_match_sklearn_for_unknown_categories(self):
        rng = np.random.default_rng(388)
        f = pd.DataFrame(rng.normal(size=(150, len(NUMERIC_FEATURES))), columns=NUMERIC_FEATURES)
        f = f.assign(trader=["a", "b"] * 75, token="na", target_2x=[0, 1] * 75,
                     realized_source_roi=rng.normal(size=150), opportunity_id=range(150),
                     lifecycle_group_id=[f"g{i // 2}" for i in range(150)])
        c, r = fit(f, iterations=3)
        payload = dict(classifier=export(c), regressor=export(r))
        unknown = f.iloc[:4].copy().assign(trader="unknown", token="unknown")
        actual = np.array([predict_payload(payload, row) for row in unknown.to_dict("records")])
        np.testing.assert_allclose(actual[:, 0], c.predict_proba(unknown)[:, 1], atol=1e-12)
        np.testing.assert_allclose(actual[:, 1], r.predict(unknown), atol=1e-12)


if __name__ == "__main__":
    unittest.main()
