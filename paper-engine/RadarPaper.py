"""Frozen BTC/ETH baseline on Freqtrade. Paper-only callbacks and durable plans."""
import json
import logging
import os
from datetime import timedelta
from pathlib import Path

import talib.abstract as ta
from pandas import DataFrame
from freqtrade.strategy import IStrategy, informative
from freqtrade.persistence import Trade

from radar_paper.core import (PAIRS, VERSION, actual_risk, exit_action, macro_allowed,
                              make_plan, quote_ok, stamp, utcnow)
from radar_paper.market import PublicQuotes
from radar_paper.evidence import candle_evidence
from radar_paper.notify import Sender
from radar_paper.risk import d, iso, round_size_down
from radar_paper.store import Store

LOG = logging.getLogger("radar.paper")


class RadarPaper(IStrategy):
    INTERFACE_VERSION = 3
    timeframe = "15m"
    can_short = True
    startup_candle_count = 230
    process_only_new_candles = True
    minimal_roi = {}
    # Normal exits use fresh sampled mark in custom_exit/adjust_trade_position.
    # This emergency FT fallback is deliberately not represented as the planned stop.
    stoploss = -.99
    position_adjustment_enable = True
    max_entry_position_adjustment = 0
    use_exit_signal = True

    def version(self):
        return VERSION

    def ft_bot_start(self, **kwargs):
        # Upstream wraps bot_start and suppresses its exceptions. Enforce the
        # paper gate outside that wrapper, before the trading loop can run.
        if self.config.get("dry_run") is not True or str(self.config.get("runmode", "")).lower() not in ("runmode.dry_run", "dry_run"):
            raise RuntimeError("RadarPaper refuses all modes except prospective dry-run")
        super().ft_bot_start(**kwargs)
        if not getattr(self, "ready", False):
            raise RuntimeError("RadarPaper initialization failed; trading loop refused")

    def bot_start(self, **kwargs):
        self.ready = False
        if self.config.get("dry_run") is not True or str(self.config.get("runmode", "")).lower() not in ("runmode.dry_run", "dry_run"):
            raise RuntimeError("RadarPaper only supports prospective dry-run")
        self.root = Path(os.environ["RADAR_DATA_DIR"])
        self.store = Store(self.root / "radar.sqlite")
        sizes = {pair: self.dp.market(pair)["info"]["szDecimals"] for pair in PAIRS}
        self.quotes = PublicQuotes(sizes)
        self.quotes.start()
        self.sender = Sender(self.store, os.environ.get("RADAR_SLACK_WEBHOOK", ""))
        self.sender.start()
        self.last_heartbeat = None
        self.ready = True

    def ft_bot_cleanup(self):
        if hasattr(self, "quotes"):
            self.quotes.close()
        if hasattr(self, "sender"):
            self.sender.close()
            self.store.close()
        super().ft_bot_cleanup()

    def bot_loop_start(self, current_time, **kwargs):
        now = utcnow()
        open_trades = Trade.get_trades_proxy(is_open=True)
        pairs = {t.pair for t in open_trades}
        for pair in PAIRS:
            plan = self.store.active(pair)
            if plan and pair not in pairs:
                age = (now-iso(plan["decision_at_utc"])).total_seconds()
                if age > 15:
                    filled = [t for t in Trade.get_trades_proxy(is_open=False, pair=pair)
                              if t.get_custom_data("radar_plan_id") == plan["trade_id"]]
                    self.store.state(plan["trade_id"], "CLOSED" if filled else "NO_ENTRY", now,
                                     {"reason": "Reconciled with native database", "recovered": True,
                                      "quality": "UNVERIFIABLE_RECOVERY"})
        if not self.last_heartbeat or (now-self.last_heartbeat).total_seconds() >= 15:
            self.store.event(None, "HEARTBEAT", {"native_open_trades": len(open_trades)}, now)
            self.last_heartbeat = now
            status = {"at_utc": stamp(now), "strategy": VERSION, **self.store.summary(),
                      "slack_configured": bool(self.sender.secret_url),
                      "market_stream": self.quotes.status(),
                      "warning": "Paper estimates; mark sampled, fills simulated, host continuity unproven"}
            temp = self.root / "status.json.tmp"
            temp.write_text(json.dumps(status, indent=2))
            temp.replace(self.root / "status.json")

    @informative("1h")
    def context_1h(self, dataframe, metadata):
        dataframe["ema20"] = ta.EMA(dataframe, timeperiod=20)
        dataframe["ema50"] = ta.EMA(dataframe, timeperiod=50)
        dataframe["context_ok"] = (dataframe["date"].diff().dt.total_seconds().eq(3600).rolling(50).sum() == 50)
        dataframe["context_ok"] &= dataframe["volume"].gt(0).rolling(51).sum().eq(51)
        return dataframe

    @informative("4h")
    def context_4h(self, dataframe, metadata):
        dataframe["ema20"] = ta.EMA(dataframe, timeperiod=20)
        dataframe["ema50"] = ta.EMA(dataframe, timeperiod=50)
        dataframe["context_ok"] = (dataframe["date"].diff().dt.total_seconds().eq(14400).rolling(50).sum() == 50)
        dataframe["context_ok"] &= dataframe["volume"].gt(0).rolling(51).sum().eq(51)
        return dataframe

    def populate_indicators(self, dataframe: DataFrame, metadata: dict):
        dataframe["prior_high"] = dataframe["high"].shift(1).rolling(20).max()
        dataframe["prior_low"] = dataframe["low"].shift(1).rolling(20).min()
        dataframe["volume_ratio"] = dataframe["volume"] / dataframe["volume"].shift(1).rolling(20).mean()
        dataframe["atr"] = ta.ATR(dataframe, timeperiod=14)
        dataframe["history_ok"] = (dataframe["date"].diff().dt.total_seconds().eq(900).rolling(21).sum() == 21)
        dataframe["history_ok"] &= dataframe["volume"].gt(0).rolling(21).sum().eq(21)
        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict):
        h1up = (dataframe["close_1h"] > dataframe["ema20_1h"]) & (dataframe["ema20_1h"] > dataframe["ema50_1h"])
        h4up = (dataframe["close_4h"] > dataframe["ema20_4h"]) & (dataframe["ema20_4h"] > dataframe["ema50_4h"])
        h1dn = (dataframe["close_1h"] < dataframe["ema20_1h"]) & (dataframe["ema20_1h"] < dataframe["ema50_1h"])
        h4dn = (dataframe["close_4h"] < dataframe["ema20_4h"]) & (dataframe["ema20_4h"] < dataframe["ema50_4h"])
        volume = (dataframe["volume"] > 0) & (dataframe["volume_ratio"] >= 1.3) & dataframe["history_ok"] & dataframe["context_ok_1h"] & dataframe["context_ok_4h"]
        dataframe["enter_long"] = ((dataframe["close"] > dataframe["prior_high"]) & h1up & h4up & volume).astype(int)
        dataframe["enter_short"] = ((dataframe["close"] < dataframe["prior_low"]) & h1dn & h4dn & volume).astype(int)
        dataframe["enter_tag"] = VERSION
        if self.config.get("dry_run") is True:
            row = dataframe.iloc[-1]
            LOG.info("PAPER_SCAN pair=%s close=%s long=%s short=%s", metadata["pair"], row["date"], int(row["enter_long"]), int(row["enter_short"]))
            if getattr(self, "ready", False):
                self.store.event(None, "CLOSED_CANDLE_SCAN", {"pair": metadata["pair"],
                    "candle_open_utc": stamp(row["date"]), "long": int(row["enter_long"]),
                    "short": int(row["enter_short"])}, utcnow())
        return dataframe

    def populate_exit_trend(self, dataframe, metadata):
        dataframe["exit_long"] = 0
        dataframe["exit_short"] = 0
        return dataframe

    def leverage(self, pair, current_time, current_rate, proposed_leverage, max_leverage, entry_tag, side, **kwargs):
        return min(2.0, max_leverage)

    def custom_stake_amount(self, pair, current_time, current_rate, proposed_stake, min_stake, max_stake, leverage, entry_tag, side, **kwargs):
        # Never let an exception fall back to Freqtrade's proposed stake.
        try:
            if not getattr(self, "ready", False) or self.config.get("dry_run") is not True or leverage != 2:
                return 0.0
            df, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
            row = df.iloc[-1]
            if not row["enter_long" if side == "long" else "enter_short"]:
                return 0.0
            quote = self.quotes.quote(pair)
            now = utcnow()
            close_at = row["date"].to_pydatetime()+timedelta(minutes=15)
            plan = make_plan(pair, side, close_at, row["atr"], row["close"], quote, quote["sz_decimals"], now)
            plan["candle_inputs"] = candle_evidence(self.dp, pair, close_at)
            plan["decision_indicators"] = {key: float(row[key]) for key in
                ("close", "prior_high", "prior_low", "volume_ratio", "atr", "close_1h", "ema20_1h",
                 "ema50_1h", "close_4h", "ema20_4h", "ema50_4h")}
            q = d(plan["preview"]["quantity"])
            stake = (q*d(plan["preview"]["entry"])/2).quantize(d(".01"), rounding="ROUND_FLOOR")
            if stake > d(max_stake) or (min_stake and stake < d(min_stake)):
                return 0.0
            if not self.store.reserve(plan, now):
                return 0.0
            self.store.event(plan["trade_id"], "PLAN_RECORDED_BEFORE_ENTRY", {"paper_only": True}, now)
            return float(stake)
        except Exception as error:
            LOG.info("PAPER_REJECT pair=%s category=%s", pair, type(error).__name__)
            return 0.0

    def confirm_trade_entry(self, pair, order_type, amount, rate, time_in_force, current_time, entry_tag, side, **kwargs):
        try:
            now = utcnow()
            plan = self.store.active(pair)
            if (not getattr(self, "ready", False) or not plan or self.config.get("dry_run") is not True or
                    plan["side"].lower() != side or
                    now >= iso(plan["expires_at_utc"]) or not quote_ok(plan["source_quote"], now) or
                    not actual_risk(plan, amount, rate)):
                return False
            self.store.state(plan["trade_id"], "CONFIRMED", now, {"quantity": amount, "entry": rate})
            try:
                review = json.loads((self.root / "macro_review.json").read_text())
            except (OSError, ValueError):
                review = {}
            if macro_allowed(review, now):
                self.store.queue(plan["trade_id"], {"type": "VALIDATED_PAPER_SIGNAL", "plan_id": plan["trade_id"], "macro_review": review})
            else:
                self.store.event(plan["trade_id"], "NO_ALERT_MACRO_UNKNOWN_OR_BLOCKED", {}, now)
            return True
        except Exception:
            return False

    def check_entry_timeout(self, pair, trade, order, current_time, **kwargs):
        plan = self.store.active(pair)
        return not plan or utcnow() >= iso(plan["expires_at_utc"])

    def order_filled(self, pair, trade, order, current_time, **kwargs):
        now = utcnow()
        if order.ft_order_side == trade.entry_side:
            plan = self.store.active(pair)
            if not plan:
                trade.set_custom_data("radar_risk_reject", True)
                self.store.event(None, "ORPHAN_NATIVE_PAPER_FILL", {"native_trade_id": trade.id}, now)
                return
            trade.set_custom_data("radar_plan_id", plan["trade_id"])
            self.store.state(plan["trade_id"], "OPEN", now, {"native_trade_id": trade.id, "fill_estimate": trade.open_rate})
            if not actual_risk(plan, trade.amount, trade.open_rate):
                trade.set_custom_data("radar_risk_reject", True)
            trade.set_custom_data("radar_last_quote_utc", stamp(now))
        else:
            ident = trade.get_custom_data("radar_plan_id")
            if ident:
                self.store.event(ident, "FREQTRADE_PAPER_EXIT_FILL", {"quantity": float(order.safe_filled), "native_order_id": order.order_id}, now)
                if not trade.is_open:
                    self.store.state(ident, "CLOSED", now, {"pnl_estimate_usdc": trade.close_profit_abs,
                        "funding_native_estimate_usdc": trade.funding_fees,
                        "quality": "UNVERIFIABLE_GAP" if trade.get_custom_data("radar_gap") else "MARK_SAMPLED_PAPER_ESTIMATE",
                        "real_fill": False})

    def _action(self, trade):
        plan = self.store.plan(trade.get_custom_data("radar_plan_id"))
        if not plan:
            return None, None
        now = utcnow()
        try:
            quote = self.quotes.quote(trade.pair)
        except Exception:
            trade.set_custom_data("radar_gap", True)
            return plan, None
        last = trade.get_custom_data("radar_last_quote_utc")
        if last and (now-iso(last)).total_seconds() > 15:
            trade.set_custom_data("radar_gap", True)
            self.store.event(plan["trade_id"], "UNOBSERVED_GAP", {"after_utc": last}, now)
        trade.set_custom_data("radar_last_quote_utc", stamp(now))
        self.store.event(plan["trade_id"], "OBSERVED_MARK_AND_BBO", quote, now)
        return plan, exit_action(plan, quote["mark"], trade.nr_of_successful_exits > 0)

    def custom_exit(self, pair, trade, current_time, current_rate, current_profit, **kwargs):
        if trade.get_custom_data("radar_risk_reject"):
            return "PAPER_RISK_REJECT_AFTER_FILL"
        if not trade.get_custom_data("radar_plan_id"):
            return "PAPER_ORPHAN_PLAN"
        _, action = self._action(trade)
        return action if action and action != "TP1" else None

    def adjust_trade_position(self, trade, current_time, current_rate, current_profit, min_stake, max_stake, **kwargs):
        if trade.has_open_orders or trade.nr_of_successful_exits > 0:
            return None
        plan, action = self._action(trade)
        if action != "TP1":
            return None
        quantity = round_size_down(d(trade.amount)/2, plan["sz_decimals"])
        stake = quantity*d(trade.stake_amount)/d(trade.amount)
        return -float(stake), "RADAR_TP1_HALF"
