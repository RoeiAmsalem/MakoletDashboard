import os
import requests
from datetime import datetime
import pytz

ISRAEL_TZ = pytz.timezone("Asia/Jerusalem")


def _is_send_window() -> bool:
    """Return True if current Israel time is between 08:00-22:00."""
    now = datetime.now(ISRAEL_TZ)
    return 8 <= now.hour < 22


def format_agent_alert(agent_name: str, error: str) -> str:
    """Format a standard alert message for a failed agent run."""
    now = datetime.now(ISRAEL_TZ)
    return (
        f"\U0001f6a8 מכולת אינשטיין - התראה\n"
        f"סוכן: {agent_name}\n"
        f"שגיאה: {error}\n"
        f"תאריך: {now.strftime('%d/%m/%Y')}\n"
        f"שעה: {now.strftime('%H:%M')}"
    )


def send_alert(message: str) -> bool:
    """Send a Telegram message. Only sends during 08:00-22:00 Israel time."""
    if not _is_send_window():
        return False

    token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "")
    if not token or not chat_id:
        print("Telegram credentials not set")
        return False

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {"chat_id": chat_id, "text": message}
    try:
        response = requests.post(url, json=payload, timeout=10)
        return response.status_code == 200
    except Exception as e:
        print(f"Telegram error: {e}")
        return False
