#!/usr/bin/env python3
"""
Deep system diagnostic for MakoletDashboard.

Tests every component end-to-end and makes each agent report its approach.
Run: python3 scripts/deep_test.py
"""

import importlib
import io
import os
import sys
from datetime import date, datetime

from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
load_dotenv()

# ---------------------------------------------------------------------------
# Styling helpers
# ---------------------------------------------------------------------------

GREEN  = "\033[92m"
RED    = "\033[91m"
YELLOW = "\033[93m"
CYAN   = "\033[96m"
BOLD   = "\033[1m"
DIM    = "\033[2m"
RESET  = "\033[0m"

PASS = f"{GREEN}PASS{RESET}"
FAIL = f"{RED}FAIL{RESET}"
WARN = f"{YELLOW}WARN{RESET}"

results = {}  # section_name → "pass" | "fail" | "warn"


def header(num: int, title: str):
    print(f"\n{BOLD}{CYAN}{'━' * 60}{RESET}")
    print(f"{BOLD}{CYAN}  {num}. {title}{RESET}")
    print(f"{BOLD}{CYAN}{'━' * 60}{RESET}\n")


def check(label: str, condition: bool, detail: str = "") -> bool:
    status = PASS if condition else FAIL
    suffix = f"  {DIM}({detail}){RESET}" if detail else ""
    print(f"  {'[' + status + ']':<20s} {label}{suffix}")
    return condition


def info(label: str, value):
    print(f"  {DIM}{label}:{RESET} {value}")


# ═══════════════════════════════════════════════════════════════════════════
# 1. DATABASE
# ═══════════════════════════════════════════════════════════════════════════

def test_database():
    header(1, "DATABASE")
    from database.db import (
        get_connection, init_db,
        calculate_estimated_profit,
        get_electricity_monthly_estimate,
        get_electricity_estimate_for_month,
    )

    ok = True
    init_db()

    expected_tables = ["daily_sales", "expenses", "employees",
                       "employee_hours", "fixed_expenses", "agent_logs"]

    with get_connection() as conn:
        existing = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}

        for t in expected_tables:
            ok &= check(f"Table '{t}' exists", t in existing)

        print()
        for t in expected_tables:
            count = conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            info(f"  {t}", f"{count} rows")

    print()
    today = date.today()
    try:
        profit = calculate_estimated_profit(today.month, today.year)
        ok &= check("calculate_estimated_profit()", isinstance(profit, dict),
                     f"profit={profit.get('profit', '?'):.2f}")
    except Exception as e:
        ok &= check("calculate_estimated_profit()", False, str(e))

    try:
        est = get_electricity_monthly_estimate()
        ok &= check("get_electricity_monthly_estimate()", est is None or isinstance(est, float),
                     f"estimate={est}")
    except Exception as e:
        ok &= check("get_electricity_monthly_estimate()", False, str(e))

    try:
        elec = get_electricity_estimate_for_month(today.year, today.month)
        ok &= check("get_electricity_estimate_for_month()",
                     elec is None or isinstance(elec, dict),
                     f"{elec}")
    except Exception as e:
        ok &= check("get_electricity_estimate_for_month()", False, str(e))

    results["Database"] = "pass" if ok else "fail"


# ═══════════════════════════════════════════════════════════════════════════
# 2. BILBOY AGENT
# ═══════════════════════════════════════════════════════════════════════════

def test_bilboy():
    header(2, "AGENT: BILBOY")
    from agents.bilboy import BilBoyAgent, API_BASE

    ok = True
    agent = BilBoyAgent()

    info("API base", API_BASE)
    info("Endpoint: branches", "GET /user/branches")
    info("Endpoint: suppliers", "GET /customer/suppliers?customerBranchId=<id>&all=true")
    info("Endpoint: invoices", "GET /customer/docs/headers?suppliers=<csv>&branches=<id>&from=<dt>&to=<dt>")
    info("Franchise filter", 'Excludes suppliers with title containing "זיכיונות המכולת"')
    info("Duplicate check", "SELECT COUNT(*) WHERE date=? AND source='bilboy' AND amount=? AND description=?")
    info("Amount field", "totalWithVat (includes VAT)")
    print()

    token = os.getenv("BILBOY_TOKEN", "")
    if not token:
        ok &= check("BILBOY_TOKEN set", False, "not in .env")
        results["BilBoy Agent"] = "fail"
        return

    ok &= check("BILBOY_TOKEN set", True)

    try:
        branch_id = agent._get_branch_id()
        ok &= check("Get branch ID", bool(branch_id), f"branch_id={branch_id}")
    except Exception as e:
        ok &= check("Get branch ID", False, str(e))
        results["BilBoy Agent"] = "fail"
        return

    try:
        suppliers_csv, skipped = agent._get_supplier_ids(branch_id)
        n_suppliers = len(suppliers_csv.split(",")) if suppliers_csv else 0
        ok &= check("Get suppliers", n_suppliers > 0,
                     f"{n_suppliers} active, {len(skipped)} franchise filtered")
        if skipped:
            for s in skipped:
                info("  Filtered", s)
    except Exception as e:
        ok &= check("Get suppliers", False, str(e))

    try:
        from datetime import timedelta
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        invoices = agent._get_invoice_headers(branch_id, suppliers_csv,
                                              from_date=yesterday, to_date=yesterday)
        ok &= check("Fetch yesterday invoices", isinstance(invoices, list),
                     f"{len(invoices)} invoices for {yesterday}")
    except Exception as e:
        ok &= check("Fetch yesterday invoices", False, str(e))

    results["BilBoy Agent"] = "pass" if ok else "fail"


# ═══════════════════════════════════════════════════════════════════════════
# 3. AVIV ALERTS AGENT
# ═══════════════════════════════════════════════════════════════════════════

def test_aviv():
    header(3, "AGENT: AVIV ALERTS")
    from agents.aviv_alerts import (
        AvivAlertsAgent, TOTAL_PATTERN_RTL, TOTAL_PATTERN_LTR,
        is_z_expected, check_missing_z_reports,
    )

    ok = True
    info("Amount source", "PDF only (NEVER filename/subject)")
    info("RTL regex", TOTAL_PATTERN_RTL.pattern)
    info("LTR regex (fallback)", TOTAL_PATTERN_LTR.pattern)
    info("PDF detection", "application/pdf + application/octet-stream, filename starts with z_")
    print()

    gmail_addr = os.getenv("GMAIL_ADDRESS", "")
    gmail_pass = os.getenv("GMAIL_APP_PASSWORD", "")
    if not gmail_addr or not gmail_pass:
        ok &= check("Gmail credentials set", False, "GMAIL_ADDRESS or GMAIL_APP_PASSWORD missing")
    else:
        ok &= check("Gmail credentials set", True)
        try:
            import imaplib
            mail = imaplib.IMAP4_SSL("imap.gmail.com", 993)
            mail.login(gmail_addr, gmail_pass)
            mail.select("inbox")
            status, data = mail.search(None, "ALL")
            total_emails = len(data[0].split()) if data and data[0] else 0
            ok &= check("IMAP connection", True, f"{total_emails} emails in inbox")

            sender = os.getenv("AVIV_SENDER_EMAIL", "")
            if sender:
                today_str = date.today().strftime("%d-%b-%Y")
                status, data = mail.search(
                    None, f'(FROM "{sender}" SINCE "{today_str}" SUBJECT "דוח סוף יום")')
                z_count = len(data[0].split()) if data and data[0] else 0
                ok &= check("Today's Z-report emails", True, f"{z_count} found")
            mail.logout()
        except Exception as e:
            ok &= check("IMAP connection", False, str(e))

    # is_z_expected tests
    print()
    info("Z-report schedule", "Sun-Fri always; Saturday only if last day of month")
    test_dates = [
        (date(2026, 3, 6),  True,  "Friday"),
        (date(2026, 3, 7),  False, "Saturday, not month-end"),
        (date(2026, 3, 8),  True,  "Sunday"),
        (date(2026, 3, 28), False, "Saturday, not month-end"),
        (date(2026, 3, 31), True,  "Tuesday"),
        (date(2026, 1, 31), True,  "Saturday, last day of Jan"),
    ]
    for d, expected, label in test_dates:
        actual = is_z_expected(d)
        ok &= check(f"is_z_expected({d}) = {actual}", actual == expected,
                     f"{label}")

    # Missing Z-reports
    print()
    try:
        missing = check_missing_z_reports()
        ok &= check("check_missing_z_reports()", True,
                     f"{len(missing)} missing" if missing else "all present")
        for d in missing:
            info("  Missing", d)
    except Exception as e:
        ok &= check("check_missing_z_reports()", False, str(e))

    results["Aviv Alerts Agent"] = "pass" if ok else "fail"


# ═══════════════════════════════════════════════════════════════════════════
# 4. ELECTRICITY AGENT
# ═══════════════════════════════════════════════════════════════════════════

def test_electricity():
    header(4, "AGENT: ELECTRICITY")
    from agents.electricity import (
        ElectricityAgent, CONTRACT_NUMBER, SKIP_SUBJECTS,
        DATE_PATTERN, AMOUNT_PATTERN,
    )
    from database.db import get_connection

    ok = True
    info("Sender email", "noreplys@iec.co.il (forwarded to Gmail)")
    info("Contract number", CONTRACT_NUMBER)
    info("Skip subjects", ", ".join(SKIP_SUBJECTS))
    info("Date regex", DATE_PATTERN.pattern)
    info("Amount regex (RTL)", AMOUNT_PATTERN.pattern)
    print()

    gmail_addr = os.getenv("GMAIL_ADDRESS", "")
    gmail_pass = os.getenv("GMAIL_APP_PASSWORD", "")
    if gmail_addr and gmail_pass:
        try:
            import imaplib
            mail = imaplib.IMAP4_SSL("imap.gmail.com", 993)
            mail.login(gmail_addr, gmail_pass)
            mail.select("inbox")
            status, data = mail.search(None, f'(SUBJECT "{CONTRACT_NUMBER}")')
            count = len(data[0].split()) if data and data[0] else 0
            ok &= check("IMAP search for electricity emails", True,
                         f"{count} emails matching contract {CONTRACT_NUMBER}")
            mail.logout()
        except Exception as e:
            ok &= check("IMAP search", False, str(e))
    else:
        ok &= check("Gmail credentials", False, "not set")

    # DB bills
    with get_connection() as conn:
        bills = conn.execute(
            "SELECT * FROM expenses WHERE category='electricity' ORDER BY date DESC"
        ).fetchall()
        ok &= check("Electricity bills in DB", len(bills) > 0, f"{len(bills)} bills")
        if bills:
            latest = bills[0]
            info("  Latest bill", f"{latest['date']} | amount={latest['amount']:.2f}")
            oldest = bills[-1]
            info("  Oldest bill", f"{oldest['date']}")
            info("  Date range", f"{oldest['date']} to {latest['date']}")

    results["Electricity Agent"] = "pass" if ok else "fail"


# ═══════════════════════════════════════════════════════════════════════════
# 5. EMPLOYEE HOURS AGENT
# ═══════════════════════════════════════════════════════════════════════════

def test_employee_hours():
    header(5, "AGENT: EMPLOYEE HOURS")
    from database.db import get_connection, get_employee_hours, get_active_employees

    ok = True
    info("Email subject", 'Contains "נוכחות באקסל"')
    info("CSV filename prefix", 'דוח שעון נוכחות מפורט_XL_')
    info("Time conversion", 'HH:MM → decimal hours (e.g. 33:47 → 33.783)')
    info("is_finalized", "Set when hours are confirmed for the month")
    info("Schedule", "Only runs on days 1-5 of month, skips if already finalized")
    print()

    gmail_addr = os.getenv("GMAIL_ADDRESS", "")
    gmail_pass = os.getenv("GMAIL_APP_PASSWORD", "")
    if gmail_addr and gmail_pass:
        try:
            import imaplib
            mail = imaplib.IMAP4_SSL("imap.gmail.com", 993)
            mail.login(gmail_addr, gmail_pass)
            mail.select("inbox")
            sender = os.getenv("AVIV_SENDER_EMAIL", "")
            sender = os.getenv("AVIV_SENDER_EMAIL", "")
            month_start = date.today().replace(day=1).strftime("%d-%b-%Y")
            if sender:
                status, data = mail.search(
                    None, f'(FROM "{sender}" SINCE "{month_start}")')
            else:
                status, data = mail.search(None, f'(SINCE "{month_start}")')
            count = len(data[0].split()) if data and data[0] else 0
            ok &= check("IMAP search for recent emails from sender", True,
                         f"{count} emails this month from sender")
            mail.logout()
        except Exception as e:
            ok &= check("IMAP search", False, str(e))
    else:
        ok &= check("Gmail credentials", False, "not set")

    today = date.today()
    employees = get_active_employees()
    ok &= check("Active employees in DB", True, f"{len(employees)} employees")
    for emp in employees:
        info(f"  {emp['name']}", f"₪{emp['hourly_rate']}/hr")

    hours = get_employee_hours(today.month, today.year)
    if hours:
        print()
        info("Current month hours", f"{len(hours)} records")
        for h in hours:
            status_str = "finalized" if h["is_finalized"] else "estimated"
            info(f"  {h['name']}", f"{h['hours_worked']:.1f} hrs ({status_str})")
    else:
        info("Current month hours", "no records yet")

    results["Employee Hours Agent"] = "pass" if ok else "fail"


# ═══════════════════════════════════════════════════════════════════════════
# 6. NOTIFICATIONS
# ═══════════════════════════════════════════════════════════════════════════

def test_notifications():
    header(6, "NOTIFICATIONS")
    from notifications.whatsapp import send_alert, _is_send_window

    ok = True
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()

    info("Telegram bot token", "set" if token else "(not set)")
    info("Telegram chat ID", chat_id if chat_id else "(not set)")
    info("Time restriction", "08:00-22:00 Israel time")
    info("Currently in window", "yes" if _is_send_window() else "no")
    print()

    ok &= check("TELEGRAM_BOT_TOKEN set", bool(token))
    ok &= check("TELEGRAM_CHAT_ID set", bool(chat_id))

    if token and chat_id:
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        try:
            send_alert(f"🧪 MakoletDashboard deep test - {now_str}")
            ok &= check("Send test Telegram", True, "message sent (or outside window)")
        except Exception as e:
            ok &= check("Send test Telegram", False, str(e))
    else:
        info("Send test", "skipped (credentials missing)")

    results["Notifications"] = "pass" if ok else ("warn" if not token else "fail")


# ═══════════════════════════════════════════════════════════════════════════
# 7. SCHEDULER
# ═══════════════════════════════════════════════════════════════════════════

def test_scheduler():
    header(7, "SCHEDULER")

    ok = True
    info("Schedule", "Nightly at 02:00 Asia/Jerusalem")
    info("Agents", "bilboy + aviv_alerts always; employee_hours days 1-5 only")
    info("Z-report check", "After aviv_alerts, checks past 7 days for missing reports")
    info("Timezone", "Asia/Jerusalem")
    print()

    try:
        # Import just the source without triggering apscheduler at module level
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "scheduler_src",
            os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scheduler.py"),
        )
        source = spec.loader.get_data(spec.origin).decode("utf-8")
        ok &= check("scheduler.py exists", True)
        ok &= check("nightly_job() defined", "def nightly_job" in source)
        ok &= check("BilBoyAgent used", "BilBoyAgent" in source)
        ok &= check("AvivAlertsAgent used", "AvivAlertsAgent" in source)
        ok &= check("check_missing_z_reports imported",
                     "check_missing_z_reports" in source)
        ok &= check("send_alert imported", "send_alert" in source)
        ok &= check("employee_hours days 1-5 guard", "today.day <= 5" in source)
        ok &= check("Cron at 02:00", "hour=2" in source)
        ok &= check("Timezone Asia/Jerusalem", "Asia/Jerusalem" in source)
    except Exception as e:
        ok &= check("Scheduler source", False, str(e))

    results["Scheduler"] = "pass" if ok else "fail"


# ═══════════════════════════════════════════════════════════════════════════
# 8. FLASK DASHBOARD
# ═══════════════════════════════════════════════════════════════════════════

def test_flask():
    header(8, "FLASK DASHBOARD")

    ok = True
    try:
        from dashboard.app import app
        ok &= check("Flask app imports", True)
    except Exception as e:
        ok &= check("Flask app imports", False, str(e))
        results["Flask Dashboard"] = "fail"
        return

    client = app.test_client()

    # Login first
    admin_user = os.getenv("ADMIN_USERNAME", "")
    admin_pass = os.getenv("ADMIN_PASSWORD", "")

    if admin_user and admin_pass:
        resp = client.post("/login", data={
            "username": admin_user,
            "password": admin_pass,
        }, follow_redirects=False)
        logged_in = resp.status_code in (200, 302)
        ok &= check("Login", logged_in, f"status={resp.status_code}")
    else:
        ok &= check("Login credentials", False, "ADMIN_USERNAME/ADMIN_PASSWORD not set")
        results["Flask Dashboard"] = "warn"
        return

    routes = [
        ("GET", "/",                       200),
        ("GET", "/fixed-expenses",         200),
        ("GET", "/employees",              200),
        ("GET", "/electricity-history",    200),
        ("GET", "/api/summary",            200),
        ("GET", "/api/history",            200),
        ("GET", "/api/electricity/estimate", 200),
        ("GET", "/api/electricity/bills",  200),
        ("GET", "/api/fixed-expenses",     200),
        ("GET", "/api/employees",          200),
    ]

    for method, path, expected in routes:
        resp = client.get(path) if method == "GET" else client.post(path)
        ok &= check(f"{method} {path}", resp.status_code == expected,
                     f"status={resp.status_code}")
        # Verify JSON for API routes
        if "/api/" in path and resp.status_code == 200:
            try:
                resp.get_json(force=True)
            except Exception:
                ok &= check(f"  {path} valid JSON", False)

    results["Flask Dashboard"] = "pass" if ok else "fail"


# ═══════════════════════════════════════════════════════════════════════════
# 9. DATA INTEGRITY
# ═══════════════════════════════════════════════════════════════════════════

def test_data_integrity():
    header(9, "DATA INTEGRITY")
    from database.db import (
        get_connection, get_electricity_monthly_estimate,
        get_electricity_estimate_for_month, get_all_fixed_expenses,
    )

    ok = True
    today = date.today()

    with get_connection() as conn:
        # Daily sales for March
        rows = conn.execute(
            "SELECT date, total_income, source FROM daily_sales "
            "WHERE date LIKE '2026-03%' ORDER BY date"
        ).fetchall()
        print(f"  {BOLD}Daily sales (March 2026):{RESET}")
        if rows:
            for r in rows:
                print(f"    {r['date']}  {r['total_income']:>12,.2f}  {r['source']}")
            total = sum(r["total_income"] for r in rows)
            print(f"    {'TOTAL':<10s}  {total:>12,.2f}  ({len(rows)} days)")
        else:
            print(f"    (no records)")
        print()

        # Goods expenses for March
        goods = conn.execute(
            "SELECT COALESCE(SUM(amount), 0) as total, COUNT(*) as cnt "
            "FROM expenses WHERE category='goods' AND date LIKE '2026-03%'"
        ).fetchone()
        info("Goods expenses (March)", f"₪{goods['total']:,.2f} ({goods['cnt']} invoices)")

        # Electricity
        latest_bill = conn.execute(
            "SELECT * FROM expenses WHERE category='electricity' ORDER BY date DESC LIMIT 1"
        ).fetchone()
        if latest_bill:
            info("Latest electricity bill",
                 f"{latest_bill['date']} | ₪{latest_bill['amount']:,.2f}")
        est = get_electricity_estimate_for_month(today.year, today.month)
        if est:
            info("Current month estimate", f"₪{est['estimate']:,.2f} "
                 f"({'estimate' if est.get('is_estimate') else 'actual'})")
        print()

        # Fixed expenses
        print(f"  {BOLD}Fixed expenses:{RESET}")
        fixed = get_all_fixed_expenses()
        for f in fixed:
            valid = f"from {f['valid_from']}"
            if f["valid_until"]:
                valid += f" to {f['valid_until']}"
            print(f"    {f['category']:<12s}  ₪{f['amount']:>10,.2f}  {valid}  {f['notes'] or ''}")
        print()

        # Duplicate check
        dupes = conn.execute(
            "SELECT date, source, COUNT(*) as cnt FROM daily_sales "
            "GROUP BY date, source HAVING cnt > 1"
        ).fetchall()
        ok &= check("No duplicate daily_sales", len(dupes) == 0,
                     f"{len(dupes)} duplicates" if dupes else "clean")
        for d in dupes:
            info("  Duplicate", f"{d['date']} source={d['source']} count={d['cnt']}")

        dupes_exp = conn.execute(
            "SELECT date, source, amount, description, COUNT(*) as cnt FROM expenses "
            "WHERE source='bilboy' "
            "GROUP BY date, source, amount, description HAVING cnt > 1"
        ).fetchall()
        ok &= check("No duplicate bilboy expenses", len(dupes_exp) == 0,
                     f"{len(dupes_exp)} duplicates" if dupes_exp else "clean")

    results["Data Integrity"] = "pass" if ok else "fail"


# ═══════════════════════════════════════════════════════════════════════════
# 10. SUMMARY
# ═══════════════════════════════════════════════════════════════════════════

def print_summary():
    header(10, "SUMMARY")

    icon = {"pass": f"{GREEN}PASS{RESET}", "fail": f"{RED}FAIL{RESET}", "warn": f"{YELLOW}WARN{RESET}"}
    max_len = max(len(k) for k in results)

    print(f"  {'Component':<{max_len + 2}}  Status")
    print(f"  {'-' * (max_len + 2)}  ------")
    for name, status in results.items():
        print(f"  {name:<{max_len + 2}}  {icon.get(status, status)}")

    total = len(results)
    passed = sum(1 for v in results.values() if v == "pass")
    failed = sum(1 for v in results.values() if v == "fail")
    warned = sum(1 for v in results.values() if v == "warn")
    print()
    print(f"  {BOLD}{passed}/{total} passed{RESET}", end="")
    if failed:
        print(f", {RED}{failed} failed{RESET}", end="")
    if warned:
        print(f", {YELLOW}{warned} warnings{RESET}", end="")
    print()


# ═══════════════════════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    print(f"\n{BOLD}{'=' * 60}{RESET}")
    print(f"{BOLD}  MakoletDashboard — Deep System Diagnostic{RESET}")
    print(f"{BOLD}  {date.today().isoformat()} {datetime.now().strftime('%H:%M:%S')}{RESET}")
    print(f"{BOLD}{'=' * 60}{RESET}")

    test_database()
    test_bilboy()
    test_aviv()
    test_electricity()
    test_employee_hours()
    test_notifications()
    test_scheduler()
    test_flask()
    test_data_integrity()
    print_summary()
