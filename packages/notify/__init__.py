"""Alert notification channels.

Decoupled from the analytics/event layers: anything that produces an AnalyticEvent
or Event can hand it to `dispatch`, which fans out to the configured channels
(webhook, email, push). Each channel is an injectable Notifier so tests never hit
the network, and every outbound notification is independently auditable by the
caller (apps.api.routers.alerts).
"""
from __future__ import annotations

import json
import ssl
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

from collections import deque


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

        # Port 465 is implicit TLS (SMTPS); STARTTLS is the 587 upgrade path.
        # Calling starttls() on 465 fails or hangs against real servers.
        if self.smtp_port == 465:
            with smtplib.SMTP_SSL(self.smtp_host, self.smtp_port, timeout=10,
                                  context=ssl.create_default_context()) as s:
                s.sendmail(self.sender, self.recipients, body)
        else:
            with smtplib.SMTP(self.smtp_host, self.smtp_port, timeout=10) as s:
                s.starttls(context=ssl.create_default_context())
                s.sendmail(self.sender, self.recipients, body)


class PushNotifier(Notifier):
    """Push notifications via ntfy (self-hostable) with a reference fallback.

    In production, configure with an ntfy ``server`` and ``topic`` (and optional
    auth token, priority 1-5, tags, click URL) and the notifier POSTs to the
    ntfy HTTP API. The HTTP transport is injectable (``post``) so the test suite
    never hits the network. Without ntfy config, behaves as the reference
    in-process rolling buffer + optional ``handler`` callback (useful for local
    dev and as a debug capture of every dispatched alert).
    """

    channel = "push"

    def __init__(
        self,
        handler: Callable[[Alert], None] | None = None,
        server: str | None = None,
        topic: str | None = None,
        auth_token: str | None = None,
        priority: int | None = None,
        tags: list[str] | str | None = None,
        click: str | None = None,
        title: str | None = None,
        post: Callable[[str, dict, dict], None] | None = None,
    ) -> None:
        self._handler = handler
        self.server = (server or "").rstrip("/") or None
        self.topic = topic
        self.auth_token = auth_token
        self.priority = priority
        self.tags = tags
        self.click = click
        self.title = title
        self._post = post
        self.sent: deque = deque(maxlen=500)

    def _ntfy_enabled(self) -> bool:
        return bool(self.server and self.topic)

    def _build_payload(self, alert: Alert) -> tuple[str, dict, dict]:
        url = f"{self.server}/{self.topic.lstrip('/')}"
        body: dict = {
            "title": self.title or alert.title or f"LocalSight · {alert.rule_type}",
            "message": alert.message or alert.title or alert.rule_type,
        }
        p = self.priority if isinstance(self.priority, int) else 3
        body["priority"] = max(1, min(5, p))
        tags_list: list[str] = []
        if isinstance(self.tags, list):
            tags_list = [str(t) for t in self.tags if str(t).strip()]
        elif isinstance(self.tags, str) and self.tags.strip():
            tags_list = [t.strip() for t in self.tags.split(",") if t.strip()]
        if not tags_list:
            tags_list = ["localsight"]
        tags_list.append(alert.severity or "info")
        if alert.camera_id:
            tags_list.append(f"camera:{alert.camera_id}")
        body["tags"] = tags_list
        if self.click:
            body["click"] = self.click
        headers = {"Content-Type": "application/json"}
        if self.auth_token:
            headers["Authorization"] = f"Bearer {self.auth_token}"
        return url, body, headers

    def send(self, alert: Alert) -> None:
        self.sent.append(alert)
        if self._handler:
            self._handler(alert)
        if not self._ntfy_enabled():
            return
        url, body, headers = self._build_payload(alert)
        if self._post:
            self._post(url, body, headers)
            return
        try:
            import httpx
        except ImportError as exc:
            raise RuntimeError("httpx is required for ntfy push delivery") from exc
        httpx.post(url, json=body, headers=headers, timeout=10)


class MqttNotifier(Notifier):
    """MQTT notifier: publishes alerts as JSON to a broker.

    The transport is injectable (``publish``) so the test suite never needs a live
    broker; in production omit it and ``paho-mqtt`` is imported lazily. Topic
    strings may template ``{camera_id}`` and ``{rule_type}``; empty segments are
    collapsed so a ``*`` / blank rule produces a clean topic.
    """

    channel = "mqtt"

    def __init__(
        self,
        host: str,
        port: int = 1883,
        topic: str = "localsight/alerts/{camera_id}/{rule_type}",
        publish: Callable[[str, str, int, bool], None] | None = None,
        username: str | None = None,
        password: str | None = None,
        tls: bool = False,
        qos: int = 0,
        retain: bool = False,
    ) -> None:
        self.host = host
        self.port = port
        self.topic_template = topic
        self._publish = publish
        self.username = username
        self.password = password
        self.tls = tls
        self.qos = qos
        # Default retain=False: alerts are transient. A retained alert would
        # replay to every future subscriber forever — stale security alerts
        # persisting on the broker long after the incident.
        self.retain = retain

    def _render_topic(self, alert: Alert) -> str:
        camera = alert.camera_id or "unknown"
        rule = alert.rule_type or "unknown"
        topic = self.topic_template.replace("{camera_id}", camera).replace("{rule_type}", rule)
        parts = [p for p in topic.split("/") if p]
        return "/".join(parts) if parts else "localsight/alerts"

    def _payload(self, alert: Alert) -> str:
        return json.dumps(
            {
                "source": "localsight",
                "rule_id": alert.rule_id,
                "rule_type": alert.rule_type,
                "camera_id": alert.camera_id,
                "severity": alert.severity,
                "title": alert.title,
                "message": alert.message,
                "detail": alert.detail,
                "ts": alert.ts,
            },
            default=str,
        )

    def send(self, alert: Alert) -> None:
        topic = self._render_topic(alert)
        payload = self._payload(alert)
        if self._publish:
            self._publish(topic, payload, self.qos, self.retain)
            return
        try:
            from paho.mqtt.publish import single as _publish_single
        except ImportError as exc:
            raise RuntimeError("paho-mqtt is required for MQTT delivery") from exc
        auth = {"username": self.username, "password": self.password or ""} if self.username else None
        tls_config = {"tls_version": ssl.PROTOCOL_TLS_CLIENT} if self.tls else None
        _publish_single(
            topic, payload, hostname=self.host, port=self.port,
            auth=auth, tls=tls_config, qos=self.qos, retain=self.retain,
        )


_CHANNELS = {"webhook": WebhookNotifier, "email": EmailNotifier, "push": PushNotifier, "mqtt": MqttNotifier}


def build_notifier(channel: str, config: dict) -> Notifier:
    if channel not in _CHANNELS:
        raise KeyError(f"unknown channel: {channel}")
    if channel == "webhook":
        return WebhookNotifier(config["url"])
    if channel == "email":
        return EmailNotifier(config["smtp_host"], config["smtp_port"], config["sender"], config["recipients"])
    if channel == "mqtt":
        return MqttNotifier(
            host=config.get("host", "localhost"),
            port=int(config.get("port", 1883)),
            topic=config.get("topic", "localsight/alerts/{camera_id}/{rule_type}"),
            publish=config.get("_publish"),
            username=config.get("username"),
            password=config.get("password"),
            tls=bool(config.get("tls", False)),
            qos=int(config.get("qos", 0)),
            retain=bool(config.get("retain", True)),
        )
    if channel == "push":
        return PushNotifier(
            server=config.get("server"),
            topic=config.get("topic"),
            auth_token=config.get("auth_token"),
            priority=config.get("priority"),
            tags=config.get("tags"),
            click=config.get("click"),
            title=config.get("title"),
            post=config.get("_post"),
        )
    return PushNotifier()


def dispatch(alert: Alert, notifiers: List[Notifier]) -> int:
    """Fan an alert out to all supplied notifiers. Returns count delivered."""
    n = 0
    for ntf in notifiers:
        ntf.send(alert)
        n += 1
    return n
