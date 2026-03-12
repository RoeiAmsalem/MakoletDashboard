"""
One-time script: download Z-report PDFs from the "השלמות Z" email in Gmail.

Attachment mapping:
    z_2459.pdf   → z_2026-03-01.pdf
    העתק Z.pdf   → z_2026-03-02.pdf
    z_2461.pdf   → z_2026-03-03.pdf
    z_2463.pdf   → z_2026-03-04.pdf  (also covers Mar 5)
    z_2464.pdf   → z_2026-03-06.pdf
"""

import email
import email.header
import imaplib
import os
import sys

from dotenv import load_dotenv

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _PROJECT_ROOT)
load_dotenv(os.path.join(_PROJECT_ROOT, ".env"))

IMAP_HOST = "imap.gmail.com"
IMAP_PORT = 993
Z_PDFS_DIR = os.path.join(_PROJECT_ROOT, "data", "z_pdfs")

# Map original attachment filename → target filename
ATTACHMENT_MAP = {
    "z_2459.pdf": "z_2026-03-01.pdf",
    "העתק Z.pdf": "z_2026-03-02.pdf",
    "z_2461.pdf": "z_2026-03-03.pdf",
    "z_2463.pdf": "z_2026-03-04.pdf",
    "z_2464.pdf": "z_2026-03-06.pdf",
}

# Mar 5 uses the same PDF as Mar 4
COPY_MAP = {
    "z_2026-03-05.pdf": "z_2026-03-04.pdf",
}


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


def main():
    os.makedirs(Z_PDFS_DIR, exist_ok=True)

    gmail_address = os.getenv("GMAIL_ADDRESS", "")
    gmail_password = os.getenv("GMAIL_APP_PASSWORD", "")

    print(f"Connecting to Gmail as {gmail_address}...")
    mail = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT)
    mail.login(gmail_address, gmail_password)
    mail.select("inbox")

    # Use Gmail's X-GM-RAW extension for searching with "has:attachment filename:z_24"
    # This avoids Hebrew encoding issues and targets the right emails
    status, data = mail.search(None, 'X-GM-RAW "has:attachment filename:z_24"')
    if status != "OK" or not data or not data[0]:
        # Fallback: search recent emails broadly
        print("X-GM-RAW search found nothing, trying SINCE search...")
        status, data = mail.search(None, '(SINCE "01-Mar-2026")')
        if status != "OK" or not data or not data[0]:
            print("ERROR: No emails found")
            mail.logout()
            return

    msg_ids = data[0].split()
    print(f"Found {len(msg_ids)} candidate email(s), scanning for Z PDFs...")

    saved = 0
    remaining = dict(ATTACHMENT_MAP)

    for msg_id in msg_ids:
        status, msg_data = mail.fetch(msg_id, "(RFC822)")
        if status != "OK":
            continue

        msg = email.message_from_bytes(msg_data[0][1])

        for part in msg.walk():
            ct = part.get_content_type()
            if ct not in ("application/pdf", "application/octet-stream"):
                continue

            raw_fn = part.get_filename() or ""
            filename = _decode_filename(raw_fn)

            if filename in remaining:
                target_name = remaining[filename]
                pdf_bytes = part.get_payload(decode=True)
                target_path = os.path.join(Z_PDFS_DIR, target_name)
                with open(target_path, "wb") as f:
                    f.write(pdf_bytes)
                print(f"  Saved: {filename} → {target_name} ({len(pdf_bytes)} bytes)")
                saved += 1
                del remaining[filename]

    mail.logout()

    # Copy Mar 4 → Mar 5
    for target, source in COPY_MAP.items():
        src_path = os.path.join(Z_PDFS_DIR, source)
        dst_path = os.path.join(Z_PDFS_DIR, target)
        if os.path.isfile(src_path):
            import shutil
            shutil.copy2(src_path, dst_path)
            print(f"  Copied: {source} → {target}")
            saved += 1
        else:
            print(f"  WARNING: Cannot copy {source} → {target} (source not found)")

    print(f"\nDone: {saved} files saved to {Z_PDFS_DIR}")
    if remaining:
        print(f"WARNING: Could not find attachments: {list(remaining.keys())}")

    # List results
    print("\nFiles in z_pdfs/:")
    for f in sorted(os.listdir(Z_PDFS_DIR)):
        size = os.path.getsize(os.path.join(Z_PDFS_DIR, f))
        print(f"  {f}  ({size:,} bytes)")


if __name__ == "__main__":
    main()
