"""Offline read-only ARGUS adapter tests. Never reach brokers or send orders."""
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

import argus_readonly_data as adapter


class TestArgusReadOnly(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.previous = adapter.ROOT
        adapter.ROOT = self.root
        self.addCleanup(setattr, adapter, "ROOT", self.previous)

    def make_db(self, name, statements):
        # sqlite3.Connection context manager commits but does NOT close on Windows.
        with closing(sqlite3.connect(self.root / name)) as db:
            for sql, params in statements:
                db.execute(sql, params)
            db.commit()

    def test_missing_database_is_not_created(self):
        before = set(self.root.iterdir())
        result = adapter.signal_log()
        self.assertFalse(result["available"])
        self.assertEqual(before, set(self.root.iterdir()))

    def test_no_unsafe_sql_allowed(self):
        with self.assertRaises(ValueError):
            adapter.read_rows("any.db", "DELETE FROM events LIMIT ?")
        with self.assertRaises(ValueError):
            adapter.read_rows("any.db", "SELECT * FROM events; DROP TABLE events")

    def test_signals_exclude_raw_webhook_details(self):
        self.make_db(adapter.SIGNAL_DB, [
            ("CREATE TABLE events(received_at TEXT, strategy TEXT, symbol TEXT, signal TEXT,"
             "result TEXT, intent_state TEXT, details TEXT)", ()),
            ("INSERT INTO events VALUES(?,?,?,?,?,?,?)",
             ("2026-11-11T19:06:00Z", "KAIROS_V5", "SPY", "BUY_PUT",
              "SIGNAL_LOGGED", "PUT_INTENT", "SECRET_DO_NOT_EXPOSE")),
        ])
        result = adapter.signal_log()
        self.assertTrue(result["available"])
        self.assertEqual(len(result["events"]), 1)
        self.assertNotIn("details", result["events"][0])
        self.assertNotIn("SECRET_DO_NOT_EXPOSE", str(result))

    def test_worker_decisions_exclude_notes(self):
        self.make_db(adapter.WORKER_DB, [
            ("CREATE TABLE signals(received_at TEXT, action TEXT, outcome TEXT, note TEXT)", ()),
            ("INSERT INTO signals VALUES(?,?,?,?)",
             ("2026-11-11T19:06:00Z", "BUY_PUT", "ENTRY_filled", "KEY_NO")),
        ])
        result = adapter.worker_decisions()
        self.assertEqual(result["decisions"][0]["outcome"], "ENTRY_filled")
        self.assertNotIn("KEY_NO", str(result))

    def test_effective_armed_false_if_kill_switch(self):
        self.make_db(adapter.WORKER_DB, [
            ("CREATE TABLE cfg(name TEXT, value TEXT)", ()),
            ("INSERT INTO cfg VALUES(?,?)", ("armed", "true")),
            ("INSERT INTO cfg VALUES(?,?)", ("paper_account_id", "NOT_RETURNED")),
            ("INSERT INTO cfg VALUES(?,?)", ("last_heartbeat", adapter.iso_now())),
        ])
        (self.root / adapter.KILL_FILE).write_text("Emergency stop secret: OMIT")
        with patch.object(adapter, "check_get", return_value={"ok": True}):
            result = adapter.health()
        self.assertEqual(result["status"], "REVIEW_REQUIRED")
        self.assertFalse(result["paper"]["effective_armed"])
        self.assertNotIn("NOT_RETURNED", str(result))
        self.assertNotIn("OMIT", str(result))

    def test_healthy_supervisor_requires_fresh_worker(self):
        self.make_db(adapter.WORKER_DB, [
            ("CREATE TABLE cfg(name TEXT, value TEXT)", ()),
            ("INSERT INTO cfg VALUES(?,?)", ("armed", "true")),
            ("INSERT INTO cfg VALUES(?,?)", ("paper_account_id", "NOT_RETURNED")),
            ("INSERT INTO cfg VALUES(?,?)", ("last_heartbeat", adapter.iso_now())),
        ])
        (self.root / adapter.SUPERVISOR_FILE).write_text(
            '{"mode":"RUNNING","worker_pid":1234,"updated_at":"' + adapter.iso_now() + '"}'
        )
        with patch.object(adapter, "check_get", return_value={"ok": True}):
            self.assertEqual(adapter.health()["status"], "HEALTHY")

    def test_research_rejects_untrusted_tickers(self):
        with patch.object(adapter, "get_json", side_effect=AssertionError("HTTP should not be called")):
            result = adapter.research("SPY&url=http://evil.invalid")
        self.assertFalse(result["available"])

    def test_local_journal_fields_exclude_identifiers(self):
        self.make_db(adapter.JOURNAL_DB, [
            ("CREATE TABLE option_fills(filled_at TEXT, option_symbol TEXT, mode TEXT,"
             "action TEXT, contracts INTEGER, fill_price REAL, fees_usd REAL,"
             "broker_order_id TEXT)", ()),
            ("INSERT INTO option_fills VALUES(?,?,?,?,?,?,?,?)",
             ("2026-11-11T19:09:07Z", "SPY   261111P00761000", "PAPER",
              "SELL_TO_CLOSE", 25, 1.02, 0.0, "private-id")),
        ])
        result = adapter.fills()
        self.assertTrue(result["available"])
        self.assertEqual(len(result["fills"]), 1)
        self.assertNotIn("private-id", str(result))
        self.assertNotIn("broker_order_id", str(result))

    def test_age_is_offset_aware(self):
        self.assertIsNone(adapter.age_seconds("not-a-time"))
        self.assertIsNone(adapter.age_seconds("2026-11-11T19:09:07"))


if __name__ == "__main__":
    unittest.main()
