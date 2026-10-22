#!/usr/bin/env python3
"""Paper-only launcher. No arbitrary Freqtrade options or account credentials."""
import argparse
import importlib.metadata
import json
import os
import signal
import sqlite3
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

from radar_paper.core import PAIRS, VERSION, stamp, utcnow

ROOT = Path(__file__).resolve().parent
PINNED = {"freqtrade": "2026.9", "ccxt": "4.5.89", "websockets": "17.3"}


def child_env(data, inherited=None):
    env = dict(os.environ if inherited is None else inherited)
    # Freqtrade environment overrides must never turn simulation into live mode.
    for key in list(env):
        if key.upper().startswith("FREQTRADE__"):
            del env[key]
    env["RADAR_DATA_DIR"] = str(data.resolve())
    env["PYTHONPATH"] = str(ROOT)
    return env


def configuration(data):
    asynchronous = {"aiohttp_trust_env": True}
    if os.environ.get("SSL_CERT_FILE"):
        asynchronous["cafile"] = os.environ["SSL_CERT_FILE"]
    return {
        "dry_run": True, "dry_run_wallet": 10396.17,
        "trading_mode": "futures", "margin_mode": "isolated",
        "stake_currency": "USDC", "stake_amount": 1000, "max_open_trades": 1,
        "tradable_balance_ratio": .99, "timeframe": "15m",
        "exchange": {"name": "hyperliquid", "walletAddress": "", "privateKey": "",
            "key": "", "secret": "", "enable_ws": False,
            "ccxt_config": {"requests_trust_env": True, "verify": True},
            "ccxt_async_config": asynchronous,
            "pair_whitelist": list(PAIRS), "pair_blacklist": []},
        "pairlists": [{"method": "StaticPairList"}],
        "entry_pricing": {"price_side": "other", "use_order_book": True, "order_book_top": 1},
        "exit_pricing": {"price_side": "other", "use_order_book": True, "order_book_top": 1},
        "order_types": {"entry": "limit", "exit": "limit", "stoploss": "limit",
                        "emergency_exit": "limit", "stoploss_on_exchange": False},
        "order_time_in_force": {"entry": "GTC", "exit": "GTC"},
        "unfilledtimeout": {"entry": 5, "exit": 1, "exit_timeout_count": 0, "unit": "minutes"},
        "exit_profit_only": False, "ignore_roi_if_entry_signal": False,
        "internals": {"process_throttle_secs": 5},
        "bot_name": "radar-btc-eth-paper", "initial_state": "running",
        "force_entry_enable": False, "api_server": {"enabled": False},
        "telegram": {"enabled": False, "token": "", "chat_id": ""}, "webhook": {"enabled": False},
        "cancel_open_orders_on_exit": True,
        "db_url": "sqlite:///"+str((data/"freqtrade.sqlite").resolve().as_posix()),
        "fee": .00045,
        "stoploss": -.99, "minimal_roi": {},
        "dataformat_ohlcv": "feather", "dataformat_trades": "feather",
    }


def require_versions():
    for package, expected in PINNED.items():
        if importlib.metadata.version(package) != expected:
            raise RuntimeError(f"Install the pinned requirements: {package}=={expected}")


def validated_configuration(data):
    from freqtrade.configuration.config_validation import validate_config_schema
    from freqtrade.enums import RunMode
    cfg = configuration(data)
    # Required even when disabled by the pinned upstream schema.
    cfg["api_server"].update({"listen_ip_address": "127.0.0.1", "listen_port": 8080,
        "username": "", "password": "", "jwt_secret_key": "disabled-rpc-no-listener-000000000000"})
    validate_config_schema({**cfg, "runmode": RunMode.DRY_RUN})
    return cfg


@contextmanager
def singleton(data):
    data.mkdir(parents=True, exist_ok=True)
    handle = (data/"process.lock").open("a+b")
    handle.seek(0)
    if handle.read(1) == b"":
        handle.write(b"0")
        handle.flush()
    handle.seek(0)
    try:
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as error:
        handle.close()
        raise RuntimeError("A paper process already uses this data directory") from error
    try:
        yield
    finally:
        handle.close()  # Keep the lock file: unlinking can create two lock domains.


def report(data):
    result = {"strategy": VERSION, "live_orders_enabled": False}
    for filename, queries in {
        "radar.sqlite": {"plans": "SELECT state,count(*) FROM radar_plans GROUP BY state",
                         "deliveries": "SELECT state,count(*) FROM radar_outbox GROUP BY state",
                         "events": "SELECT type,count(*) FROM radar_events GROUP BY type"},
        "freqtrade.sqlite": {"native_paper_trades": "SELECT is_open,count(*) FROM trades GROUP BY is_open"},
    }.items():
        path = data/filename
        if path.exists():
            with sqlite3.connect(path.as_uri()+"?mode=ro", uri=True) as db:
                result[filename] = {name: dict(db.execute(sql).fetchall()) for name, sql in queries.items()}
    status = data/"status.json"
    if status.exists():
        result["last_heartbeat"] = json.loads(status.read_text())
    return result


def launch(data, seconds):
    require_versions()
    cfg = validated_configuration(data)
    config = data/"generated-paper.json"
    config.write_text(json.dumps(cfg, indent=2))
    config.chmod(0o600)
    userdir = data/"user_data"
    userdir.mkdir(exist_ok=True)
    command = [sys.executable, "-m", "freqtrade", "trade", "--dry-run", "--config", str(config),
               "--strategy", "RadarPaper", "--strategy-path", str(ROOT), "--userdir", str(userdir),
               "--logfile", str(data/"paper.log")]
    process = subprocess.Popen(command, env=child_env(data))
    deadline = time.monotonic()+seconds if seconds else None
    stopped_for_probe = False
    old_handlers = {}

    def stop(*_):
        nonlocal stopped_for_probe
        stopped_for_probe = True
        if process.poll() is None:
            process.send_signal(signal.SIGINT if os.name != "nt" else signal.SIGTERM)

    for sig in (signal.SIGINT, signal.SIGTERM):
        old_handlers[sig] = signal.signal(sig, stop)
    try:
        while process.poll() is None:
            if deadline and time.monotonic() >= deadline:
                stop()
                try:
                    process.wait(timeout=30)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
                    raise RuntimeError("Paper process failed to stop cleanly; inspect journal")
            time.sleep(.2)
    finally:
        for sig, handler in old_handlers.items():
            signal.signal(sig, handler)
    if stopped_for_probe:
        print(json.dumps({"bounded_or_requested_stop": True, "child_exit_code": process.returncode,
                          "paper_summary": report(data)}, indent=2))
        return 0 if process.returncode in (0, 130, -signal.SIGINT, -signal.SIGTERM) else 1
    # Upstream can exit 0 on a configuration error: an unsolicited exit is a failure.
    return process.returncode or 1


def main(argv=None):
    parser = argparse.ArgumentParser(description="Radar BTC/ETH — simulation exclusivement")
    parser.add_argument("action", choices=("check", "trade", "summary", "test-slack"))
    parser.add_argument("--data", type=Path, default=Path(os.environ.get("RADAR_DATA_DIR", ROOT/"data")))
    parser.add_argument("--seconds", type=int, default=0, help="Bounded paper probe; 0 runs until stopped")
    args = parser.parse_args(argv)
    data = args.data.resolve()
    if args.seconds < 0:
        parser.error("seconds must be nonnegative")
    if args.action == "summary":
        print(json.dumps(report(data), indent=2))
        return 0
    if args.action == "check":
        require_versions()
        validated_configuration(data)
        print(json.dumps({"pinned_versions": PINNED, "paper_only": True,
                          "slack_configured": bool(os.environ.get("RADAR_SLACK_WEBHOOK")),
                          "macro_default": "UNKNOWN", "data": str(data)}, indent=2))
        return 0
    with singleton(data):
        if args.action == "trade":
            return launch(data, args.seconds)
        from radar_paper.notify import Sender
        from radar_paper.store import Store
        secret = os.environ.get("RADAR_SLACK_WEBHOOK", "")
        if not secret:
            raise RuntimeError("RADAR_SLACK_WEBHOOK is missing; no test was sent")
        store = Store(data/"radar.sqlite")
        try:
            ident = "TEST-"+stamp(utcnow())+"-"+uuid4().hex[:10]
            store.queue(ident, {"type": "TEST"})
            outcome = Sender(store, secret).once(only_ident=ident)
            print(json.dumps({"signal_id": ident, "outcome": outcome,
                              "notice": "HTTP ACK tests the emitter; Work receipt and user notification must be verified separately"}))
            return 0 if outcome == "SENT" else 1
        finally:
            store.close()


if __name__ == "__main__":
    raise SystemExit(main())
