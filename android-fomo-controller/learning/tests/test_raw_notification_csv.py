from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import pandas as pd

from learning.core import load_learning_csv
from learning.raw_notification_csv import import_raw_notifications


class RawNotificationCsvTest(unittest.TestCase):
    def test_first_sell_resolves_all_buys_without_using_partial_sell_dollars(self) -> None:
        events = [
            ("orphan", 1, "sold", "NA", 110, 100),
            ("buy1", 2, "bought", "NA", 100, 1000),
            ("buy1-repeat", 3, "bought", "NA", 100, 1000),
            ("buy2", 4, "bought", "NA", 105, 500),
            ("sell", 5, "sold", "NA", 120, 100),
            ("open", 6, "bought", "COIN", 200, 250),
        ]
        raw = pd.DataFrame([{
            "event_id": i, "notification_key": key,
            "package_name": "family.fomo.app", "post_time": 1_000_000 + minute * 60_000,
            "action": action, "trader": "Trader", "coin": coin,
            "market_cap": cap, "source_amount": amount,
        } for i, (key, minute, action, coin, cap, amount) in enumerate(events, 1)])
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "raw.csv"
            prepared = Path(temp) / "prepared.csv"
            raw.to_csv(path, index=False)
            output, summary = import_raw_notifications(path)
            output.to_csv(prepared, index=False)
            valid = load_learning_csv(prepared)
        self.assertEqual(1, summary["orphan_sells"])
        self.assertEqual(1, summary["duplicates_3m"])
        self.assertEqual(2, summary["resolved_buys"])
        self.assertEqual(1, summary["open_buys"])
        self.assertEqual(1, valid.lifecycle_group_id.nunique())
        self.assertEqual(["NA", "NA"], valid.token.tolist())
        self.assertEqual([1, 1], valid.profitable.tolist())
        self.assertAlmostEqual(0.2, valid.loc[valid.source_buy_usd.eq(1000), "realized_source_roi"].iloc[0])

    def test_rejects_missing_schema_or_duplicate_event_id(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "bad.csv"
            pd.DataFrame([{"event_id": 1}]).to_csv(path, index=False)
            with self.assertRaisesRegex(ValueError, "missing columns"):
                import_raw_notifications(path)
            complete = pd.DataFrame([{
                "event_id": 1, "notification_key": "key", "package_name": "family.fomo.app",
                "post_time": 1, "action": "bought", "trader": "x", "coin": "COIN",
                "market_cap": 100, "source_amount": 10,
            }] * 2)
            complete.to_csv(path, index=False)
            with self.assertRaisesRegex(ValueError, "duplicate event_id"):
                import_raw_notifications(path)
