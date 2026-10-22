import sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import audit_metrics

EXAMPLE={'trade_id':'forged_result','status':'CLOSED_ESTIMATE',
  'data_provenance':'CAPTURED_LIVE_REVIEWED','incomplete_market_data':False,
  'independent_quote_times_checked':True,'funding_included':False,
  'hypothetical_fills_only':True,'estimated_realised_pnl_ex_funding_usdc':'99999',
  'events':[{'event':'TP2_ESTIMATE','at_utc':'2026-10-22T01:00:00Z'}]}

class Tests(unittest.TestCase):
    def test_self_attested_is_not_automatically_reviewed(self):
        o=audit_metrics.evaluate([EXAMPLE])
        self.assertEqual(o['counted_closed_reviewed_quotes'],0)
        self.assertEqual(o['estimated_pnl_ex_funding_usdc'],0)
        self.assertEqual(o['excluded']['NO_INDEPENDENT_OPERATOR_REVIEW'],1)
    def test_explicit_review_is_still_hypothetical(self):
        o=audit_metrics.evaluate([EXAMPLE],explicitly_reviewed_ids={'forged_result'})
        self.assertEqual(o['counted_closed_reviewed_quotes'],1)
        self.assertEqual(o['status'],'EXPLORATORY_PAPER_ESTIMATE_NOT_LIVE_PNL')
        self.assertFalse(o['sample_sufficient_to_validate_edge'])
if __name__=='__main__':unittest.main()
