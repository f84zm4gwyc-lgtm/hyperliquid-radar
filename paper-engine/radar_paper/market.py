"""Small public /info reader. No exchange or wallet write capability."""
import json
from urllib.request import Request, urlopen

from .core import PAIRS, quote_ok, stamp, utcnow

API = "https://api.hyperliquid.xyz/info"


def public_info(payload):
    if payload.get("type") not in ("metaAndAssetCtxs", "l2Book"):
        raise ValueError("Only public market reads allowed")
    if payload["type"] == "l2Book" and payload.get("coin") not in PAIRS.values():
        raise ValueError("Unsupported coin")
    request = Request(API, data=json.dumps(payload).encode(),
                      headers={"Content-Type": "application/json"}, method="POST")
    with urlopen(request, timeout=4) as response:
        raw = response.read(2_000_001)
    if len(raw) > 2_000_000:
        raise ValueError("Oversized public response")
    return json.loads(raw)


class PublicQuotes:
    """Public BBO + asset context stream; callbacks only read the fresh cache."""
    def __init__(self, sizes):
        import threading
        if set(sizes) != set(PAIRS) or any(isinstance(v, bool) or not isinstance(v, int) or not 0 <= v <= 6 for v in sizes.values()):
            raise ValueError("Use current public market metadata for both pairs")
        self.sizes = sizes
        self.lock = threading.RLock()
        self.stop = threading.Event()
        self.thread = None
        self.connected = False
        self.reconnects = 0
        self.last_error_category = None
        self.marks, self.books = {}, {}

    def process(self, message, now=None):
        from datetime import datetime, timezone
        now = now or utcnow()
        channel, data = message.get("channel"), message.get("data")
        if not isinstance(data, dict) or data.get("coin") not in PAIRS.values():
            return
        coin = data["coin"]
        with self.lock:
            if channel == "activeAssetCtx":
                self.marks[coin] = {"mark": float(data["ctx"]["markPx"]), "mark_at_utc": stamp(now)}
            elif channel == "bbo":
                bids = data["bbo"]
                if len(bids) != 2 or not all(bids):
                    return
                book_at = datetime.fromtimestamp(int(data["time"])/1000, timezone.utc)
                value = {"bid": float(bids[0]["px"]), "ask": float(bids[1]["px"]),
                         "book_at_utc": stamp(book_at), "received_at_utc": stamp(now)}
                if coin not in self.books or value["book_at_utc"] >= self.books[coin]["book_at_utc"]:
                    self.books[coin] = value

    def _snapshot(self, pair):
        coin = PAIRS[pair]
        if coin not in self.marks or coin not in self.books:
            raise ValueError("Waiting for both public subscriptions")
        return {**self.marks[coin], **self.books[coin], "sz_decimals": self.sizes[pair],
                "mark_time_basis": "LOCAL_RECEIPT_NO_EXCHANGE_TIMESTAMP",
                "book_time_basis": "EXCHANGE_EVENT_TIMESTAMP",
                "source": "HYPERLIQUID_MAINNET_PUBLIC_WEBSOCKET"}

    def quote(self, pair):
        with self.lock:
            value = self._snapshot(pair)
            if not self.connected or not quote_ok(value, utcnow()):
                raise ValueError("Disconnected, stale or incoherent public quote")
            return value

    def status(self):
        with self.lock:
            quotes = {}
            for pair in PAIRS:
                try:
                    q = self._snapshot(pair)
                    quotes[pair] = {"fresh": self.connected and quote_ok(q, utcnow()), "quote": q}
                except ValueError:
                    quotes[pair] = {"fresh": False}
            return {"connected": self.connected, "reconnects": self.reconnects,
                    "last_error_category": self.last_error_category, "quotes": quotes}

    async def consume(self):
        import asyncio
        from websockets.asyncio.client import connect
        while not self.stop.is_set():
            try:
                async with connect("wss://api.hyperliquid.xyz/ws", open_timeout=8,
                                   ping_interval=20, ping_timeout=20, close_timeout=3,
                                   max_size=2_000_000, max_queue=64) as ws:
                    with self.lock:
                        self.connected = True
                        self.reconnects += 1
                        self.marks.clear(); self.books.clear()
                        self.last_error_category = None
                    for coin in PAIRS.values():
                        for kind in ("bbo", "activeAssetCtx"):
                            await ws.send(json.dumps({"method": "subscribe", "subscription": {"type": kind, "coin": coin}}))
                    while not self.stop.is_set():
                        try:
                            raw = await asyncio.wait_for(ws.recv(), timeout=1)
                        except asyncio.TimeoutError:
                            continue
                        try:
                            self.process(json.loads(raw))
                        except (KeyError, ValueError, TypeError, IndexError, OverflowError):
                            self.last_error_category = "INVALID_PUBLIC_MESSAGE"
            except Exception as error:
                self.last_error_category = type(error).__name__
            finally:
                self.connected = False
            # Short cancellable backoff, never block an entry/exit callback.
            for _ in range(20):
                if self.stop.is_set():
                    break
                await asyncio.sleep(.1)

    def start(self):
        import asyncio
        import threading
        self.thread = threading.Thread(target=lambda: asyncio.run(self.consume()), daemon=True,
                                       name="radar-public-hyperliquid")
        self.thread.start()

    def close(self):
        self.stop.set()
        if self.thread:
            self.thread.join(timeout=12)
