import sys,unittest,copy
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import audit_metrics


def rec(id,pnl,stamp,provenance='CAPTURED_LIVE_REVIEWED',status='CLOSED_ESTIMATE'):
 return {'trade_id':id,'status':status,'data_provenance':provenance,
         'incomplete_market_data':False,'independent_quote_times_checked':True,
         'funding_included':False,'hypothetical_fills_only':True,
         'estimated_realised_pnl_ex_funding_usdc':pnl,
         'events':[{'event':'ENTRY_ESTIMATE','at_utc':'2026-10-20T10:00:01Z'},
                   {'event':'TP2_ESTIMATE' if pnl>0 else 'STOP_ESTIMATE','at_utc':stamp}]}
class TestMetrics(unittest.TestCase):
 def test_empty(self):
  r=audit_metrics.evaluate([])
  self.assertIsNone(r['estimated_win_rate_pct']);self.assertEqual(r['counted_closed_reviewed_quotes'],0)
 def test_synthetic_excluded(self):
  a=rec('a',200,'2026-10-20T10:01:00Z','SYNTHETIC_EXAMPLE')
  r=audit_metrics.evaluate([a]);self.assertEqual(r['counted_closed_reviewed_quotes'],0)
 def test_unknown_excluded(self):
  a=rec('a',200,'2026-10-20T10:01:00Z','UNVERIFIED_EXTERNAL_QUOTES')
  r=audit_metrics.evaluate([a]);self.assertEqual(r['counted_closed_reviewed_quotes'],0)
 def test_gap_excluded(self):
  a=rec('a',200,'2026-10-20T10:01:00Z');a['incomplete_market_data']=True
  self.assertEqual(audit_metrics.evaluate([a])['counted_closed_reviewed_quotes'],0)
 def test_unsynced_excluded(self):
  a=rec('a',200,'2026-10-20T10:01:00Z');a['independent_quote_times_checked']=False
  self.assertEqual(audit_metrics.evaluate([a])['counted_closed_reviewed_quotes'],0)
 def test_open_excluded(self):
  a=rec('a',200,'2026-10-20T10:01:00Z',status='OPEN_UNREALIZED')
  self.assertEqual(audit_metrics.evaluate([a])['counted_closed_reviewed_quotes'],0)
 def test_bad_end_excluded(self):
  a=rec('a',200,'2026-10-20T10:01:00Z');a['events'][-1]['event']='UNKNOWN'
  self.assertEqual(audit_metrics.evaluate([a])['counted_closed_reviewed_quotes'],0)
 def test_duplicate_rejected(self):
  a=rec('a',200,'2026-10-20T10:01:00Z')
  with self.assertRaises(ValueError):audit_metrics.evaluate([a,a])
 def test_order_is_closure_time(self):
  # Loss closes earlier than win, despite reversed input.
  win=rec('W',40,'2026-10-20T10:03:00Z')
  loss=rec('L',-20,'2026-10-20T10:02:00Z')
  r=audit_metrics.evaluate([win,loss],explicitly_reviewed_ids={'W','L'});self.assertEqual([e['trade_id'] for e in r['closing_order_utc']],['L','W'])
  self.assertEqual(r['estimated_peak_to_trough_drawdown_usdc'],20)
  self.assertEqual(r['estimated_win_rate_pct'],50.0)
  self.assertEqual(r['estimated_profit_factor'],2)
 def test_no_profit_factor_without_loss(self):
  a=rec('a',20,'2026-10-20T10:03:00Z')
  self.assertIsNone(audit_metrics.evaluate([a],explicitly_reviewed_ids={'a'})['estimated_profit_factor'])
 def test_nan_refused(self):
  a=rec('a',5,'2026-10-20T10:03:00Z');a['estimated_realised_pnl_ex_funding_usdc']='NaN'
  with self.assertRaises(ValueError):audit_metrics.evaluate([a],explicitly_reviewed_ids={'a'})
 def test_missing_funding_disclosure(self):
  a=rec('a',20,'2026-10-20T10:03:00Z');del a['funding_included']
  self.assertEqual(audit_metrics.evaluate([a])['counted_closed_reviewed_quotes'],0)

if __name__=='__main__':unittest.main()
