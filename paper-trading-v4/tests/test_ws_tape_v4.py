import json
import sys
import tempfile
import unittest
from pathlib import Path
from datetime import datetime,timedelta,timezone
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from ws_tape import QuoteAssembler,convert
from collect_ws import to_envelope
from replay_quotes import replay
from paper_radar import iso

START=datetime(2026,10,20,12,0,0,tzinfo=timezone.utc)
def t(n):return (START+timedelta(seconds=n)).isoformat().replace('+00:00','Z')
def mark(n,price='80000',coin='BTC',source='RECORDED_PUBLIC_WS_UNREVIEWED'):
 return {'captured_at_utc':t(n),'raw_feed_provenance':source,
         'ws':{'channel':'activeAssetCtx','data':{'coin':coin,'ctx':{'markPx':price}}}}
def bbo(n,price1='79999',price2='80001',coin='BTC',event_n=None,source='RECORDED_PUBLIC_WS_UNREVIEWED'):
 return {'captured_at_utc':t(n),'raw_feed_provenance':source,
         'ws':{'channel':'bbo','data':{'coin':coin,'time':int((START+timedelta(seconds=n if event_n is None else event_n)).timestamp()*1000),
                                      'bbo':[{'px':price1,'sz':'1','n':1},{'px':price2,'sz':'1','n':1}]}}}
PLAN={'trade_id':'tape-v4','symbol':'BTC','side':'LONG','signal_status':'VALIDATED_PAPER_SIGNAL',
      'alert_sent_at_utc':t(-10),'alert_received_at_utc':t(-9),
      'indicative_entry':80000,'stop':79500,'tp1':81500,'tp2':83500,'sz_decimals':5}

class WSQuoteAssembler(unittest.TestCase):
 def test_sync_pair_creates_unreviewed_quote(self):
  a=QuoteAssembler()
  self.assertIsNone(a.process(bbo(0)))
  q=a.process(mark(1))
  self.assertEqual(q['symbol'],'BTC');self.assertEqual(q['bid'],'79999')
  self.assertEqual(q['mark_time_basis'],'LOCAL_RECEIPT_NO_EXCHANGE_TIMESTAMP')
  self.assertEqual(q['book_time_basis'],'EXCHANGE_EVENT_TIMESTAMP')
  self.assertEqual(q['captured_at_utc'],t(1))
 def test_no_quote_without_two_independent_channels(self):
  a=QuoteAssembler()
  self.assertIsNone(a.process(mark(0)))
  self.assertIsNone(a.process(mark(1)))
 def test_bbo_clock_in_future_rejected(self):
  a=QuoteAssembler();a.process(mark(0))
  self.assertIsNone(a.process(bbo(1,event_n=3)))
  self.assertEqual(a.rejected,1)
 def test_old_bbo_rejected(self):
  a=QuoteAssembler();a.process(mark(0))
  self.assertIsNone(a.process(bbo(5,event_n=0)))
 def test_no_stale_marks_after_3_sec(self):
  a=QuoteAssembler();a.process(mark(0))
  self.assertIsNone(a.process(bbo(4)))
 def test_overspread_rejected(self):
  a=QuoteAssembler();a.process(mark(0))
  self.assertIsNone(a.process(bbo(1,'78000','82000')))
 def test_no_crossed_book(self):
  a=QuoteAssembler();a.process(mark(0))
  self.assertIsNone(a.process(bbo(1,'80002','80001')))
 def test_out_of_order_arrivals_rejected(self):
  a=QuoteAssembler();a.process(bbo(2))
  self.assertIsNone(a.process(mark(1)))
  self.assertEqual(a.rejected,1)
 def test_cross_symbol_mixing_never_happens(self):
  a=QuoteAssembler();a.process(bbo(0,coin='ETH'))
  self.assertIsNone(a.process(mark(1,coin='BTC')))
 def test_unknown_coin_rejected(self):
  a=QuoteAssembler();self.assertIsNone(a.process(mark(1,coin='DOGE')))
  self.assertEqual(a.rejected,1)
 def test_throttles_duplicates_per_symbol(self):
  a=QuoteAssembler(min_interval_seconds=2)
  a.process(bbo(0))
  self.assertIsNotNone(a.process(mark(1)))
  self.assertIsNone(a.process(mark(1.5)))
  self.assertIsNotNone(a.process(mark(3)))
 def test_receipt_basis_never_promoted(self):
  a=QuoteAssembler();a.process(bbo(0));q=a.process(mark(1))
  q['data_provenance']='RECORDED_PUBLIC_WS_UNREVIEWED'
  with self.assertRaisesRegex(ValueError,'Local mark receipt'):
   replay(PLAN,[q],provenance='CAPTURED_LIVE_REVIEWED')
 def test_prevents_source_relabeling(self):
  a=QuoteAssembler();a.process(bbo(0));q=a.process(mark(1))
  q['data_provenance']='RECORDED_PUBLIC_WS_UNREVIEWED'
  with self.assertRaisesRegex(ValueError,'provenance mismatch'):
   replay(PLAN,[q],provenance='UNVERIFIED_EXTERNAL_QUOTES')
 def test_unreviewed_replay_stays_unreviewed(self):
  a=QuoteAssembler();a.process(bbo(0));q=a.process(mark(1))
  q['data_provenance']='RECORDED_PUBLIC_WS_UNREVIEWED'
  r=replay(PLAN,[q],provenance='RECORDED_PUBLIC_WS_UNREVIEWED')
  self.assertEqual(r['data_provenance'],'RECORDED_PUBLIC_WS_UNREVIEWED')
  self.assertEqual(r['status'],'OPEN_UNREALIZED')
 def test_json_recorder_envelope(self):
  s=json.dumps(bbo(0)['ws']);row=to_envelope(s,t(2))
  self.assertEqual(row['captured_at_utc'],t(2))
  self.assertEqual(row['raw_feed_provenance'],'RECORDED_PUBLIC_WS_UNREVIEWED')
 def test_convert_and_no_overwrite(self):
  with tempfile.TemporaryDirectory() as tmp:
   src=Path(tmp)/'raw.jsonl';dest=Path(tmp)/'quotes.jsonl'
   src.write_text('\n'.join(json.dumps(x) for x in [bbo(0),mark(1)])+'\n')
   result=convert(src,dest,source='RECORDED_PUBLIC_WS_UNREVIEWED')
   self.assertEqual(result['quotes'],1)
   self.assertFalse(result['paper_pnl_eligible'])
   self.assertEqual(len(dest.read_text().splitlines()),1)
   with self.assertRaises(FileExistsError):convert(src,dest,source='RECORDED_PUBLIC_WS_UNREVIEWED')
 def test_convert_rejects_fabricated_source(self):
  with tempfile.TemporaryDirectory() as tmp:
   src=Path(tmp)/'raw.jsonl';dest=Path(tmp)/'q.jsonl'
   src.write_text(json.dumps(mark(0,source='SYNTHETIC_EXAMPLE'))+'\n')
   with self.assertRaisesRegex(ValueError,'mismatched'):
    convert(src,dest,source='RECORDED_PUBLIC_WS_UNREVIEWED')
   self.assertFalse(dest.exists())
 def test_no_nan_prices(self):
  a=QuoteAssembler();a.process(mark(0))
  self.assertIsNone(a.process(bbo(1,'NaN','80001')))
 def test_mark_reject_negative(self):
  a=QuoteAssembler();self.assertIsNone(a.process(mark(0,'-1')))

if __name__=='__main__':unittest.main()
