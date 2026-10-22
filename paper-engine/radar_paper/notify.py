"""Durable Slack webhook sender; secret URL is never logged or persisted."""
import json
import threading
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import Request, build_opener, HTTPRedirectHandler

from .core import macro_allowed, quote_ok, slack_message, utcnow


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def webhook_send(secret_url, message):
    parsed = urlsplit(secret_url)
    if (parsed.scheme != "https" or parsed.hostname not in ("hooks.slack.com", "hooks.slack-gov.com") or
            not parsed.path.startswith("/services/") or parsed.username or parsed.password or
            parsed.query or parsed.fragment or parsed.port not in (None, 443)):
        raise ValueError("Invalid Slack webhook configuration")
    request = Request(secret_url, data=json.dumps({"text": message}).encode(),
                      headers={"Content-Type": "application/json"}, method="POST")
    with build_opener(NoRedirect()).open(request, timeout=4) as response:
        if response.status != 200 or response.read(100).strip() != b"ok":
            raise RuntimeError("Slack did not acknowledge")


class Sender:
    def __init__(self, store, secret_url, transport=webhook_send):
        self.store, self.secret_url, self.transport = store, secret_url, transport
        self.stop = threading.Event()
        self.thread = None

    def once(self, now=None, only_ident=None):
        now = now or utcnow()
        if not self.secret_url:
            return "UNCONFIGURED"
        claimed = self.store.claim(now, only_ident)
        if not claimed:
            return "IDLE"
        ident, payload = claimed
        try:
            if payload.get("type") == "TEST":
                message = "RADAR_E2E_TEST — TEST — Radar papier, aucun signal de trading.\nsignal_id=" + ident
            elif payload.get("type") == "VALIDATED_PAPER_SIGNAL":
                plan = self.store.plan(payload["plan_id"])
                if (not plan or self.store.status(payload["plan_id"]) not in ("CONFIRMED", "OPEN") or
                        not quote_ok(plan["source_quote"], now, max_age=180)):
                    raise ValueError("Signal source expired")
                message = slack_message(plan, payload["macro_review"], now)
            else:
                raise ValueError("Unvalidated event type")
        except (KeyError, ValueError, TypeError):
            self.store.delivery(ident, "EXPIRED_OR_REJECTED")
            return "EXPIRED_OR_REJECTED"
        try:
            self.transport(self.secret_url, message)
        except HTTPError as error:
            if error.code == 429:
                try:
                    delay = min(300, max(1, float(error.headers.get("Retry-After", "30"))))
                except (ValueError, TypeError):
                    delay = 30
                self.store.delivery(ident, "PENDING", now.timestamp()+delay)
                return "RATE_LIMITED"
            self.store.delivery(ident, "FAILED" if 400 <= error.code < 500 else "UNKNOWN")
            return "FAILED_OR_UNKNOWN"
        except Exception:
            # A timeout can happen after Slack accepted the POST: no blind retry.
            self.store.delivery(ident, "UNKNOWN")
            return "UNKNOWN"
        self.store.delivery(ident, "SENT")
        self.store.event(payload.get("plan_id"), "SLACK_HTTP_ACK", {"signal_id": ident}, utcnow())
        return "SENT"

    def start(self):
        def worker():
            while not self.stop.is_set():
                self.once()
                self.stop.wait(1)
        self.thread = threading.Thread(target=worker, daemon=True, name="radar-slack-outbox")
        self.thread.start()

    def close(self):
        self.stop.set()
        if self.thread:
            self.thread.join(timeout=6)
