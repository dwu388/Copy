from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

from learning.core import causal_training, load_learning_csv
from learning.policy_labels import HOLD_MS, build_policy_labels, reference_net_roi


class PolicyLabelTest(unittest.TestCase):
    def build(self, events, as_of=None):
        rows = []
        for i, (key, action, token, post, captured, cap) in enumerate(events, 1):
            rows.append(dict(event_id=i, notification_key=key, package_name="family.fomo.app",
                             post_time=post, captured_time=captured, action=action,
                             trader="Trader", coin=token, market_cap=cap, source_amount=500))
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "raw.csv"
            pd.DataFrame(rows).to_csv(path, index=False)
            return build_policy_labels(path, as_of)[0]

    def test_late_sell_cannot_replace_deadline_loss(self):
        start = 1_000_000
        frame = self.build([
            ("buy", "bought", "X", start, start, 100),
            ("late", "sold", "X", start + 2 * HOLD_MS, start + 2 * HOLD_MS, 200),
        ])
        row = frame.iloc[0]
        self.assertEqual("TIMEOUT_ASSUMED_LOSS", row.outcome_kind)
        self.assertEqual(-1, row.realized_source_roi)
        self.assertEqual(1, row.source_realized_roi)
        self.assertEqual(start + HOLD_MS + 1, row.label_available_time)

    def test_early_sell_still_waits_for_complete_observation_window(self):
        start = 1_000_000
        frame = self.build([
            ("buy", "bought", "NA", start, start, 100),
            ("sell", "sold", "NA", start + 1000, start + 1000, 150),
        ])
        self.assertEqual("PENDING", frame.iloc[0].status)
        self.assertTrue(pd.isna(frame.iloc[0].profitable))

    def test_net_not_gross_win_and_roundtrip_fees(self):
        start = 1_000_000
        frame = self.build([
            ("buy", "bought", "NA", start, start, 100),
            ("sell", "sold", "NA", start + 1000, start + 1000, 101),
            ("end", "sold", "OTHER", start + HOLD_MS + 10, start + HOLD_MS + 10, 50),
        ])
        row = frame.iloc[0]
        self.assertEqual("SOURCE_SELL", row.outcome_kind)
        self.assertAlmostEqual(0.01, row.realized_source_roi)
        self.assertEqual(0, row.profitable)
        self.assertAlmostEqual(0.01 - 1.9 / 25, reference_net_roi(frame)[0])

    def test_deadline_mark_requires_capture_and_post_freshness_and_no_future(self):
        start = 1_000_000
        end = start + HOLD_MS + 1
        # Quotes use invalid action to avoid resolving the source opportunity.
        frame = self.build([
            ("buy", "bought", "X", start, start, 100),
            ("fresh", "quote", "X", end - 1000, end - 1000, 120),
            ("stale-post", "quote", "X", end - 400_000, end - 500, 1000),
            ("future", "quote", "X", end + 100, end + 100, 500),
            ("end", "sold", "OTHER", end + 200, end + 200, 50),
        ])
        self.assertEqual("TIMEOUT_MARK", frame.iloc[0].outcome_kind)
        self.assertAlmostEqual(0.2, frame.iloc[0].realized_source_roi)

    def test_capture_order_controls_sell_availability(self):
        start = 1_000_000
        end = start + HOLD_MS + 1
        frame = self.build([
            ("buy", "bought", "X", start, start, 100),
            ("delayed", "sold", "X", start + 100, end + 100, 150),
        ])
        self.assertEqual("TIMEOUT_ASSUMED_LOSS", frame.iloc[0].outcome_kind)

    def test_asof_excludes_later_sell_and_rejects_unobserved_future(self):
        start = 1_000_000
        end = start + HOLD_MS + 1
        events = [("buy", "bought", "X", start, start, 100),
                  ("late", "sold", "X", end + 100, end + 100, 200)]
        frame = self.build(events, end)
        self.assertEqual("TIMEOUT_ASSUMED_LOSS", frame.iloc[0].outcome_kind)
        self.assertTrue(pd.isna(frame.iloc[0].source_realized_roi))
        with self.assertRaisesRegex(ValueError, "exceeds observed"):
            self.build(events, end + 200)

    def test_loader_rejects_legacy_and_roundtrips_policy_labels(self):
        start = 1_000_000
        frame = self.build([
            ("buy", "bought", "X", start, start, 100),
            ("end", "sold", "OTHER", start + HOLD_MS + 10, start + HOLD_MS + 10, 50),
        ])
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "labels.csv"
            frame.to_csv(path, index=False)
            loaded = load_learning_csv(path)
            self.assertEqual(-1, loaded.iloc[0].realized_source_roi)
            frame.drop(columns="label_contract").to_csv(path, index=False)
            with self.assertRaisesRegex(ValueError, "Legacy"):
                load_learning_csv(path)

    def test_training_labels_must_precede_buy_and_groups_are_purged(self):
        frame = pd.DataFrame({"entry_time": [1, 2, 3, 10],
                              "label_available_time": [9, 10, 11, 20],
                              "lifecycle_group_id": ["safe", "equal", "future", "safe"]})
        validation = pd.DataFrame({"entry_time": [10], "lifecycle_group_id": ["holdout"]})
        self.assertEqual([0], causal_training(frame, validation).index.tolist())
        validation.lifecycle_group_id = "safe"
        self.assertTrue(causal_training(frame, validation).empty)


if __name__ == "__main__":
    unittest.main()
