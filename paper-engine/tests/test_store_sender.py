import json
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from urllib.error import HTTPError
from fixtures import T, plan, review
from radar_paper.notify import Sender, webhook_send
from radar_paper.store import Store


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name)/"journal.sqlite"
        self.store = Store(self.path)

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def test_reservation_survives_restart_and_blocks_other_pair(self):
        p = plan()
        self.assertTrue(self.store.reserve(p, T))
        self.store.close(); self.store = Store(self.path)
        self.assertFalse(self.store.reserve(plan(pair="ETH/USDC:USDC"), T))
        self.store.state(p["trade_id"], "CONFIRMED", T)
        self.store.state(p["trade_id"], "OPEN", T)
        self.store.state(p["trade_id"], "CLOSED", T)
        self.assertFalse(self.store.reserve(p, T))  # Same candle cannot re-enter.
        self.assertTrue(self.store.reserve(plan(pair="ETH/USDC:USDC"), T))

    def test_confirmed_expiration_does_not_release_pending_order_risk(self):
        p = plan()
        self.store.reserve(p, T)
        self.store.state(p["trade_id"], "CONFIRMED", T)
        later = T+timedelta(minutes=6)
        self.assertFalse(self.store.reserve(plan(now=later, pair="ETH/USDC:USDC"), later))

    def test_unconfirmed_expired_plan_can_release(self):
        self.store.reserve(plan(), T)
        later = T+timedelta(minutes=6)
        self.assertTrue(self.store.reserve(plan(now=later), later))

    def test_plan_tampering_and_lifecycle_rejected(self):
        p = plan(); self.store.reserve(p, T)
        with self.assertRaises(ValueError): self.store.state(p["trade_id"], "CLOSED", T)
        self.store.db.execute("UPDATE radar_plans SET payload=?", (json.dumps({**p,"stop": 90}),))
        self.store.db.commit()
        with self.assertRaisesRegex(ValueError, "integrity"): self.store.plan(p["trade_id"])

    def test_two_connections_share_one_risk_slot(self):
        other = Store(self.path)
        try:
            self.assertTrue(self.store.reserve(plan(), T))
            self.assertFalse(other.reserve(plan(pair="ETH/USDC:USDC"), T))
        finally: other.close()

    def test_no_webhook_leaves_queue_pending(self):
        self.store.queue("test", {"type": "TEST"})
        self.assertEqual(Sender(self.store, "").once(T), "UNCONFIGURED")
        self.assertEqual(self.store.summary()["outbox_by_state"], {"PENDING": 1})

    def test_one_delivery_despite_requeue_restart(self):
        calls = []
        self.store.queue("test", {"type": "TEST"})
        sender = Sender(self.store, "opaque-test-secret", lambda *args: calls.append(args[1]))
        self.assertEqual(sender.once(T), "SENT")
        self.assertFalse(self.store.queue("test", {"type": "TEST"}))
        self.store.close(); self.store = Store(self.path)
        self.assertEqual(Sender(self.store, "opaque-test-secret", sender.transport).once(T), "IDLE")
        self.assertEqual(len(calls), 1)
        self.assertTrue(calls[0].startswith("RADAR_E2E_TEST"))

    def test_ambiguous_timeout_not_retried(self):
        self.store.queue("test", {"type": "TEST"})
        def timeout(*args): raise TimeoutError("Ambiguous after POST")
        sender = Sender(self.store, "opaque-test-secret", timeout)
        self.assertEqual(sender.once(T), "UNKNOWN")
        self.assertEqual(sender.once(T+timedelta(minutes=1)), "IDLE")

    def test_crash_after_claim_becomes_unknown(self):
        self.store.queue("test", {"type": "TEST"})
        self.store.claim(T)
        self.store.close(); self.store = Store(self.path)
        self.assertEqual(self.store.summary()["outbox_by_state"], {"UNKNOWN": 1})

    def test_rate_limit_retries_once_after_delay(self):
        self.store.queue("test", {"type": "TEST"})
        calls = []
        def transport(*args):
            calls.append(1)
            if len(calls) == 1: raise HTTPError("opaque", 429, "rate", {"Retry-After": "2"}, None)
        sender = Sender(self.store, "opaque-test-secret", transport)
        self.assertEqual(sender.once(T), "RATE_LIMITED")
        self.assertEqual(sender.once(T+timedelta(seconds=1)), "IDLE")
        self.assertEqual(sender.once(T+timedelta(seconds=2)), "SENT")
        self.assertEqual(len(calls), 2)

    def test_macro_missing_or_source_stale_never_sends(self):
        p = plan(); self.store.reserve(p, T)
        self.store.queue(p["trade_id"], {"type": "VALIDATED_PAPER_SIGNAL", "plan_id": p["trade_id"], "macro_review": {}})
        calls = []
        sender = Sender(self.store, "opaque-test-secret", lambda *a: calls.append(a))
        self.assertEqual(sender.once(T), "EXPIRED_OR_REJECTED")
        self.assertEqual(calls, [])

    def test_valid_fresh_signal_delivers_with_preserved_identifier(self):
        p = plan(); self.store.reserve(p, T)
        self.store.state(p["trade_id"], "CONFIRMED", T)
        self.store.queue(p["trade_id"], {"type": "VALIDATED_PAPER_SIGNAL", "plan_id": p["trade_id"], "macro_review": review()})
        calls = []
        sender = Sender(self.store, "opaque-test-secret", lambda *a: calls.append(a[1]))
        self.assertEqual(sender.once(T), "SENT")
        self.assertIn(p["trade_id"], calls[0])

    def test_explicit_test_does_not_deliver_an_older_real_signal(self):
        self.store.queue("old", {"type": "VALIDATED_PAPER_SIGNAL"})
        self.store.queue("new", {"type": "TEST"})
        calls = []
        sender = Sender(self.store, "opaque-test-secret", lambda *a: calls.append(a[1]))
        self.assertEqual(sender.once(T, only_ident="new"), "SENT")
        self.assertIn("signal_id=new", calls[0])
        self.assertEqual(self.store.summary()["outbox_by_state"], {"PENDING": 1, "SENT": 1})

    def test_webhook_rejects_wrong_host_before_network(self):
        for url in ("https://example.org/services/test", "http://hooks.slack.com/services/test", "https://hooks.slack.com@evil.org/services/x"):
            with self.assertRaises(ValueError): webhook_send(url, "TEST")


if __name__ == "__main__": unittest.main()
