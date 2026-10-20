import asyncio,json,sys,tempfile,time,unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import app
from test_engine import bars,BASE

class MarketTests(unittest.TestCase):
    def test_unconnected_not_healthy(self):
        self.assertEqual(app.MarketEngine().readiness(),(False,'ws_disconnected'))
    def test_ws_bbo(self):
        e=app.MarketEngine();stamp=int(time.time()*1000)
        e.process_message({'channel':'bbo','data':{'coin':'BTC','time':stamp,
                    'bbo':[{'px':'81000','sz':'1'}, {'px':'81001','sz':'1'}]}})
        self.assertEqual(e.book['BTC']['ask'],81001)
    def test_ws_invalid_bbo_rejected(self):
        e=app.MarketEngine();stamp=int(time.time()*1000)
        e.process_message({'channel':'bbo','data':{'coin':'BTC','time':stamp,
                    'bbo':[{'px':'81001','sz':'1'}, {'px':'81000','sz':'1'}]}})
        self.assertNotIn('BTC',e.book)
    def test_ws_mark(self):
        e=app.MarketEngine()
        e.process_message({'channel':'activeAssetCtx','data':{'coin':'ETH','ctx':{'markPx':'2500','funding':'0.0001','openInterest':'1000'}}})
        self.assertEqual(e.mark['ETH']['mark'],2500)
    def test_candle_invalid_partial_never_evaluates(self):
        e=app.MarketEngine();b=bars('BTC','15m',1)[0]
        e.process_message({'channel':'candle','data':b})
        self.assertEqual(len(e.bars[('BTC','15m')]),1)
    def test_candidate_deduplication_across_restart(self):
        with tempfile.TemporaryDirectory() as d:
            with patch.object(app,'DB_PATH',Path(d)/'db.sqlite'):
                e=app.MarketEngine();e.open_db();x={'id':'BTC:15m:100:LONG','status':'WATCH_ONLY'}
                e._persist_candidate(x);e._persist_candidate(x)
                self.assertEqual(len(e.events),1)
                e.conn.close();e2=app.MarketEngine();e2.open_db()
                self.assertEqual(len(e2.events),1);e2.conn.close()
    def test_historical_noncontiguous_rejected(self):
        e=app.MarketEngine();x=bars('BTC','1h',8)
        e.merge_bars('BTC','1h',x[:4]+x[5:])
        self.assertEqual(len(e.bars[('BTC','1h')]),7)
    def test_mark_only_insufficient_for_readiness(self):
        e=app.MarketEngine();e.connected=True;e.last_ws_at=time.time();e.last_history_refresh_at=time.time()
        self.assertFalse(e.readiness()[0])
    def test_status_contains_no_trading_execution(self):
        e=app.MarketEngine();self.assertIn('NO ORDER EXECUTION',e.status()['disclaimer'])

if __name__=='__main__':unittest.main()
