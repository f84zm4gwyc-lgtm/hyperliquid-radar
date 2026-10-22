import sys,unittest,copy
from pathlib import Path
from datetime import datetime,timedelta,timezone
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import replay_quotes

PLAN={'trade_id':'v3-test','symbol':'BTC','side':'LONG','signal_status':'VALIDATED_PAPER_SIGNAL',
'alert_sent_at_utc':'2026-10-20T10:00:00Z','alert_received_at_utc':'2026-10-20T10:00:05Z',
'indicative_entry':81000,'stop':80000,'tp1':83000,'tp2':86000,'sz_decimals':5,
'entry_deadline_minutes':0.1}
BASE=datetime(2026,10,20,10,tzinfo=timezone.utc)
def quote(seconds,lag=0):
 observed=BASE+timedelta(seconds=seconds)
 captured=observed+timedelta(seconds=lag)
 return {'symbol':'BTC','observed_at_utc':observed.isoformat(),
         'captured_at_utc':captured.isoformat(),'mark_observed_at_utc':observed.isoformat(),
         'book_observed_at_utc':observed.isoformat(),'bid':'80999','ask':'81001','mark':'81000'}
class QuoteTimeSafety(unittest.TestCase):
 def test_capture_after_deadline_is_skipped(self):
  # Old V2 would accept observed t=10 before deadline=11 even if received at t=14.
  result=replay_quotes.replay(PLAN,[quote(10,4)])
  self.assertEqual(result['status'],'NO_ENTRY')
 def test_capture_is_action_time(self):
  result=replay_quotes.replay(PLAN,[quote(6,1)])
  self.assertEqual(result['status'],'OPEN_UNREALIZED')
  self.assertEqual(result['events'][0]['at_utc'],quote(6,1)['captured_at_utc'])
 def test_bad_independent_skew(self):
  row=quote(6);row['book_observed_at_utc']=(BASE+timedelta(seconds=2)).isoformat()
  with self.assertRaises(ValueError):replay_quotes.replay(PLAN,[row])
 def test_stale_mark(self):
  row=quote(6);row['mark_observed_at_utc']=(BASE).isoformat()
  with self.assertRaises(ValueError):replay_quotes.replay(PLAN,[row])
 def test_capture_order_rejects_backfill(self):
  with self.assertRaisesRegex(ValueError,'capture timestamps'):
   replay_quotes.replay(PLAN,[quote(6,3),quote(7,1)])
 def test_live_claim_must_have_independent_ts(self):
  row=quote(6);del row['book_observed_at_utc']
  with self.assertRaisesRegex(ValueError,'independent mark and book'):
   replay_quotes.replay(PLAN,[row],provenance='CAPTURED_LIVE_REVIEWED')
 def test_unknown_source_cannot_be_silently_reviewed(self):
  row=quote(6);del row['book_observed_at_utc']
  result=replay_quotes.replay(PLAN,[row],provenance='UNVERIFIED_EXTERNAL_QUOTES')
  self.assertFalse(result['independent_quote_times_checked'])

if __name__=='__main__': unittest.main()
