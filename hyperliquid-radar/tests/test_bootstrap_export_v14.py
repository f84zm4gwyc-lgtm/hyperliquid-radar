"""Producer/consumer contract: use zero network, synthetic candles only."""
import importlib.util
import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'render-radar'))
import bootstrap

radar_py=ROOT/'hyperliquid-radar/radar.py'
spec=importlib.util.spec_from_file_location('radar_producer_v14',radar_py)
radar=importlib.util.module_from_spec(spec)
spec.loader.exec_module(radar)

NOW=1_798_100_000.0


def sample_bars(coin,tf,now=NOW):
    step=radar.TF_MS[tf]
    cutoff=int(now*1000)//step*step
    return [{'t':cutoff-(300-i)*step,'T':cutoff-(299-i)*step-1,
             'o':100.,'h':105.,'l':95.,'c':100.,'v':10.} for i in range(300)]


class ProducerConsumerContract(unittest.TestCase):
    def test_generated_cache_is_readable_by_render_and_has_no_orders(self):
        meta=[{'universe':[{'name':'BTC','szDecimals':5},{'name':'ETH','szDecimals':4}]},
             [{'markPx':'100','midPx':'100','openInterest':'12','funding':'0',
               'dayNtlVlm':'100000'} for _ in range(2)]]
        with tempfile.TemporaryDirectory() as temp:
            out=Path(temp)
            with patch.object(radar,'OUT',out),patch.object(radar,'post_info',return_value=meta),\
                 patch.object(radar,'retrieve_bars',side_effect=lambda c,tf,now:sample_bars(c,tf)),\
                 patch.object(radar,'retrieve_book',return_value={'best_bid':99,'best_ask':101}),\
                 patch.object(radar,'time',return_value=NOW),\
                 patch.dict('os.environ',{'CHARTS_ENABLED':'0'}):
                radar.main()
            contents=(out/'candle_bootstrap.json.gz').read_bytes()
            bars=bootstrap.parse_archive(contents,now=NOW)
            self.assertEqual(len(bars),6)
            self.assertTrue(all(len(bars[c,tf])==270 for c in ('BTC','ETH') for tf in ('15m','1h','4h')))
            pub=json.loads((out/'latest.json').read_text())
            self.assertEqual(pub['schema_version'],2)
            # This archive contains only public OHLCV candles, not keys or orders.
            self.assertNotIn(b'privateKey',contents)

if __name__=='__main__':unittest.main()
