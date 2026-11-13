import tempfile
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

from learning.notification_features import FeatureHistory, NUMERIC_FEATURES, build_dataset, net_multiple
from learning.train_2x import export, fit, predict_payload, split, split_by_fraction


def event(i, action="bought", post=None, captured=None, mc=100, amount=1000, trader="A", token="NA"):
    return dict(event_id=i, notification_key=f"k{i}", package_name="family.fomo.app",
                action=action, post_time=post or i * 200_000, captured_time=captured or post or i * 200_000,
                market_cap=mc, source_amount=amount, trader=trader, coin=token)


class Notification2xTest(unittest.TestCase):
    def test_fraction_split_uses_latest_history_and_purges_crossing_labels(self):
        frame = pd.DataFrame(dict(opportunity_id=range(100), entry_time=np.arange(100)*10,
                                  exit_time=np.arange(100)*10+1,
                                  lifecycle_group_id=[f"g{i}" for i in range(100)]))
        frame.loc[79, "exit_time"] = 805
        frame.loc[89, "exit_time"] = 905
        train, validation, trial, contract = split_by_fraction(frame)
        self.assertEqual(79, len(train))
        self.assertEqual(9, len(validation))
        self.assertEqual(10, len(trial))
        self.assertEqual(2, contract["purged_boundary_rows"])
        self.assertLess(train.exit_time.max(), validation.entry_time.min())
        self.assertLess(validation.exit_time.max(), trial.entry_time.min())
        self.assertEqual(990, trial.entry_time.max())

    def test_fraction_split_keeps_equal_time_entries_together(self):
        frame = pd.DataFrame(dict(opportunity_id=range(100), entry_time=np.arange(100)*10,
                                  exit_time=np.arange(100)*10+1,
                                  lifecycle_group_id=[f"g{i}" for i in range(100)]))
        frame.loc[79, "entry_time"] = 800
        frame.loc[79, "exit_time"] = 801
        train, validation, trial, _ = split_by_fraction(frame)
        self.assertNotIn(79, train.index)
        self.assertTrue({79, 80}.issubset(validation.index))
        self.assertTrue(set(train.lifecycle_group_id).isdisjoint(trial.lifecycle_group_id))

    def test_fraction_split_rejects_invalid_reserves(self):
        for train, val in [(.9, .1), (1, .1), (.8, 0), (float("nan"), .1)]:
            with self.assertRaises(ValueError):
                split_by_fraction(pd.DataFrame(), train, val)

    def test_default_ratio_ten_changes_cost_adjusted_target(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "raw.csv"
            pd.DataFrame([event(1), event(2, "sold", mc=204)]).to_csv(path, index=False)
            default, _ = build_dataset(path)
            twenty, _ = build_dataset(path, ratio=20)
        self.assertEqual(1, default.target_2x.iloc[0])
        self.assertEqual(0, twenty.target_2x.iloc[0])

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
