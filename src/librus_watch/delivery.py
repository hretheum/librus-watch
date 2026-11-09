"""Kanały wysyłki: Apprise (sekret `APPRISE_CONFIG`) i bot Telegrama (`TELEGRAM_BOT_TOKEN`)."""

from __future__ import annotations

import logging
import sys
from datetime import date
from typing import Any

from librus_watch import whatsapp
from librus_watch.alerts import Alert
from librus_watch.config import Config
from librus_watch.notify import AppriseSender, Sender, send
from librus_watch.secret_env import get_secret
from librus_watch.telegram import chat_ids, http_call, send_alert

log = logging.getLogger(__name__)


def channels(cfg: Config) -> tuple[Sender | None, Any]:
    """Kanały: Apprise (gdy jest `APPRISE_CONFIG`) i bot Telegrama (gdy jest token)."""
    sender: Sender | None = None
    raw = get_secret("APPRISE_CONFIG", required=False)
    if raw is not None:
        sender = AppriseSender(raw.get_secret_value())
    token = get_secret("TELEGRAM_BOT_TOKEN", required=False)
    bot = (http_call(token.get_secret_value()), chat_ids()) if token is not None else None
    return sender, bot


def deliver(alert: Alert, cfg: Config, today: date) -> bool:
    """Wysyła powiadomienie wszystkimi kanałami; `True`, gdy któryś zadziałał."""
    sender, bot = channels(cfg)
    ok = False
    if sender is not None:
        ok = send(alert, cfg, sender, today).sent > 0
    if bot is not None:
        call, chats = bot
        try:
            ok = send_alert(alert, cfg, call, chats, today) > 0 or ok
        except Exception as e:
            log.warning("Telegram nieudany (%s)", type(e).__name__)
    wa = whatsapp.graph_call(cfg)
    if wa is not None:
        try:
            ok = whatsapp.send_alert(alert, cfg, wa, whatsapp.recipients(), today) > 0 or ok
        except Exception as e:
            log.warning("WhatsApp nieudany (%s)", type(e).__name__)
    if sender is None and bot is None and wa is None:
        print("brak kanałów: ustaw APPRISE_CONFIG albo TELEGRAM_BOT_TOKEN", file=sys.stderr)
    return ok


def deliver_text(cfg: Config, text: str) -> bool:
    """Wysyła zwykły komunikat (np. raport o awarii) wszystkimi kanałami."""
    sender, bot = channels(cfg)
    ok = False
    if sender is not None:
        tags = sorted({t for r in cfg.alerts.routes for t in r.tags})
        try:
            ok = sender.notify(text, "librus-watch", tags)
        except Exception as e:
            log.warning("raport: Apprise nieudany (%s)", type(e).__name__)
    if bot is not None:
        call, chats = bot
        for chat in chats:
            try:
                call("sendMessage", {"chat_id": chat, "text": text})
                ok = True
            except Exception as e:
                log.warning("raport: Telegram nieudany (%s)", type(e).__name__)
    wa = whatsapp.graph_call(cfg)
    if wa is not None:
        try:
            ok = whatsapp.send_info(text, cfg, wa, whatsapp.recipients()) > 0 or ok
        except Exception as e:
            log.warning("raport: WhatsApp nieudany (%s)", type(e).__name__)
    return ok
