import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import radar

FIXED_TIME=1792487500.0  # fixed epoch, reproducible and intraday

class IndicatorTests(unittest.TestCase):
    def test_ema(self):
        x=[float(i) for i in range(1,250)]
        self.assertAlmostEqual(radar.ema(x,20)[-1],239.5)
        self.assertIsNone(radar.ema([1.,2.],20)[-1])
    def test_rsi_increasing(self):
        self.assertEqual(radar.rsi_wilder(list(range(60)))[-1],100)
    def test_rsi_decreasing(self):
        self.assertEqual(radar.rsi_wilder(list(range(60,0,-1)))[-1],0)
    def test_rsi_flat(self):
        self.assertEqual(radar.rsi_wilder([100]*45)[-1],50)
    def test_macd_rising(self):
        h=radar.macd([float(i) for i in range(100)])[2]
        self.assertAlmostEqual(h[-1],0,places=4)
    def test_atr(self):
        bars=[{'h':103.,'l':97.,'c':100.} for _ in range(32)]
        self.assertAlmostEqual(radar.atr_wilder(bars)[-1],6)
    def test_sizing_long(self):
        z=radar.size_position(81000,80000,82500,84000)
        self.assertLessEqual(z['margin_usdc'],1000)
        self.assertLessEqual(z['stop_loss_usdc_est'],52.01)
        self.assertGreater(z['tp2_net_est_usdc'],z['tp1_net_est_usdc'])
        self.assertGreater(z['rr_tp2_net_est'],1)
    def test_sizing_short(self):
        z=radar.size_position(81000,82000,79500,78000)
        self.assertLessEqual(z['stop_loss_usdc_est'],52.01)
        self.assertLessEqual(z['margin_usdc'],1000)
    def test_sizing_too_tight_reward_is_not_validated(self):
        z=radar.size_position(81000,80000,81100,81200)
        self.assertLess(z['rr_tp2_net_est'],1.8)
    def test_sizing_wrong_direction(self):
        with self.assertRaises(ValueError):radar.size_position(81000,80000,79000,78000)
    def test_sizing_invalid_cost(self):
        with self.assertRaises(ValueError):radar.size_position(81000,80000,82500,84000,conservative_cost_fraction=-0.1)
    def test_nonfinite(self):
        with self.assertRaises(ValueError):radar.finite_float('NaN')


def candles(payload):
    req=payload['req'];tf=req['interval'];coin=req['coin'];interval=radar.TF_MS[tf]
    anchor=int(FIXED_TIME*1000)//interval*interval
    out=[]
    for i in range(370,0,-1):
        t=anchor-i*interval
        px=(81000 if coin=='BTC' else 2400)+(370-i)*1.2
        out.append({'t':t,'T':t+interval-1,'o':str(px-0.5),'h':str(px+5),
                    'l':str(px-5),'c':str(px),'v':'15'})
    # Deliberately unfinished current candle: must be removed.
    out.append({'t':anchor,'T':anchor+interval-1,'o':'1','h':'1000000','l':'0.5','c':'999999','v':'100000'})
    return out


def mock_info(payload):
    coin=payload.get('coin')
    if payload['type']=='metaAndAssetCtxs':
        return [{'universe':[{'name':'BTC','szDecimals':5},{'name':'ETH','szDecimals':4}]},
                [{'markPx':'81000','midPx':'81001','openInterest':'100','funding':'0.000012','dayNtlVlm':'1000000'},
                 {'markPx':'2400','midPx':'2401','openInterest':'200','funding':'-0.000013','dayNtlVlm':'2000000'}]]
    if payload['type']=='candleSnapshot':return candles(payload)
    if payload['type']=='l2Book':
        return {'time':int(FIXED_TIME*1000)-1200,'coin':coin,
                'levels':[[{'px':'81000' if coin=='BTC' else '2400','sz':'3'}],
                          [{'px':'81010' if coin=='BTC' else '2401','sz':'4'}]]}
    raise ValueError('unsupported mock info payload')

class CollectionTests(unittest.TestCase):
    def test_pipeline_full(self):
        with tempfile.TemporaryDirectory() as d:
            with patch.object(radar,'OUT',Path(d)),patch.object(radar,'time',return_value=FIXED_TIME),\
                 patch.object(radar,'post_info',side_effect=mock_info),\
                 patch.dict('os.environ',{'CHARTS_ENABLED':'0'}):
                radar.main()
            s=json.loads((Path(d)/'latest.json').read_text())
            self.assertEqual(s['schema_version'],2)
            self.assertEqual(set(s['assets']),{'BTC','ETH'})
            self.assertEqual(set(s['assets']['BTC']['timeframes']),set(radar.TF_MS))
            self.assertNotEqual(s['assets']['BTC']['timeframes']['5m']['last_close'],999999)
            self.assertIn('spread_bps',s['assets']['ETH']['order_book'])
            self.assertEqual(s['assets']['BTC']['timeframes']['15m']['bar_count'],370)
            self.assertGreater(len((Path(d)/'report.md').read_text()),1200)
            self.assertEqual(len(list(Path(d).glob('*.png'))),0)
            self.assertEqual(s['assets']['BTC']['perp_context']['funding_hourly_pct'],0.0012)
    def test_gap_rejected(self):
        raw=candles({'req':{'interval':'5m','coin':'BTC'}})
        raw.pop(100)
        with patch.object(radar,'post_info',return_value=raw):
            with self.assertRaisesRegex(RuntimeError,'Gaps'):
                radar.retrieve_bars('BTC','5m',int(FIXED_TIME*1000))
    def test_older_candles_rejected(self):
        raw=candles({'req':{'interval':'5m','coin':'BTC'}})[:-1]
        for b in raw:
            b['t']-=radar.TF_MS['5m']*5;b['T']-=radar.TF_MS['5m']*5
        with patch.object(radar,'post_info',return_value=raw):
            with self.assertRaisesRegex(RuntimeError,'Stale'):
                radar.retrieve_bars('BTC','5m',int(FIXED_TIME*1000))
    def test_stale_book_rejected(self):
        with patch.object(radar,'post_info',return_value={'time':int(FIXED_TIME*1000)-600_000,'levels':[[{'px':'100','sz':'1'}],[{'px':'101','sz':'1'}]]}):
            with self.assertRaisesRegex(ValueError,'Stale'):
                radar.retrieve_book('BTC',int(FIXED_TIME*1000))
    def test_invalid_book_rejected(self):
        with patch.object(radar,'post_info',return_value={'time':int(FIXED_TIME*1000),'levels':[[{'px':'101','sz':'1'}],[{'px':'100','sz':'1'}]]}):
            with self.assertRaisesRegex(ValueError,'Crossed'):
                radar.retrieve_book('BTC',int(FIXED_TIME*1000))

if __name__=='__main__':unittest.main()
