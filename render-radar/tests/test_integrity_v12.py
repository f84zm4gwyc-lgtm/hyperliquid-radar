"""Regression tests for stricter live integrity and observation safety."""
import sys, unittest, time
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app
from engine import TF_MS

NOW = 1792663200.0 # Fixed 2026-10 UTC epoch; numerical time alone not trading data.


def closed_bars(coin,tf,now=NOW):
    period=TF_MS[tf]
    finished=int(now*1000)//period*period
    start=finished-230*period
    result=[]
    for n in range(230):
        t=start+n*period
        result.append({'t':t,'T':t+period-1,'s':coin,'i':tf,'o':100.,'h':101.,'l':99.,'c':100.,'v':10.})
    return result


def ready_engine():
    engine=app.MarketEngine()
    engine.connected=True
    engine.last_ws_at=NOW-2
    engine.last_history_refresh_at=NOW-2
    for coin in ('BTC','ETH'):
        engine.mark[coin]={'mark':100.,'_ts':NOW-1}
        engine.book[coin]={'bid':99.99,'ask':100.01,'_ts':NOW-1}
        for tf in app.HISTORY_TFS:
            engine.merge_bars(coin,tf,closed_bars(coin,tf))
    return engine


class IntegrityTests(unittest.TestCase):
    def test_fully_recent_data_can_be_ready(self):
        with patch.object(app.time,'time',return_value=NOW):
            self.assertEqual(ready_engine().readiness(),(True,'ok'))

    def test_missing_book_fails_closed(self):
        with patch.object(app.time,'time',return_value=NOW):
            eng=ready_engine();eng.book.pop('BTC')
            self.assertEqual(eng.readiness(),(False,'BTC_book_stale'))

    def test_old_book_fails_closed_even_with_live_ws(self):
        with patch.object(app.time,'time',return_value=NOW):
            eng=ready_engine();eng.book['BTC']['_ts']=NOW-400
            self.assertEqual(eng.readiness(),(False,'BTC_book_stale'))

    def test_crossed_book_fails_closed(self):
        with patch.object(app.time,'time',return_value=NOW):
            eng=ready_engine();eng.book['BTC']['ask']=99.5
            self.assertEqual(eng.readiness(),(False,'BTC_book_invalid'))

    def test_large_spread_fails_closed(self):
        with patch.object(app.time,'time',return_value=NOW):
            eng=ready_engine();eng.book['ETH']['ask']=102
            self.assertEqual(eng.readiness(),(False,'ETH_book_invalid'))

    def test_missing_1h_history_fails_closed(self):
        with patch.object(app.time,'time',return_value=NOW):
            eng=ready_engine();eng.bars[('BTC','1h')].clear()
            self.assertEqual(eng.readiness(),(False,'BTC_1h_candles_missing'))

    def test_old_4h_history_fails_closed(self):
        with patch.object(app.time,'time',return_value=NOW):
            eng=ready_engine()
            eng.bars[('ETH','4h')].clear()
            eng.merge_bars('ETH','4h',closed_bars('ETH','4h',NOW-2*TF_MS['4h']/1000))
            self.assertEqual(eng.readiness(),(False,'ETH_4h_candle_stale'))

    def test_watch_only_candidate_requires_quote(self):
        with patch.object(app.time,'time',return_value=NOW):
            eng=ready_engine();eng.book.pop('BTC')
            eng.verify_candidates('BTC',int(NOW*1000))
            self.assertEqual(eng.candidate_suppressed,1)
            self.assertEqual(eng.events,[])

    def test_status_uses_explicit_observation_warning(self):
        with patch.object(app.time,'time',return_value=NOW):
            s=ready_engine().status()
            self.assertIn('WATCH_ONLY',s['integrity_policy'])
            self.assertEqual(s['candidate_checks'],0)

if __name__=='__main__':unittest.main()
