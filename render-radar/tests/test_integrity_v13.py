"""Adversarial market-feed safety regressions (synthetic only)."""
import json, unittest
from unittest.mock import patch

import app
from test_integrity_v12 import ready_engine, NOW
from engine import TF_MS


class NonfiniteFeedTests(unittest.TestCase):
    def test_nan_bbo_rejects_ready(self):
        with patch.object(app.time, "time", return_value=NOW):
            eng = ready_engine()
            eng.book["BTC"]["bid"] = float("nan")
            self.assertEqual(eng.readiness(), (False, "BTC_book_invalid"))
            self.assertIsNone(eng.status()["assets"]["BTC"]["book_bid_ask"])
            json.dumps(eng.status(), allow_nan=False)

    def test_infinite_mark_rejects_ready(self):
        with patch.object(app.time, "time", return_value=NOW):
            eng = ready_engine()
            eng.mark["ETH"]["mark"] = float("inf")
            self.assertEqual(eng.readiness(), (False, "ETH_mark_invalid"))
            self.assertIsNone(eng.status()["assets"]["ETH"]["mark"])
            json.dumps(eng.status(), allow_nan=False)

    def test_nan_ws_book_never_stored(self):
        with patch.object(app.time, "time", return_value=NOW):
            eng = ready_engine()
            before = eng.book["BTC"]["bid"]
            eng.process_message({"channel": "bbo", "data": {
                "coin": "BTC", "time": int(NOW * 1000),
                "bbo": [{"px": "NaN"}, {"px": "101"}]
            }})
            self.assertEqual(eng.book["BTC"]["bid"], before)

    def test_nan_ws_mark_never_stored(self):
        with patch.object(app.time, "time", return_value=NOW):
            eng = ready_engine()
            before = eng.mark["ETH"]["mark"]
            eng.process_message({"channel": "activeAssetCtx", "data": {
                "coin": "ETH", "ctx": {"markPx": "NaN", "funding": "0", "openInterest": "10"}
            }})
            self.assertEqual(eng.mark["ETH"]["mark"], before)

    def test_recent_1h_gap_rejects_ready(self):
        with patch.object(app.time, "time", return_value=NOW):
            eng = ready_engine()
            bars = eng.bars[("BTC", "1h")]
            keys = sorted(bars)
            bars.pop(keys[-5])
            t = keys[0] - TF_MS["1h"]
            bars[t] = {"t": t, "T": t + TF_MS["1h"] - 1}
            self.assertEqual(eng.readiness(), (False, "BTC_1h_candles_gap"))


if __name__ == "__main__":
    unittest.main()
