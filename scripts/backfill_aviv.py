"""
Backfill Aviv daily sales from a historical Gmail message containing .eml attachments.

The email (message ID 19ccd2d7f8ef9122) contains forwarded daily Z-report emails.
Each .eml has a PDF attachment (z_XXXX.pdf) with the real sales total.

IMPORTANT: Filename amounts are unreliable — we extract from the PDF itself.

Usage:
    python3 scripts/backfill_aviv.py
"""

import email
import email.header
import imaplib
import io
import os
import re
import sys
from datetime import datetime

import pdfplumber
from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database.db import get_connection, init_db, insert_daily_sale

load_dotenv()

IMAP_HOST = "imap.gmail.com"
IMAP_PORT = 993
GMAIL_MESSAGE_ID = "19ccd2d7f8ef9122"

# RTL PDF text: "20295.85 ₪ :כ"הס" (visual order, reversed Hebrew)
# Match: amount ₪ :כ"הס  (the main sales total line)
TOTAL_PATTERN = re.compile(r'([\d,]+\.?\d*)\s*₪\s*:כ"הס')

# Regex to extract date from .txt filename: tran_02459_20260301214102.txt → 20260301
TXT_DATE_PATTERN = re.compile(r'tran_\d+_(\d{8})\d+\.txt')


def decode_header_value(raw: str) -> str:
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


def decode_filename(part) -> str:
    raw = part.get_filename() or ""
    return decode_header_value(raw)


def extract_nested_messages(msg: email.message.Message) -> list[email.message.Message]:
    """Extract all message/rfc822 parts from the outer email."""
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


def find_pdf_bytes(msg: email.message.Message) -> bytes | None:
    """Find the z_*.pdf attachment in a message and return its bytes."""
    # Pass 1: look for z_*.pdf by name
    for part in msg.walk():
        if part.get_content_type() == "application/pdf":
            fn = decode_filename(part).lower()
            if fn.startswith("z_"):
                return part.get_payload(decode=True)
    # Pass 2: any PDF
    for part in msg.walk():
        if part.get_content_type() == "application/pdf":
            return part.get_payload(decode=True)
    # Pass 3: octet-stream with .pdf filename
    for part in msg.walk():
        if part.get_content_type() == "application/octet-stream":
            fn = decode_filename(part).lower()
            if fn.endswith(".pdf"):
                data = part.get_payload(decode=True)
                if data and data[:5] == b"%PDF-":
                    return data
    return None


def extract_total_from_pdf(pdf_bytes: bytes) -> float | None:
    """Parse PDF and extract the main total line: '20295.85 ₪ :כ"הס'."""
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for page in pdf.pages:
            text = page.extract_text() or ""
            match = TOTAL_PATTERN.search(text)
            if match:
                return float(match.group(1).replace(",", ""))
    return None


def extract_date_from_eml(msg: email.message.Message) -> str | None:
    """
    Try to get the report date from:
    1. A .txt filename like tran_02459_20260301214102.txt
    2. The email Date header as fallback
    """
    # Check .txt filenames for date
    for part in msg.walk():
        fn = decode_filename(part)
        m = TXT_DATE_PATTERN.match(fn)
        if m:
            ds = m.group(1)  # "20260301"
            return f"{ds[:4]}-{ds[4:6]}-{ds[6:8]}"

    # Fallback: email Date header
    date_str = msg.get("Date", "")
    if date_str:
        try:
            dt = email.utils.parsedate_to_datetime(date_str)
            return dt.strftime("%Y-%m-%d")
        except Exception:
            pass
    return None


def main():
    init_db()

    gmail_address = os.getenv("GMAIL_ADDRESS", "")
    gmail_password = os.getenv("GMAIL_APP_PASSWORD", "")
    if not gmail_address or not gmail_password:
        print("ERROR: GMAIL_ADDRESS / GMAIL_APP_PASSWORD not set in .env")
        return

    # Connect to Gmail
    print("Connecting to Gmail via IMAP...")
    mail = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT)
    mail.login(gmail_address, gmail_password)
    mail.select("inbox")

    # Search by Gmail message ID (X-GM-MSGID)
    msg_id_dec = int(GMAIL_MESSAGE_ID, 16)
    status, data = mail.search(None, f"X-GM-MSGID {msg_id_dec}")
    if status != "OK" or not data or not data[0]:
        print(f"ERROR: Message {GMAIL_MESSAGE_ID} not found")
        mail.logout()
        return

    seq_num = data[0].split()[0]
    print(f"Found message (IMAP seq={seq_num.decode()})")

    # Fetch the full message
    status, msg_data = mail.fetch(seq_num, "(RFC822)")
    mail.logout()
    if status != "OK":
        print("ERROR: Failed to fetch message")
        return

    outer = email.message_from_bytes(msg_data[0][1])
    nested = extract_nested_messages(outer)
    print(f"Found {len(nested)} nested .eml attachment(s)\n")

    if not nested:
        print("ERROR: No nested messages found. Check the message ID.")
        return

    # Process each nested email
    results = []
    for i, eml in enumerate(nested, 1):
        subject = decode_header_value(eml.get("Subject", ""))
        short_subj = subject[:60]

        # Only process Z-reports
        if "דוח סוף יום" not in subject and "דו\"ח" not in subject and "z_" not in subject.lower():
            print(f"  [{i}] SKIP (not Z-report): {short_subj}")
            continue

        report_date = extract_date_from_eml(eml)
        if not report_date:
            print(f"  [{i}] SKIP (no date found): {short_subj}")
            continue

        pdf_bytes = find_pdf_bytes(eml)
        if not pdf_bytes:
            print(f"  [{i}] SKIP (no PDF): {short_subj}")
            continue

        total = extract_total_from_pdf(pdf_bytes)
        if total is None:
            print(f"  [{i}] SKIP (PDF parse failed): {short_subj}")
            continue

        # Check for duplicate
        with get_connection() as conn:
            existing = conn.execute(
                "SELECT COUNT(*) FROM daily_sales WHERE date = ? AND source = 'aviv'",
                (report_date,),
            ).fetchone()[0]

        if existing > 0:
            results.append({"date": report_date, "amount": total, "status": "skipped"})
            continue

        insert_daily_sale(date=report_date, total_income=total, source="aviv")
        results.append({"date": report_date, "amount": total, "status": "saved"})

    # Summary
    print(f"\n{'='*60}")
    print(f"{'Date':<14} {'Amount':>12}  Status")
    print(f"{'-'*14} {'-'*12}  {'-'*10}")
    for r in sorted(results, key=lambda x: x["date"]):
        print(f"{r['date']:<14} {r['amount']:>12,.2f}  {r['status']}")

    saved = sum(1 for r in results if r["status"] == "saved")
    skipped = sum(1 for r in results if r["status"] == "skipped")
    print(f"\nSUMMARY: {saved} saved, {skipped} duplicates skipped")


if __name__ == "__main__":
    main()
