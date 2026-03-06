"""
Employee Hours agent - reads the monthly attendance CSV from Aviv POS email
and saves hours to the employee_hours table with is_finalized=True.

Flow:
    Connect to Gmail IMAP (imap.gmail.com:993 SSL)
    Search for this month's email with subject containing "נוכחות באקסל"
    Download the CSV attachment (filename: "דוח שעון נוכחות מפורט_XL_*.csv")
    Parse each employee: name from ID row, total hours from "סה''כ שורות" row
    Match names to employees table and upsert employee_hours

CSV format:
    "382 רועי אמסלם",... ← employee row matches: ^\\d+\\s+(.+)
    ... shift rows ...
    "סה''כ שורות  7,,,,,33:47"  ← summary row, total in last column (HH:MM)
    ...next employee...
    ",,,,,284:13"               ← grand total row (ignored)

Credentials (.env):
    GMAIL_ADDRESS        - Gmail account address
    GMAIL_APP_PASSWORD   - Google App Password
    AVIV_SENDER_EMAIL    - Sender address (same as other Aviv emails)
"""

import csv
import email
import imaplib
import io
import os
import re
from datetime import date

from dotenv import load_dotenv

from agents.base_agent import BaseAgent
from database.db import get_connection, upsert_employee_hours

load_dotenv()

IMAP_HOST = "imap.gmail.com"
IMAP_PORT = 993

# Employee name row: "382 רועי אמסלם" → capture group 1 = "רועי אמסלם"
EMPLOYEE_ROW_RE = re.compile(r"^\d+\s+(.+)$")

# Summary row contains this Hebrew text
SUMMARY_TEXT = "סה''כ שורות"

# CSV attachment filename prefix
CSV_FILENAME_PREFIX = "דוח שעון נוכחות מפורט_XL_"


def _decode_csv(raw: bytes) -> str:
    """Try multiple encodings; Israeli software often uses cp1255."""
    for enc in ("utf-8-sig", "utf-8", "cp1255", "iso-8859-8"):
        try:
            return raw.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", errors="replace")


def _hhmm_to_hours(time_str: str) -> float:
    """Convert "33:47" to 33.783..."""
    parts = time_str.strip().split(":")
    if len(parts) != 2:
        raise ValueError(f"Unexpected time format: {time_str!r}")
    return int(parts[0]) + int(parts[1]) / 60


def parse_hours_csv(csv_bytes: bytes) -> list[dict]:
    """
    Parse the attendance CSV and return a list of
    {"name": str, "hours": float} dicts — one per employee.
    """
    content = _decode_csv(csv_bytes)
    reader  = csv.reader(io.StringIO(content))

    results: list[dict] = []
    current_name: str | None = None

    for row in reader:
        if not row:
            continue

        first_col = row[0].strip()

        # Employee name row
        m = EMPLOYEE_ROW_RE.match(first_col)
        if m:
            current_name = m.group(1).strip()
            continue

        # Summary row for current employee
        if current_name and SUMMARY_TEXT in first_col:
            # Total hours is in the last non-empty column
            time_str = None
            for col in reversed(row):
                col = col.strip()
                if col:
                    time_str = col
                    break

            if time_str and ":" in time_str:
                hours = _hhmm_to_hours(time_str)
                results.append({"name": current_name, "hours": round(hours, 4)})

            current_name = None  # reset; next employee starts fresh

    return results


class EmployeeHoursAgent(BaseAgent):
    name = "employee_hours"

    def __init__(self):
        super().__init__()
        self._gmail_address = os.getenv("GMAIL_ADDRESS", "")
        self._gmail_password = os.getenv("GMAIL_APP_PASSWORD", "")
        self._sender_email  = os.getenv("AVIV_SENDER_EMAIL", "")

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _connect(self) -> imaplib.IMAP4_SSL:
        mail = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT)
        mail.login(self._gmail_address, self._gmail_password)
        mail.select("inbox")
        return mail

    def _search_this_month_email(self, mail: imaplib.IMAP4_SSL) -> list[bytes]:
        """Search for attendance email sent since the 1st of current month."""
        first_of_month = date.today().replace(day=1).strftime("%d-%b-%Y")
        criteria = (
            f'(FROM "{self._sender_email}" SINCE "{first_of_month}"'
            f' SUBJECT "נוכחות באקסל")'
        )
        status, data = mail.search(None, criteria)
        if status != "OK" or not data or not data[0]:
            return []
        return data[0].split()

    def _fetch_csv_attachment(self, mail: imaplib.IMAP4_SSL, msg_id: bytes) -> bytes | None:
        """Fetch the CSV attachment whose filename starts with the expected prefix."""
        status, msg_data = mail.fetch(msg_id, "(RFC822)")
        if status != "OK":
            return None

        msg = email.message_from_bytes(msg_data[0][1])

        for part in msg.walk():
            ct       = part.get_content_type()
            filename = part.get_filename() or ""

            is_csv = (
                ct in ("text/csv", "application/vnd.ms-excel",
                       "application/octet-stream", "application/csv")
                or filename.lower().endswith(".csv")
            )
            if is_csv and filename.startswith(CSV_FILENAME_PREFIX):
                return part.get_payload(decode=True)

        return None

    # ------------------------------------------------------------------
    # BaseAgent interface
    # ------------------------------------------------------------------

    def fetch_data(self) -> list[dict]:
        """
        Connect to Gmail, find this month's attendance email, parse the CSV.

        Returns:
            [{"name": "רועי אמסלם", "hours": 33.78}, ...]
            or [] if no email found.
        """
        mail = self._connect()
        try:
            msg_ids = self._search_this_month_email(mail)
            if not msg_ids:
                self.logger.info("[employee_hours] No attendance email found for this month.")
                return []

            # Use the latest matching email
            csv_bytes = self._fetch_csv_attachment(mail, msg_ids[-1])
            if csv_bytes is None:
                raise ValueError(
                    f"Email found but no CSV attachment starting with "
                    f"'{CSV_FILENAME_PREFIX}'."
                )

            records = parse_hours_csv(csv_bytes)
            self.logger.info("[employee_hours] Parsed %d employee records.", len(records))
            return records
        finally:
            mail.logout()

    def save_to_db(self, data: list[dict]) -> None:
        """Match employees by name and upsert their hours as finalized."""
        today = date.today()

        with get_connection() as conn:
            rows = conn.execute(
                "SELECT id, name FROM employees WHERE is_active = 1"
            ).fetchall()

        name_to_id: dict[str, int] = {r["name"]: r["id"] for r in rows}

        saved = 0
        for record in data:
            employee_id = name_to_id.get(record["name"])
            if employee_id is None:
                self.logger.warning(
                    "[employee_hours] Employee not found in DB: %r — skipping.",
                    record["name"],
                )
                continue

            upsert_employee_hours(
                employee_id=employee_id,
                month=today.month,
                year=today.year,
                hours_worked=record["hours"],
                is_finalized=True,
            )
            saved += 1

        self.logger.info("[employee_hours] Saved %d / %d employee hours.", saved, len(data))


# ---------------------------------------------------------------------------
# Manual run entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import logging
    logging.basicConfig(level=logging.INFO)
    result = EmployeeHoursAgent().run()
    if result["success"]:
        print(f"Success: {len(result['data'])} employees saved.")
    else:
        print(f"Failed: {result['error']}")
