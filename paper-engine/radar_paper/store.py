"""Durable plan-before-entry journal and transactional delivery outbox."""
import json
import sqlite3
import threading
from pathlib import Path

from .core import canonical, digest, stamp
from .risk import iso


class Store:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.db = sqlite3.connect(path, timeout=10, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
        PRAGMA journal_mode=WAL;
        PRAGMA synchronous=FULL;
        CREATE TABLE IF NOT EXISTS radar_plans (
          id TEXT PRIMARY KEY, pair TEXT NOT NULL, payload TEXT NOT NULL,
          sha256 TEXT NOT NULL, state TEXT NOT NULL, expires TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS radar_events (
          seq INTEGER PRIMARY KEY, plan_id TEXT, at_utc TEXT NOT NULL,
          type TEXT NOT NULL, payload TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS radar_outbox (
          id TEXT PRIMARY KEY, payload TEXT NOT NULL, state TEXT NOT NULL,
          not_before REAL NOT NULL DEFAULT 0, attempts INTEGER NOT NULL DEFAULT 0);
        CREATE UNIQUE INDEX IF NOT EXISTS radar_one_active
          ON radar_plans((1)) WHERE state IN ('PREPARED','CONFIRMED','OPEN');
        """)
        # A crash after HTTP acceptance cannot safely be retried as a new message.
        self.db.execute("UPDATE radar_outbox SET state='UNKNOWN' WHERE state='SENDING'")
        self.db.commit()

    def close(self):
        self.db.close()

    def event(self, ident, kind, payload, now):
        with self.lock, self.db:
            self.db.execute("INSERT INTO radar_events(plan_id,at_utc,type,payload) VALUES(?,?,?,?)",
                            (ident, stamp(now), kind, canonical(payload)))

    def reserve(self, plan, now):
        """Single global paper risk slot, including orders awaiting a fill."""
        with self.lock, self.db:
            self.db.execute("BEGIN IMMEDIATE")
            self.db.execute("UPDATE radar_plans SET state='EXPIRED' WHERE state='PREPARED' AND expires<=?",
                            (stamp(now),))
            if self.db.execute("SELECT 1 FROM radar_plans WHERE state IN ('PREPARED','CONFIRMED','OPEN')").fetchone():
                return False
            cursor = self.db.execute("INSERT OR IGNORE INTO radar_plans VALUES(?,?,?,?,?,?)",
                (plan["trade_id"], plan["pair"], canonical(plan), digest(plan), "PREPARED", plan["expires_at_utc"]))
            return cursor.rowcount == 1

    def plan(self, ident):
        with self.lock:
            row = self.db.execute("SELECT * FROM radar_plans WHERE id=?", (ident,)).fetchone()
        if not row:
            return None
        obj = json.loads(row["payload"])
        if digest(obj) != row["sha256"]:
            raise ValueError("Plan integrity failure")
        return obj

    def active(self, pair):
        with self.lock:
            row = self.db.execute("SELECT id FROM radar_plans WHERE pair=? AND state IN ('PREPARED','CONFIRMED','OPEN')", (pair,)).fetchone()
        return self.plan(row[0]) if row else None

    def status(self, ident):
        with self.lock:
            row = self.db.execute("SELECT state FROM radar_plans WHERE id=?", (ident,)).fetchone()
        return row[0] if row else None

    def state(self, ident, state, now, payload=None):
        if state not in ("CONFIRMED", "OPEN", "CLOSED", "NO_ENTRY", "EXPIRED"):
            raise ValueError("Invalid state")
        with self.lock, self.db:
            row = self.db.execute("SELECT state FROM radar_plans WHERE id=?", (ident,)).fetchone()
            transitions = {"PREPARED": {"CONFIRMED", "NO_ENTRY", "EXPIRED"},
                           "CONFIRMED": {"OPEN", "NO_ENTRY"}, "OPEN": {"CLOSED"},
                           "CLOSED": set(), "NO_ENTRY": set(), "EXPIRED": set()}
            if payload and payload.get("recovered") is True:
                transitions["CONFIRMED"].add("CLOSED")
            if not row or (state != row[0] and state not in transitions[row[0]]):
                raise ValueError("Invalid paper lifecycle transition")
            self.db.execute("UPDATE radar_plans SET state=? WHERE id=?", (state, ident))
            self.db.execute("INSERT INTO radar_events(plan_id,at_utc,type,payload) VALUES(?,?,?,?)",
                            (ident, stamp(now), state, canonical(payload or {})))

    def queue(self, ident, payload):
        with self.lock, self.db:
            return self.db.execute("INSERT OR IGNORE INTO radar_outbox(id,payload,state) VALUES(?,?,'PENDING')",
                                   (ident, canonical(payload))).rowcount == 1

    def claim(self, now, ident=None):
        with self.lock, self.db:
            self.db.execute("BEGIN IMMEDIATE")
            row = self.db.execute("SELECT * FROM radar_outbox WHERE state='PENDING' AND not_before<=? AND (? IS NULL OR id=?) ORDER BY rowid LIMIT 1", (now.timestamp(), ident, ident)).fetchone()
            if not row:
                return None
            self.db.execute("UPDATE radar_outbox SET state='SENDING',attempts=attempts+1 WHERE id=?", (row["id"],))
        return row["id"], json.loads(row["payload"])

    def delivery(self, ident, state, not_before=0):
        with self.lock, self.db:
            self.db.execute("UPDATE radar_outbox SET state=?,not_before=? WHERE id=?", (state, not_before, ident))

    def summary(self):
        with self.lock:
            plans = dict(self.db.execute("SELECT state,count(*) FROM radar_plans GROUP BY state").fetchall())
            outbox = dict(self.db.execute("SELECT state,count(*) FROM radar_outbox GROUP BY state").fetchall())
        return {"plans_by_state": plans, "outbox_by_state": outbox,
                "live_orders_enabled": False, "pnl_type": "FREQTRADE_PAPER_ESTIMATE"}
