"""Debug script: extract and print all text from IEC electricity bill PDFs."""

import re
import pdfplumber

PDF_PATH = "data/electricity_bills/2025-401912582_20251123_203258.pdf"
AMOUNT_PATTERN = re.compile(r'סה"כ כולל מע"מ לתקופת חשבון\s*([\d,]+\.?\d*)')

print(f"=== Reading: {PDF_PATH} ===\n")

with pdfplumber.open(PDF_PATH) as pdf:
    for i, page in enumerate(pdf.pages, 1):
        text = page.extract_text() or "(no text extracted)"
        print(f"--- Page {i} ---")
        print(text)
        print()

    # Now try the regex on all pages combined
    print("=" * 60)
    print("=== Regex test ===")
    print(f"Pattern: {AMOUNT_PATTERN.pattern}\n")

    for i, page in enumerate(pdf.pages, 1):
        text = page.extract_text() or ""
        m = AMOUNT_PATTERN.search(text)
        if m:
            print(f"MATCH on page {i}: amount = {m.group(1)}")
        else:
            print(f"No match on page {i}")

    # The text is extracted in visual RTL order:
    #   סה"כ → כ"הס
    #   מע"מ → מ"עמ
    # And the amount appears BEFORE the label text.
    print("\n=== Broader search: lines containing מ\"עמ (RTL of מע\"מ) ===")
    for i, page in enumerate(pdf.pages, 1):
        text = page.extract_text() or ""
        for line in text.split("\n"):
            if 'מ"עמ' in line:
                print(f"  Page {i}: {line.strip()}")

    print("\n=== Broader search: lines containing כ\"הס (RTL of סה\"כ) ===")
    for i, page in enumerate(pdf.pages, 1):
        text = page.extract_text() or ""
        for line in text.split("\n"):
            if 'כ"הס' in line:
                print(f"  Page {i}: {line.strip()}")

    # Try corrected regex: amount BEFORE the RTL label
    FIXED_PATTERN = re.compile(r'([\d,]+\.?\d*)\s+ןובשח תפוקתל מ"עמ ללוכ כ"הס')
    print("\n=== Fixed regex test ===")
    print(f"Pattern: {FIXED_PATTERN.pattern}\n")
    for i, page in enumerate(pdf.pages, 1):
        text = page.extract_text() or ""
        m = FIXED_PATTERN.search(text)
        if m:
            print(f"MATCH on page {i}: amount = {m.group(1)}")
