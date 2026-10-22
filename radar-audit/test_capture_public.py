"""Offline safety tests: no external network."""
import importlib.util, tempfile, unittest, json
from pathlib import Path
P=Path(__file__).parent/'capture_public.py'
spec=importlib.util.spec_from_file_location('capture_public',P)
mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)


def status():
    return {'system':'radar-readonly-v1','live_ready':True,'connected':True,
       'assets':{'BTC':{'mark':100,'mark_age_s':1,'book_age_s':1,'closed_candles':{'15m':270}},
                 'ETH':{'mark':200,'mark_age_s':1,'book_age_s':1,'closed_candles':{'15m':270}}},
       'watch_only_candidates':[]}

def event():
    return {'id':'BTC:15m:123456789000:LONG','coin':'BTC','direction':'LONG',
            'closed_at_ms':123456789999,'status':'WATCH_ONLY','close':100}

class CaptureTests(unittest.TestCase):
    def test_reject_other_endpoints(self):
        with self.assertRaises(ValueError):mod.fetch_json('https://evil.invalid/status')
    def test_reject_modified_status(self):
        x=status();x['system']='trading-live'
        with self.assertRaises(ValueError):mod.validate_status(x)
    def test_reject_forged_live_signal(self):
        e=event();e['status']='VALIDATED_PAPER_SIGNAL'
        with self.assertRaises(ValueError):mod.validate_event(e)
    def test_no_duplicates_and_marked_unverified(self):
        with tempfile.TemporaryDirectory() as t:
            o=Path(t)/'observations.jsonl';e=Path(t)/'events.jsonl'
            p={'type':'WATCH_ONLY','events':[event()]}
            for _ in range(2):mod.store(status(),p,observations=o,events=e,captured_at='2026-10-22T00:00:00Z')
            self.assertEqual(len(o.read_text().splitlines()),2)
            rows=[json.loads(x) for x in e.read_text().splitlines()]
            self.assertEqual(len(rows),1)
            self.assertFalse(rows[0]['pnl_eligible'])
            self.assertFalse(rows[0]['verified_executable_signal'])
    def test_fallback_five_only(self):
        with tempfile.TemporaryDirectory() as t:
            x=status();x['watch_only_candidates']=[event()]
            r=mod.store(x,None,observations=Path(t)/'o',events=Path(t)/'e',captured_at='2026-10-22T00:00:00Z')
            self.assertEqual(r['candidate_endpoint'],'UNAVAILABLE_STATUS_LAST_5_ONLY')
    def test_bad_event_fails_before_writing(self):
        with tempfile.TemporaryDirectory() as t:
            p={'type':'WATCH_ONLY','events':[event(),{**event(),'status':'ORDER_EXECUTED'}]}
            o=Path(t)/'o'
            with self.assertRaises(ValueError):mod.store(status(),p,observations=o,events=Path(t)/'e',captured_at='2026-10-22T00:00:00Z')
            self.assertFalse(o.exists())
    def test_observed_status_not_performance(self):
        r=mod.record_status(status(),'2026-10-22T00:00:00Z')
        self.assertFalse(r['pnl_eligible'])
        self.assertEqual(r['completeness'],'SAMPLE_ONLY_MISSED_INTERVALS_UNKNOWN')

if __name__=='__main__':unittest.main()
