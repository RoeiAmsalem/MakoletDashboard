"""Debug: inspect the historical email structure to find .eml attachments."""

import email
import email.header
import imaplib
import os
import sys

from dotenv import load_dotenv
load_dotenv()

IMAP_HOST = "imap.gmail.com"
IMAP_PORT = 993

mail = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT)
mail.login(os.getenv("GMAIL_ADDRESS"), os.getenv("GMAIL_APP_PASSWORD"))
mail.select("inbox")

status, data = mail.search(None, '(FROM "shimonmakolet@gmail.com" ON 07-Mar-2026)')
print(f"Search status: {status}, IDs: {data}")

if not data or not data[0]:
    # Try broader search
    print("\nTrying broader search...")
    status, data = mail.search(None, '(FROM "shimonmakolet@gmail.com" SINCE 06-Mar-2026 BEFORE 08-Mar-2026)')
    print(f"Search status: {status}, IDs: {data}")

if not data or not data[0]:
    print("\nTrying even broader - all from shimonmakolet...")
    status, data = mail.search(None, '(FROM "shimonmakolet@gmail.com")')
    print(f"Search status: {status}, IDs: {data}")

msg_ids = data[0].split() if data and data[0] else []

for msg_id in msg_ids:
    status, msg_data = mail.fetch(msg_id, "(RFC822)")
    if status != "OK":
        continue
    msg = email.message_from_bytes(msg_data[0][1])

    # Decode subject
    raw_subject = msg.get("Subject", "")
    parts = email.header.decode_header(raw_subject)
    subject = ""
    for p, enc in parts:
        if isinstance(p, bytes):
            subject += p.decode(enc or "utf-8", errors="replace")
        else:
            subject += p

    date_str = msg.get("Date", "")
    from_str = msg.get("From", "")
    print(f"\n--- MSG ID: {msg_id} ---")
    print(f"From: {from_str}")
    print(f"Date: {date_str}")
    print(f"Subject: {subject[:100] if subject else '(empty)'}")

    # List all parts
    print("Parts:")
    for i, part in enumerate(msg.walk()):
        ct = part.get_content_type()
        fn = part.get_filename()
        disp = part.get("Content-Disposition", "")
        size = len(part.get_payload(decode=True) or b"") if not part.is_multipart() else 0
        print(f"  [{i}] type={ct}, filename={fn}, disp={disp[:40]}, size={size}")

mail.logout()
