import json
from pathlib import Path
import unittest

from learning.trader_guard import History, HOLD_MS, assess, net_reference
from learning.backtest_trader_guard import Quotes, simulate

FIXTURES = Path(__file__).resolve().parents[2] / "app/src/test/resources/trader_guard_parity.json"


class TraderGuardTest(unittest.TestCase):
    def test_shared_kotlin_assessment_fixtures(self):
        for c in json.loads(FIXTURES.read_text())["assessments"]:
            with self.subTest(c["name"]):
                a = assess(c["mode"], c["outcomes"], c["now"], c["threshold"])
                self.assertEqual((c["status"], c["samples"], c["wins"], c["allowed"]),
                                 (a.status, a.samples, a.wins, a.allowed))

    def test_shared_kotlin_valuation_fixtures(self):
        for c in json.loads(FIXTURES.read_text())["valuations"]:
            self.assertAlmostEqual(c["net"], net_reference(c["source"], c["entry"], c["exit"]))

    def test_source_sell_groups_multiple_buys_into_one_episode(self):
        h = History()
        h.observe_buy("a", " Trader ", "X", 500, 100, 1, "FILTER", .5)
        h.observe_buy("b", "trader", "x", 1000, 110, 2, "FILTER", .5)
        h.observe_sell("TRADER", " X ", 120, 3)
        self.assertEqual(1, len(h.episodes))
        self.assertEqual(2, h.episodes[0]["lot_count"])
        self.assertFalse(h.buys)

    def test_skipped_buys_can_restore_eligibility(self):
        h = History()
        for i in range(20):
            t = 100 + 10 * i
            a = h.observe_buy(str(i), "a", str(i), 1000, 100, t, "FILTER", .5)
            self.assertFalse(a.allowed)
            h.observe_sell("a", str(i), 200, t + 1)
        a = h.observe_buy("next", "a", "new", 1000, 100, 1000, "FILTER", .5)
        self.assertTrue(a.allowed)
        self.assertEqual((20, 20), (a.samples, a.wins))

    def test_expiry_is_strict_and_immutable_after_late_sell(self):
        h = History()
        h.observe_buy("a", "t", "x", 500, 100, 100, "WARN", .5)
        h.expire(100 + HOLD_MS, lambda *args: None)
        self.assertTrue(h.buys)
        h.expire(101 + HOLD_MS, lambda *args: None)
        self.assertAlmostEqual(-25.95, h.episodes[0]["net_reference_usd"])
        h.observe_sell("t", "x", 1000, 102 + HOLD_MS)
        self.assertEqual(1, len(h.episodes))
        self.assertEqual("TIMEOUT_ASSUMED_LOSS", h.episodes[0]["outcome_kind"])

    def test_expiry_availability_uses_actual_sweep_time(self):
        h = History()
        h.observe_buy("a", "t", "x", 500, 100, 100, "WARN", .5)
        now = 100 + 2 * HOLD_MS
        h.expire(now, lambda *args: 200)
        a = h.observe_buy("b", "t", "y", 500, 100, now, "WARN", .5)
        self.assertEqual(0, a.samples)
        a = h.observe_buy("c", "t", "z", 500, 100, now + 1, "WARN", .5)
        self.assertEqual((1, 1), (a.samples, a.wins))

    def test_duplicate_buy_does_not_reopen_closed_history(self):
        h = History()
        h.observe_buy("a", "t", "x", 500, 100, 1, "WARN", .5)
        h.observe_sell("t", "x", 200, 2)
        h.observe_buy("a", "t", "x", 500, 100, 3, "WARN", .5)
        h.observe_sell("t", "x", 300, 4)
        self.assertEqual(1, len(h.episodes))
        self.assertFalse(h.buys)

    def test_quote_freshness_requires_both_timestamps(self):
        q = Quotes()
        q.add(dict(package_name="family.fomo.app", trader="T", coin="X", captured_time=900_000,
                   post_time=100, market_cap=999))
        self.assertIsNone(q.get("t", "x", 900_000))
        q.add(dict(package_name="family.fomo.app", trader="T", coin="X", captured_time=900_001,
                   post_time=900_001, market_cap=120))
        self.assertEqual(120, q.get("t", "x", 900_001))
        self.assertIsNone(q.get("t", "x", 900_000))

    def test_invalid_inputs_rejected(self):
        for threshold in [float("nan"), float("inf"), -.1, 1.1]:
            with self.assertRaises(ValueError):
                assess("WARN", [], 100, threshold)
        with self.assertRaises(ValueError):
            net_reference(100, 0, 10)


class GuardPortfolioIntegrationTest(unittest.TestCase):
    def payload(self):
        from learning.core import read_payload
        return read_payload(Path(__file__).resolve().parents[2] / "app/src/main/assets/adaptive_hybrid_v2.json.gz")

    @staticmethod
    def row(id, action, when, cap=100):
        return dict(event_id=id, notification_key=str(id), package_name="family.fomo.app",
                    post_time=when, captured_time=when, trader="test", coin="X",
                    market_cap=cap, source_amount=1000, action=action, valid=True,
                    opportunity_id=f"{id}@{when}")

    def test_warn_identity_and_filter_cold_start(self):
        p = self.payload()
        rows = [self.row(1, "bought", 100), self.row(2, "sold", 200, 150),
                self.row(3, "sold", 100 + HOLD_MS + 1000)]
        score = {1: dict(probability=.9, predicted_roi=1, confidence=1)}
        off, ledger, _ = simulate(rows, p, score, 100, "OFF", .5)
        warn, warning_ledger, checks = simulate(rows, p, score, 100, "WARN", .5)
        filtered, _, _ = simulate(rows, p, score, 100, "FILTER", .5)
        self.assertEqual(ledger, warning_ledger)
        self.assertEqual(off["final_cash"], warn["final_cash"])
        self.assertEqual(1, warn["copied_buys"])
        self.assertEqual("INSUFFICIENT", checks[0]["status"])
        self.assertEqual((0, 1000), (filtered["copied_buys"], filtered["final_cash"]))
        self.assertAlmostEqual(1023.1, warn["final_cash"])

    def test_same_notification_updates_keep_own_feature_predictions(self):
        from learning.backtest_trader_guard import score_buys
        from unittest.mock import patch
        import pandas as pd
        rows = [self.row(1, "bought", 100, 100), self.row(2, "bought", 101, 200),
                self.row(3, "sold", HOLD_MS + 1000)]
        rows[1]["opportunity_id"] = rows[0]["opportunity_id"]
        fake = pd.DataFrame(dict(probability=[.1, .9], predicted_roi=[0, 1], confidence=[0, 1]))
        with patch("learning.backtest_trader_guard.score_payload", return_value=fake):
            scores = score_buys(rows, self.payload(), 100)
        self.assertEqual(.1, scores[1]["probability"])
        self.assertEqual(.9, scores[2]["probability"])

    def test_wallet_writeoff_preserves_account_reconciliation(self):
        p = self.payload()
        rows = [self.row(1, "bought", 100),
                dict(self.row(2, "sold", HOLD_MS + 1000), coin="UNRELATED")]
        score = {1: dict(probability=.9, predicted_roi=1, confidence=1)}
        result, ledger, _ = simulate(rows, p, score, 100, "WARN", .5)
        self.assertAlmostEqual(949.05, result["final_cash"])
        self.assertAlmostEqual(-50.95, ledger[0]["net"])

    def test_promotion_requires_ten_exits_and_demotes(self):
        from learning.strategy_replay import Wallet
        w = Wallet(self.payload()["config"], 0)
        w.cash = w.peak = 2000
        for _ in range(9):
            w.update_stage(True)
        self.assertEqual(0, w.stage)
        w.update_stage(True)
        self.assertEqual(1, w.stage)
        w.cash = 1999
        w.update_stage()
        self.assertEqual(0, w.stage)
