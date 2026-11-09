r"""AION KAIROS V5 PAPER safety regression tests -- NO BROKER ORDERS.

Run from the AION-Execution directory:
    .\.venv\Scripts\python.exe -m unittest -v test_aion_paper_safety.py

Every mutable database, kill switch and lock file is redirected into a temporary
folder. Tests use fake broker objects, never construct AlpacaPaper clients,
and block the AlpacaPaper.call network method.
"""

import json
import os
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from unittest.mock import Mock, patch

import aion_alpaca_paper_algo as aion


class PaperSafetyTests(unittest.TestCase):
    SYMBOL = "SPY261109C00700000"  # Test-only, never submitted to a real broker
    ACCOUNT_ID = "offline-mock-paper-account"
    WINDOWS = {
        "repository_0935_1430": ((9, 35), (14, 30), (9, 35), (15, 2)),
        "local_0931_1445": ((9, 31), (14, 45), (9, 31), (15, 2)),
    }

    def setUp(self):
        # Strict known contract, not automatically derived from the code under test.
        self.profile = os.environ.get("AION_SAFETY_WINDOW_PROFILE", "repository_0935_1430")
        if self.profile not in self.WINDOWS:
            raise RuntimeError("Unknown AION_SAFETY_WINDOW_PROFILE")
        self.temp = tempfile.TemporaryDirectory(prefix="aion-paper-safety-")
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        for name in ("STATE", "SIGNALS", "KILL", "LOCK"):
            patcher = patch.object(aion, name, root / f"{name.lower()}.test")
            patcher.start()
            self.addCleanup(patcher.stop)

        # Python's sqlite3 connection context manager commits/rolls back but
        # does NOT close the connection. On Windows/Python 3.13, leaving those
        # test connections open locks the temporary SQLite files (WinError 32).
        # Track connections created only inside this test process and close
        # them BEFORE TemporaryDirectory.cleanup. Never access production DBs.
        self._test_connections = []
        original_connect = sqlite3.connect

        def tracked_connect(*args, **kwargs):
            connection = original_connect(*args, **kwargs)
            self._test_connections.append(connection)
            return connection

        connection_patcher = patch.object(aion.sqlite3, "connect", side_effect=tracked_connect)
        connection_patcher.start()
        self.addCleanup(connection_patcher.stop)
        self.addCleanup(self._close_test_connections)

        network_block = patch.object(
            aion.AlpacaPaper,
            "call",
            side_effect=AssertionError("NETWORK FORBIDDEN DURING OFFLINE SAFETY TEST"),
        )
        network_block.start()
        self.addCleanup(network_block.stop)
        aion.init_db()
        aion.KILL.write_text("Safety-test default: DISARMED", encoding="utf-8")
        self.api = Mock()
        self.api.account.return_value = {
            "id": self.ACCOUNT_ID,
            "status": "ACTIVE",
            "options_trading_level": 3,
            "cash": "10000",
            "trading_blocked": False,
            "account_blocked": False,
        }
        self.api.clock.return_value = {"is_open": True}
        self.api.positions.return_value = []
        self.api.orders.return_value = []
        self.api.submit.side_effect = AssertionError("Unexpected mock submission")
        self.api.cancel.side_effect = AssertionError("Unexpected mock cancellation")
        self.api.order_by_client_id.side_effect = AssertionError("Unexpected mock reconciliation")

    def _close_test_connections(self):
        # Cleanup callbacks execute in reverse order: close tracked handles,
        # restore the sqlite3 connect function, unpatch file paths, then
        # finally remove the temporary directory on Windows.
        for connection in reversed(self._test_connections):
            try:
                connection.close()
            except sqlite3.ProgrammingError:
                pass  # Already closed by the function under test.
        self._test_connections.clear()

    @staticmethod
    def et(hour, minute, second=0, day=9):
        return datetime(2026, 11, day, hour, minute, second, tzinfo=aion.NY)

    def enable_in_isolated_test(self):
        # Only the temporary database and temporary kill file are touched.
        aion.put_cfg("armed", "true")
        aion.put_cfg("paper_account_id", self.ACCOUNT_ID)
        aion.KILL.unlink(missing_ok=True)

    def insert_order(self, *, cid="mock-001", action="BUY_TO_OPEN", qty=2,
                     filled=0, state="new", age_seconds=0, journaled=0,
                     broker_order_id="mock-broker-order"):
        sent = (datetime.now(timezone.utc) - timedelta(seconds=age_seconds)).isoformat()
        with sqlite3.connect(aion.STATE) as db:
            db.execute(
                """INSERT INTO paper_orders
                (client_order_id,event_id,submitted_at,action,option_symbol,qty,
                 state,broker_order_id,filled_qty,journaled_qty,journaled_notional)
                VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (cid, "mock-event", sent, action, self.SYMBOL, qty, state,
                 broker_order_id, str(filled), journaled, "0"),
            )

    def assert_window_matches(self, signal, start, end):
        begin = self.et(*start)
        cutoff = self.et(*end)
        self.assertFalse(aion.market_window(signal, begin - timedelta(seconds=1)),
                         f"{self.profile}: {signal} permitted before intended start")
        self.assertTrue(aion.market_window(signal, begin),
                        f"{self.profile}: {signal} blocked at intended start")
        self.assertTrue(aion.market_window(signal, cutoff - timedelta(seconds=1)),
                        f"{self.profile}: {signal} blocked before intended cutoff")
        self.assertFalse(aion.market_window(signal, cutoff),
                         f"{self.profile}: {signal} permitted at cutoff")
        self.assertFalse(aion.market_window(signal, self.et(15, 40)))

    def test_entry_cutoff_exact_boundaries(self):
        entry_start, entry_end, _, _ = self.WINDOWS[self.profile]
        self.assert_window_matches("ENTRY", entry_start, entry_end)

    def test_exit_cutoff_exact_boundaries(self):
        _, _, exit_start, exit_end = self.WINDOWS[self.profile]
        self.assert_window_matches("EXIT", exit_start, exit_end)

    def test_weekend_closed(self):
        self.assertFalse(aion.market_window("ENTRY", self.et(10, 0, day=8)))
        self.assertFalse(aion.market_window("EXIT", self.et(10, 0, day=8)))

    def test_contract_budget_and_quantity_limits(self):
        self.assertEqual(aion.contract_quantity(Decimal("1"), Decimal("10000")), 25)
        self.assertEqual(aion.contract_quantity(Decimal("10"), Decimal("10000")), 2)
        self.assertEqual(aion.contract_quantity(Decimal("4"), Decimal("500")), 1)
        self.assertEqual(aion.contract_quantity(Decimal("30"), Decimal("10000")), 0)

    def test_account_mismatch_rejected(self):
        with self.assertRaisesRegex(RuntimeError, "MISMATCH"):
            aion.broker_account_check(self.api, "different-paper-account")
        self.api.submit.assert_not_called()

    def test_blocked_account_rejected(self):
        self.api.account.return_value["trading_blocked"] = True
        with self.assertRaisesRegex(RuntimeError, "inactive or blocked"):
            aion.broker_account_check(self.api)

    def test_disarmed_worker_never_runs_once(self):
        with self.assertRaisesRegex(RuntimeError, "DISARMED"):
            aion.once(self.api)
        self.api.submit.assert_not_called()

    def test_kill_switch_blocks_order_submission(self):
        aion.put_cfg("armed", "true")  # KILL file deliberately remains present.
        with self.assertRaisesRegex(RuntimeError, "Not armed"):
            aion.submit_intent(self.api, "mock-signal", "BUY_TO_OPEN",
                               self.SYMBOL, Decimal("1.00"), qty=1,
                               current=self.et(10, 0))
        self.api.submit.assert_not_called()

    def test_premium_over_2500_rejected_before_submission(self):
        self.enable_in_isolated_test()
        with self.assertRaisesRegex(RuntimeError, "exceeds"):
            aion.submit_intent(self.api, "mock-signal", "BUY_TO_OPEN",
                               self.SYMBOL, Decimal("25.01"), qty=1,
                               current=self.et(10, 0))
        self.api.submit.assert_not_called()

    def test_closed_broker_clock_blocks_submission(self):
        self.enable_in_isolated_test()
        self.api.clock.return_value = {"is_open": False}
        with self.assertRaisesRegex(RuntimeError, "market closed"):
            aion.submit_intent(self.api, "mock-signal", "BUY_TO_OPEN",
                               self.SYMBOL, Decimal("1.00"), qty=1,
                               current=self.et(10, 0))
        self.api.submit.assert_not_called()

    def test_duplicate_order_never_resubmitted(self):
        self.enable_in_isolated_test()
        event = "mock-event-duplicate"
        cid = aion.unique_client_id(event, "BUY_TO_OPEN")
        self.api.submit.side_effect = None
        self.api.submit.return_value = {
            "client_order_id": cid,
            "symbol": self.SYMBOL,
            "status": "new",
            "id": "mock-order-id",
        }
        aion.submit_intent(self.api, event, "BUY_TO_OPEN",
                           self.SYMBOL, Decimal("1.00"), qty=1,
                           current=self.et(10, 0))
        with self.assertRaisesRegex(RuntimeError, "Duplicate attempt"):
            aion.submit_intent(self.api, event, "BUY_TO_OPEN",
                               self.SYMBOL, Decimal("1.00"), qty=1,
                               current=self.et(10, 0))
        self.assertEqual(self.api.submit.call_count, 1)

    def test_position_discrepancy_fails_closed(self):
        self.insert_order(qty=1, filled=1, state="filled")
        with self.assertRaisesRegex(RuntimeError, "unclosed fills"):
            aion.owned_position(self.api, self.SYMBOL)
        self.api.submit.assert_not_called()

    def test_unmanaged_position_fails_closed(self):
        self.api.positions.return_value = [{
            "symbol": self.SYMBOL, "asset_class": "us_option", "qty": "1", "side": "long"
        }]
        with self.assertRaisesRegex(RuntimeError, "Unmanaged/manual"):
            aion.owned_position(self.api, self.SYMBOL)
        self.api.submit.assert_not_called()

    def test_pending_entry_cancellation_not_repeated(self):
        self.insert_order(qty=1, age_seconds=31)
        self.api.cancel.side_effect = None
        result = aion.monitor_pending_order_timeouts(self.api)
        self.assertEqual(len(result), 1)
        self.api.cancel.assert_called_once_with("mock-broker-order")
        aion.monitor_pending_order_timeouts(self.api)
        self.assertEqual(self.api.cancel.call_count, 1)

    def test_stale_exit_requires_manual_review(self):
        self.insert_order(action="SELL_TO_CLOSE", qty=1, age_seconds=31)
        with self.assertRaisesRegex(RuntimeError, "manual review"):
            aion.monitor_pending_order_timeouts(self.api)
        self.api.submit.assert_not_called()

    def test_queued_exit_does_not_trade_when_disarmed(self):
        aion.put_cfg("pending_exit", json.dumps({"event_id": "mock-exit", "reason": "mock"}))
        result = aion.process_pending_exit(self.api)
        self.assertEqual(result, "EXIT_PENDING_DISARMED_CHECK_BROKER")
        self.assertIsNotNone(aion.pending_exit())
        self.api.submit.assert_not_called()
        self.api.cancel.assert_not_called()

    def test_emergency_exit_at_1445_queues_without_duplicate(self):
        self.enable_in_isolated_test()
        self.insert_order(qty=1, filled=1, state="filled")
        with patch.object(aion, "now_utc", return_value=self.et(14, 44).astimezone(timezone.utc)), \
             patch.object(aion, "request_strategy_exit", return_value="MOCK_QUEUED") as request:
            self.assertIsNone(aion.risk_exit(self.api))
            request.assert_not_called()
        with patch.object(aion, "now_utc", return_value=self.et(14, 45).astimezone(timezone.utc)), \
             patch.object(aion, "request_strategy_exit", return_value="MOCK_QUEUED") as request:
            self.assertEqual(aion.risk_exit(self.api), "MOCK_QUEUED")
            request.assert_called_once()
        self.api.submit.assert_not_called()

    def test_incremental_fills_journal_once(self):
        self.insert_order(qty=2, filled=0, state="new", journaled=0)
        payload = {
            "client_order_id": "mock-001", "symbol": self.SYMBOL,
            "status": "partially_filled", "filled_qty": "1",
            "filled_avg_price": "1.20",
            "filled_at": "2026-11-09T15:00:00Z", "id": "mock-broker-order",
        }
        self.api.order_by_client_id.side_effect = None
        self.api.order_by_client_id.return_value = payload
        with patch("aion_execution_journal.record_fill", return_value=True) as record:
            aion.sync_orders(self.api)
            self.assertEqual(record.call_count, 1)
            self.assertAlmostEqual(record.call_args.kwargs["fill_price"], 1.20)
            payload = dict(payload, status="filled", filled_qty="2", filled_avg_price="1.10")
            self.api.order_by_client_id.return_value = payload
            aion.sync_orders(self.api)
            self.assertEqual(record.call_count, 2)
            self.assertAlmostEqual(record.call_args.kwargs["fill_price"], 1.00)
            aion.sync_orders(self.api)
            self.assertEqual(record.call_count, 2)
        self.api.submit.assert_not_called()


if __name__ == "__main__":
    unittest.main(verbosity=2)
