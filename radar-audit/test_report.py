import unittest,importlib.util
from datetime import datetime,timedelta,timezone
from pathlib import Path
p=Path(__file__).parent/'report.py'
s=importlib.util.spec_from_file_location('radar_report',p);m=importlib.util.module_from_spec(s);s.loader.exec_module(m)
NOW=datetime(2026,10,22,7,0,tzinfo=timezone.utc)

def r(minute,ready,up=1000):
    return {'capture_at_utc':(NOW+timedelta(minutes=minute)).isoformat(),
            'live_ready_reported':ready,'reason':'ok' if ready else 'book_stale','up_seconds':up}

class Tests(unittest.TestCase):
    def test_gap_does_not_count_as_uptime(self):
        q=m.summarize([r(-60,True),r(-10,True)],now=NOW)
        self.assertEqual(q['max_gap_minutes'],50)
        self.assertEqual(q['coverage'],'SPARSE_SAMPLES_NOT_CONTINUOUS_UPTIME')
        self.assertFalse(q['pnl_evaluated'])
    def test_failed_fetch_counts_as_unready_observation(self):
        a=r(-20,False);a['reason']='FETCH_FAILED'
        q=m.summarize([a,r(-10,True)],now=NOW)
        self.assertEqual(q['fetch_failure_snapshots'],1)
        self.assertEqual(q['sample_ready_fraction_pct'],50)
    def test_restart_indications(self):
        q=m.summarize([r(-60,True,up=5000),r(-10,True,up=30)],now=NOW)
        self.assertEqual(q['uptime_reset_indications'],1)
    def test_no_observations(self):
        self.assertEqual(m.summarize([],now=NOW)['coverage'],'NO_OBSERVATIONS')
    def test_timezone_required(self):
        with self.assertRaises(ValueError):m.parse_dt('2026-10-22T09:00:00')
    def test_limits_window(self):
        with self.assertRaises(ValueError):m.summarize([],now=NOW,hours=0)
if __name__=='__main__':unittest.main()
