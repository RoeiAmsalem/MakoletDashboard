"""
Electricity agent - reads IEC bill emails from Gmail and saves them
to the expenses table with category='electricity'.

Email source: noreplys@iec.co.il → forwarded to makoletdeshboard@gmail.com
Contract:     346412955 (must appear in subject; skip 347597870 and others)

Flow per email:
    1. subject must contain "346412955" and "לתקופה"
    2. subject must NOT contain any skip-pattern (receipts, warnings, etc.)
    3. parse period dates from subject (END date first, then START)
    4. detect is_correction when billing period > 90 days
    5. download PDF attachment → data/electricity_bills/
    6. extract סה"כ כולל מע"מ amount via pdfplumber
    7. skip if already in expenses table (by pdf_filename)
    8. return record dict

Credentials (.env):
    GMAIL_ADDRESS       - Gmail account
    GMAIL_APP_PASSWORD  - Google App Password
"""

import email
import imaplib
import io
import logging
import os
import re
from datetime import date, datetime

import pdfplumber
from dotenv import load_dotenv

from agents.base_agent import BaseAgent
from database.db import get_connection

load_dotenv()

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

IMAP_HOST = "imap.gmail.com"
IMAP_PORT = 993

CONTRACT_NUMBER = "346412955"

SKIP_SUBJECTS = [
    "שובר תשלום",
    "קבלה",
    "התראה בגין אי תשלום",
    "הודעה על העברת חוב",
    "אישור החלפת לקוחות",
    "אישור הצטרפות",
]

# Subject: "... לתקופה - DD/MM/YYYY - DD/MM/YYYY ..."
# Note: END date appears FIRST in the subject, then START date
DATE_PATTERN   = re.compile(r'לתקופה - (\d{2}/\d{2}/\d{4}) - (\d{2}/\d{2}/\d{4})')
AMOUNT_PATTERN = re.compile(r'([\d,]+\.?\d*)\s+ןובשח תפוקתל מ"עמ ללוכ כ"הס')
PDF_NAME_RE    = re.compile(r'^\d{4}-\d+_\d{8}_\d{6}\.pdf$', re.IGNORECASE)

_PROJECT_ROOT  = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PDF_BILLS_DIR  = os.path.join(_PROJECT_ROOT, "data", "electricity_bills")


# ---------------------------------------------------------------------------
# Module-level helpers (pure functions — easy to unit-test)
# ---------------------------------------------------------------------------

def should_process_email(subject: str) -> bool:
    """
    Return True only if the subject belongs to an actual electricity bill
    for our contract (346412955) covering a billing period.
    """
    if CONTRACT_NUMBER not in subject:
        return False
    if "לתקופה" not in subject:
        return False
    for skip in SKIP_SUBJECTS:
        if skip in subject:
            return False
    return True


def parse_dates_from_subject(subject: str) -> tuple[str, str] | None:
    """
    Extract (period_start, period_end) from subject as YYYY-MM-DD strings.

    The subject encodes END date first, then START date:
        'לתקופה - END - START'

    Returns (start, end) tuple, or None if the pattern doesn't match.
    """
    m = DATE_PATTERN.search(subject)
    if not m:
        return None

    def _to_iso(dmy: str) -> str:
        d, mo, y = dmy.split("/")
        return f"{y}-{mo}-{d}"

    end_dmy, start_dmy = m.group(1), m.group(2)   # END comes first in subject
    return _to_iso(start_dmy), _to_iso(end_dmy)


def extract_amount_from_pdf(pdf_bytes: bytes) -> float | None:
    """Extract total-including-VAT from an IEC PDF bill."""
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for page in pdf.pages:
            text = page.extract_text() or ""
            m = AMOUNT_PATTERN.search(text)
            if m:
                return float(m.group(1).replace(",", ""))
    return None


# ---------------------------------------------------------------------------
# Agent class
# ---------------------------------------------------------------------------

class ElectricityAgent(BaseAgent):
    name = "electricity"

    def __init__(self):
        super().__init__()
        self._gmail_address  = os.getenv("GMAIL_ADDRESS", "")
        self._gmail_password = os.getenv("GMAIL_APP_PASSWORD", "")

    # ------------------------------------------------------------------
    # IMAP helpers
    # ------------------------------------------------------------------

    def _connect(self) -> imaplib.IMAP4_SSL:
        mail = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT)
        mail.login(self._gmail_address, self._gmail_password)
        mail.select("inbox")
        return mail

    def _search_all_emails(self, mail: imaplib.IMAP4_SSL) -> list[bytes]:
        """Return all message IDs whose subject contains the contract number."""
        status, data = mail.search(None, f'(SUBJECT "{CONTRACT_NUMBER}")')
        if status != "OK" or not data or not data[0]:
            return []
        return data[0].split()

    def _fetch_email(self, mail: imaplib.IMAP4_SSL, msg_id: bytes) -> email.message.Message:
        status, msg_data = mail.fetch(msg_id, "(RFC822)")
        if status != "OK":
            raise IOError(f"IMAP fetch failed for msg_id={msg_id}")
        return email.message_from_bytes(msg_data[0][1])

    def _get_pdf_attachment(self, msg: email.message.Message) -> tuple[str, bytes] | None:
        """Return (filename, raw_bytes) for the first IEC-style PDF attachment, or None."""
        for part in msg.walk():
            if part.get_content_type() != "application/pdf":
                continue
            filename = part.get_filename() or ""
            if PDF_NAME_RE.match(filename):
                return filename, part.get_payload(decode=True)
        # Fallback: any PDF attachment
        for part in msg.walk():
            if part.get_content_type() == "application/pdf":
                filename = part.get_filename() or "electricity_bill.pdf"
                return filename, part.get_payload(decode=True)
        return None

    # ------------------------------------------------------------------
    # Duplicate detection
    # ------------------------------------------------------------------

    def _is_processed(self, pdf_filename: str) -> bool:
        """Return True if this PDF filename is already in the expenses table."""
        with get_connection() as conn:
            count = conn.execute(
                "SELECT COUNT(*) FROM expenses WHERE category='electricity' AND pdf_filename=?",
                (pdf_filename,),
            ).fetchone()[0]
        return count > 0

    # ------------------------------------------------------------------
    # BaseAgent interface
    # ------------------------------------------------------------------

    def fetch_data(self) -> list[dict]:
        """
        Scan Gmail for all IEC bill emails, filter, parse, and return records.

        Each record:
            {
                "period_start":  "YYYY-MM-DD",
                "period_end":    "YYYY-MM-DD",
                "days":          int,
                "amount":        float,
                "is_correction": bool,
                "pdf_filename":  str,
            }
        """
        os.makedirs(PDF_BILLS_DIR, exist_ok=True)

        mail = self._connect()
        records = []
        try:
            msg_ids = self._search_all_emails(mail)
            self.logger.info("[electricity] Found %d candidate emails.", len(msg_ids))

            for msg_id in msg_ids:
                try:
                    msg = self._fetch_email(mail, msg_id)

                    # Decode subject
                    raw_subject = msg.get("Subject", "")
                    subject = email.header.decode_header(raw_subject)
                    subject_str = ""
                    for part, enc in subject:
                        if isinstance(part, bytes):
                            subject_str += part.decode(enc or "utf-8", errors="replace")
                        else:
                            subject_str += part

                    if not should_process_email(subject_str):
                        self.logger.debug("[electricity] Skipping: %s", subject_str[:80])
                        continue

                    dates = parse_dates_from_subject(subject_str)
                    if dates is None:
                        self.logger.warning("[electricity] No dates in subject: %s", subject_str[:80])
                        continue
                    period_start, period_end = dates

                    attachment = self._get_pdf_attachment(msg)
                    if attachment is None:
                        self.logger.warning("[electricity] No PDF attachment: %s", subject_str[:80])
                        continue
                    pdf_filename, pdf_bytes = attachment

                    if self._is_processed(pdf_filename):
                        self.logger.info("[electricity] Already processed: %s", pdf_filename)
                        continue

                    # Save PDF to disk
                    pdf_path = os.path.join(PDF_BILLS_DIR, pdf_filename)
                    with open(pdf_path, "wb") as f:
                        f.write(pdf_bytes)

                    amount = extract_amount_from_pdf(pdf_bytes)
                    if amount is None:
                        self.logger.warning("[electricity] Could not extract amount from %s", pdf_filename)
                        continue

                    start_dt = date.fromisoformat(period_start)
                    end_dt   = date.fromisoformat(period_end)
                    days     = (end_dt - start_dt).days
                    is_correction = days > 90

                    records.append({
                        "period_start":  period_start,
                        "period_end":    period_end,
                        "days":          days,
                        "amount":        amount,
                        "is_correction": is_correction,
                        "pdf_filename":  pdf_filename,
                    })
                    self.logger.info(
                        "[electricity] Parsed %.2f ₪ for %s–%s (%d days)%s",
                        amount, period_start, period_end, days,
                        " [CORRECTION]" if is_correction else "",
                    )

                except Exception as exc:  # noqa: BLE001
                    self.logger.warning("[electricity] Error processing msg_id=%s: %s", msg_id, exc)
                    continue

        finally:
            mail.logout()

        return records

    def save_to_db(self, data: list[dict]) -> None:
        """Insert each electricity bill into the expenses table."""
        with get_connection() as conn:
            for record in data:
                description = (
                    f"חשמל {record['period_start']}–{record['period_end']}"
                    f" ({record['days']} ימים)"
                )
                conn.execute(
                    """INSERT INTO expenses
                       (date, category, amount, description, source,
                        is_correction, pdf_filename, period_start, period_end, billing_days)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        record["period_end"],          # date = end of period
                        "electricity",
                        record["amount"],
                        description,
                        "iec",
                        int(record["is_correction"]),
                        record["pdf_filename"],
                        record["period_start"],
                        record["period_end"],
                        record["days"],
                    ),
                )
        self.logger.info("[electricity] Saved %d bill(s) to DB.", len(data))


# ---------------------------------------------------------------------------
# Manual run entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import logging

    logging.basicConfig(level=logging.INFO)
    result = ElectricityAgent().run()
    if result["success"]:
        print(f"Success: {len(result['data'])} bill(s) saved.")
    else:
        print(f"Failed: {result['error']}")
