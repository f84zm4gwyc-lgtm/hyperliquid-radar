# Hyperliquid Radar — Render read-only WebSocket proof of concept

**Stage: experimental / no live trading / paper-signals only.**

A lightweight Python process for listening to official Hyperliquid **public** BTC and ETH perpetual feeds and flagging **WATCH_ONLY** candidates. It never requests an account key, makes a private API call, opens an order, or sends a trading recommendation. Signals are not a validated strategy and must not be traded.

## Features
- WebSocket: BTC/ETH `activeAssetCtx` (mark price, OI/funding), `bbo`, 5m and 15m candles.
- REST snapshot bootstrap/refresh every ~15 minutes: closed 5m, 15m, 1h, 4h and daily candles (no future data). The first 1h/4h feed uses time-aligned close for 15m breakout checks.
- Reconnect, heartbeat, data-age checks, basic OHLC sanity checks and missed candles detected before signals.
- Candidates **only** on a confirmed 15m breakout with volume and 1h/4h trend; not a backtested edge.
- Deduplicated events in SQLite. Set `RADAR_DB_PATH=/var/data/radar.sqlite3` with a **persistent disk** for production-grade continuity.
- GET `/healthz` returns 200 only when recent market data and history are available; GET `/status` contains no account credentials, watch-only candidates and feed age.
- No direct connection to ChatGPT notifications. This is a prototype sensor, not a completed trading-alert pipeline.

## Render settings — service not yet deployed
1. Create a **Web Service**, source `https://github.com/f84zm4gwyc-lgtm/hyperliquid-radar` branch `main` **after** uploading the `render-radar/` directory.
2. Region: Frankfurt. Runtime: Python 3.12. Root directory: leave blank.
3. Build command: `pip install -r render-radar/requirements.txt`.
4. Start command: `python render-radar/app.py`.
5. Optional env var: `RADAR_DB_PATH=/var/data/radar.sqlite3` only when attaching a persistent disk mounted to `/var/data`.
6. For a one-time technical demo use the Free plan (it sleeps, does NOT offer ongoing monitoring, and its local database is ephemeral); for continuous use choose a paid always-on instance **after budget approval**. A 512MB light worker/web service should be benchmarked before promising capacity; this is not sized for Freqtrade.
7. Once deployed, inspect `/healthz` and `/status` after the history bootstrap. Check Render logs and ensure reconnects/restarts are handled.

## Local tests

```bash
python -m unittest discover -s render-radar/tests -v
```

No live-market/Render integration test has been performed yet; **unit tests are not proof of uptime, low latency, or profitability**.

## Security limits
- No external trade API, wallet address, API key, or private key. Do **not** add a trading key.
- `/status` is public on a web-service URL; it contains only public market data and candidate metadata. Avoid adding private info to this endpoint.
- A sudden gap, API disconnect, weak fill, or exchange outage can make even a well-designed signal unusable.
- Render Free service sleeps after idle and is not appropriate for a 24/7 trading radar. If it restarts, `/tmp/radar_ws.sqlite3` is lost; use paid persistent storage if continuity is essential.

## Scientific evaluation
Log emitted candidates as raw observations and compare to pre-registered simulated orders **only after** recording the market price available at alert receipt, realistic fee/slippage assumptions, and all outcomes including canceled/missed alerts. In backtests enforce no lookahead and test out-of-sample across multiple regimes. Compare against naive hold/cash baselines.

## Official docs
- https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/websocket
- https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/websocket/subscriptions
- https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/websocket/timeouts-and-heartbeats
- https://render.com/docs/free
