import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import pandas as pd
from fixtures import T, plan, quote
from RadarPaper import RadarPaper
from radar_paper.store import Store
from freqtrade.enums import CandleType


class FakeTrade:
    """Synthetic callback fixture; never a native market performance record."""
    def __init__(self, p):
        self.id = 123
        self.pair = p["pair"]
        self.data = {}
        self.open_rate = p["preview"]["entry"]
        self.amount = p["preview"]["quantity"]
        self.stake_amount = self.open_rate*self.amount/2
        self.entry_side = "buy" if p["side"] == "LONG" else "sell"
        self.nr_of_successful_exits = 0
        self.has_open_orders = False
        self.is_open = True
        self.close_profit_abs = None
        self.funding_fees = 0

    def set_custom_data(self, key, value): self.data[key] = value
    def get_custom_data(self, key): return self.data.get(key)


class StrategyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.strategy = RadarPaper({"dry_run": True, "candle_type_def": CandleType.FUTURES})
        self.strategy.ready = True
        self.strategy.root = Path(self.temp.name)
        self.strategy.store = Store(self.strategy.root/"journal.sqlite")
        self.strategy.quotes = SimpleNamespace(quote=lambda pair: quote())
        row = {"date": pd.Timestamp(T-timedelta(minutes=15, seconds=5)),
               "atr": 1, "close": 100, "enter_long": 1, "enter_short": 0,
               "prior_high": 99, "prior_low": 90, "volume_ratio": 1.5,
               "close_1h": 100, "ema20_1h": 99, "ema50_1h": 98,
               "close_4h": 100, "ema20_4h": 99, "ema50_4h": 98}
        self.strategy.dp = SimpleNamespace(get_analyzed_dataframe=lambda *a: (pd.DataFrame([row]), None))
        def raw(pair, timeframe):
            freq = {"15m": "15min", "1h": "1h", "4h": "4h"}[timeframe]
            end = pd.Timestamp(T).floor(freq)-pd.Timedelta(freq)
            return pd.DataFrame({"date": pd.date_range(end=end, periods=230, freq=freq),
                                 "open": 100., "high": 101., "low": 99., "close": 100., "volume": 10.})
        self.strategy.dp.get_pair_dataframe = raw
        self.clock = patch("RadarPaper.utcnow", return_value=T)
        self.clock.start()

    def tearDown(self):
        self.clock.stop()
        self.strategy.store.close()
        self.temp.cleanup()

    def stake(self, **overrides):
        args = dict(pair="BTC/USDC:USDC", current_time=T, current_rate=100.01,
                    proposed_stake=1000, min_stake=10, max_stake=1000,
                    leverage=2, entry_tag="SYNTHETIC", side="long")
        return self.strategy.custom_stake_amount(**{**args, **overrides})

    def confirmed(self):
        self.assertGreater(self.stake(), 0)
        p = self.strategy.store.active("BTC/USDC:USDC")
        ok = self.strategy.confirm_trade_entry(p["pair"], "limit", p["preview"]["quantity"],
                p["preview"]["entry"], "GTC", T, "SYNTHETIC", "long")
        self.assertTrue(ok)
        return p

    def test_plan_before_native_entry_and_no_macro_alert(self):
        p = self.confirmed()
        self.assertEqual(self.strategy.store.status(p["trade_id"]), "CONFIRMED")
        events = self.strategy.store.db.execute("SELECT type FROM radar_events").fetchall()
        self.assertEqual([r[0] for r in events], ["PLAN_RECORDED_BEFORE_ENTRY", "CONFIRMED", "NO_ALERT_MACRO_UNKNOWN_OR_BLOCKED"])
        self.assertEqual(self.strategy.store.summary()["outbox_by_state"], {})

    def test_live_wrong_leverage_and_callback_exception_return_zero(self):
        self.strategy.config["dry_run"] = False
        self.assertEqual(self.stake(), 0)
        self.strategy.config["dry_run"] = True
        self.assertEqual(self.stake(leverage=3), 0)
        self.strategy.quotes.quote = lambda pair: (_ for _ in ()).throw(RuntimeError("offline"))
        self.assertEqual(self.stake(), 0)

    def test_native_lifecycle_rejects_live_and_backtest_before_callbacks(self):
        from freqtrade.enums import RunMode
        for mode, dry in ((RunMode.LIVE, False), (RunMode.BACKTEST, True)):
            self.strategy.config.update({"runmode": mode, "dry_run": dry})
            with self.assertRaisesRegex(RuntimeError, "refuses"):
                self.strategy.ft_bot_start()
        self.strategy.config.update({"runmode": RunMode.DRY_RUN, "dry_run": True})

    def test_wrong_side_and_late_confirmation_fail(self):
        self.stake()
        p = self.strategy.store.active("BTC/USDC:USDC")
        args = (p["pair"], "limit", p["preview"]["quantity"], p["preview"]["entry"], "GTC", T, "SYNTHETIC")
        self.assertFalse(self.strategy.confirm_trade_entry(*args, "short"))
        with patch("RadarPaper.utcnow", return_value=T+timedelta(seconds=6)):
            self.assertFalse(self.strategy.confirm_trade_entry(*args, "long"))

    def test_native_fill_sets_plan_and_second_tp_half_is_not_repeated(self):
        p = self.confirmed(); trade = FakeTrade(p)
        self.strategy.order_filled(p["pair"], trade, SimpleNamespace(ft_order_side=trade.entry_side), T)
        self.assertEqual(trade.get_custom_data("radar_plan_id"), p["trade_id"])
        self.assertEqual(self.strategy.store.status(p["trade_id"]), "OPEN")
        self.strategy.quotes.quote = lambda pair: {**quote(), "mark": p["preview"]["tp1"]}
        adjustment = self.strategy.adjust_trade_position(trade, T, 103, .03, 10, 1000)
        self.assertIsNotNone(adjustment)
        self.assertLess(adjustment[0], 0)
        self.assertAlmostEqual(abs(adjustment[0])*trade.amount/trade.stake_amount, p["preview"]["tp1_quantity"], places=4)
        trade.nr_of_successful_exits = 1
        self.assertIsNone(self.strategy.adjust_trade_position(trade, T, 103, .03, 10, 1000))

    def test_gap_marks_result_unverifiable_and_stop_remains_exit(self):
        p = self.confirmed(); trade = FakeTrade(p)
        self.strategy.order_filled(p["pair"], trade, SimpleNamespace(ft_order_side=trade.entry_side), T)
        self.strategy.quotes.quote = lambda pair: {**quote(T+timedelta(seconds=20)), "mark": p["preview"]["stop"]}
        with patch("RadarPaper.utcnow", return_value=T+timedelta(seconds=20)):
            self.assertEqual(self.strategy.custom_exit(p["pair"], trade, T, 99, -.01), "STOP")
            self.assertTrue(trade.get_custom_data("radar_gap"))
            trade.is_open = False; trade.close_profit_abs = -35
            self.strategy.order_filled(p["pair"], trade, SimpleNamespace(ft_order_side="sell", safe_filled=trade.amount, order_id="SYNTHETIC"), T)
        row = self.strategy.store.db.execute("SELECT payload FROM radar_events WHERE type='CLOSED'").fetchone()
        self.assertIn("UNVERIFIABLE_GAP", row[0])

    def test_orphan_native_position_is_closed_in_paper(self):
        trade = FakeTrade(plan())
        self.assertEqual(self.strategy.custom_exit(trade.pair, trade, T, 100, 0), "PAPER_ORPHAN_PLAN")

    def test_stale_entry_is_canceled(self):
        p = self.confirmed()
        with patch("RadarPaper.utcnow", return_value=T+timedelta(minutes=5)):
            self.assertTrue(self.strategy.check_entry_timeout(p["pair"], None, None, T))

    def test_informative_merge_cannot_use_unclosed_hour(self):
        from freqtrade.strategy import merge_informative_pair
        base = pd.DataFrame({"date": pd.date_range("2026-10-22T08:00Z", periods=8, freq="15min"), "close": range(8)})
        inf = pd.DataFrame({"date": pd.date_range("2026-10-22T07:00Z", periods=3, freq="1h"), "sentinel": [7, 8, 9]})
        merged = merge_informative_pair(base, inf, "15m", "1h", ffill=True)
        self.assertEqual(merged.iloc[2]["sentinel_1h"], 7)  # 08:30 candle: 08:00 hour not closed.
        self.assertEqual(merged.iloc[3]["sentinel_1h"], 8)  # 08:45 candle closes at 09:00.


if __name__ == "__main__": unittest.main()
