import unittest
import sys
import copy
from pathlib import Path
from decimal import Decimal
from datetime import datetime,timezone,timedelta
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import paper_radar as r
import metrics

BASE={
 'trade_id':'PAPER-001','symbol':'BTC','side':'LONG','alert_received_at_utc':'2026-10-20T10:00:00+00:00',
 'indicative_entry':81000,'stop':80000,'tp1':83000,'tp2':86000,'sz_decimals':5,
 'tp1_fraction':0.5,'max_margin_usdc':1000,'max_risk_usdc':52,'leverage':2,
 'cost_reserve_fraction':0.005,'entry_deadline_minutes':15,
}

def bar(mins,o,h,l,c):
    t=datetime(2026,10,20,10,0,tzinfo=timezone.utc)+timedelta(minutes=mins)
    return {'start_utc':t,'open':Decimal(str(o)),'high':Decimal(str(h)),'low':Decimal(str(l)),'close':Decimal(str(c))}

class Levels(unittest.TestCase):
 def test_size_precision(self):
  self.assertEqual(r.round_size_down('0.123456789',5),Decimal('0.12345'))
 def test_btc_tick(self):
  self.assertEqual(r.tick_for_price(81523.3,5),Decimal(1))
 def test_eth_tick(self):
  self.assertEqual(r.tick_for_price(2385.07,4),Decimal('.1'))
 def test_btc_round_price_up(self):
  self.assertEqual(r.compliant_price(81523.3,5,'up'),Decimal(81524))
 def test_size_non_finite(self):
  with self.assertRaises(ValueError):r.round_size_down('NaN',5)
 def test_direction_rejected(self):
  p=copy.deepcopy(BASE);p['stop']=82000
  with self.assertRaises(ValueError):r.position(p)
 def test_leverage_maximum(self):
  p=copy.deepcopy(BASE);p['leverage']=3
  with self.assertRaises(ValueError):r.position(p)
 def test_invalid_tp_share(self):
  p=copy.deepcopy(BASE);p['tp1_fraction']=1
  with self.assertRaises(ValueError):r.position(p)
 def test_rr_weighted_less_than_tp2_only(self):
  p=r.position(BASE)
  self.assertTrue(p['accepted']);self.assertLess(p['weighted_rr_net_est'],(2*p['tp2_gain_est_usdc'])/p['estimated_max_stop_loss_usdc'])
 def test_rr_filter_prevents_low_rr(self):
  p=dict(BASE,tp1=82000,tp2=82800)
  result=r.position(p)
  self.assertFalse(result['accepted']);self.assertIn('R/R',result['reason'])
 def test_position_max_margin_risk(self):
  p=r.position(BASE)
  self.assertLessEqual(p['margin_usdc'],1000)
  self.assertLessEqual(p['estimated_max_stop_loss_usdc'],52)
  self.assertAlmostEqual(p['tp1_quantity']+p['tp2_quantity'],p['quantity'])
 def test_short(self):
  s=dict(BASE,side='SHORT',stop=82000,tp1=79000,tp2=76000)
  p=r.position(s)
  self.assertTrue(p['accepted']);self.assertLess(p['tp2'],p['tp1'])
 def test_hash_same_regardless_dictionary_order(self):
  self.assertEqual(r.plan_hash(BASE),r.plan_hash(dict(reversed(list(BASE.items())))))
 def test_hash_changes_if_stop_changed(self):
  self.assertNotEqual(r.plan_hash(BASE),r.plan_hash(dict(BASE,stop=79999)))

class Simulation(unittest.TestCase):
 def test_no_retroactive_bar(self):
  bars=[bar(-5,81000,90000,70000,81000),bar(0,81000,82000,80700,81500)]
  res=r.simulate(BASE,bars)
  self.assertEqual(res['events'][0]['event'],'ENTRY')
  self.assertEqual(res['status'],'OPEN_UNREALIZED')
 def test_open_price_drift_skips(self):
  res=r.simulate(BASE,[bar(0,83000,84000,82500,83500)])
  self.assertEqual(res['status'],'NO_ENTRY')
 def test_stop_wins_ambiguous(self):
  res=r.simulate(BASE,[bar(0,81000,87000,79000,85000)])
  self.assertEqual([x['event'] for x in res['events']],['ENTRY','STOP'])
  self.assertTrue(res['events'][-1]['same_bar_tp_ambiguous'])
 def test_tp1_then_stop(self):
  res=r.simulate(BASE,[bar(0,81000,81500,80600,81200),bar(5,82000,83500,82000,83000),bar(10,83000,84000,79500,80800)])
  self.assertEqual([x['event'] for x in res['events']],['ENTRY','TP1','STOP'])
  self.assertEqual(res['status'],'CLOSED_ESTIMATE')
 def test_tp1_tp2(self):
  res=r.simulate(BASE,[bar(0,81000,81800,80900,81500),bar(5,82500,83300,82000,83000),bar(10,84500,86100,84400,86000)])
  self.assertEqual([x['event'] for x in res['events']],['ENTRY','TP1','TP2'])
  self.assertTrue(res['estimated_realised_pnl_ex_funding_usdc']>0)
  self.assertFalse(res['funding_included'])
 def test_future_bar_only(self):
  res=r.simulate(BASE,[bar(-5,81000,81800,80800,81400)])
  self.assertEqual(res['status'],'NO_ENTRY')
 def test_expired_entry(self):
  res=r.simulate(BASE,[bar(20,81000,81800,80800,81400)])
  self.assertEqual(res['status'],'NO_ENTRY')
 def test_short_stop_ambiguous(self):
  p=dict(BASE,side='SHORT',stop=82000,tp1=79000,tp2=76000)
  res=r.simulate(p,[bar(0,81000,82500,74500,80000)])
  self.assertEqual(res['events'][-1]['event'],'STOP')
 def test_metrics(self):
  a=r.simulate(BASE,[bar(0,81000,81900,80800,81500),bar(5,82500,83500,82100,83000),bar(10,84500,86100,84400,85900)])
  s=dict(BASE,trade_id='PAPER-002')
  b=r.simulate(s,[bar(0,81000,81400,79800,80500)])
  score=metrics.score([a,b])
  self.assertEqual(score['wins'],1);self.assertEqual(score['losses'],1)
  self.assertEqual(score['win_rate_pct'],50.0)
 def test_metrics_duplicate_protection(self):
  a=r.simulate(BASE,[bar(0,81000,81400,79800,80500)])
  with self.assertRaises(ValueError):metrics.score([a,a])
 def test_fees_not_guaranteed(self):
  a=r.simulate(BASE,[bar(0,81000,81400,79800,80500)])
  self.assertTrue(any('estimés' in x for x in a['caveats']))

if __name__=='__main__':unittest.main()
