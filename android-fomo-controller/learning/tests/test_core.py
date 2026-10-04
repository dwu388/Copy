from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

from learning.core import compare_grouped, load_learning_csv


class LearningDataTest(unittest.TestCase):
    def test_unresolved_and_causally_invalid_rows_are_not_training_labels(self) -> None:
        rows = pd.DataFrame([
            dict(opportunity_id="ok", trader="a", token="x", entry_market_cap=100,
                 source_buy_usd=50, entry_time=1000, exit_time=2000,
                 realized_source_roi=0.2, profitable=1, lifecycle_group_id="g1", status="RESOLVED"),
            dict(opportunity_id="open", trader="a", token="x", entry_market_cap=100,
                 source_buy_usd=50, entry_time=1000, exit_time=np.nan,
                 realized_source_roi=np.nan, profitable=np.nan, lifecycle_group_id=np.nan, status="OPEN"),
            dict(opportunity_id="backward", trader="a", token="x", entry_market_cap=100,
                 source_buy_usd=50, entry_time=3000, exit_time=2000,
                 realized_source_roi=-0.2, profitable=0, lifecycle_group_id="g2", status="RESOLVED"),
        ])
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "learning.csv"
            rows.to_csv(path, index=False)
            actual = load_learning_csv(path)
        self.assertEqual(["ok"], actual["opportunity_id"].tolist())

    def test_bootstrap_uses_lifecycle_groups_not_duplicate_rows(self) -> None:
        frame = pd.DataFrame({
            "lifecycle_group_id": np.repeat([f"g{i}" for i in range(20)], 3),
            "realized_source_roi": np.tile([0.2, 0.2, 0.2], 20),
            "profitable": 1,
        })
        champion = pd.DataFrame({"probability": 0.5, "predicted_roi": 0.0, "confidence": 0.0}, index=frame.index)
        challenger = pd.DataFrame({"probability": 0.8, "predicted_roi": 0.2, "confidence": 0.9}, index=frame.index)
        result = compare_grouped(frame, champion, challenger, bootstrap_samples=100)
        self.assertTrue(result.promote)
        self.assertAlmostEqual(0.2, result.mean_delta)


if __name__ == "__main__":
    unittest.main()
