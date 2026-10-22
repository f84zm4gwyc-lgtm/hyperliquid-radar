import sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import risk_budget
PLAN={'trade_id':'paper-a','symbol':'BTC','side':'LONG','signal_status':'VALIDATED_PAPER_SIGNAL',
 'alert_sent_at_utc':'2026-10-20T10:00:00Z','alert_received_at_utc':'2026-10-20T10:00:05Z',
 'indicative_entry':81000,'stop':80000,'tp1':83000,'tp2':86000,'sz_decimals':5,
 'max_margin_usdc':1000,'max_risk_usdc':52,'leverage':2}
class TestRisk(unittest.TestCase):
 def test_unknown_live_positions_refuse(self):
  r=risk_budget.check_combined_risk(PLAN,[])
  self.assertFalse(r['accepted'])
 def test_flat_no_positions_accept_if_individually_valid(self):
  r=risk_budget.check_combined_risk(PLAN,[],real_positions_known_flat=True)
  self.assertTrue(r['accepted']);self.assertFalse(r['real_order_authorized'])
 def test_aggregate_risk_blocks(self):
  x={'trade_id':'paper-b','state':'OPEN_PAPER','reserved_initial_stop_risk_usdc':20}
  r=risk_budget.check_combined_risk(PLAN,[x],real_positions_known_flat=True)
  self.assertFalse(r['accepted'])
 def test_reject_duplicate(self):
  x={'trade_id':'paper-a','state':'OPEN_PAPER','reserved_initial_stop_risk_usdc':0}
  with self.assertRaises(ValueError):risk_budget.check_combined_risk(PLAN,[x],real_positions_known_flat=True)
 def test_reject_negative(self):
  x={'trade_id':'paper-b','state':'OPEN_PAPER','reserved_initial_stop_risk_usdc':-1}
  with self.assertRaises(ValueError):risk_budget.check_combined_risk(PLAN,[x],real_positions_known_flat=True)
 def test_reject_missing_existing_risk(self):
  x={'trade_id':'paper-b','state':'OPEN_PAPER'}
  with self.assertRaises(ValueError):risk_budget.check_combined_risk(PLAN,[x],real_positions_known_flat=True)
 def test_reject_reusing_closed_position(self):
  x={'trade_id':'paper-b','state':'CLOSED_PAPER','reserved_initial_stop_risk_usdc':5}
  with self.assertRaises(ValueError):risk_budget.check_combined_risk(PLAN,[x],real_positions_known_flat=True)
 def test_ceiling_above_52_refused(self):
  with self.assertRaises(ValueError):risk_budget.check_combined_risk(PLAN,[],risk_ceiling=100,real_positions_known_flat=True)
 def test_minimum_risk_ceiling_rejects(self):
  r=risk_budget.check_combined_risk(PLAN,[],risk_ceiling=1,real_positions_known_flat=True)
  self.assertFalse(r['accepted'])
 def test_invalid_individual_plan(self):
  p=dict(PLAN,leverage=5)
  with self.assertRaises(ValueError):risk_budget.check_combined_risk(p,[],real_positions_known_flat=True)
 def test_partial_reserves_initial_full_loss(self):
  x={'trade_id':'paper-b','state':'PARTIAL_PAPER','reserved_initial_stop_risk_usdc':20}
  a=risk_budget.check_combined_risk(PLAN,[x],real_positions_known_flat=True)
  self.assertEqual(a['existing_reserved_risk_usdc'],20)
if __name__=='__main__':unittest.main()
