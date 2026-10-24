import tempfile
import unittest
from pathlib import Path

from openpyxl import Workbook

from learning.historical_workbook import import_notification_log


class HistoricalWorkbookTest(unittest.TestCase):
    def test_duplicates_partial_sell_multiple_buys_and_open(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "legacy.xlsx"
            wb = Workbook()
            ws = wb.active
            ws.title = "Notifications"
            ws.append(["Time", "Type", "Title", "Text"])
            # Intentionally newest first: chronology must come from Time.
            ws.append(["2026-09-02 00:00:00", "bought", "A at $100k MC", "@X bought $3"])
            ws.append(["2026-09-01 00:06:00", "sold", "A at $120k MC", "@X sold $1"])
            ws.append(["2026-09-01 00:03:00", "bought", "A at $110k MC", "@X bought $2"])
            ws.append(["2026-09-01 00:02:00", "bought", "A at $110k MC", "@X bought $2"])
            ws.append(["2026-09-01 00:00:00", "bought", "A at $100k MC", "@X bought $10"])
            wb.save(path)
            wb.close()
            frame, summary = import_notification_log(path)
            self.assertEqual(1, summary["duplicates_3m"])
            self.assertEqual(2, summary["resolved_buys"])
            self.assertEqual(1, summary["open_buys"])
            closed = frame.loc[frame.status.eq("RESOLVED")]
            self.assertEqual(1, closed.lifecycle_group_id.nunique())
            self.assertEqual({1}, set(closed.profitable))
            self.assertAlmostEqual(0.2, closed.loc[closed.source_buy_usd.eq(10), "realized_source_roi"].iloc[0])


if __name__ == "__main__":
    unittest.main()
