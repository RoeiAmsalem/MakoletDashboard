"""
Aviv Alerts agent - reads daily Z-report email from Aviv POS and saves
the total daily income to the daily_sales table.

Flow:
    Connect to Gmail via IMAP (imap.gmail.com:993 SSL)
    Search for today's email from AVIV_SENDER_EMAIL with subject "דוח סוף יום"
    Download the PDF attachment (filename starts with "z_")
    Extract text with pdfplumber
    Parse: סה"כ: ₪ <amount>
    Save to daily_sales

Credentials from .env:
    GMAIL_ADDRESS        - Gmail account address
    GMAIL_APP_PASSWORD   - Google App Password (not the regular password)
    AVIV_SENDER_EMAIL    - Sender address of the Aviv daily report
"""

import email
import imaplib
import io
import os
import re
from datetime import date

import pdfplumber
from dotenv import load_dotenv

from agents.base_agent import BaseAgent
from database.db import insert_daily_sale

load_dotenv()

IMAP_HOST = "imap.gmail.com"
IMAP_PORT = 993

# Regex to extract the total from: סה"כ: ₪ 12377.92
TOTAL_PATTERN = re.compile(r'סה"כ[:\s]+₪?\s*([\d,]+\.?\d*)')


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

    def _search_today_email(self, mail: imaplib.IMAP4_SSL) -> list[bytes]:
        """
        Search for emails sent today from the Aviv sender.
        Returns a list of message-id byte strings.
        """
        today_str = date.today().strftime("%d-%b-%Y")  # e.g. "06-Mar-2026"
        search_criteria = (
            f'(FROM "{self._sender_email}" SINCE "{today_str}" SUBJECT "דוח סוף יום")'
        )
        status, data = mail.search(None, search_criteria)
        if status != "OK" or not data or not data[0]:
            return []
        return data[0].split()

    def _fetch_pdf_attachment(self, mail: imaplib.IMAP4_SSL, msg_id: bytes) -> bytes | None:
        """
        Fetch the first PDF attachment whose filename starts with 'z_'.
        Returns raw PDF bytes or None if not found.
        """
        status, msg_data = mail.fetch(msg_id, "(RFC822)")
        if status != "OK":
            return None

        raw_email = msg_data[0][1]
        msg = email.message_from_bytes(raw_email)

        for part in msg.walk():
            if part.get_content_type() != "application/pdf":
                continue
            filename = part.get_filename() or ""
            if filename.lower().startswith("z_"):
                return part.get_payload(decode=True)

        return None

    def _extract_total_from_pdf(self, pdf_bytes: bytes) -> float | None:
        """Parse PDF bytes and extract the סה"כ total."""
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            for page in pdf.pages:
                text = page.extract_text() or ""
                match = TOTAL_PATTERN.search(text)
                if match:
                    amount_str = match.group(1).replace(",", "")
                    return float(amount_str)
        return None

    # ------------------------------------------------------------------
    # BaseAgent interface
    # ------------------------------------------------------------------

    def fetch_data(self) -> list[dict]:
        """
        Connect to Gmail, find today's Z-report, parse the total.

        Returns:
            [{"date": "YYYY-MM-DD", "total_income": float, "source": "aviv"}]
            or [] if no email found today.
        """
        mail = self._connect()
        try:
            msg_ids = self._search_today_email(mail)
            if not msg_ids:
                self.logger.info("[aviv_alerts] No Z-report email found for today.")
                return []

            # Use the latest matching email (last in list)
            msg_id = msg_ids[-1]
            pdf_bytes = self._fetch_pdf_attachment(mail, msg_id)
            if pdf_bytes is None:
                raise ValueError("Email found but no z_*.pdf attachment detected.")

            total = self._extract_total_from_pdf(pdf_bytes)
            if total is None:
                raise ValueError("PDF attachment found but could not parse סה\"כ total.")

            today = date.today().isoformat()
            self.logger.info("[aviv_alerts] Parsed total_income=%.2f for %s", total, today)
            return [{"date": today, "total_income": total, "source": "aviv"}]
        finally:
            mail.logout()

    def save_to_db(self, data: list[dict]) -> None:
        """Insert each record into daily_sales."""
        for record in data:
            insert_daily_sale(
                date=record["date"],
                total_income=record["total_income"],
                source=record["source"],
            )
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
