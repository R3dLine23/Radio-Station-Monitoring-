"""Ways to deliver an alert. Each notifier is a small class with ``send(alert)``."""

from __future__ import annotations

import base64
import json
import logging
import sys
import urllib.parse
import urllib.request
from dataclasses import dataclass, field

log = logging.getLogger(__name__)
HTTP_TIMEOUT = 10


@dataclass
class Alert:
    title: str
    message: str
    urgent: bool = True
    tags: list[str] = field(default_factory=list)
    # Optional link to open when the notification is tapped, e.g. an
    # "sms:" URL that opens a text to the station with the message filled in.
    click_url: str | None = None


def _post(url: str, data: bytes, headers: dict[str, str]) -> None:
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
        resp.read()


def _post_form(url: str, form: dict[str, str], headers: dict[str, str] | None = None) -> None:
    body = urllib.parse.urlencode(form).encode()
    _post(url, body, {"Content-Type": "application/x-www-form-urlencoded", **(headers or {})})


def _post_json(url: str, payload: dict) -> None:
    _post(url, json.dumps(payload).encode(), {"Content-Type": "application/json"})


class ConsoleNotifier:
    name = "console"

    def __init__(self, bell: bool = True):
        self.bell = bell

    def send(self, alert: Alert) -> None:
        bell = "\a" if self.bell and alert.urgent else ""
        bar = "=" * 70
        print(f"{bell}\n{bar}\n{alert.title}\n{alert.message}\n{bar}\n", flush=True, file=sys.stdout)


class NtfyNotifier:
    """Push to a phone through https://ntfy.sh (free, no account needed).

    Install the ntfy app, subscribe to your topic, and you're done. Treat
    the topic name like a password: anyone who knows it can read it.
    """

    name = "ntfy"

    def __init__(self, topic: str, server: str = "https://ntfy.sh", token: str = "", priority: int = 5):
        if not topic:
            raise ValueError("ntfy.topic is required")
        self.url = f"{server.rstrip('/')}/{urllib.parse.quote(topic)}"
        self.token = token
        self.priority = priority

    def send(self, alert: Alert) -> None:
        # Headers have to be latin-1, so non-ASCII text goes in the body only.
        headers = {
            "Title": alert.title.encode("ascii", "ignore").decode().strip() or "Radio monitor",
            "Priority": str(self.priority if alert.urgent else 3),
            "Content-Type": "text/plain; charset=utf-8",
        }
        if alert.tags:
            headers["Tags"] = ",".join(alert.tags)
        if alert.click_url:
            headers["Click"] = alert.click_url
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        _post(self.url, alert.message.encode("utf-8"), headers)


class PushoverNotifier:
    name = "pushover"

    def __init__(self, user_key: str, app_token: str, priority: int = 1, sound: str = "siren"):
        if not user_key or not app_token:
            raise ValueError("pushover.user_key and pushover.app_token are required")
        self.user_key, self.app_token = user_key, app_token
        self.priority, self.sound = priority, sound

    def send(self, alert: Alert) -> None:
        form = {
            "token": self.app_token,
            "user": self.user_key,
            "title": alert.title,
            "message": alert.message,
            "priority": str(self.priority if alert.urgent else 0),
        }
        if alert.urgent and self.sound:
            form["sound"] = self.sound
        if self.priority == 2 and alert.urgent:
            # Emergency priority repeats until acknowledged.
            form.update(retry="30", expire="300")
        if alert.click_url:
            form.update(url=alert.click_url, url_title="Text the station")
        _post_form("https://api.pushover.net/1/messages.json", form)


class TelegramNotifier:
    name = "telegram"

    def __init__(self, bot_token: str, chat_id: str):
        if not bot_token or not chat_id:
            raise ValueError("telegram.bot_token and telegram.chat_id are required")
        self.url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        self.chat_id = str(chat_id)

    def send(self, alert: Alert) -> None:
        _post_json(self.url, {
            "chat_id": self.chat_id,
            "text": f"{alert.title}\n{alert.message}",
            "disable_notification": not alert.urgent,
        })


class TwilioSmsNotifier:
    name = "twilio"

    def __init__(self, account_sid: str, auth_token: str, from_number: str, to_numbers: list[str]):
        if not (account_sid and auth_token and from_number and to_numbers):
            raise ValueError("twilio needs account_sid, auth_token, from_number and to_numbers")
        self.url = f"https://api.twilio.com/2010-04-01/Accounts/{account_sid}/Messages.json"
        creds = base64.b64encode(f"{account_sid}:{auth_token}".encode()).decode()
        self.auth = {"Authorization": f"Basic {creds}"}
        self.from_number = from_number
        self.to_numbers = list(to_numbers)

    def send(self, alert: Alert) -> None:
        for to in self.to_numbers:
            _post_form(self.url, {
                "From": self.from_number,
                "To": to,
                "Body": f"{alert.title}\n{alert.message}",
            }, self.auth)


class WebhookNotifier:
    """POST JSON to a Discord or Slack incoming webhook, or any URL.

    Sends ``content`` (Discord) and ``text`` (Slack). Each service ignores
    the field it doesn't use.
    """

    name = "webhook"

    def __init__(self, url: str):
        if not url:
            raise ValueError("webhook.url is required")
        self.url = url

    def send(self, alert: Alert) -> None:
        text = f"**{alert.title}**\n{alert.message}"
        _post_json(self.url, {"content": text, "text": text})


_BUILDERS = {
    "console": lambda c: ConsoleNotifier(bell=c.get("bell", True)),
    "ntfy": lambda c: NtfyNotifier(
        topic=c.get("topic", ""), server=c.get("server", "https://ntfy.sh"),
        token=c.get("token", ""), priority=int(c.get("priority", 5)),
    ),
    "pushover": lambda c: PushoverNotifier(
        user_key=c.get("user_key", ""), app_token=c.get("app_token", ""),
        priority=int(c.get("priority", 1)), sound=c.get("sound", "siren"),
    ),
    "telegram": lambda c: TelegramNotifier(bot_token=c.get("bot_token", ""), chat_id=c.get("chat_id", "")),
    "twilio": lambda c: TwilioSmsNotifier(
        account_sid=c.get("account_sid", ""), auth_token=c.get("auth_token", ""),
        from_number=c.get("from_number", ""), to_numbers=c.get("to_numbers", []),
    ),
    "webhook": lambda c: WebhookNotifier(url=c.get("url", "")),
}


def build_notifiers(config: dict) -> list:
    """Create every notifier marked ``enabled = true`` in the [notify.*] tables."""
    notifiers = []
    for name, section in config.items():
        if not isinstance(section, dict) or not section.get("enabled", False):
            continue
        builder = _BUILDERS.get(name)
        if builder is None:
            raise ValueError(f"unknown notifier [notify.{name}]; choose from {', '.join(_BUILDERS)}")
        notifiers.append(builder(section))
    return notifiers
