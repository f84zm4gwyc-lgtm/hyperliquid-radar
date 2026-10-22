"""Synthetic inputs only; these are never recorded as market performance."""
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from radar_paper.core import make_plan, stamp

T = datetime(2026, 10, 22, 9, 15, 5, tzinfo=timezone.utc)


def quote(now=T):
    return {"bid": 99.99, "ask": 100.01, "mark": 100, "sz_decimals": 4,
            "mark_at_utc": stamp(now), "book_at_utc": stamp(now),
            "received_at_utc": stamp(now), "source": "SYNTHETIC_TEST_ONLY"}


def plan(side="long", now=T, pair="BTC/USDC:USDC", atr=1):
    return make_plan(pair, side, now-timedelta(seconds=5), atr, 100, quote(now), 4, now)


def review(now=T):
    return {"decision": "ALLOW", "reviewer": "SYNTHETIC_TEST_REVIEWER",
            "reviewed_at_utc": stamp(now), "expires_at_utc": stamp(now+timedelta(hours=1)),
            "sources": ["https://example.org/synthetic-test"]}
