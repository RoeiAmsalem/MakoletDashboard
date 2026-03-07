"""
Load historical electricity bills from a Gmail message containing .eml attachments.

The email was sent from shimonmakolet@gmail.com on 07/03/2026 at 11:37,
with an empty subject. Each .eml attachment is a nested message/rfc822 part
containing an original IEC bill email with a PDF attachment.
"""

import email
import email.header
import imaplib
import io
import os
import re
import sys
from datetime import date

import pdfplumber
from dotenv import load_dotenv

# Allow imports from project root
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database.db import get_connection, init_db

load_dotenv()

# ---------------------------------------------------------------------------
# Constants (reused from agents/electricity.py)
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

DATE_PATTERN   = re.compile(r'לתקופה - (\d{2}/\d{2}/\d{4}) - (\d{2}/\d{2}/\d{4})')
AMOUNT_PATTERN = re.compile(r'([\d,]+\.?\d*)\s+ןובשח תפוקתל מ"עמ ללוכ כ"הס')
PDF_NAME_RE    = re.compile(r'^\d{4}-\d+_\d{8}_\d{6}\.pdf$', re.IGNORECASE)

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PDF_BILLS_DIR = os.path.join(_PROJECT_ROOT, "data", "electricity_bills")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def decode_subject(msg: email.message.Message) -> str:
    raw = msg.get("Subject", "")
    parts = email.header.decode_header(raw)
    result = ""
    for part, enc in parts:
        if isinstance(part, bytes):
            result += part.decode(enc or "utf-8", errors="replace")
        else:
            result += part
    return result


def should_process(subject: str) -> bool:
    if CONTRACT_NUMBER not in subject:
        return False
    if "לתקופה" not in subject:
        return False
    for skip in SKIP_SUBJECTS:
        if skip in subject:
            return False
    return True


def parse_dates(subject: str) -> tuple[str, str] | None:
    m = DATE_PATTERN.search(subject)
    if not m:
        return None
    def _to_iso(dmy: str) -> str:
        d, mo, y = dmy.split("/")
        return f"{y}-{mo}-{d}"
    end_dmy, start_dmy = m.group(1), m.group(2)
    return _to_iso(start_dmy), _to_iso(end_dmy)


def extract_amount(pdf_bytes: bytes) -> float | None:
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for page in pdf.pages:
            text = page.extract_text() or ""
            m = AMOUNT_PATTERN.search(text)
            if m:
                return float(m.group(1).replace(",", ""))
    return None


def get_pdf_from_message(msg: email.message.Message) -> tuple[str, bytes] | None:
    """Find a PDF attachment in a message. Prefers IEC-named PDFs, then any PDF,
    then octet-stream attachments with .pdf in the decoded filename."""
    # Pass 1: IEC-named PDFs
    for part in msg.walk():
        if part.get_content_type() == "application/pdf":
            raw_fn = part.get_filename() or ""
            filename = _decode_filename(raw_fn)
            if PDF_NAME_RE.match(filename):
                return filename, part.get_payload(decode=True)

    # Pass 2: any application/pdf
    for part in msg.walk():
        if part.get_content_type() == "application/pdf":
            raw_fn = part.get_filename() or ""
            filename = _decode_filename(raw_fn) or "electricity_bill.pdf"
            return filename, part.get_payload(decode=True)

    # Pass 3: octet-stream with .pdf filename (some IEC bills use this)
    for part in msg.walk():
        if part.get_content_type() == "application/octet-stream":
            raw_fn = part.get_filename() or ""
            filename = _decode_filename(raw_fn)
            if filename.lower().endswith(".pdf"):
                payload = part.get_payload(decode=True)
                if payload and payload[:5] == b"%PDF-":
                    return filename, payload

    return None


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


def is_already_in_db(pdf_filename: str) -> bool:
    with get_connection() as conn:
        count = conn.execute(
            "SELECT COUNT(*) FROM expenses WHERE category='electricity' AND pdf_filename=?",
            (pdf_filename,),
        ).fetchone()[0]
    return count > 0


def save_record(record: dict) -> None:
    description = (
        f"חשמל {record['period_start']}–{record['period_end']}"
        f" ({record['days']} ימים)"
    )
    with get_connection() as conn:
        conn.execute(
            """INSERT INTO expenses
               (date, category, amount, description, source,
                is_correction, pdf_filename, period_start, period_end, billing_days)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                record["period_end"],
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


def extract_nested_messages(msg: email.message.Message) -> list[email.message.Message]:
    """Extract all top-level message/rfc822 parts from the outer email."""
    nested = []
    for part in msg.walk():
        if part.get_content_type() == "message/rfc822":
            payload = part.get_payload()
            if isinstance(payload, list):
                for sub in payload:
                    if isinstance(sub, email.message.Message):
                        nested.append(sub)
            elif isinstance(payload, email.message.Message):
                nested.append(payload)
    return nested


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    init_db()
    os.makedirs(PDF_BILLS_DIR, exist_ok=True)

    gmail_address  = os.getenv("GMAIL_ADDRESS", "")
    gmail_password = os.getenv("GMAIL_APP_PASSWORD", "")

    print("Connecting to Gmail...")
    mail = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT)
    mail.login(gmail_address, gmail_password)
    mail.select("inbox")

    # Search for the historical email from shimonmakolet@gmail.com on 07-Mar-2026
    print("Searching for historical email...")
    status, data = mail.search(None, '(FROM "shimonmakolet@gmail.com" ON 07-Mar-2026)')
    if status != "OK" or not data or not data[0]:
        print("ERROR: Could not find the historical email.")
        mail.logout()
        return

    msg_ids = data[0].split()
    print(f"Found {len(msg_ids)} candidate email(s).")

    # Find the right one: empty subject with message/rfc822 attachments
    target_msg = None
    for msg_id in msg_ids:
        status, msg_data = mail.fetch(msg_id, "(RFC822)")
        if status != "OK":
            continue
        msg = email.message_from_bytes(msg_data[0][1])
        subject = decode_subject(msg)
        has_rfc822 = any(
            part.get_content_type() == "message/rfc822"
            for part in msg.walk()
        )
        if not subject.strip() and has_rfc822:
            target_msg = msg
            print(f"  Found target: msg_id={msg_id}, Date={msg.get('Date')}")
            break

    if target_msg is None:
        print("ERROR: No email with .eml attachments found.")
        mail.logout()
        return

    mail.logout()

    # Extract all nested message/rfc822 parts
    nested_messages = extract_nested_messages(target_msg)
    print(f"Found {len(nested_messages)} nested .eml message(s).\n")

    records = []
    skipped = {"filter": 0, "no_dates": 0, "no_pdf": 0, "no_amount": 0, "duplicate": 0}

    for i, inner_msg in enumerate(nested_messages, 1):
        subject = decode_subject(inner_msg)
        short_subj = subject[:80] if subject else "(empty)"

        if not should_process(subject):
            skipped["filter"] += 1
            print(f"  [{i:2d}] SKIP (filter): {short_subj}")
            continue

        dates = parse_dates(subject)
        if dates is None:
            skipped["no_dates"] += 1
            print(f"  [{i:2d}] SKIP (no dates): {short_subj}")
            continue
        period_start, period_end = dates

        attachment = get_pdf_from_message(inner_msg)
        if attachment is None:
            skipped["no_pdf"] += 1
            print(f"  [{i:2d}] SKIP (no PDF): {short_subj}")
            continue
        pdf_filename, pdf_bytes = attachment

        if is_already_in_db(pdf_filename):
            skipped["duplicate"] += 1
            print(f"  [{i:2d}] SKIP (already in DB): {pdf_filename}")
            continue

        # Save PDF to disk
        pdf_path = os.path.join(PDF_BILLS_DIR, pdf_filename)
        with open(pdf_path, "wb") as f:
            f.write(pdf_bytes)

        amount = extract_amount(pdf_bytes)
        if amount is None:
            skipped["no_amount"] += 1
            print(f"  [{i:2d}] SKIP (no amount): {pdf_filename}")
            continue

        start_dt = date.fromisoformat(period_start)
        end_dt   = date.fromisoformat(period_end)
        days     = (end_dt - start_dt).days
        is_correction = days > 90

        record = {
            "period_start":  period_start,
            "period_end":    period_end,
            "days":          days,
            "amount":        amount,
            "is_correction": is_correction,
            "pdf_filename":  pdf_filename,
        }
        save_record(record)
        records.append(record)
        corr = " [CORRECTION]" if is_correction else ""
        print(f"  [{i:2d}] SAVED: {amount:>10,.2f} NIS | {period_start} - {period_end} ({days}d){corr} | {pdf_filename}")

    # Summary
    print("\n" + "=" * 80)
    print(f"SUMMARY: {len(records)} bill(s) saved, "
          f"{sum(skipped.values())} skipped "
          f"(filter={skipped['filter']}, no_dates={skipped['no_dates']}, "
          f"no_pdf={skipped['no_pdf']}, no_amount={skipped['no_amount']}, "
          f"duplicate={skipped['duplicate']})")

    if records:
        print(f"\n{'Period':<27} {'Days':>5} {'Amount (NIS)':>14} {'Correction':>12}  PDF")
        print("-" * 90)
        for r in sorted(records, key=lambda x: x["period_start"]):
            corr = "YES" if r["is_correction"] else ""
            print(f"{r['period_start']} - {r['period_end']}  {r['days']:>5}  {r['amount']:>14,.2f}  {corr:>12}  {r['pdf_filename']}")
        total = sum(r["amount"] for r in records)
        print("-" * 90)
        print(f"{'TOTAL':<27} {'':>5}  {total:>14,.2f}")


if __name__ == "__main__":
    main()
