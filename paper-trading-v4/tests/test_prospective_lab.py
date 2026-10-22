"""No network, no exchange orders. Synthetic scenarios only."""
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import prospective_lab as lab

T=datetime(2026,10,22,9,15,tzinfo=timezone.utc)


def stamp(t):return lab.fmt(t)

def asset(coin='BTC',side='LONG'):
    mark=100.0
    if side=='LONG':
        h1={'last_close':110.,'ema20':104.,'ema50':99.}; h4={'last_close':110.,'ema20':104.,'ema50':99.}
        f={'closed_above_prior20_high':True,'closed_below_prior20_low':False,
           'prior20_high':98.,'prior20_low':87.}
    else:
        h1={'last_close':95.,'ema20':103.,'ema50':105.}; h4={'last_close':95.,'ema20':103.,'ema50':105.}
        f={'closed_above_prior20_high':False,'closed_below_prior20_low':True,
           'prior20_high':118.,'prior20_low':102.}
    return {'timeframes':{'15m':{'last_closed_candle_utc':stamp(T-timedelta(seconds=60)),
                                 'last_close':mark,'volume_vs_previous20':1.7,'atr14':0.8,**f},
                          '1h':h1,'4h':h4},
            'perp_context':{'price_observed_at_utc':stamp(T-timedelta(seconds=4)),
                            'mark_price_usdc':mark,'size_decimals':4},
            'order_book':{'observed_at_utc':stamp(T-timedelta(seconds=3)),
                          'best_bid':99.99,'best_ask':100.01}}

def snapshot(side='LONG'):
    return {'schema_version':2,'updated_at_utc':stamp(T),'collection_started_at_utc':stamp(T-timedelta(seconds=15)),
            'assets':{'BTC':asset(side=side),'ETH':{'perp_context':{}}}}

def bars(start,num, coin='BTC',open_=100., high=100.2,low=99.8, close=100.):
    result=[]
    for j in range(num):
        t=start+timedelta(minutes=j)
        ms=int(t.timestamp()*1000)
        result.append({'t':ms,'T':ms+59_999,'s':coin,'i':'1m','o':str(open_),
                       'h':str(high),'l':str(low),'c':str(close),'v':'10'})
    return result

class Scanner(unittest.TestCase):
    def test_long_real_plan_only_at_fresh_scan(self):
        plans=lab.scan(snapshot(),T+timedelta(seconds=5))
        self.assertEqual(len(plans),1)
        self.assertEqual(plans[0]['side'],'LONG')
        self.assertEqual(plans[0]['signal_status'],'PROSPECTIVE_PAPER_HYPOTHESIS')
        self.assertTrue(plans[0]['paper_proxy_only'])
        self.assertTrue(lab.position(plans[0])['accepted'])
        self.assertFalse(lab.position(plans[0])['ready_for_real_trade'])
    def test_short_hypothesis(self):
        p=lab.scan(snapshot('SHORT'),T+timedelta(seconds=2))
        self.assertEqual(len(p),1)
        self.assertEqual(p[0]['side'],'SHORT')
    def test_stale_snapshot_cannot_backdate(self):
        self.assertEqual(lab.scan(snapshot(),T+timedelta(seconds=92)),[])
    def test_future_snapshot_forbidden(self):
        self.assertEqual(lab.scan(snapshot(),T-timedelta(seconds=5)),[])
    def test_does_not_trade_low_volume(self):
        s=snapshot();s['assets']['BTC']['timeframes']['15m']['volume_vs_previous20']=1.2
        self.assertEqual(lab.scan(s,T),[])
    def test_invalid_market_quote_skips(self):
        s=snapshot();s['assets']['BTC']['order_book']['best_bid']=100.1
        self.assertEqual(lab.scan(s,T),[])
    def test_outdated_market_quote_skips(self):
        s=snapshot();s['assets']['BTC']['perp_context']['price_observed_at_utc']=stamp(T-timedelta(seconds=60))
        self.assertEqual(lab.scan(s,T),[])
    def test_mark_vs_close_mismatch_skips(self):
        s=snapshot();s['assets']['BTC']['perp_context']['mark_price_usdc']=120
        self.assertEqual(lab.scan(s,T),[])
    def test_nonfinite_protection(self):
        s=snapshot();s['assets']['BTC']['perp_context']['mark_price_usdc']='NaN'
        self.assertEqual(lab.scan(s,T),[])

class ForwardTesting(unittest.TestCase):
    def setUp(self):
        self.plan=lab.scan(snapshot(),T)[0]
        self.commit=T+timedelta(seconds=18)
        self.first=lab.eligibility_time(self.plan,self.commit)
        self.assertEqual(self.first,T+timedelta(minutes=2))
    def test_never_consider_price_before_commit_plus_buffer(self):
        pre=bars(self.commit.replace(second=0),2,open_=100,high=200,low=20)
        post=bars(self.first,3,open_=100,high=100.1,low=99.8)
        outcome=lab.evaluate(self.plan,stamp(self.commit),pre+post,evaluated_at_utc=stamp(self.first+timedelta(minutes=3)))
        self.assertEqual(outcome['status'],'OHLCV_PROXY_OPEN_UNREALIZED')
        self.assertFalse(outcome['pnl_eligible'])
        self.assertEqual(outcome['sim_events'][0]['event'],'ENTRY')
    def test_commit_cannot_restart_expired_original_validity(self):
        late_commit=T+timedelta(minutes=30)
        first=lab.eligibility_time(self.plan,late_commit)
        out=lab.evaluate(self.plan,stamp(late_commit),bars(first,3),
                         evaluated_at_utc=stamp(first+timedelta(minutes=3)))
        self.assertEqual(out['status'],'OHLCV_PROXY_NO_ENTRY')
        self.assertEqual(out['original_entry_deadline_utc'],stamp(T+timedelta(minutes=5)))
        self.assertNotIn('sim_events',out)
    def test_postcommit_buffer_cannot_extend_nearly_expired_plan(self):
        committed=T+timedelta(minutes=4,seconds=10)
        out=lab.evaluate(self.plan,stamp(committed),[],evaluated_at_utc=stamp(T+timedelta(minutes=8)))
        self.assertEqual(out['status'],'OHLCV_PROXY_NO_ENTRY')
    def test_gap_before_tp_must_fail_closed(self):
        rows=bars(self.first,4)
        rows.pop(2)
        rows[-1]['h']='140'
        out=lab.evaluate(self.plan,stamp(self.commit),rows,evaluated_at_utc=stamp(self.first+timedelta(minutes=4)))
        self.assertEqual(out['status'],'UNVERIFIABLE')
        self.assertNotIn('estimated_pnl_ex_funding_usdc',out)
    def test_closed_stop_is_proxy_not_validated(self):
        rows=bars(self.first,3,low=90)
        out=lab.evaluate(self.plan,stamp(self.commit),rows,evaluated_at_utc=stamp(self.first+timedelta(minutes=3)))
        self.assertEqual(out['status'],'OHLCV_PROXY_CLOSED_ESTIMATE')
        self.assertLess(out['estimated_pnl_ex_funding_usdc'],0)
        self.assertFalse(out['pnl_eligible'])
        self.assertEqual(out['sim_events'][-1]['event'],'STOP')
    def test_same_bar_stop_and_tp_stop_wins(self):
        rows=bars(self.first,2,low=90,high=120)
        out=lab.evaluate(self.plan,stamp(self.commit),rows,evaluated_at_utc=stamp(self.first+timedelta(minutes=2)))
        self.assertEqual(out['sim_events'][-1]['event'],'STOP')
        self.assertTrue(out['sim_events'][-1]['same_bar_tp_ambiguous'])
    def test_refuses_non_committed_evidence(self):
        with self.assertRaisesRegex(ValueError,'Commit predates'):
            lab.evaluate(self.plan,stamp(T-timedelta(minutes=1)),[],evaluated_at_utc=stamp(T+timedelta(minutes=3)))
    def test_unknown_provenance_rejected(self):
        p=dict(self.plan,signal_status='WATCH_ONLY')
        with self.assertRaisesRegex(ValueError,'independently committed'):
            lab.evaluate(p,stamp(self.commit),[],evaluated_at_utc=stamp(T+timedelta(minutes=3)))
    def test_old_more_than_three_days_unverifiable(self):
        out=lab.evaluate(self.plan,stamp(self.commit),[],evaluated_at_utc=stamp(T+timedelta(days=4)))
        self.assertEqual(out['status'],'UNVERIFIABLE')
    def test_1m_end_missing_rejects(self):
        rows=bars(self.first,2)
        out=lab.evaluate(self.plan,stamp(self.commit),rows,evaluated_at_utc=stamp(self.first+timedelta(minutes=4)))
        self.assertEqual(out['status'],'UNVERIFIABLE')
    def test_duplicate_minute_rejects(self):
        rows=bars(self.first,2)
        out=lab.evaluate(self.plan,stamp(self.commit),rows+rows[-1:],evaluated_at_utc=stamp(self.first+timedelta(minutes=2)))
        self.assertEqual(out['status'],'UNVERIFIABLE')
    def test_still_waiting_for_first_full_minute(self):
        out=lab.evaluate(self.plan,stamp(self.commit),[],evaluated_at_utc=stamp(self.first+timedelta(seconds=40)))
        self.assertEqual(out['status'],'WAIT_MORE_DATA')
    def test_no_http_in_simulation(self):
        with patch.object(lab,'urlopen',side_effect=AssertionError('No network')):
            out=lab.evaluate(self.plan,stamp(self.commit),bars(self.first,2),evaluated_at_utc=stamp(self.first+timedelta(minutes=2)))
        self.assertEqual(out['source'],'OHLCV_1M_TRADE_PRICE_NOT_MARK')

if __name__=='__main__':unittest.main()

class DataSourceQuality(unittest.TestCase):
    def test_no_future_or_unclosed_bars_in_replay(self):
        plan=lab.scan(snapshot(),T)[0]
        committed=T+timedelta(seconds=20)
        start=lab.eligibility_time(plan,committed)
        feed=bars(start,4)
        feed[-1]['T']+=60_000
        with self.assertRaisesRegex(ValueError,'Bad 1m metadata'):
            lab.evaluate(plan,stamp(committed),feed,evaluated_at_utc=stamp(start+timedelta(minutes=4)))
    def test_scan_does_not_emit_when_older_candle_after_snapshot(self):
        s=snapshot();s['assets']['BTC']['timeframes']['15m']['last_closed_candle_utc']=stamp(T+timedelta(seconds=10))
        self.assertEqual(lab.scan(s,T),[])
    def test_mark_nan_does_not_become_plan(self):
        s=snapshot();s['assets']['BTC']['order_book']['best_ask']='Infinity'
        self.assertEqual(lab.scan(s,T),[])
    def test_snapshot_timestamp_requires_timezone(self):
        s=snapshot();s['updated_at_utc']='2026-10-22T09:15:00'
        with self.assertRaises(ValueError):lab.scan(s,T)

class StatisticalHonesty(unittest.TestCase):
    def test_zero_trades_does_not_report_win_rate(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)
            s=lab.summarize_paper(p,p)
            self.assertEqual(s['immutable_hypotheses_recorded'],0)
            self.assertIsNone(s['closed_proxy_win_rate_pct'])
            self.assertFalse(s['validated_edge'])
    def test_closed_proxy_cannot_be_called_validated(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp);a=p/'plans';b=p/'results';a.mkdir();b.mkdir()
            (a/'a.json').write_text('{}')
            (b/'a.json').write_text('{"status":"OHLCV_PROXY_CLOSED_ESTIMATE","estimated_pnl_ex_funding_usdc":7.25}')
            s=lab.summarize_paper(a,b)
            self.assertEqual(s['closed_proxy_count'],1)
            self.assertEqual(s['closed_proxy_estimated_pnl_ex_funding_usdc'],7.25)
            self.assertEqual(s['validated_trades'],0)
            self.assertFalse(s['validated_edge'])

class GitEvidenceIntegration(unittest.TestCase):
    def test_shallow_clone_cannot_claim_latest_snapshot_as_plan_creation(self):
        import os, subprocess, tempfile, json
        previous=Path.cwd()
        with tempfile.TemporaryDirectory() as tmp:
            origin=Path(tmp)/'origin';origin.mkdir()
            try:
                os.chdir(origin)
                for args in (['init','-q'], ['config','user.name','OfflineAuditBot'],
                             ['config','user.email','offline@example.org']):
                    subprocess.run(['git',*args],check=True)
                p=Path('plan.json');p.write_text(json.dumps(lab.scan(snapshot(),T)[0]))
                subprocess.run(['git','add','plan.json'],check=True)
                subprocess.run(['git','commit','-qm','Original plan'],check=True)
                Path('unrelated.txt').write_text('A later snapshot')
                subprocess.run(['git','add','unrelated.txt'],check=True)
                subprocess.run(['git','commit','-qm','Unrelated later snapshot'],check=True)
                clone=Path(tmp)/'shallow'
                subprocess.run(['git','clone','-q','--depth=1',origin.as_uri(),str(clone)],check=True)
                os.chdir(clone)
                with self.assertRaisesRegex(ValueError,'Full git history'):
                    lab.commit_time(Path('plan.json'))
            finally:os.chdir(previous)
    def test_git_committed_plan_precedes_first_eligible_minute(self):
        """REAL git commit; future candles are synthetic, never a live paper trade."""
        import os
        import subprocess
        import tempfile
        import json
        prior=Path.cwd()
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            try:
                os.chdir(root)
                subprocess.run(['git','init','-q'],check=True)
                subprocess.run(['git','config','user.name','OfflineAuditBot'],check=True)
                subprocess.run(['git','config','user.email','offline@example.org'],check=True)
                path=Path('data/plans/btc.json');path.parent.mkdir(parents=True)
                plan=lab.scan(snapshot(),T)[0]
                path.write_text(json.dumps(plan),encoding='utf-8')
                with self.assertRaisesRegex(ValueError,'not yet committed'):
                    lab.commit_time(path)
                subprocess.run(['git','add',str(path)],check=True)
                commit_at=T+timedelta(seconds=18)
                env=dict(os.environ,GIT_AUTHOR_DATE=stamp(commit_at),GIT_COMMITTER_DATE=stamp(commit_at))
                subprocess.run(['git','commit','-qm','Commit before observing future'],check=True,env=env)
                audited=lab.commit_time(path)
                self.assertEqual(lab.iso(audited),commit_at)
                first=lab.eligibility_time(plan,lab.iso(audited))
                self.assertGreater(first,commit_at)
                future=bars(first,3,low=90)
                result=lab.evaluate(plan,audited,future,evaluated_at_utc=stamp(first+timedelta(minutes=3)))
                self.assertEqual(result['status'],'OHLCV_PROXY_CLOSED_ESTIMATE')
                self.assertFalse(result['pnl_eligible'])
                self.assertEqual(result['first_eligible_1m_candle_utc'],stamp(first))
            finally:os.chdir(prior)


class ProductionGateTests(unittest.TestCase):
    def test_simultaneous_btc_eth_signals_produce_single_candidate(self):
        x=snapshot()
        x['assets']['ETH']=asset(coin='ETH',side='LONG')
        picks=lab.scan(x,T)
        self.assertEqual(len(picks),1)
        self.assertEqual(picks[0]['symbol'],'BTC')

    def test_modified_plan_after_commit_must_fail_closed(self):
        import os
        import tempfile
        import subprocess
        import json
        prior=Path.cwd()
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            try:
                os.chdir(root)
                subprocess.run(['git','init','-q'],check=True)
                subprocess.run(['git','config','user.name','OfflineAuditBot'],check=True)
                subprocess.run(['git','config','user.email','offline@example.org'],check=True)
                plan_path=Path('paper-trading-v4/data/plans/immutable.json')
                plan_path.parent.mkdir(parents=True)
                plan=lab.scan(snapshot(),T)[0]
                plan_path.write_text(json.dumps(plan))
                subprocess.run(['git','add',str(plan_path)],check=True)
                env=dict(os.environ,GIT_AUTHOR_DATE=stamp(T+timedelta(seconds=10)),
                         GIT_COMMITTER_DATE=stamp(T+timedelta(seconds=10)))
                subprocess.run(['git','commit','-qm','first plan'],check=True,env=env)
                self.assertEqual(lab.iso(lab.commit_time(plan_path)),T+timedelta(seconds=10))
                plan['tp2']+=20
                plan_path.write_text(json.dumps(plan))
                with self.assertRaisesRegex(ValueError,'modified'):
                    lab.commit_time(plan_path)
                subprocess.run(['git','add',str(plan_path)],check=True)
                subprocess.run(['git','commit','-qm','tamper plan'],check=True,env=env)
                with self.assertRaisesRegex(ValueError,'modified'):
                    lab.commit_time(plan_path)
            finally:
                os.chdir(prior)

    def test_scan_then_commit_then_later_evaluate_all_cli(self):
        import os
        import json
        import tempfile
        import subprocess
        from unittest.mock import patch
        import sys
        previous=Path.cwd()
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            try:
                os.chdir(root)
                subprocess.run(['git','init','-q'],check=True)
                subprocess.run(['git','config','user.name','OfflineAuditBot'],check=True)
                subprocess.run(['git','config','user.email','offline@example.org'],check=True)
                Path('latest.json').write_text(json.dumps(snapshot()),encoding='utf-8')
                class ClockBefore(datetime):
                    @classmethod
                    def now(cls,tz=None):return T
                with patch.object(lab,'datetime',ClockBefore),patch.object(sys,'argv',[
                        'lab','scan','--snapshot','latest.json','--output-dir','data/plans',
                        '--results-dir','data/results','--audit-file','data/audit.jsonl']):
                    lab.main()
                plans=list(Path('data/plans').glob('*.json'))
                self.assertEqual(len(plans),1)
                self.assertEqual(len(Path('data/audit.jsonl').read_text().splitlines()),1)
                subprocess.run(['git','add','data'],check=True)
                committed=T+timedelta(seconds=18)
                env=dict(os.environ,GIT_AUTHOR_DATE=stamp(committed),GIT_COMMITTER_DATE=stamp(committed))
                subprocess.run(['git','commit','-qm','prospective hypothesis'],check=True,env=env)
                eligible=lab.eligibility_time(json.loads(plans[0].read_text()),committed)
                class ClockAfter(datetime):
                    @classmethod
                    def now(cls,tz=None):return eligible+timedelta(minutes=3)
                with patch.object(lab,'datetime',ClockAfter),patch.object(lab,'get_candles',return_value=bars(eligible,3,low=90)) as req,patch.object(sys,'argv',[
                        'lab','evaluate-all','--plans-dir','data/plans','--results-dir','data/results']):
                    lab.main()
                    req.assert_called_once()
                result=json.loads((Path('data/results')/plans[0].name).read_text())
                self.assertEqual(result['status'],'OHLCV_PROXY_CLOSED_ESTIMATE')
                self.assertTrue(result['paper_signal_only'])
                self.assertFalse(result['pnl_eligible'])
                self.assertLess(result['estimated_pnl_ex_funding_usdc'],0)
                stats=json.loads(Path('data/results/summary.json').read_text())
                self.assertEqual(stats['closed_proxy_count'],1)
                self.assertEqual(stats['validated_trades'],0)
            finally:
                os.chdir(previous)
