"""
Parse Aviv POS attendance CSV (tab-separated, Hebrew).

Format:
    Employee block starts with: "382 רועי אמסלם" (ID + space + name)
    Following rows: same employee's daily entries (empty first column)
    Summary row: "סה''כ שורות  7  ...  33:47:00"
    Total hours in LAST column, format HH:MM:SS

Usage:
    from agents.parse_attendance_csv import parse_attendance_csv
    results = parse_attendance_csv("path/to/file.csv")
"""

import re
import io


# Matches employee ID + name at start of block: "382 רועי אמסלם"
_EMPLOYEE_RE = re.compile(r'^\d+\s+(.+)')

# Summary row marker
_SUMMARY_PREFIX = "סה''כ שורות"

# HH:MM:SS or HH:MM
_TIME_RE = re.compile(r'(\d+):(\d{2})(?::(\d{2}))?')


def _parse_hms(raw: str) -> float:
    """Convert HH:MM:SS or HH:MM to decimal hours."""
    m = _TIME_RE.search(raw.strip())
    if not m:
        return 0.0
    hours = int(m.group(1))
    minutes = int(m.group(2))
    seconds = int(m.group(3)) if m.group(3) else 0
    return round(hours + minutes / 60 + seconds / 3600, 3)


def parse_attendance_csv(path_or_bytes) -> list[dict]:
    """
    Parse attendance CSV file.

    Args:
        path_or_bytes: file path (str) or bytes/BytesIO content

    Returns:
        List of dicts: { 'name': str, 'hours': float, 'raw_hours': str }
    """
    # Read content
    if isinstance(path_or_bytes, (bytes, bytearray)):
        raw_bytes = path_or_bytes
    elif hasattr(path_or_bytes, 'read'):
        raw_bytes = path_or_bytes.read()
    else:
        with open(path_or_bytes, 'rb') as f:
            raw_bytes = f.read()

    # Try encodings
    text = None
    for enc in ('utf-8-sig', 'utf-8', 'windows-1255', 'cp1255', 'iso-8859-8'):
        try:
            text = raw_bytes.decode(enc)
            break
        except (UnicodeDecodeError, LookupError):
            continue

    if text is None:
        raise ValueError("Cannot decode CSV file with any supported encoding")

    results = []
    current_name = None

    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue

        # Split by tab
        cols = line.split('\t')
        first = cols[0].strip()

        # Check for summary row
        if _SUMMARY_PREFIX in first or _SUMMARY_PREFIX in line:
            if current_name:
                # Total hours is in the LAST non-empty column
                raw_hours = ""
                for col in reversed(cols):
                    col = col.strip()
                    if col and _TIME_RE.search(col):
                        raw_hours = col.strip()
                        break
                hours = _parse_hms(raw_hours)
                results.append({
                    'name': current_name,
                    'hours': hours,
                    'raw_hours': raw_hours,
                })
                current_name = None
            continue

        # Check for new employee block
        m = _EMPLOYEE_RE.match(first)
        if m:
            current_name = m.group(1).strip()

    return results


if __name__ == '__main__':
    import sys
    if len(sys.argv) > 1:
        results = parse_attendance_csv(sys.argv[1])
        for r in results:
            print(f"{r['name']}: {r['raw_hours']} = {r['hours']:.2f} hrs")
    else:
        # Inline test with expected sample data
        sample = (
            "עובד\tיום בשבוע\tתאריך כניסה\tתאריך יציאה\tהערות\tכמות שעות\n"
            "382 רועי אמסלם\tראשון\t01/02/2026 06:30\t01/02/2026 17:00\t\t10:30:00\n"
            "\tשני\t02/02/2026 06:30\t02/02/2026 14:00\t\t07:30:00\n"
            "סה''כ שורות\t7\t\t\t\t33:47:00\n"
            "501 דיאנה דוחוניקוב איינשטיין\tראשון\t01/02/2026 06:30\t01/02/2026 17:00\t\t10:30:00\n"
            "סה''כ שורות\t25\t\t\t\t126:26:00\n"
            "502 עמוס גולד איינשטיין\tראשון\t01/02/2026 06:30\t01/02/2026 14:00\t\t07:30:00\n"
            "סה''כ שורות\t6\t\t\t\t32:09:00\n"
            "503 דניאל מור יוסף איינשטיין\tראשון\t01/02/2026 06:30\t01/02/2026 17:00\t\t10:30:00\n"
            "סה''כ שורות\t10\t\t\t\t60:00:00\n"
            "504 יהונתן שטיינר אינשטיין\tראשון\t01/02/2026 06:30\t01/02/2026 14:00\t\t07:30:00\n"
            "סה''כ שורות\t6\t\t\t\t31:49:00\n"
        )
        results = parse_attendance_csv(sample.encode('utf-8-sig'))
        print("=== Inline Test ===")
        for r in results:
            print(f"{r['name']}: {r['raw_hours']} = {r['hours']:.2f} hrs")

        # Verify expected
        expected = {
            'רועי אמסלם': 33.783,
            'דיאנה דוחוניקוב איינשטיין': 126.433,
            'עמוס גולד איינשטיין': 32.15,
            'דניאל מור יוסף איינשטיין': 60.0,
            'יהונתן שטיינר אינשטיין': 31.817,
        }
        ok = True
        for r in results:
            exp = expected.get(r['name'])
            if exp is None:
                print(f"  UNEXPECTED: {r['name']}")
                ok = False
            elif abs(r['hours'] - exp) > 0.01:
                print(f"  MISMATCH: {r['name']} got {r['hours']:.3f}, expected {exp}")
                ok = False
        if len(results) != len(expected):
            print(f"  COUNT MISMATCH: got {len(results)}, expected {len(expected)}")
            ok = False
        print(f"\n{'PASS' if ok else 'FAIL'}")
