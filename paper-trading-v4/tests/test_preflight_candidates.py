"""Non-trading candidate preflight; all data synthetically generated."""
import copy, importlib.util, json, tempfile, unittest
from datetime import datetime, timezone
from pathlib import Path

P = Path(__file__).resolve().parents[1] / 'preflight_candidates.py'
spec = importlib.util.spec_from_file_location('paper_preflight', P)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
TS = '2026-10-22T06:45:01Z'
CLOSE_MS = int(datetime(2026,10,22,6,44,tzinfo=timezone.utc).timestamp()*1000)

def event():
    return {'source':'RENDER_PUBLIC_WATCH_ONLY','pnl_eligible':False,
            'captured_at_utc':TS,'event':{
                'id':f'BTC:15m:{CLOSE_MS-900000}:LONG','coin':'BTC','direction':'LONG',
                'status':'WATCH_ONLY','closed_at_ms':CLOSE_MS,
                'close':100.0,'boundary':99.0}}

def status():
    return {'source':'RENDER_PUBLIC_STATUS_SNAPSHOT', 'capture_at_utc':TS,
            'live_ready_reported':True, 'reason':'ok',
            'candidate_endpoint':'LAST_100_EPHEMERAL',
            'assets':{'BTC':{'mark':100,'mark_age_s':0.2,'book_age_s':0.3,
                              'book_bid_ask':[99.99,100.01]},
                       'ETH':{'mark':200,'mark_age_s':0.2,'book_age_s':0.3,
                              'book_bid_ask':[199.99,200.01]}}}

class QualitySafety(unittest.TestCase):
    def test_good_candidate_quote_screen_only(self):
        s=mod.screen(event(),status())
        self.assertEqual(s['result'],'QUOTE_SCREEN_PASS')
        self.assertFalse(s['paper_signal_created'])
        self.assertFalse(s['order_created'])
        self.assertFalse(s['pnl_eligible'])
        self.assertNotIn('entry',s)
        self.assertNotIn('stop',s)
    def test_reject_unhealthy_engine(self):
        s=status();s['live_ready_reported']=False
        self.assertEqual(mod.screen(event(),s)['reason'],'ENGINE_NOT_READY')
    def test_reject_late_candidate(self):
        e=event();e['event']['closed_at_ms']-=60*60*1000
        self.assertEqual(mod.screen(e,status())['reason'],'CANDIDATE_LATE_OR_FUTURE')
    def test_reject_future_candle(self):
        e=event();e['event']['closed_at_ms']+=5*60*1000
        self.assertEqual(mod.screen(e,status())['reason'],'CANDIDATE_LATE_OR_FUTURE')
    def test_reject_mark_stale(self):
        s=status();s['assets']['BTC']['mark_age_s']=6
        self.assertEqual(mod.screen(event(),s)['reason'],'STALE_QUOTES')
    def test_reject_book_stale(self):
        s=status();s['assets']['BTC']['book_age_s']=6
        self.assertEqual(mod.screen(event(),s)['reason'],'STALE_QUOTES')
    def test_reject_missing_bbo(self):
        s=status();s['assets']['BTC'].pop('book_bid_ask')
        self.assertEqual(mod.screen(event(),s)['reason'],'INVALID_QUOTE_OR_CANDIDATE_PRICE')
    def test_reject_spread(self):
        s=status();s['assets']['BTC']['book_bid_ask']=[99,101]
        self.assertEqual(mod.screen(event(),s)['reason'],'SPREAD_TOO_WIDE')
    def test_reject_nan(self):
        s=status();s['assets']['BTC']['mark']=float('nan')
        self.assertEqual(mod.screen(event(),s)['reason'],'INVALID_QUOTE_OR_CANDIDATE_PRICE')
    def test_reject_inf(self):
        s=status();s['assets']['BTC']['book_bid_ask']=[99.99,float('inf')]
        self.assertEqual(mod.screen(event(),s)['reason'],'INVALID_QUOTE_OR_CANDIDATE_PRICE')
    def test_reject_mark_book_divergence(self):
        s=status();s['assets']['BTC']['mark']=100.7
        self.assertEqual(mod.screen(event(),s)['reason'],'MARK_BOOK_DIVERGENCE')
    def test_reject_signal_market_movement(self):
        e=event();e['event']['close']=99.45
        self.assertEqual(mod.screen(e,status())['reason'],'PRICE_MOVED_SINCE_CANDLE')
    def test_reject_crossed_book(self):
        s=status();s['assets']['BTC']['book_bid_ask']=[100.01,99.99]
        self.assertEqual(mod.screen(event(),s)['reason'],'CROSSED_BOOK')
    def test_reject_wrong_status_source(self):
        s=status();s['source']='FAKE'
        self.assertEqual(mod.screen(event(),s)['reason'],'SOURCE_OR_CAPTURE_MISMATCH')
    def test_reject_missing_capture(self):
        self.assertEqual(mod.screen(event(),None)['reason'],'MISSING_MATCHING_STATUS')
    def test_reject_fallback_event_source(self):
        s=status();s['candidate_endpoint']='UNAVAILABLE_STATUS_LAST_5_ONLY'
        self.assertEqual(mod.screen(event(),s)['reason'],'INCOMPLETE_EVENT_SOURCE')
    def test_reject_unverified_new_signal(self):
        e=event();e['event']['status']='VALIDATED_PAPER_SIGNAL'
        with self.assertRaises(ValueError):mod.screen(e,status())
    def test_reject_other_provenance(self):
        e=event();e['source']='SIGNAL_EXECUTION'
        with self.assertRaises(ValueError):mod.screen(e,status())
    def test_reject_short_boundary_invalid(self):
        e=event();e['event']['direction']='SHORT';e['event']['boundary']=99
        self.assertEqual(mod.screen(e,status())['reason'],'INVALID_SHORT_BOUNDARY')
    def test_accept_short_screen_only(self):
        e=event();e['event']['direction']='SHORT';e['event']['boundary']=101
        self.assertEqual(mod.screen(e,status())['result'],'QUOTE_SCREEN_PASS')
    def test_cannot_infer_from_old_archive_without_bbo(self):
        s=status();s['assets']['BTC'].pop('book_bid_ask')
        self.assertEqual(mod.screen(event(),s)['result'],'REJECT')
    def test_read_only_archive_idempotent(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);o=root/'o.jsonl';e=root/'e.jsonl';dest=root/'p.jsonl'
            o.write_text(json.dumps(status())+'\n');e.write_text(json.dumps(event())+'\n')
            a=mod.update_archive(o,e,dest)
            self.assertEqual(a['new_quality_pass'],1)
            self.assertEqual(a['generated_paper_positions'],0)
            b=mod.update_archive(o,e,dest)
            self.assertEqual(b['new_screens'],0)
            self.assertEqual(len(dest.read_text().splitlines()),1)
    def test_unavailable_candidate_archive_creates_no_records(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);o=root/'o';o.write_text(json.dumps(status())+'\n');dest=root/'p'
            x=mod.update_archive(o,root/'missing',dest)
            self.assertEqual(x['new_screens'],0)
            self.assertFalse(dest.exists())
    def test_missing_snapshot_archives_rejection_not_fake_success(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);e=root/'e';dest=root/'p';e.write_text(json.dumps(event())+'\n')
            x=mod.update_archive(root/'no_status',e,dest)
            self.assertEqual(x['new_rejected'],1)
            self.assertEqual(json.loads(dest.read_text())['reason'],'MISSING_MATCHING_STATUS')
    def test_duplicate_input_fails_without_edit(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);o=root/'o';e=root/'e';dest=root/'p'
            o.write_text(json.dumps(status())+'\n');e.write_text(2*(json.dumps(event())+'\n'))
            with self.assertRaises(ValueError):mod.update_archive(o,e,dest)
            self.assertFalse(dest.exists())
    def test_reject_timezone_missing(self):
        e=event();e['captured_at_utc']='2026-10-22T06:45:01'
        with self.assertRaises(ValueError):mod.screen(e,status())

if __name__=='__main__':unittest.main()
