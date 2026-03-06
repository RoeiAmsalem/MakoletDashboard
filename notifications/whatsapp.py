"""
WhatsApp notifications via CallMeBot for MakoletDashboard.

Usage:
    from notifications.whatsapp import send_alert
    send_alert(message)

Only sends between 08:00–22:00 Israel time (Asia/Jerusalem).
Outside those hours the call is silently skipped.
Credentials are read from .env:
    WHATSAPP_PHONE   — international format, e.g. 972501234567
    WHATSAPP_API_KEY — provided by CallMeBot
"""

import logging
import os
from datetime import datetime
from urllib.parse import quote
from zoneinfo import ZoneInfo

import requests
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

_CALLMEBOT_URL = "https://api.callmebot.com/whatsapp.php"
_ISRAEL_TZ = ZoneInfo("Asia/Jerusalem")
_SEND_HOUR_START = 8   # 08:00
_SEND_HOUR_END   = 22  # up to but not including 22:00


def _is_send_window() -> bool:
    """Return True if current Israel time is within 08:00–22:00."""
    now = datetime.now(_ISRAEL_TZ)
    return _SEND_HOUR_START <= now.hour < _SEND_HOUR_END


def send_alert(message: str) -> None:
    """
    Send a WhatsApp alert via CallMeBot.

    Silent no-op if outside 08:00–22:00 Israel time or if credentials
    are not configured. Logs success / failure to console.

    Args:
        message: Plain-text message to send. Will be URL-encoded.
    """
    phone   = os.getenv("WHATSAPP_PHONE", "").strip()
    api_key = os.getenv("WHATSAPP_API_KEY", "").strip()

    if not phone or not api_key:
        logger.warning("[whatsapp] WHATSAPP_PHONE or WHATSAPP_API_KEY not set — skipping alert")
        return

    if not _is_send_window():
        now_str = datetime.now(_ISRAEL_TZ).strftime("%H:%M")
        logger.info("[whatsapp] Outside send window (%s Israel time) — alert suppressed", now_str)
        return

    url = (
        f"{_CALLMEBOT_URL}"
        f"?phone={quote(phone)}"
        f"&text={quote(message)}"
        f"&apikey={quote(api_key)}"
    )

    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        logger.info("[whatsapp] Alert sent successfully (status %d)", response.status_code)
    except requests.RequestException as exc:
        logger.error("[whatsapp] Failed to send alert: %s", exc)


def format_agent_alert(agent_name: str, error: str) -> str:
    """
    Build the standard Hebrew alert message for an agent failure.

    Args:
        agent_name: Name of the failing agent (e.g. "bilboy").
        error:      Error description string.

    Returns:
        Formatted multi-line Hebrew message string.
    """
    now_str = datetime.now(_ISRAEL_TZ).strftime("%d/%m/%Y %H:%M")
    return (
        f"\U0001f6a8 מכולת אינשטיין\n"
        f"סוכן: {agent_name}\n"
        f"שגיאה: {error}\n"
        f"תאריך: {now_str}"
    )
