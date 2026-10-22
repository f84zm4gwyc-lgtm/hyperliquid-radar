"""Immutable plans and fail-closed validation, shared by callbacks and sender."""
import hashlib
import json
import math
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from urllib.parse import urlsplit

from .risk import d, iso, position, round_size_down

VERSION = "breakout-15m-volume-1.3-ema20-50-1h-4h-v1"
PAIRS = {"BTC/USDC:USDC": "BTC", "ETH/USDC:USDC": "ETH"}


def utcnow():
    return datetime.now(timezone.utc)


def stamp(dt):
    # Fixed precision also makes UTC TEXT timestamps safely sortable in SQLite.
    return dt.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def canonical(obj):
    return json.dumps(obj, sort_keys=True, allow_nan=False, separators=(",", ":"))


def digest(obj):
    return hashlib.sha256(canonical(obj).encode()).hexdigest()


def quote_ok(quote, now, max_age=5):
    try:
        for key in ("mark_at_utc", "book_at_utc", "received_at_utc"):
            if not 0 <= (now - iso(quote[key])).total_seconds() <= max_age:
                return False
        bid, ask, mark = (float(quote[k]) for k in ("bid", "ask", "mark"))
        mid = (bid + ask) / 2
        return (all(math.isfinite(x) for x in (bid, ask, mark)) and
                0 < bid < ask and mark > 0 and (ask-bid)/mid <= .003 and
                abs(mark-mid)/mid <= .005)
    except (KeyError, ValueError, TypeError, OverflowError, ZeroDivisionError):
        return False


def macro_allowed(review, now):
    """Operator review with explicit sources; absence never becomes ALLOW."""
    try:
        observed = iso(review["reviewed_at_utc"])
        end = iso(review["expires_at_utc"])
        sources = review["sources"]
        return (review["decision"] == "ALLOW" and bool(review["reviewer"].strip()) and
                observed <= now < end and 0 < (end-observed).total_seconds() <= 7200 and
                isinstance(sources, list) and 1 <= len(sources) <= 10 and
                all(isinstance(x, str) and urlsplit(x).scheme == "https" and
                    bool(urlsplit(x).hostname) for x in sources))
    except (KeyError, TypeError, ValueError, AttributeError):
        return False


def make_plan(pair, side, close_at, atr, candle_close, quote, sz_decimals, now):
    if pair not in PAIRS or side not in ("long", "short"):
        raise ValueError("Unsupported paper market")
    if not 3 <= (now-close_at).total_seconds() <= 180 or not quote_ok(quote, now):
        raise ValueError("Expired candle or quotes")
    entry = float(quote["ask"] if side == "long" else quote["bid"])
    if (not math.isfinite(float(atr)) or not math.isfinite(float(candle_close)) or
            float(atr) <= 0 or float(candle_close) <= 0):
        raise ValueError("Invalid ATR or close")
    if abs(float(quote["mark"])/float(candle_close)-1) > .005:
        raise ValueError("Price moved since close")
    distance = max(float(atr)*1.5, entry*.005)
    direction = 1 if side == "long" else -1
    plan = {
        "trade_id": f"{PAIRS[pair]}-{int(close_at.timestamp())}-{side}-paper-v1",
        "pair": pair, "symbol": PAIRS[pair], "side": side.upper(),
        "strategy_version": VERSION, "signal_status": "PROSPECTIVE_PAPER_HYPOTHESIS",
        "decision_at_utc": stamp(now), "recorded_candle_close_utc": stamp(close_at),
        "expires_at_utc": stamp(now+timedelta(minutes=5)),
        "alert_received_at_utc": stamp(now), "indicative_entry": entry,
        "stop": entry-direction*distance, "tp1": entry+direction*2.5*distance,
        "tp2": entry+direction*5*distance, "sz_decimals": sz_decimals,
        "max_margin_usdc": 1000, "max_risk_usdc": 52, "leverage": 2,
        "tp1_fraction": .5, "min_weighted_rr": "1.8", "cost_reserve_fraction": .005,
        "entry_deadline_minutes": 5, "source_quote": quote,
        "execution_model": "FREQTRADE_DRY_RUN_MARK_SAMPLED_NOT_EXCHANGE_FILLS",
        "live_order_authorized": False,
    }
    preview = position(plan)
    if not preview["accepted"]:
        raise ValueError(preview["reason"])
    plan["preview"] = preview
    return plan


def actual_risk(plan, quantity, entry):
    """Recompute risk and weighted RR for the amount FT actually proposes."""
    q, e = d(quantity), d(entry)
    if q <= 0 or e <= 0 or abs(e/d(plan["indicative_entry"])-1) > d(".0025"):
        return False
    if q != round_size_down(q, plan["sz_decimals"]):
        return False
    s, t1, t2 = (d(plan["preview"][k]) for k in ("stop", "tp1", "tp2"))
    if not ((s < e < t1 < t2) if plan["side"] == "LONG" else (t2 < t1 < e < s)):
        return False
    q1 = round_size_down(q/2, plan["sz_decimals"])
    q2 = q-q1
    loss = abs(e-s)*q + e*q*d(".005")
    profit = abs(t1-e)*q1 + abs(t2-e)*q2 - e*q*d(".005")
    return (min(q1,q2)*e >= 10 and e*q/2 <= 1000 and
            0 < loss <= 52 and profit/loss >= d("1.8"))


def exit_action(plan, mark, tp1_done):
    mark = d(mark)
    s,t1,t2 = (d(plan["preview"][k]) for k in ("stop","tp1","tp2"))
    long = plan["side"] == "LONG"
    if (mark <= s if long else mark >= s):
        return "STOP"
    if (mark >= t2 if long else mark <= t2):
        # If both targets are first observed at this sample, full paper exit.
        return "TP2" if tp1_done else "TP_BOTH_AT_ONE_SAMPLE"
    if not tp1_done and (mark >= t1 if long else mark <= t1):
        return "TP1"
    return None


def slack_message(plan, review, now):
    if not macro_allowed(review, now) or now >= iso(plan["expires_at_utc"]):
        raise ValueError("Macro review missing/expired or plan expired")
    p = plan["preview"]
    return (f"PAPIER — VALIDATED_PAPER_SIGNAL — {plan['symbol']} {plan['side']} x2\n"
            f"Entrée indicative {p['entry']:g} | Stop {p['stop']:g}\n"
            f"TP1 {p['tp1']:g} / TP2 {p['tp2']:g} — 50/50 arrondis\n"
            f"Marge {p['margin_usdc']:.2f} | Risque estimé {p['estimated_max_stop_loss_usdc']:.2f} USDC | R/R net {p['weighted_rr_net_est']:g}\n"
            f"Heure UTC {plan['decision_at_utc']} | signal_id={plan['trade_id']}\n"
            "Simulation, aucun ordre réel. Captures 1H+4H avant toute décision humaine.")
