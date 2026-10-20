import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from time import time
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import radar


class TechnicalTests(unittest.TestCase):
    def test_ema_and_rsi(self):
        values=[float(i) for i in range(1,250)]
        self.assertAlmostEqual(radar.ema(values,20)[-1],239.5,places=4)
        self.assertEqual(radar.rsi_wilder(values)[-1],100.0)
        self.assertIsNone(radar.ema([1.0,2.0],20)[-1])

    def test_flat_rsi(self):
        self.assertEqual(radar.rsi_wilder([42.0]*100)[-1],50.0)

    def test_macd(self):
        line,signal,hist=radar.macd([float(i) for i in range(100)])
        self.assertIsNotNone(hist[-1]); self.assertIsNotNone(line[-1]); self.assertIsNotNone(signal[-1])
        self.assertAlmostEqual(hist[-1],0.0,places=4)

    def test_pipeline_with_mock_api(self):
        def mocked(payload,*args,**kwargs):
            if payload['type']=='metaAndAssetCtxs':
                return [{'universe':[{'name':'BTC'},{'name':'ETH'}]},[
                    {'markPx':'82000','openInterest':'100','funding':'0.00001','dayNtlVlm':'100000'},
                    {'markPx':'2400','openInterest':'200','funding':'-0.00001','dayNtlVlm':'200000'}]]
            if payload['type']=='candleSnapshot':
                req=payload['req']; iv=radar.TF_MS[req['interval']]; now=int(time()*1000)
                base=(now//iv)*iv
                results=[]
                for i in range(370,0,-1):
                    t=base-i*iv; px=(82000 if req['coin']=='BTC' else 2400)+((370-i)*2)
                    results.append({'t':t,'T':t+iv-1,'o':str(px-1),'h':str(px+4),'l':str(px-4),'c':str(px),'v':'13.5'})
                results.append({'t':base,'T':base+iv-1,'o':'999','h':'999','l':'999','c':'999','v':'1'})
                return results
            raise ValueError('unsupported')
        with tempfile.TemporaryDirectory() as d:
            with patch.object(radar,'OUT',Path(d)), patch.object(radar,'post_info',side_effect=mocked):
                radar.main()
                snap=__import__('json').loads((Path(d)/'latest.json').read_text())
                self.assertEqual(set(snap['assets'].keys()),{'BTC','ETH'})
                self.assertEqual(snap['assets']['BTC']['timeframes']['4h']['bar_count'],370)
                self.assertEqual(snap['assets']['BTC']['timeframes']['4h']['rsi14'],100)
                self.assertTrue((Path(d)/'ETH_1h.png').exists())
                self.assertEqual(len(list(Path(d).glob('*.png'))),6)
                self.assertIn('Funding horaire',(Path(d)/'report.md').read_text())

if __name__=='__main__':
    unittest.main()
