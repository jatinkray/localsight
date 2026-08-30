"""Alert notification channels.

Decoupled from the analytics/event layers: anything that produces an AnalyticEvent
or Event can hand it to `dispatch`, which fans out to the configured channels
(webhook, email, push). Each channel is an injectable Notifier so tests never hit
the network, and every outbound notification is independently auditable by the
caller (apps.api.routers.alerts).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional


@dataclass
class Alert:
    rule_id: str
    rule_type: str
    camera_id: str
    severity: str = "info"  # info | warning | critical
    title: str = ""
    message: str = ""
    detail: dict = field(default_factory=dict)
    ts: Optional[str] = None


class Notifier:
    channel = "base"

    def send(self, alert: Alert) -> None:  # pragma: no cover - interface
        raise NotImplementedError


class WebhookNotifier(Notifier):
    channel = "webhook"

    def __init__(self, url: str, post: Callable[[str, dict], None] | None = None) -> None:
        self.url = url
        self._post = post

    def send(self, alert: Alert) -> None:
        payload = {
            "rule_id": alert.rule_id, "rule_type": alert.rule_type,
            "camera_id": alert.camera_id, "severity": alert.severity,
            "title": alert.title, "message": alert.message, "detail": alert.detail,
            "ts": alert.ts,
        }
        if self._post:
            self._post(self.url, payload)
            return
        try:
            import httpx
        except Exception as exc:
            raise RuntimeError("httpx is required for webhook delivery") from exc
        httpx.post(self.url, json=payload, timeout=10)


class EmailNotifier(Notifier):
    channel = "email"

    def __init__(self, smtp_host: str, smtp_port: int, sender: str, recipients: List[str],
                 send: Callable[[str, List[str], str], None] | None = None) -> None:
        self.smtp_host = smtp_host
        self.smtp_port = smtp_port
        self.sender = sender
        self.recipients = recipients
        self._send = send

    def send(self, alert: Alert) -> None:
        body = f"[{alert.severity.upper()}] {alert.title}\n{alert.message}\n{alert.detail}"
        if self._send:
            self._send(self.sender, self.recipients, body)
            return
        import smtplib
        import ssl

        with smtplib.SMTP(self.smtp_host, self.smtp_port, timeout=10) as s:
            if self.smtp_port == 465:
                s.starttls(context=ssl.create_default_context())
            s.sendmail(self.sender, self.recipients, body)


class PushNotifier(Notifier):
    """Reference push channel: delivers to a local subscriber callback/log.

    Production would target a push gateway (APNs/FCM) or a websocket fan-out;
    the interface is identical so swapping in a real gateway is a one-line change.
    """

    channel = "push"

    def __init__(self, handler: Callable[[Alert], None] | None = None) -> None:
        self._handler = handler
        self.sent: List[Alert] = []

    def send(self, alert: Alert) -> None:
        self.sent.append(alert)
        if self._handler:
            self._handler(alert)


_CHANNELS = {"webhook": WebhookNotifier, "email": EmailNotifier, "push": PushNotifier}


def build_notifier(channel: str, config: dict) -> Notifier:
    if channel not in _CHANNELS:
        raise KeyError(f"unknown channel: {channel}")
    if channel == "webhook":
        return WebhookNotifier(config["url"])
    if channel == "email":
        return EmailNotifier(config["smtp_host"], config["smtp_port"], config["sender"], config["recipients"])
    return PushNotifier()


def dispatch(alert: Alert, notifiers: List[Notifier]) -> int:
    """Fan an alert out to all supplied notifiers. Returns count delivered."""
    n = 0
    for ntf in notifiers:
        ntf.send(alert)
        n += 1
    return n
