"""Public quote archiving retains provenance and never creates fills."""
import importlib.util, tempfile, json, unittest
from pathlib import Path
P=Path(__file__).parent/'capture_public.py'
spec=importlib.util.spec_from_file_location('capture_public_v15',P)
mod=importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


def status():
    return {'system':'radar-readonly-v1','live_ready':True,'connected':True,
            'assets':{'BTC':{'mark':100,'mark_age_s':.1,'book_age_s':.2,'book_bid_ask':[99.99,100.01], 'closed_candles':{'15m':270}},
                      'ETH':{'mark':200,'mark_age_s':.1,'book_age_s':.2,'book_bid_ask':[199.99,200.01], 'closed_candles':{'15m':270}}},
            'watch_only_candidates':[]}

class TestQuoteArchive(unittest.TestCase):
    def test_bid_ask_archived_without_trade_or_secrets(self):
        row=mod.record_status(status(),'2026-10-22T08:10:00Z')
        self.assertEqual(row['assets']['BTC']['book_bid_ask'],[99.99,100.01])
        self.assertFalse(row['pnl_eligible'])
        self.assertNotIn('order',row)
        self.assertNotIn('wallet',row)
    def test_missing_quote_explicitly_unavailable(self):
        s=status();s['assets']['BTC'].pop('book_bid_ask')
        r=mod.record_status(s,'2026-10-22T08:10:00Z')
        self.assertIsNone(r['assets']['BTC']['book_bid_ask'])
    def test_full_status_store_produces_same_timestamp(self):
        with tempfile.TemporaryDirectory() as t:
            root=Path(t); p={'type':'WATCH_ONLY','events':[]}
            mod.store(status(),p,observations=root/'o',events=root/'e',captured_at='2026-10-22T08:10:00Z')
            row=json.loads((root/'o').read_text())
            self.assertEqual(row['capture_at_utc'],'2026-10-22T08:10:00Z')
            self.assertEqual(row['candidate_endpoint'],'LAST_100_EPHEMERAL')
            self.assertEqual(row['assets']['ETH']['book_bid_ask'],[199.99,200.01])

if __name__=='__main__':unittest.main()
