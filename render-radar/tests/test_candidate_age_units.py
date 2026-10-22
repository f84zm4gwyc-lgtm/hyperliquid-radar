"""Regression for ms/seconds mismatch; synthetic bars and mocked detection."""
import sys
import unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app
from test_bootstrap_v14 import build_document, BASE_NOW


class CandidateAge(unittest.TestCase):
    def test_live_confirmation_window_is_seconds_not_milliseconds(self):
        doc = build_document()
        cut = doc["assets"]["BTC"]["15m"][-1]["T"]+1
        for age_ms, expected in ((2999, False), (3000, True), (4000, True),
                                 (179999, True), (180000, True), (180001, False)):
            with self.subTest(age_ms=age_ms):
                now = (cut+age_ms)/1000
                engine = app.MarketEngine()
                for tf, bars in doc["assets"]["BTC"].items():
                    engine.merge_bars("BTC", tf, bars)
                engine.book["BTC"] = {"bid": 100, "ask": 100.01, "_ts": now}
                engine.mark["BTC"] = {"mark": 100, "_ts": now}
                with patch.object(app.time, "time", return_value=now), \
                     patch.object(app, "candidate", return_value={"id": "SYNTHETIC"}), \
                     patch.object(engine, "_persist_candidate") as persist:
                    engine.verify_candidates("BTC", cut+age_ms)
                self.assertEqual(persist.called, expected)
