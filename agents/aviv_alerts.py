"""
Aviv Alerts agent - reads daily Z-report email from Aviv POS and saves
the total daily income to the daily_sales table.

Flow:
    Connect to Gmail via IMAP (imap.gmail.com:993 SSL)
    Search for today's email from AVIV_SENDER_EMAIL with subject "דוח סוף יום"
    Download the PDF attachment (filename starts with "z_")
    Extract amount from PDF with pdfplumber (NEVER trust email subject amounts)
    Save to daily_sales

Credentials from .env:
    GMAIL_ADDRESS        - Gmail account address
    GMAIL_APP_PASSWORD   - Google App Password (not the regular password)
    AVIV_SENDER_EMAIL    - Sender address of the Aviv daily report
"""

import calendar
import email
import email.header
import email.utils
import imaplib
import io
import os
import re
from datetime import date, timedelta
from zoneinfo import ZoneInfo

import pdfplumber
from dotenv import load_dotenv

from agents.base_agent import BaseAgent
from database.db import get_connection, insert_daily_sale, add_pending_fetch, resolve_pending_fetch

load_dotenv()

IMAP_HOST = "imap.gmail.com"
IMAP_PORT = 993

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
Z_PDFS_DIR = os.path.join(_PROJECT_ROOT, "data", "z_pdfs")

# RTL PDF text renders as: "20295.85 ₪ :כ"הס"
# This regex matches the main total line (with colon before כ"הס)
TOTAL_PATTERN_RTL = re.compile(r'([\d,]+\.?\d*)\s*₪\s*:כ"הס')
# Fallback for non-RTL PDFs: סה"כ: ₪ 12377.92
TOTAL_PATTERN_LTR = re.compile(r'סה["\u05f4]כ[:\s]+₪?\s*([\d,]+\.?\d*)')


def _decode_filename(raw: str) -> str:
    """Decode a possibly MIME-encoded filename."""
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


def is_z_expected(d: date) -> bool:
    """
    Return True if a Z-report is expected for the given date.

    Schedule:
    - Sunday–Friday (weekday 6, 0–4): always expected
    - Saturday (weekday 5): only if it's the last day of the month
    """
    if d.weekday() != 5:  # Not Saturday
        return True
    # Saturday: only expected on last day of month
    last_day = calendar.monthrange(d.year, d.month)[1]
    return d.day == last_day


def check_missing_z_reports() -> list[str]:
    """
    Check the past 7 days for missing Z-reports.
    Returns a list of date strings (YYYY-MM-DD) where a report was expected
    but no daily_sales record exists.
    Also registers missing dates as pending fetches.
    """
    today = date.today()
    missing = []
    with get_connection() as conn:
        for i in range(1, 8):  # yesterday through 7 days ago
            d = today - timedelta(days=i)
            if not is_z_expected(d):
                continue
            count = conn.execute(
                "SELECT COUNT(*) FROM daily_sales WHERE date = ?",
                (d.isoformat(),),
            ).fetchone()[0]
            if count == 0:
                missing.append(d.isoformat())
                add_pending_fetch("aviv_alerts", d.isoformat(), "Z report missing")
    return missing


class AvivAlertsAgent(BaseAgent):
    name = "aviv_alerts"

    def __init__(self):
        super().__init__()
        self._gmail_address = os.getenv("GMAIL_ADDRESS", "")
        self._gmail_password = os.getenv("GMAIL_APP_PASSWORD", "")
        self._sender_email = os.getenv("AVIV_SENDER_EMAIL", "")

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _connect(self) -> imaplib.IMAP4_SSL:
        """Open an authenticated IMAP connection."""
        mail = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT)
        mail.login(self._gmail_address, self._gmail_password)
        mail.select("inbox")
        return mail

    def _search_recent_emails(self, mail: imaplib.IMAP4_SSL,
                              since_days: int = 7) -> list[bytes]:
        """
        Search for emails from the Aviv sender in the last `since_days` days.
        Returns a list of message-id byte strings.
        """
        since_str = (date.today() - timedelta(days=since_days)).strftime("%d-%b-%Y")
        if self._sender_email:
            criteria = f'(FROM "{self._sender_email}" SINCE "{since_str}")'
        else:
            criteria = f'(SINCE "{since_str}")'
        status, data = mail.search(None, criteria)
        if status != "OK" or not data or not data[0]:
            return []
        return data[0].split()

    @staticmethod
    def _parse_email_date(msg: email.message.Message) -> date | None:
        """Extract the business date from the email's Date header (Israel time)."""
        date_str = msg.get("Date")
        if not date_str:
            return None
        try:
            dt = email.utils.parsedate_to_datetime(date_str)
            return dt.astimezone(ZoneInfo("Asia/Jerusalem")).date()
        except Exception:
            return None

    @staticmethod
    def _extract_z_pdf(msg: email.message.Message) -> bytes | None:
        """
        Extract the first PDF attachment whose filename starts with 'z_'.
        Handles both application/pdf and application/octet-stream content types.
        Returns raw PDF bytes or None if not found.
        """
        for part in msg.walk():
            ct = part.get_content_type()
            if ct not in ("application/pdf", "application/octet-stream"):
                continue
            raw_fn = part.get_filename() or ""
            filename = _decode_filename(raw_fn)
            if filename.lower().startswith("z_") and filename.lower().endswith(".pdf"):
                return part.get_payload(decode=True)
        return None

    def _extract_total_from_pdf(self, pdf_bytes: bytes) -> float | None:
        """
        Parse PDF bytes and extract the סה"כ total.
        Tries RTL pattern first (pdfplumber visual order), then LTR fallback.
        """
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            for page in pdf.pages:
                text = page.extract_text() or ""
                # RTL pattern: "20295.85 ₪ :כ"הס"
                match = TOTAL_PATTERN_RTL.search(text)
                if match:
                    return float(match.group(1).replace(",", ""))
                # LTR fallback: 'סה"כ: ₪ 12377.92'
                match = TOTAL_PATTERN_LTR.search(text)
                if match:
                    return float(match.group(1).replace(",", ""))
        return None

    # ------------------------------------------------------------------
    # BaseAgent interface
    # ------------------------------------------------------------------

    def fetch_data(self) -> list[dict]:
        """
        Connect to Gmail, find Z-report emails from the last 7 days,
        and parse any that are missing from daily_sales.

        Returns a list of {"date", "total_income", "source"} dicts.
        """
        os.makedirs(Z_PDFS_DIR, exist_ok=True)
        mail = self._connect()
        try:
            msg_ids = self._search_recent_emails(mail)
            if not msg_ids:
                self.logger.info("[aviv_alerts] No Z-report email found in last 7 days.")
                return []

            self.logger.info("[aviv_alerts] Found %d emails in last 7 days", len(msg_ids))

            # Dates we already have in DB
            cutoff = (date.today() - timedelta(days=7)).isoformat()
            with get_connection() as conn:
                existing = set(
                    r[0] for r in conn.execute(
                        "SELECT date FROM daily_sales WHERE date >= ?", (cutoff,)
                    ).fetchall()
                )

            records = []
            for msg_id in msg_ids:
                status, msg_data = mail.fetch(msg_id, "(RFC822)")
                if status != "OK":
                    continue

                msg = email.message_from_bytes(msg_data[0][1])
                email_date = self._parse_email_date(msg)
                if email_date is None:
                    continue

                date_str = email_date.isoformat()
                if date_str in existing:
                    self.logger.debug("[aviv_alerts] Skipping %s — already in DB", date_str)
                    continue

                pdf_bytes = self._extract_z_pdf(msg)
                if pdf_bytes is None:
                    continue

                total = self._extract_total_from_pdf(pdf_bytes)
                if total is None:
                    self.logger.warning("[aviv_alerts] Could not parse total from PDF for %s", date_str)
                    continue

                # Save PDF to disk
                pdf_filename = f"z_{date_str}.pdf"
                pdf_path = os.path.join(Z_PDFS_DIR, pdf_filename)
                with open(pdf_path, "wb") as f:
                    f.write(pdf_bytes)

                self.logger.info("[aviv_alerts] Parsed total_income=%.2f for %s", total, date_str)
                records.append({"date": date_str, "total_income": total, "source": "aviv", "pdf_path": pdf_filename})
                existing.add(date_str)  # prevent duplicates within same run

            return records
        finally:
            mail.logout()

    def fetch_data_for_date(self, target_date: str) -> list[dict]:
        """Search for a Z-report for a specific date."""
        os.makedirs(Z_PDFS_DIR, exist_ok=True)
        target = date.fromisoformat(target_date)
        mail = self._connect()
        try:
            # Search from the day before (email might arrive late)
            since_str = (target - timedelta(days=1)).strftime("%d-%b-%Y")
            if self._sender_email:
                criteria = f'(FROM "{self._sender_email}" SINCE "{since_str}")'
            else:
                criteria = f'(SINCE "{since_str}")'
            status, data = mail.search(None, criteria)
            if status != "OK" or not data or not data[0]:
                return []

            for msg_id in data[0].split():
                status, msg_data = mail.fetch(msg_id, "(RFC822)")
                if status != "OK":
                    continue
                msg = email.message_from_bytes(msg_data[0][1])
                email_date = self._parse_email_date(msg)
                if email_date != target:
                    continue
                pdf_bytes = self._extract_z_pdf(msg)
                if pdf_bytes is None:
                    continue
                total = self._extract_total_from_pdf(pdf_bytes)
                if total is None:
                    continue

                # Save PDF to disk
                pdf_filename = f"z_{target_date}.pdf"
                pdf_path = os.path.join(Z_PDFS_DIR, pdf_filename)
                with open(pdf_path, "wb") as f:
                    f.write(pdf_bytes)

                self.logger.info("[aviv_alerts] Parsed total_income=%.2f for %s", total, target_date)
                return [{"date": target_date, "total_income": total, "source": "aviv", "pdf_path": pdf_filename}]
            return []
        finally:
            mail.logout()

    def save_to_db(self, data: list[dict]) -> None:
        """Insert each record into daily_sales and resolve pending fetches."""
        for record in data:
            insert_daily_sale(
                date=record["date"],
                total_income=record["total_income"],
                source=record["source"],
                pdf_path=record.get("pdf_path"),
            )
            resolve_pending_fetch("aviv_alerts", record["date"])
        self.logger.info("[aviv_alerts] Saved %d daily sale record(s) to DB.", len(data))


# ---------------------------------------------------------------------------
# Manual run entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import logging

    logging.basicConfig(level=logging.INFO)
    result = AvivAlertsAgent().run()
    if result["success"]:
        data = result["data"]
        if data:
            print(f"Success: total_income={data[0]['total_income']} saved for {data[0]['date']}")
        else:
            print("Success: no email found for today.")
    else:
        print(f"Failed: {result['error']}")
