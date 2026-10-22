import sys,unittest,tempfile,copy,json
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import journal

NOW='2026-10-20T10:07:20+00:00'
CAND={'id':'BTC:15m:1792490400000:LONG','coin':'BTC','direction':'LONG',
      'closed_at_ms':1792491300000,'close':81000,'status':'WATCH_ONLY'}
STATUS={'system':'radar-readonly-v1','live_ready':True,'reason':'ok',
        'assets':{'BTC':{'mark':81000},'ETH':{'mark':3000}},
        'watch_only_candidates':[CAND]}

class TestJournal(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.path=Path(self.tmp.name)/'journal.jsonl'
 def tearDown(self):self.tmp.cleanup()
 def test_initial_empty(self):self.assertEqual(journal.verify_records(journal.load(self.path))['event_count'],0)
 def test_append_roundtrip(self):
  e=journal.append(self.path,'WATCH_ONLY',CAND,NOW)
  self.assertEqual(e['seq'],1);self.assertEqual(journal.load(self.path)[0]['hash'],e['hash'])
 def test_status_archives_candidate(self):
  result=journal.capture_status(self.path,STATUS,NOW)
  self.assertEqual(result['new_watch_only_candidates'],1)
  self.assertEqual(len(journal.load(self.path)),2)
 def test_idempotent_candidate_dedupe_but_status_retained(self):
  journal.capture_status(self.path,STATUS,NOW)
  result=journal.capture_status(self.path,STATUS,NOW)
  self.assertEqual(result['new_watch_only_candidates'],0)
  self.assertEqual(len(journal.load(self.path)),3)
 def test_reject_promotion(self):
  with self.assertRaises(ValueError):journal.append(self.path,'WATCH_ONLY',dict(CAND,status='VALIDATED_PAPER_SIGNAL'),NOW)
 def test_reject_unvalidated_paper(self):
  with self.assertRaises(ValueError):journal.append(self.path,'PAPER_SIGNAL',dict(CAND,status='WATCH_ONLY'),NOW)
 def test_unknown_event(self):
  with self.assertRaises(ValueError):journal.append(self.path,'REAL_ORDER',{},NOW)
 def test_wrong_timestamp(self):
  with self.assertRaises(ValueError):journal.append(self.path,'WATCH_ONLY',CAND,'2026-10-20T10:07:20')
 def test_tamper_detected(self):
  journal.append(self.path,'WATCH_ONLY',CAND,NOW)
  s=self.path.read_text().replace('81000','81999')
  self.path.write_text(s)
  with self.assertRaisesRegex(ValueError,'Hash mismatch'):journal.load(self.path)
 def test_hash_chain_detects_deleting_first(self):
  journal.append(self.path,'WATCH_ONLY',CAND,NOW)
  journal.append(self.path,'WATCH_ONLY',dict(CAND,id='OTHER'),NOW)
  lines=self.path.read_text().splitlines();self.path.write_text(lines[1]+'\n')
  with self.assertRaisesRegex(ValueError,'sequence'):journal.load(self.path)
 def test_fail_without_partial_writes(self):
  bad=copy.deepcopy(STATUS);bad['watch_only_candidates']=[dict(CAND,status='HACK')]
  with self.assertRaises(ValueError):journal.capture_status(self.path,bad,NOW)
  self.assertFalse(self.path.exists())
 def test_allow_unhealthy_status_archiving(self):
  status=dict(STATUS,live_ready=False,reason='history_stale')
  r=journal.capture_status(self.path,status,NOW)
  self.assertEqual(r['new_watch_only_candidates'],1)
  self.assertFalse(journal.load(self.path)[0]['payload']['status']['live_ready'])
 def test_wrong_schema(self):
  with self.assertRaises(ValueError):journal.capture_status(self.path,dict(STATUS,system='unknown'),NOW)
 def test_status_empty_events(self):
  status=dict(STATUS,watch_only_candidates=[])
  x=journal.capture_status(self.path,status,NOW)
  self.assertEqual(x['new_watch_only_candidates'],0)
  self.assertEqual(len(journal.load(self.path)),1)

if __name__=='__main__':unittest.main()
