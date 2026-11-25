import unittest

import pandas as pd

from learning.portfolio_replay import paired_day_bootstrap, replay


class PortfolioReplayTest(unittest.TestCase):
    def config(self):
        return {"modes": {"NORMAL": {
            "confidence_threshold": 0.8, "ratio_floor": 20, "reserve_floor_usd": 150,
            "reserve_fraction": 0.05, "max_open_exposure": 0.9, "max_trader_exposure": 0.35,
            "max_token_exposure": 0.25, "max_15m_new_exposure": 0.3,
            "uncertainty_multiplier": 0, "fee_burden_trigger": 1, "fee_margin_multiple": 2,
        }}}

    def frame(self):
        return pd.DataFrame([dict(opportunity_id="a", trader="A", token="X", source_buy_usd=2000,
                                  entry_time=1000, exit_time=2000, realized_source_roi=-1,
                                  lifecycle_group_id="g", outcome_kind="TIMEOUT_ASSUMED_LOSS")])

    def test_writeoff_does_not_refund_principal_or_charge_exit_fee(self):
        frame = self.frame()
        scores = pd.DataFrame({"probability": [0.8], "predicted_roi": [0.5], "confidence": [0.9]})
        report, ledger = replay(frame, scores, self.config(), 0)
        self.assertAlmostEqual(899.05, report["final_cash"])
        self.assertAlmostEqual(-100.95, ledger.net_usd.iloc[0])

    def test_equal_time_exit_releases_cash_before_next_buy(self):
        frame = self.frame()
        frame.realized_source_roi = 1
        frame.source_buy_usd = 2000
        other = frame.copy()
        other.opportunity_id = "b"
        other.entry_time = 2000
        other.exit_time = 3000
        frame = pd.concat([frame, other], ignore_index=True)
        scores = pd.DataFrame({"probability": [0.8] * 2, "predicted_roi": [0.5] * 2,
                               "confidence": [0.9] * 2})
        report, _ = replay(frame, scores, self.config(), 0)
        self.assertEqual(2, report["executed_buys"])
        self.assertAlmostEqual(1196.10, report["final_cash"])

    def test_empty_ledgers_keep_zero_trade_days(self):
        frame = pd.DataFrame({"entry_time": [1, 2 * 86400000 + 1]})
        report = paired_day_bootstrap(frame, pd.DataFrame(), pd.DataFrame(), 100)
        self.assertEqual(3, report["calendar_days"])
        self.assertEqual([0, 0], report["daily_delta_ci95"])


if __name__ == "__main__":
    unittest.main()
