"""Notifiers: how an attention notice reaches the person. One small interface, several transports.

    class Notifier: def send(self, title: str, text: str, alert: dict) -> str   # returns what happened, for the log

  log      prints one line to stderr (the default; what a dry run always uses)
  stub     keeps the notices in memory (tests)
  webhook  POSTs JSON {title, text, rule, key, at, repeat} to a URL: a phone push service, a chat webhook, a relay.
           The URL is best kept out of the file with `url_env`. Only https, or http to the loopback address, is used.
  desktop  a desktop notification on the machine the supervisor runs on: a toast on Windows (PowerShell), notify-send
           on Linux, osascript on macOS. The platform is chosen when a notice is sent; nothing is imported for it.

A notifier never raises into the loop: a failed send is returned as text and logged.
"""

from __future__ import annotations

import ipaddress
import json
import os
import shutil
import sys
import urllib.parse
import urllib.request
from typing import Any, Protocol

from .. import procs
from .config import NotifierSpec

TIMEOUT = 10


class Notifier(Protocol):
    def send(self, title: str, text: str, alert: dict[str, Any]) -> str: ...


class LogNotifier:
    def __init__(self, stream: Any = None):
        self.stream = stream

    def send(self, title: str, text: str, alert: dict[str, Any]) -> str:
        print(f"[attention] {title}: {text}", file=self.stream or sys.stderr)
        return "logged"


class StubNotifier:
    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []

    def send(self, title: str, text: str, alert: dict[str, Any]) -> str:
        self.sent.append({"title": title, "text": text, **alert})
        return "kept"


def _loopback(host: str | None) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host or "").is_loopback
    except ValueError:
        return False


def _allowed_url(url: str) -> bool:
    u = urllib.parse.urlsplit(url)
    return u.scheme == "https" or (u.scheme == "http" and _loopback(u.hostname))


class WebhookNotifier:
    def __init__(self, url: str, opener: Any = None):
        if not _allowed_url(url):
            raise ValueError("a webhook URL must be https, or http to the loopback address")
        self.url, self._open = url, opener or urllib.request.urlopen

    def send(self, title: str, text: str, alert: dict[str, Any]) -> str:
        body = json.dumps(
            {"title": title, "text": text, **{k: alert.get(k) for k in ("rule", "key", "at", "repeat")}}
        )
        req = urllib.request.Request(
            self.url,
            data=body.encode("utf-8"),
            method="POST",
            headers={"Content-Type": "application/json", "User-Agent": "onloop-supervisor"},
        )
        try:
            with self._open(req, timeout=TIMEOUT) as resp:
                return f"webhook {getattr(resp, 'status', '?')}"
        except Exception as e:  # noqa: BLE001 - a notice never stops the loop
            return f"webhook failed: {type(e).__name__}"


# The toast script takes its text from environment variables, so nothing the notice says is parsed as PowerShell.
_TOAST = r"""
$ErrorActionPreference = 'Stop'
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] > $null
[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] > $null
$t = [Security.SecurityElement]::Escape($env:ONLOOP_NOTICE_TITLE)
$b = [Security.SecurityElement]::Escape($env:ONLOOP_NOTICE_TEXT)
$x = New-Object Windows.Data.Xml.Dom.XmlDocument
$x.LoadXml("<toast><visual><binding template='ToastGeneric'><text>$t</text><text>$b</text></binding></visual></toast>")
$app = '{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe'
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($app).Show([Windows.UI.Notifications.ToastNotification]::new($x))
"""
_OSA = "on run argv\ndisplay notification (item 2 of argv) with title (item 1 of argv)\nend run"


class DesktopNotifier:
    def __init__(self, run: Any = None, platform: str | None = None):
        self._run = run or (lambda argv, env=None: procs.run_text(argv, timeout=TIMEOUT, env=env))
        self.platform = platform or sys.platform

    def command(self, title: str, text: str) -> tuple[list[str], dict[str, str] | None]:
        if self.platform == "win32":
            env = {**os.environ, "ONLOOP_NOTICE_TITLE": title, "ONLOOP_NOTICE_TEXT": text}
            return ["powershell", "-NoProfile", "-NonInteractive", "-Command", _TOAST], env
        if self.platform == "darwin":
            return ["osascript", "-e", _OSA, title, text], None
        return ["notify-send", "--app-name=onloop", title, text], None

    def send(self, title: str, text: str, alert: dict[str, Any]) -> str:
        argv, env = self.command(title, text[:400])
        if shutil.which(argv[0]) is None:
            return f"desktop: {argv[0]} not found"
        try:
            p = self._run(argv, env=env)
        except Exception as e:  # noqa: BLE001
            return f"desktop failed: {type(e).__name__}"
        return f"desktop exit {getattr(p, 'returncode', '?')}"


def make(spec: NotifierSpec, environ: Any = None) -> Notifier:
    env = os.environ if environ is None else environ
    if spec.type == "stub":
        return StubNotifier()
    if spec.type == "webhook":
        url = spec.url or env.get(spec.url_env or "", "")
        if not url:
            raise ValueError(f"the webhook URL variable {spec.url_env} is not set")
        return WebhookNotifier(url)
    if spec.type == "desktop":
        return DesktopNotifier()
    return LogNotifier()
