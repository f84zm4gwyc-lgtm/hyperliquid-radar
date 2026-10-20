import sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from engine import *

BASE=1_792_483_200_000 # fixed UTC aligned epoch

def bars(coin,tf,count=80,drift=1.0,vol=10.0,offset=0):
    period=TF_MS[tf];base=BASE//period*period+offset*period
    return [check_bar({'t':base+i*period,'T':base+(i+1)*period-1,'s':coin,'i':tf,
             'o':10000+(i-0.5)*drift, 'h':10000+i*drift+2,'l':10000+i*drift-2,
             'c':10000+i*drift,'v':vol}) for i in range(count)]

class Tests(unittest.TestCase):
    def test_valid_bar(self):self.assertEqual(bars('BTC','5m',1)[0]['i'],'5m')
    def test_bad_coin(self):
        with self.assertRaises(ValueError):check_bar({**bars('BTC','5m',1)[0], 's':'PEPE'})
    def test_wrong_duration(self):
        with self.assertRaises(ValueError):check_bar({**bars('BTC','5m',1)[0], 'T':BASE+5})
    def test_future_lookahead_blocked(self):
        a=bars('ETH','15m',65)
        self.assertEqual(aligned_bars(a,a[-2]['T']+1)[-1]['t'],a[-2]['t'])
    def test_zero_volume_no_signal(self):
        x=bars('BTC','15m',80,vol=0);y=bars('BTC','1h',80);z=bars('BTC','4h',80)
        self.assertIsNone(candidate('BTC',x,aligned_bars(y,x[-1]['T']+1),aligned_bars(z,x[-1]['T']+1)))
    def test_non_breakout_none(self):
        x=bars('BTC','15m',80);y=bars('BTC','1h',80);z=bars('BTC','4h',80)
        self.assertIsNone(candidate('BTC',x,aligned_bars(y,x[-1]['T']+1),aligned_bars(z,x[-1]['T']+1)))
    def test_no_future_htf(self):
        x=bars('BTC','15m',80);y=bars('BTC','1h',80,offset=100);z=bars('BTC','4h',80,offset=100)
        self.assertIsNone(candidate('BTC',x,y,z))
    def test_ema_rising(self):self.assertAlmostEqual(ema(list(range(1,101)),20),90.5)
    def test_ema_insufficient(self):self.assertIsNone(ema([1,2,3],20))
    def test_atr_nonnegative(self):self.assertGreater(atr(bars('BTC','15m',80)),0)
    def test_invalid_ohlc(self):
        with self.assertRaises(ValueError):check_bar({**bars('BTC','5m',1)[0],'l':99999})
    def test_wrong_symbol_not_candidate(self):
        x=bars('BTC','15m',80);y=bars('BTC','1h',80);z=bars('BTC','4h',80)
        self.assertIsNone(candidate('SOL',x,y,z))

if __name__=='__main__':unittest.main()

class Confirmations(unittest.TestCase):
    def test_bull_candidate_reaches_watch_only(self):
        # Arrange histories so both 1h and 4h close at or before 15m cutoff.
        m15=bars('BTC','15m',80)
        m1=bars('BTC','1h',80,offset=-60)
        m4=bars('BTC','4h',80,offset=-75)
        last=m15[-1]
        last.update({'o':10078.,'c':10100.,'h':10101.,'l':10075.,'v':25.})
        r=candidate('BTC',m15,aligned_bars(m1,last['T']+1),aligned_bars(m4,last['T']+1))
        self.assertIsNotNone(r)
        self.assertEqual(r['status'],'WATCH_ONLY')
        self.assertEqual(r['direction'],'LONG')
        self.assertNotIn('stop',r)
    def test_subthreshold_volume_rejected(self):
        x=bars('BTC','15m',80);h1=bars('BTC','1h',80,offset=-60);h4=bars('BTC','4h',80,offset=-75)
        x[-1].update({'o':10078.,'c':10100.,'h':10101.,'l':10075.,'v':11.})
        self.assertIsNone(candidate('BTC',x,aligned_bars(h1,x[-1]['T']+1),aligned_bars(h4,x[-1]['T']+1)))
