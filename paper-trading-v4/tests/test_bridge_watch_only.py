import json,sys,tempfile,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from import_watch_only import bridge
from journal import load

E={'id':'BTC:15m:12345:LONG','coin':'BTC','direction':'LONG','status':'WATCH_ONLY'}
R={'source':'RENDER_PUBLIC_WATCH_ONLY','pnl_eligible':False,'captured_at_utc':'2026-10-22T00:00:00Z','event':E}
class Tests(unittest.TestCase):
    def test_bridge_dedup_and_journal_integrity(self):
        with tempfile.TemporaryDirectory() as d:
            src=Path(d)/'src';dst=Path(d)/'journal';src.write_text(json.dumps(R)+'\n')
            self.assertEqual(bridge(src,dst)['new'],1)
            self.assertEqual(bridge(src,dst)['new'],0)
            saved=load(dst)
            self.assertEqual(len(saved),1)
            self.assertEqual(saved[0]['event_type'],'WATCH_ONLY')
    def test_never_promote_validated_signal(self):
        with tempfile.TemporaryDirectory() as d:
            src=Path(d)/'src';dst=Path(d)/'journal'
            src.write_text(json.dumps({**R,'event':{**E,'status':'VALIDATED_PAPER_SIGNAL'}})+'\n')
            with self.assertRaises(ValueError):bridge(src,dst)
            self.assertFalse(dst.exists())
    def test_missing_source_is_not_success_evidence(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertTrue(bridge(Path(d)/'absent',Path(d)/'journal')['source_missing'])
    def test_reject_data_provenance_switch(self):
        with tempfile.TemporaryDirectory() as d:
            src=Path(d)/'src';dst=Path(d)/'journal'
            src.write_text(json.dumps({**R,'source':'FAKE_LIVE'})+'\n')
            with self.assertRaises(ValueError):bridge(src,dst)
if __name__=='__main__':unittest.main()
