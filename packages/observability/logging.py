"""Structured JSON logging configuration.

Every log line is a single JSON object with a request ID when available, so logs
are machine-parseable and never accidentally include secrets (we never log
credentials, tokens, or raw biometric data — enforced by call sites).
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import sys


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": dt.datetime.fromtimestamp(record.created, dt.timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        # never include arbitrary `extra` secrets; whitelist request_id only
        rid = getattr(record, "request_id", None)
        if rid:
            payload["request_id"] = rid
        return json.dumps(payload, default=str)


def configure_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
    # Quiet noisy libraries.
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
