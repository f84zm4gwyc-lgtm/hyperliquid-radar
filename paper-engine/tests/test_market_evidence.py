import unittest
from datetime import timedelta
from unittest.mock import patch
import pandas as pd
from fixtures import T, quote
from radar_paper.core import PAIRS
from radar_paper.market import PublicQuotes, public_info
from radar_paper.evidence import candle_evidence


class PublicStreamTests(unittest.TestCase):
    def setUp(self):
        self.quotes = PublicQuotes({pair: 4 for pair in PAIRS})
        self.quotes.connected = True

    def feed(self, now=T):
        self.quotes.process({"channel": "activeAssetCtx", "data": {"coin": "BTC", "ctx": {"markPx": "100"}}}, now)
        self.quotes.process({"channel": "bbo", "data": {"coin": "BTC", "time": int(now.timestamp()*1000),
                            "bbo": [{"px": "99.99"}, {"px": "100.01"}]}}, now)

    def test_both_subscriptions_required_then_freshness_disconnect(self):
        with patch("radar_paper.market.utcnow", return_value=T):
            with self.assertRaises(ValueError): self.quotes.quote("BTC/USDC:USDC")
            self.feed()
            self.assertEqual(self.quotes.quote("BTC/USDC:USDC")["mark"], 100)
            self.quotes.connected = False
            with self.assertRaises(ValueError): self.quotes.quote("BTC/USDC:USDC")

    def test_stale_or_future_timestamp_never_valid(self):
        self.feed()
        for now in (T+timedelta(seconds=6), T-timedelta(milliseconds=1)):
            with patch("radar_paper.market.utcnow", return_value=now):
                with self.assertRaises(ValueError): self.quotes.quote("BTC/USDC:USDC")

    def test_out_of_order_book_does_not_regress_current_quote(self):
        self.feed()
        newer = T+timedelta(seconds=1)
        self.feed(newer)
        self.feed(T)
        with patch("radar_paper.market.utcnow", return_value=newer):
            self.assertEqual(self.quotes.quote("BTC/USDC:USDC")["book_at_utc"], quote(newer)["book_at_utc"])

    def test_subsecond_book_update_after_whole_second_is_preserved(self):
        self.feed(T)
        newer = T+timedelta(milliseconds=100)
        self.feed(newer)
        with patch("radar_paper.market.utcnow", return_value=newer):
            self.assertEqual(self.quotes.quote("BTC/USDC:USDC")["book_at_utc"], quote(newer)["book_at_utc"])

    def test_private_endpoint_or_invalid_precision_rejected(self):
        with self.assertRaises(ValueError): public_info({"type": "order"})
        with self.assertRaises(ValueError): PublicQuotes({pair: True for pair in PAIRS})


class EvidenceTests(unittest.TestCase):
    def frame(self, pair, timeframe):
        freq = {"15m": "15min", "1h": "1h", "4h": "4h"}[timeframe]
        # Last row is still forming and must never enter the frozen evidence.
        return pd.DataFrame({"date": pd.date_range(end=pd.Timestamp(T).floor(freq), periods=231, freq=freq),
                             "open": 100., "high": 101., "low": 99., "close": 100., "volume": 10.})

    def test_freeze_only_closed_inputs(self):
        class Provider: pass
        dp = Provider(); dp.get_pair_dataframe = self.frame
        evidence = candle_evidence(dp, "BTC/USDC:USDC", T)
        for tf, rows in evidence["histories"].items():
            self.assertEqual(len(rows), 230)
            self.assertLess(rows[-1][0], self.frame("BTC/USDC:USDC", tf).iloc[-1]["date"].isoformat())

    def test_gapped_history_rejects_entry_evidence(self):
        class Provider: pass
        dp = Provider(); dp.get_pair_dataframe = lambda **kwargs: self.frame(**kwargs).drop(index=210)
        with self.assertRaises(ValueError): candle_evidence(dp, "BTC/USDC:USDC", T)
