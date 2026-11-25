"""Exercise the Android store's actual DDL against SQLite, without Android JNI."""
import re
import sqlite3
from pathlib import Path
import unittest


class GuardSqlSchemaTest(unittest.TestCase):
    def test_schema_and_immutable_unique_buy_identity(self):
        source = (Path(__file__).resolve().parents[2] /
                  "app/src/main/java/com/dwu/fomocontroller/data/TraderGuardDatabase.kt").read_text()
        ddl = re.findall(r'db\.execSQL\("""(.*?)"""\)', source, re.S)
        indexes = re.findall(r'db\.execSQL\("(CREATE INDEX [^"]+)"\)', source)
        self.assertEqual(3, len(ddl))
        db = sqlite3.connect(":memory:")
        for statement in ddl + indexes:
            db.execute(statement)
        row = ("buy", "key", "t", "x", 100, 500, 1, 86400001)
        db.execute("INSERT INTO buys(opportunity_id,notification_key,trader,token,entry_cap,source_usd,opened_time,deadline) VALUES (?,?,?,?,?,?,?,?)", row)
        db.execute("INSERT OR IGNORE INTO buys(opportunity_id,notification_key,trader,token,entry_cap,source_usd,opened_time,deadline) VALUES (?,?,?,?,?,?,?,?)", row)
        self.assertEqual(1, db.execute("SELECT COUNT(*) FROM buys").fetchone()[0])
        for i in range(25):
            db.execute("INSERT INTO episodes(episode_id,trader,token,known_time,outcome_kind,net_reference_usd,lot_count,first_buy_time,last_buy_time) VALUES (?,?,?,?,?,?,?,?,?)",
                       (str(i), "t", "x", i + 10, "SOURCE_SELL", i, 1, 1, 1))
        known = db.execute("SELECT known_time,net_reference_usd FROM episodes WHERE trader=? AND known_time<? ORDER BY known_time DESC,id DESC LIMIT ?", ("t", 30, 20)).fetchall()
        self.assertEqual(20, len(known))
        self.assertEqual(29, known[0][0])
        self.assertTrue(all(time < 30 for time, _ in known))
        db.close()
