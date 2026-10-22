"""Candle cache tests: all inputs synthetic, no internet or orders."""
import copy
import gzip
import json
import sys
import unittest
from datetime import datetime,timezone
from pathlib import Path
from unittest.mock import patch,MagicMock

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import bootstrap
import app
from engine import PAIRS,TF_MS

BASE_NOW=1_798_100_000.0  # fixed synthetic UTC instant


def build_document(now=BASE_NOW):
    assets={}
    for coin in PAIRS:
        assets[coin]={}
        for tf in ('15m','1h','4h'):
            period=TF_MS[tf]
            # last completed candle ends at previous interval boundary
            stop=(int(now*1000)//period)*period
            bars=[]
            for i in range(250):
                start=stop-(250-i)*period
                bars.append({'s':coin,'i':tf,'t':start,'T':start+period-1,
                             'o':100.,'h':105.,'l':95.,'c':100.,'v':2.})
            assets[coin][tf]=bars
    return {'schema_version':1,'source':'HYPERLIQUID_OFFICIAL_INFO_CLOSED_CANDLES',
            'generated_at_utc':datetime.fromtimestamp(now-30,timezone.utc).isoformat(),
            'assets':assets,'pnl_eligible':False}


def gz(doc):
    return gzip.compress(json.dumps(doc,separators=(',',':')).encode(),mtime=0)


class BootstrapChecks(unittest.TestCase):
    def setUp(self):
        bootstrap._memory=None
        bootstrap._cache_time=0
        self.doc=build_document()

    def test_all_six_from_valid_archive(self):
        ret=bootstrap.parse_archive(gz(self.doc),now=BASE_NOW)
        self.assertEqual(len(ret),6)
        self.assertTrue(all(len(ret[c,tf])==250 for c in PAIRS for tf in ('15m','1h','4h')))

    def test_reject_stale_publication(self):
        self.doc['generated_at_utc']=datetime.fromtimestamp(BASE_NOW-3601,timezone.utc).isoformat()
        with self.assertRaisesRegex(bootstrap.BootstrapError,'stale'):bootstrap.parse_archive(gz(self.doc),now=BASE_NOW)

    def test_reject_future_publication(self):
        self.doc['generated_at_utc']=datetime.fromtimestamp(BASE_NOW+50,timezone.utc).isoformat()
        with self.assertRaises(bootstrap.BootstrapError):bootstrap.parse_archive(gz(self.doc),now=BASE_NOW)

    def test_reject_missing_pair(self):
        del self.doc['assets']['ETH']
        with self.assertRaises(bootstrap.BootstrapError):bootstrap.parse_archive(gz(self.doc),now=BASE_NOW)

    def test_reject_nonfinite_candle(self):
        self.doc['assets']['BTC']['15m'][7]['o']='NaN'
        with self.assertRaises(bootstrap.BootstrapError):bootstrap.parse_archive(gz(self.doc),now=BASE_NOW)

    def test_reject_gapped_history(self):
        bars=self.doc['assets']['BTC']['15m']
        bars.pop(-5)
        prior=copy.copy(bars[0]); prior['t']-=TF_MS['15m']; prior['T']-=TF_MS['15m']
        bars.insert(0,prior)
        with self.assertRaisesRegex(bootstrap.BootstrapError,'Discontinuous'):bootstrap.parse_archive(gz(self.doc),now=BASE_NOW)

    def test_reject_bad_zip(self):
        with self.assertRaises(bootstrap.BootstrapError):bootstrap.parse_archive(b'not-gzip',now=BASE_NOW)

    def test_reject_gzip_bomb(self):
        huge=gzip.compress(b'X'*(bootstrap.MAX_EXPANDED+1),mtime=0)
        with self.assertRaises(bootstrap.BootstrapError):bootstrap.parse_archive(huge,now=BASE_NOW)

    def test_reject_bad_source(self):
        self.doc['source']='OTHER'
        with self.assertRaises(bootstrap.BootstrapError):bootstrap.parse_archive(gz(self.doc),now=BASE_NOW)

    def test_reject_too_old_candles(self):
        for bar in self.doc['assets']['BTC']['15m']:
            bar['t']-=7*TF_MS['15m'];bar['T']-=7*TF_MS['15m']
        with self.assertRaises(bootstrap.BootstrapError):bootstrap.parse_archive(gz(self.doc),now=BASE_NOW)

    def test_one_download_shared_by_six(self):
        with patch.object(bootstrap,'download_archive',return_value=bootstrap.parse_archive(gz(self.doc),now=BASE_NOW)) as fetch:
            for coin in PAIRS:
                for tf in ('15m','1h','4h'):
                    self.assertEqual(len(bootstrap.get_public_bars(coin,tf,now=BASE_NOW)),250)
            fetch.assert_called_once()

    def test_get_bars_public_avoids_rest(self):
        with patch.object(app,'get_public_bars',return_value=bootstrap.parse_archive(gz(self.doc),now=BASE_NOW)[('BTC','15m')]):
            with patch.object(app,'read_rest_with_backoff',side_effect=AssertionError('REST should not be called')):
                result=app.get_bars('BTC','15m')
        self.assertEqual(len(result),250)

    def test_unavailable_cache_rest_fallback(self):
        with patch.object(app,'get_public_bars',side_effect=bootstrap.BootstrapError('unavailable')):
            with patch.object(app,'read_rest_with_backoff',side_effect=ValueError('rate limited')) as rest:
                with self.assertRaises(ValueError):app.get_bars('BTC','15m')
                rest.assert_called_once()

if __name__=='__main__':unittest.main()
