"""
WhatsApp notifications via Green API for MakoletDashboard.

Usage:
    from notifications.whatsapp import send_alert
    send_alert(message)

Only sends between 08:00–22:00 Israel time (Asia/Jerusalem).
Outside those hours the call is silently skipped.
Credentials are read from .env:
    WHATSAPP_PHONE        — international format, e.g. 972501234567
    GREENAPI_INSTANCE_ID  — Green API instance ID
    GREENAPI_API_URL      — Green API base URL
    GREENAPI_TOKEN        — Green API token
"""

import logging
import os
from datetime import datetime
from zoneinfo import ZoneInfo

import requests
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

_ISRAEL_TZ = ZoneInfo("Asia/Jerusalem")
_SEND_HOUR_START = 8   # 08:00
_SEND_HOUR_END   = 22  # up to but not including 22:00


def _is_send_window() -> bool:
    """Return True if current Israel time is within 08:00–22:00."""
    now = datetime.now(_ISRAEL_TZ)
    return _SEND_HOUR_START <= now.hour < _SEND_HOUR_END


def send_alert(message: str) -> None:
    """
    Send a WhatsApp alert via Green API.

    Silent no-op if outside 08:00–22:00 Israel time or if credentials
    are not configured. Logs success / failure to console.

    Args:
        message: Plain-text message to send.
    """
    phone       = os.getenv("WHATSAPP_PHONE", "").strip()
    instance_id = os.getenv("GREENAPI_INSTANCE_ID", "").strip()
    api_url     = os.getenv("GREENAPI_API_URL", "").strip()
    token       = os.getenv("GREENAPI_TOKEN", "").strip()

    if not phone or not instance_id or not api_url or not token:
        logger.warning("[whatsapp] Green API credentials not fully set — skipping alert")
        return

    if not _is_send_window():
        now_str = datetime.now(_ISRAEL_TZ).strftime("%H:%M")
        logger.info("[whatsapp] Outside send window (%s Israel time) — alert suppressed", now_str)
        return

    url = f"{api_url}/waInstance{instance_id}/sendMessage/{token}"

    try:
        response = requests.post(url, json={
            "chatId": f"{phone}@c.us",
            "message": message,
        }, timeout=10)
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
