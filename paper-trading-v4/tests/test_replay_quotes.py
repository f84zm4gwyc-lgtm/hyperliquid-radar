import sys,unittest,copy
from pathlib import Path
from datetime import datetime,timedelta,timezone
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import replay_quotes as m
import metrics

B={
    'trade_id':'QUOTE-DEMO-01','symbol':'BTC','side':'LONG',
    'signal_status':'VALIDATED_PAPER_SIGNAL',
    'alert_sent_at_utc':'2026-10-20T10:00:00+00:00',
    'alert_received_at_utc':'2026-10-20T10:00:05+00:00',
    'indicative_entry':81000,'stop':80000,'tp1':83000,'tp2':86000,
    'sz_decimals':5,'entry_deadline_minutes':5}
BASE=datetime(2026,10,20,10,0,0,tzinfo=timezone.utc)

def q(seconds,mark,bid=None,ask=None,delay=0):
    stamp=(BASE+timedelta(seconds=seconds)).isoformat()
    capture=(BASE+timedelta(seconds=seconds+delay)).isoformat()
    return {'symbol':'BTC','observed_at_utc':stamp,'captured_at_utc':capture,
            'bid':str(bid if bid is not None else mark-1),
            'ask':str(ask if ask is not None else mark+1),'mark':str(mark)}

class Replay(unittest.TestCase):
    def test_no_future_leakage_before_receipt(self):
        x=m.replay(B,[q(1,79000),q(6,81000),q(10,83100),q(14,86100)])
        self.assertEqual(x['status'],'CLOSED_ESTIMATE')
        self.assertEqual([e['event'] for e in x['events']],['ENTRY_ESTIMATE','TP1_ESTIMATE','TP2_ESTIMATE'])
        self.assertEqual(x['events'][0]['at_utc'],q(6,81000)['observed_at_utc'])
    def test_invalid_candidate_rejected(self):
        with self.assertRaisesRegex(ValueError,'WATCH_ONLY'):
            m.replay(dict(B,signal_status='WATCH_ONLY'),[q(6,81000)])
    def test_receipt_before_sent_rejected(self):
        with self.assertRaises(ValueError):m.replay(dict(B,alert_received_at_utc='2026-10-20T09:59:00+00:00'),[q(6,81000)])
    def test_alert_time_needed(self):
        a=copy.deepcopy(B);del a['alert_sent_at_utc']
        with self.assertRaisesRegex(ValueError,'sent timestamp'):m.replay(a,[q(6,81000)])
    def test_quote_after_deadline_skipped(self):
        x=m.replay(B,[q(306,81000)])
        self.assertEqual(x['status'],'NO_ENTRY')
        self.assertEqual(x['events'][0]['event'],'SKIP')
    def test_entry_moved_skipped(self):
        x=m.replay(B,[q(6,85000)])
        self.assertEqual(x['status'],'NO_ENTRY')
    def test_stop_mark_quote_exit(self):
        x=m.replay(B,[q(6,81000),q(10,79900,79890,79910)])
        self.assertEqual(x['status'],'CLOSED_ESTIMATE')
        self.assertEqual(x['events'][-1]['event'],'STOP_ESTIMATE')
        self.assertLess(x['events'][-1]['estimated_fill'],79900)
    def test_entry_fee_charged(self):
        x=m.replay(B,[q(6,81000)])
        self.assertEqual(x['status'],'OPEN_UNREALIZED')
        self.assertLess(x['estimated_realised_pnl_ex_funding_usdc'],0)
    def test_short_stop(self):
        a=dict(B,side='SHORT',stop=82000,tp1=79000,tp2=76000)
        x=m.replay(a,[q(6,81000),q(10,82200)])
        self.assertEqual(x['events'][-1]['event'],'STOP_ESTIMATE')
        self.assertGreater(x['events'][-1]['estimated_fill'],82200)
    def test_short_tps(self):
        a=dict(B,side='SHORT',stop=82000,tp1=79000,tp2=76000)
        x=m.replay(a,[q(6,81000),q(10,78000),q(15,75800)])
        self.assertEqual([z['event'] for z in x['events']],['ENTRY_ESTIMATE','TP1_ESTIMATE','TP2_ESTIMATE'])
        self.assertGreater(x['estimated_realised_pnl_ex_funding_usdc'],0)
    def test_quote_invalid_crossed(self):
        with self.assertRaises(ValueError):m.replay(B,[q(6,81000,bid=81002,ask=81001)])
    def test_quote_invalid_out_of_order(self):
        with self.assertRaisesRegex(ValueError,'Unsorted'):
            m.replay(B,[q(10,81000),q(6,81000)])
    def test_stale_quote_rejected(self):
        with self.assertRaisesRegex(ValueError,'latency'):
            m.replay(B,[q(6,81000,delay=40)])
    def test_unknown_quote_symbol_rejected(self):
        t=q(6,81000);t['symbol']='ETH'
        with self.assertRaises(ValueError):m.replay(B,[t])
    def test_large_gap_is_low_confidence(self):
        a=dict(B,entry_deadline_minutes=30)
        x=m.replay(a,[q(6,81000),q(200,87000)],max_gap_seconds=60)
        self.assertEqual(x['status'],'CLOSED_LOW_CONFIDENCE')
        self.assertTrue(x['incomplete_market_data'])
        score=metrics.score([x]);self.assertEqual(score['closed_trade_count'],0)
    def test_no_trigger_does_not_close(self):
        x=m.replay(B,[q(6,81000),q(10,81400),q(12,81600)])
        self.assertEqual(x['status'],'OPEN_UNREALIZED')
    def test_funding_excluded_explicit(self):
        x=m.replay(B,[q(6,81000),q(9,83100),q(12,86100)])
        self.assertFalse(x['funding_included'])
        self.assertTrue(x['hypothetical_fills_only'])
    def test_same_quote_price_tp1_tp2_no_double_exit(self):
        x=m.replay(B,[q(6,81000),q(9,87000)])
        self.assertEqual(x['status'],'CLOSED_ESTIMATE')
        self.assertEqual([e['event'] for e in x['events']],['ENTRY_ESTIMATE','TP1_ESTIMATE','TP2_ESTIMATE'])
        self.assertEqual(round(sum(e['quantity'] for e in x['events'] if e['event']!='ENTRY_ESTIMATE'),5),round(x['position']['quantity'],5))

if __name__=='__main__': unittest.main()
