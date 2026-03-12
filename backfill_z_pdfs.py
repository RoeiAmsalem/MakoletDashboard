"""
One-time backfill script: re-download Z-report PDFs from Gmail
for existing daily_sales records that have no pdf_path.

Usage:
    python backfill_z_pdfs.py
"""

import email
import email.header
import email.utils
import imaplib
import logging
import os
from datetime import date, timedelta
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

load_dotenv()

# Ensure project root on path
import sys
_PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from database.db import get_connection, init_db

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

IMAP_HOST = "imap.gmail.com"
IMAP_PORT = 993
Z_PDFS_DIR = os.path.join(_PROJECT_ROOT, "data", "z_pdfs")


def _decode_filename(raw: str) -> str:
    if not raw:
        return ""
    parts = email.header.decode_header(raw)
    result = ""
    for part, enc in parts:
        if isinstance(part, bytes):
            result += part.decode(enc or "utf-8", errors="replace")
        else:
            result += part
    return result.strip()


def backfill():
    init_db()
    os.makedirs(Z_PDFS_DIR, exist_ok=True)

    # Find dates that need backfill
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT id, date FROM daily_sales WHERE pdf_path IS NULL ORDER BY date"
        ).fetchall()

    if not rows:
        logger.info("No records need backfill.")
        return

    dates_to_fill = {r["date"]: r["id"] for r in rows}
    logger.info("Need to backfill PDFs for %d dates: %s",
                len(dates_to_fill), list(dates_to_fill.keys()))

    # Connect to Gmail
    gmail_address = os.getenv("GMAIL_ADDRESS", "")
    gmail_password = os.getenv("GMAIL_APP_PASSWORD", "")
    sender_email = os.getenv("AVIV_SENDER_EMAIL", "")

    mail = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT)
    mail.login(gmail_address, gmail_password)
    mail.select("inbox")

    # Search for emails covering the date range
    earliest = min(dates_to_fill.keys())
    since_str = (date.fromisoformat(earliest) - timedelta(days=2)).strftime("%d-%b-%Y")
    if sender_email:
        criteria = f'(FROM "{sender_email}" SINCE "{since_str}")'
    else:
        criteria = f'(SINCE "{since_str}")'

    status, data = mail.search(None, criteria)
    if status != "OK" or not data or not data[0]:
        logger.info("No emails found.")
        mail.logout()
        return

    msg_ids = data[0].split()
    logger.info("Found %d emails to scan.", len(msg_ids))

    saved = 0
    for msg_id in msg_ids:
        status, msg_data = mail.fetch(msg_id, "(RFC822)")
        if status != "OK":
            continue

        msg = email.message_from_bytes(msg_data[0][1])

        # Parse email date
        date_str_raw = msg.get("Date")
        if not date_str_raw:
            continue
        try:
            dt = email.utils.parsedate_to_datetime(date_str_raw)
            email_date = dt.astimezone(ZoneInfo("Asia/Jerusalem")).date()
        except Exception:
            continue

        date_str = email_date.isoformat()
        if date_str not in dates_to_fill:
            continue

        # Extract Z PDF
        pdf_bytes = None
        for part in msg.walk():
            ct = part.get_content_type()
            if ct not in ("application/pdf", "application/octet-stream"):
                continue
            raw_fn = part.get_filename() or ""
            filename = _decode_filename(raw_fn)
            if filename.lower().startswith("z_") and filename.lower().endswith(".pdf"):
                pdf_bytes = part.get_payload(decode=True)
                break

        if pdf_bytes is None:
            continue

        # Save to disk
        pdf_filename = f"z_{date_str}.pdf"
        pdf_path = os.path.join(Z_PDFS_DIR, pdf_filename)
        with open(pdf_path, "wb") as f:
            f.write(pdf_bytes)

        # Update DB
        row_id = dates_to_fill[date_str]
        with get_connection() as conn:
            conn.execute(
                "UPDATE daily_sales SET pdf_path = ? WHERE id = ?",
                (pdf_filename, row_id),
            )

        logger.info("Backfilled PDF for %s (row %d)", date_str, row_id)
        saved += 1
        del dates_to_fill[date_str]

    mail.logout()
    logger.info("Backfill complete: %d/%d PDFs saved.", saved, saved + len(dates_to_fill))
    if dates_to_fill:
        logger.warning("Still missing PDFs for: %s", list(dates_to_fill.keys()))


if __name__ == "__main__":
    backfill()
