"""
Employee Hours agent - automatically fetches monthly attendance CSV from Gmail,
parses it, matches employees to DB, saves hours+salary, and sends Telegram alert.

Flow:
    1. Connect to Gmail IMAP (imap.gmail.com:993 SSL)
    2. Search for UNREAD emails with subject containing "נוכחות באקסל"
    3. For each matching email:
       a. Download the CSV attachment
       b. Parse with parse_attendance_csv()
       c. Determine month from CSV dates
       d. Match employees to DB (case-insensitive substring)
       e. Save to employee_monthly_hours + employee_hours tables
       f. Mark email as READ
       g. Send Telegram notification with results

CSV format:
    "382 רועי אמסלם",... ← employee row matches: ^\\d+\\s+(.+)
    ... shift rows ...
    "סה''כ שורות  7,,,,,33:47:00"  ← summary row, total in last column (HH:MM:SS)

Credentials (.env):
    GMAIL_ADDRESS        - Gmail account address
    GMAIL_APP_PASSWORD   - Google App Password
    AVIV_SENDER_EMAIL    - Sender address (same as other Aviv emails)
"""

import email
import email.header
import imaplib
import os
import re
from datetime import date

from dotenv import load_dotenv

from agents.base_agent import BaseAgent
from agents.parse_attendance_csv import parse_attendance_csv
from database.db import (
    get_active_employees,
    get_connection,
    upsert_employee_hours,
    upsert_employee_monthly_hours,
)

load_dotenv()

IMAP_HOST = "imap.gmail.com"
IMAP_PORT = 993

# CSV attachment filename prefix
CSV_FILENAME_PREFIX = "דוח שעון נוכחות מפורט_XL_"

# Date pattern in CSV: DD/MM/YYYY
_DATE_RE = re.compile(r'(\d{2})/(\d{2})/(\d{4})')

# Hebrew month names for Telegram message
HEBREW_MONTHS = {
    1: 'ינואר', 2: 'פברואר', 3: 'מרץ', 4: 'אפריל', 5: 'מאי', 6: 'יוני',
    7: 'יולי', 8: 'אוגוסט', 9: 'ספטמבר', 10: 'אוקטובר', 11: 'נובמבר', 12: 'דצמבר',
}


def _extract_month_from_csv(csv_bytes: bytes) -> str | None:
    """
    Extract YYYY-MM from the first date found in the CSV (תאריך כניסה column).
    Returns None if no date found.
    """
    for enc in ('utf-8-sig', 'utf-8', 'windows-1255', 'cp1255'):
        try:
            text = csv_bytes.decode(enc)
            break
        except (UnicodeDecodeError, LookupError):
            continue
    else:
        return None

    for line in text.splitlines():
        m = _DATE_RE.search(line)
        if m:
            day, month, year = m.group(1), m.group(2), m.group(3)
            return f"{year}-{month}"
    return None


def _match_employee(csv_name: str, db_employees: list) -> dict | None:
    """Case-insensitive substring match: db_name in csv_name OR csv_name in db_name."""
    csv_lower = csv_name.strip().lower()
    for emp in db_employees:
        db_lower = emp["name"].strip().lower()
        if db_lower in csv_lower or csv_lower in db_lower:
            return emp
    return None


class EmployeeHoursAgent(BaseAgent):
    name = "employee_hours"

    def __init__(self):
        super().__init__()
        self._gmail_address = os.getenv("GMAIL_ADDRESS", "")
        self._gmail_password = os.getenv("GMAIL_APP_PASSWORD", "")
        self._sender_email = os.getenv("AVIV_SENDER_EMAIL", "")

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _connect(self) -> imaplib.IMAP4_SSL:
        mail = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT)
        mail.login(self._gmail_address, self._gmail_password)
        mail.select("inbox")
        return mail

    def _search_unread_emails(self, mail: imaplib.IMAP4_SSL) -> list[bytes]:
        """Search for UNREAD attendance emails from Aviv sender.

        Note: IMAP SUBJECT search with Hebrew causes ASCII encoding errors,
        so we search by FROM+UNSEEN only and filter by subject client-side.
        """
        criteria = f'(UNSEEN FROM "{self._sender_email}")'
        status, data = mail.search(None, criteria)
        if status != "OK" or not data or not data[0]:
            return []

        # Filter by subject containing "נוכחות באקסל" client-side
        all_ids = data[0].split()
        matching = []
        for msg_id in all_ids:
            status2, header_data = mail.fetch(msg_id, "(BODY[HEADER.FIELDS (SUBJECT)])")
            if status2 != "OK":
                continue
            raw_header = header_data[0][1]
            # Decode the subject header
            subject_raw = email.header.decode_header(
                email.message_from_bytes(raw_header).get("Subject", "")
            )
            subject = ""
            for part, charset in subject_raw:
                if isinstance(part, bytes):
                    subject += part.decode(charset or "utf-8", errors="replace")
                else:
                    subject += part
            if "נוכחות באקסל" in subject:
                matching.append(msg_id)

        return matching

    def _fetch_csv_attachment(self, mail: imaplib.IMAP4_SSL, msg_id: bytes) -> bytes | None:
        """Fetch the CSV attachment whose filename starts with the expected prefix."""
        status, msg_data = mail.fetch(msg_id, "(RFC822)")
        if status != "OK":
            return None

        msg = email.message_from_bytes(msg_data[0][1])

        for part in msg.walk():
            ct = part.get_content_type()
            filename = part.get_filename() or ""

            is_csv = (
                ct in ("text/csv", "application/vnd.ms-excel",
                       "application/octet-stream", "application/csv")
                or filename.lower().endswith(".csv")
            )
            if is_csv and filename.startswith(CSV_FILENAME_PREFIX):
                return part.get_payload(decode=True)

        return None

    def _mark_as_read(self, mail: imaplib.IMAP4_SSL, msg_id: bytes) -> None:
        """Mark email as read (SEEN)."""
        mail.store(msg_id, '+FLAGS', '\\Seen')

    def _process_csv(self, csv_bytes: bytes) -> dict:
        """
        Parse CSV, match employees, save to DB.
        Returns summary dict with matched/unmatched/month/total_salary.
        """
        # Parse CSV
        parsed = parse_attendance_csv(csv_bytes)
        if not parsed:
            raise ValueError("No employee data found in CSV")

        # Determine month from CSV content
        month_str = _extract_month_from_csv(csv_bytes)
        if not month_str:
            # Fallback: previous month (CSV arrives on 1st for prior month)
            today = date.today()
            if today.month == 1:
                month_str = f"{today.year - 1}-12"
            else:
                month_str = f"{today.year}-{today.month - 1:02d}"

        # Get DB employees
        db_employees = get_active_employees()

        matched = []
        unmatched = []

        for entry in parsed:
            csv_name = entry["name"].strip()
            emp = _match_employee(csv_name, db_employees)

            if emp:
                salary = round(entry["hours"] * emp["hourly_rate"], 2)
                upsert_employee_monthly_hours(
                    employee_name=emp["name"],
                    month=month_str,
                    total_hours=entry["hours"],
                    total_salary=salary,
                )
                # Also update employee_hours table for compatibility
                month_num = int(month_str.split("-")[1])
                year_num = int(month_str.split("-")[0])
                upsert_employee_hours(
                    employee_id=emp["id"],
                    month=month_num,
                    year=year_num,
                    hours_worked=entry["hours"],
                    is_finalized=True,
                )
                matched.append({
                    "db_name": emp["name"],
                    "csv_name": csv_name,
                    "hours": entry["hours"],
                    "raw_hours": entry["raw_hours"],
                    "hourly_rate": emp["hourly_rate"],
                    "salary": salary,
                })
            else:
                # Save with salary=0 to signal unmatched
                upsert_employee_monthly_hours(
                    employee_name=csv_name,
                    month=month_str,
                    total_hours=entry["hours"],
                    total_salary=0,
                )
                unmatched.append({
                    "name": csv_name,
                    "hours": entry["hours"],
                    "raw_hours": entry["raw_hours"],
                })

        total_salary = sum(m["salary"] for m in matched)

        return {
            "matched": matched,
            "unmatched": unmatched,
            "month": month_str,
            "total_salary": round(total_salary, 2),
            "total_employees": len(parsed),
        }

    def _send_telegram_notification(self, result: dict) -> None:
        """Send Telegram alert with processing results."""
        try:
            from notifications.whatsapp import send_alert
        except ImportError:
            return

        month_str = result["month"]
        year_num = int(month_str.split("-")[0])
        month_num = int(month_str.split("-")[1])
        month_display = f"{HEBREW_MONTHS.get(month_num, month_str)} {year_num}"

        lines = [
            f"👷 דוח נוכחות עובדים עובד לחודש {month_display}",
            f"✅ {len(result['matched'])} עובדים הותאמו",
        ]

        if result["unmatched"]:
            unmatched_names = ", ".join(u["name"] for u in result["unmatched"])
            lines.append(f"⚠️ עובדים לא מזוהים: {unmatched_names}")

        lines.append(f"💰 סה\"כ שכר: ₪{result['total_salary']:,.2f}")

        if result["unmatched"]:
            lines.append("")
            lines.append("❗ עובדים הבאים מופיעים בדוח אך אינם במערכת:")
            for u in result["unmatched"]:
                lines.append(f"  • {u['name']} ({u['hours']:.1f} שעות)")
            lines.append("→ כנס לדשבורד ← עובדים → והוסף אותם עם התעריף שלהם")

        send_alert("\n".join(lines), force=True)

    # ------------------------------------------------------------------
    # BaseAgent interface
    # ------------------------------------------------------------------

    def fetch_data(self) -> list[dict]:
        """
        Connect to Gmail, find UNREAD attendance emails, parse and process each.
        Returns list of result summaries (one per email processed).
        """
        mail = self._connect()
        try:
            msg_ids = self._search_unread_emails(mail)
            if not msg_ids:
                self.logger.info("[employee_hours] No unread attendance emails found.")
                return []

            self.logger.info("[employee_hours] Found %d unread attendance email(s).", len(msg_ids))
            all_results = []

            for msg_id in msg_ids:
                csv_bytes = self._fetch_csv_attachment(mail, msg_id)
                if csv_bytes is None:
                    self.logger.warning(
                        "[employee_hours] Email %s: no CSV attachment with prefix '%s'",
                        msg_id, CSV_FILENAME_PREFIX,
                    )
                    # Still mark as read to avoid reprocessing
                    self._mark_as_read(mail, msg_id)
                    continue

                result = self._process_csv(csv_bytes)
                self._mark_as_read(mail, msg_id)
                self._send_telegram_notification(result)

                self.logger.info(
                    "[employee_hours] Processed month %s: %d matched, %d unmatched, salary=%.2f",
                    result["month"], len(result["matched"]),
                    len(result["unmatched"]), result["total_salary"],
                )
                all_results.append(result)

            return all_results
        finally:
            mail.logout()

    def save_to_db(self, data: list[dict]) -> None:
        """DB writes already happen in _process_csv(). Nothing more to do."""
        pass


# ---------------------------------------------------------------------------
# Manual run entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import logging
    logging.basicConfig(level=logging.INFO)
    result = EmployeeHoursAgent().run()
    if result["success"]:
        results = result.get("data") or []
        print(f"Success: processed {len(results)} email(s).")
        for r in results:
            print(f"  Month {r['month']}: {len(r['matched'])} matched, "
                  f"{len(r['unmatched'])} unmatched, salary=₪{r['total_salary']:,.2f}")
    else:
        print(f"Failed: {result['error']}")
