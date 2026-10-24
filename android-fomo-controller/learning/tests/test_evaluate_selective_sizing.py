import unittest

from learning.evaluate_selective_sizing import evaluate, fee, size_and_net, wilson
import pandas as pd


MODE = {
    "ratio_floor": 10.0, "uncertainty_multiplier": 0.25,
    "fee_burden_trigger": 0.075, "fee_margin_multiple": 2.0,
    "confidence_threshold": 0.80,
}


class SelectiveSizingTests(unittest.TestCase):
    def test_fee_matches_android_floor_and_discount(self):
        self.assertEqual(fee(100, 0), 0.95)
        self.assertEqual(fee(200, 0), 1.0)
        self.assertEqual(fee(100, 0.10), 0.855)

    def test_exact_sell_fee_changes_net_win_label(self):
        # A $100 copy needs more than 1.9% growth to pay two floor fees.
        ratio, principal, net = size_and_net(2000, 0.018, 0.10, 0, MODE, 0, 0)
        self.assertEqual((ratio, principal), (20, 100))
        self.assertLess(net, 0)
        self.assertGreater(size_and_net(2000, 0.03, 0.10, 0, MODE, 0, 0)[2], 0)

    def test_fee_gate_rejects_all_smaller_sizing_when_ev_is_too_low(self):
        self.assertIsNone(size_and_net(200, 0.5, 0, 0, MODE, 0, 0))

    def test_lifecycle_counts_are_not_row_counts(self):
        frame = pd.DataFrame({"source_buy_usd": [2000, 2000, 2000],
                              "realized_source_roi": [0.1, 0.1, -0.1],
                              "lifecycle_group_id": ["a", "a", "b"]})
        scores = pd.DataFrame({"confidence": [0.9, 0.9, 0.9],
                               "predicted_roi": [0.2, 0.2, 0.2]})
        result = evaluate(frame, scores, 0.85,
                          {"modes": {"NORMAL": MODE}, "slippage_per_side": 0}, 0, 0)
        self.assertEqual(result["selected_rows"], 3)
        self.assertEqual(result["selected_lifecycles"], 2)
        self.assertAlmostEqual(result["lifecycle_net_win_rate"], 0.5)
        self.assertLess(wilson(1, 2), 0.5)


if __name__ == "__main__":
    unittest.main()
