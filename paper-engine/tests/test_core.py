import unittest
from datetime import timedelta
from fixtures import T, plan, quote, review
from radar_paper.core import actual_risk, exit_action, macro_allowed, make_plan, quote_ok, slack_message, stamp
from radar_paper.risk import round_size_down


class CoreTests(unittest.TestCase):
    def test_quote_age_and_future(self):
        self.assertTrue(quote_ok(quote(), T+timedelta(seconds=5)))
        self.assertFalse(quote_ok(quote(), T+timedelta(seconds=5.001)))
        self.assertFalse(quote_ok(quote(), T-timedelta(milliseconds=1)))

    def test_invalid_quote_shapes(self):
        for patch in ({"bid": 101}, {"mark": float("nan")}, {"ask": 102}, {"mark": 104}):
            self.assertFalse(quote_ok({**quote(), **patch}, T))

    def test_exact_close_freshness_boundaries(self):
        for age, accepted in ((2.999, False), (3, True), (180, True), (180.001, False)):
            args = ("BTC/USDC:USDC", "long", T-timedelta(seconds=age), 1, 100, quote(), 4, T)
            if accepted:
                self.assertTrue(make_plan(*args)["preview"]["accepted"])
            else:
                with self.assertRaises(ValueError): make_plan(*args)

    def test_no_low_volatility_signal_to_force_trade(self):
        with self.assertRaisesRegex(ValueError, "pondéré"):
            plan(atr=.1)

    def test_recompute_long_and_short_risk(self):
        for side in ("long", "short"):
            p = plan(side)
            self.assertTrue(actual_risk(p, p["preview"]["quantity"], p["preview"]["entry"]))
            self.assertFalse(actual_risk(p, 1000, 100))
            self.assertFalse(actual_risk(p, p["preview"]["quantity"], 105))
            self.assertFalse(actual_risk(p, .00001, 100))

    def test_precision_rejects_boolean(self):
        with self.assertRaises(ValueError): round_size_down(1, True)
        with self.assertRaises(ValueError): make_plan("BTC/USDC:USDC", "long", T-timedelta(seconds=5), 1, 100, quote(), True, T)

    def test_stop_and_two_targets_both_directions(self):
        for side in ("long", "short"):
            p = plan(side)
            levels = p["preview"]
            self.assertEqual(exit_action(p, levels["stop"], False), "STOP")
            self.assertEqual(exit_action(p, levels["tp1"], False), "TP1")
            self.assertIsNone(exit_action(p, levels["tp1"], True))
            self.assertEqual(exit_action(p, levels["tp2"], False), "TP_BOTH_AT_ONE_SAMPLE")
            self.assertEqual(exit_action(p, levels["tp2"], True), "TP2")

    def test_macro_expiration_sources_unknown(self):
        self.assertTrue(macro_allowed(review(), T))
        self.assertFalse(macro_allowed(review(), T+timedelta(hours=1)))
        for change in ({"decision": "UNKNOWN"}, {"sources": []}, {"sources": ["http://example.org"]},
                       {"expires_at_utc": stamp(T+timedelta(hours=3))}, {"reviewer": ""}):
            self.assertFalse(macro_allowed({**review(), **change}, T))

    def test_message_is_paper_and_refuses_late(self):
        p = plan()
        msg = slack_message(p, review(), T)
        self.assertIn("PAPIER", msg)
        self.assertIn(p["trade_id"], msg)
        with self.assertRaises(ValueError): slack_message(p, review(), T+timedelta(minutes=5))


if __name__ == "__main__": unittest.main()
