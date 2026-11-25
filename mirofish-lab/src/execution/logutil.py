"""Structured JSON logging with secret redaction and size-based rotation."""
from __future__ import annotations

import json
import logging
import logging.handlers
import os
import re
import sys
import time
from pathlib import Path

SECRET_ENV_VARS = ("POLY_PRIVATE_KEY", "POLY_API_KEY", "POLY_API_SECRET", "POLY_API_PASSPHRASE",
                   "TELEGRAM_BOT_TOKEN")
_HEX_KEY = re.compile(r"0x[0-9a-fA-F]{64}")          # private-key shaped strings
_STD = set(logging.LogRecord("", 0, "", 0, "", (), None).__dict__) | {"message", "asctime"}


def _redact(s: str) -> str:
    for k in SECRET_ENV_VARS:
        v = os.environ.get(k)
        if v and len(v) >= 6:
            s = s.replace(v, "***REDACTED***")
    return _HEX_KEY.sub("0x***REDACTED_KEY***", s)


class JsonFormatter(logging.Formatter):
    def format(self, r: logging.LogRecord) -> str:
        d = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(r.created)) + f".{int(r.msecs):03d}Z",
             "lvl": r.levelname, "logger": r.name, "msg": r.getMessage()}
        for k, v in r.__dict__.items():
            if k not in _STD and not k.startswith("_"):
                d[k] = v
        if r.exc_info:
            d["exc"] = self.formatException(r.exc_info)
        return _redact(json.dumps(d, default=str))


def setup_logging(log_dir: str | Path | None = None, level: str = "INFO", max_mb: int = 20, backups: int = 5) -> None:
    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(level)
    fmt = JsonFormatter()
    h = logging.StreamHandler(sys.stdout)
    h.setFormatter(fmt)
    root.addHandler(h)
    if log_dir:
        Path(log_dir).mkdir(parents=True, exist_ok=True)
        fh = logging.handlers.RotatingFileHandler(Path(log_dir) / "runner.jsonl", maxBytes=max_mb * 2**20,
                                                  backupCount=backups)
        fh.setFormatter(fmt)
        root.addHandler(fh)
    for noisy in ("urllib3", "httpx", "py_clob_client"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
