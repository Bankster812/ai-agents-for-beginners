"""Optional alerting. Sends to a Telegram bot iff TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID are set;
otherwise every call is a silent no-op. Never raises into the trading loop. Rate-limited so an
error storm cannot get the bot banned (max 20 messages / 10 min, duplicates within 5 min dropped).
"""
from __future__ import annotations

import logging
import os
import threading
import time
from collections import deque

import requests

log = logging.getLogger("execution.notify")


class Notifier:
    def __init__(self, prefix: str = "[mirofish-exec]", max_per_window: int = 20, window_s: float = 600):
        self.token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
        self.chat = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
        self.prefix = prefix
        self.enabled = bool(self.token and self.chat)
        self.sent: deque[float] = deque()
        self.recent: dict[str, float] = {}
        self.max, self.window = max_per_window, window_s
        self.lock = threading.Lock()

    def __call__(self, text: str) -> bool:
        return self.send(text)

    def send(self, text: str) -> bool:
        if not self.enabled:
            return False
        now = time.time()
        with self.lock:
            while self.sent and now - self.sent[0] > self.window:
                self.sent.popleft()
            if len(self.sent) >= self.max or now - self.recent.get(text, 0) < 300:
                return False
            self.sent.append(now)
            self.recent[text] = now
        try:
            r = requests.post(f"https://api.telegram.org/bot{self.token}/sendMessage",
                              json={"chat_id": self.chat, "text": f"{self.prefix} {text}"[:4000],
                                    "disable_web_page_preview": True}, timeout=10)
            return r.status_code == 200
        except Exception as e:  # noqa: BLE001  (never log the URL: it contains the token)
            log.warning("telegram_send_failed", extra={"evt": "notify_failed", "err": type(e).__name__})
            return False
