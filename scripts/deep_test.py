#!/usr/bin/env python3
"""
Deep system diagnostic for MakoletDashboard.

Tests every component end-to-end and makes each agent report its approach.
Run: python3 scripts/deep_test.py
"""

import importlib
import io
import os
import re
import stat
import subprocess
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
                       "employee_hours", "fixed_expenses", "agent_logs",
                       "pending_fetches"]

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
                    None, f'(FROM "{sender}" SINCE "{today_str}")')
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

    # --- Scheduler systemd service check (VPS only) ---
    is_vps = os.path.isdir("/opt/makolet-dashboard")
    if is_vps:
        try:
            result = subprocess.run(
                ["systemctl", "is-active", "makolet-scheduler"],
                capture_output=True, text=True, timeout=5,
            )
            active = result.stdout.strip() == "active"
            ok &= check("makolet-scheduler service active", active,
                         result.stdout.strip())
        except Exception as e:
            ok &= check("makolet-scheduler service active", False, str(e))
    else:
        print(f"  {DIM}[SKIP] makolet-scheduler service check (not on VPS){RESET}")
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
# 10.5 PENDING FETCHES
# ═══════════════════════════════════════════════════════════════════════════

def test_pending_fetches():
    header("10.5", "PENDING FETCHES")
    from database.db import get_pending_fetches, get_connection

    ok = True

    # Check table exists
    with get_connection() as conn:
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        ok &= check("Table 'pending_fetches' exists", "pending_fetches" in tables)

    pending = get_pending_fetches()
    info("Total unresolved", f"{len(pending)} pending fetch(es)")

    if pending:
        # Count per agent
        by_agent = {}
        for row in pending:
            agent = row["agent"]
            by_agent[agent] = by_agent.get(agent, 0) + 1
        print()
        for agent, count in sorted(by_agent.items()):
            info(f"  {agent}", f"{count} pending")

        print()
        print(f"  {BOLD}Unresolved pending fetches:{RESET}")
        for row in pending:
            attempts_str = f"attempts={row['attempts']}"
            reason_str = f"reason={row['reason']}" if row["reason"] else ""
            last = f"last_try={row['last_attempt_at']}" if row["last_attempt_at"] else "never tried"
            print(f"    {row['agent']:<15s}  {row['date']}  {attempts_str}  {last}  {reason_str}")
    else:
        info("Status", "all caught up")

    results["Pending Fetches"] = "pass" if ok else "fail"


# ═══════════════════════════════════════════════════════════════════════════
# 10. AGENT DEEP APPROACH REPORT
# ═══════════════════════════════════════════════════════════════════════════

def test_agent_approach():
    header(10, "AGENT DEEP APPROACH REPORT")

    # --- BILBOY ---
    print(f"  {BOLD}{CYAN}BILBOY (goods invoices){RESET}")
    info("Auth method", "JWT Bearer token via Authorization header")
    info("Token storage", "BILBOY_TOKEN in .env → os.getenv() → requests.Session header")
    info("Token renewal", "Manual OTP-based; agent raises PermissionError on 401")
    print(f"  {BOLD}API flow:{RESET}")
    info("  Step 1", "GET /user/branches → pick first branch (branchId)")
    info("  Step 2", "GET /customer/suppliers?customerBranchId=<id>&all=true → all supplier IDs")
    info("  Step 3", 'Filter out suppliers with title containing "זיכיונות המכולת"')
    info("  Step 4", "GET /customer/docs/headers?suppliers=<csv>&branches=<id>&from=<dt>&to=<dt>")
    info("  Step 5", "Parse each invoice: date, totalWithVat, supplierName, refNumber")
    info("Duplicate detection", "SELECT COUNT(*) WHERE date=? AND source='bilboy' AND amount=? AND description=?")
    info("On failure", "add_pending_fetch('bilboy', date, error) → retried on next run")
    info("On success", "resolve_pending_fetch('bilboy', date) for each saved date")
    info("Date-specific retry", "fetch_data_for_date(date) → API call with specific from/to range")
    info("DB target", "expenses table: category='goods', source='bilboy'")
    info("DB fields", "date, category, amount, description, source")
    print()

    # --- AVIV ALERTS ---
    print(f"  {BOLD}{CYAN}AVIV ALERTS (daily Z-reports / sales){RESET}")
    info("Auth method", "Gmail IMAP SSL (port 993) with App Password")
    info("Credentials", "GMAIL_ADDRESS + GMAIL_APP_PASSWORD from .env")
    print(f"  {BOLD}Email filter chain:{RESET}")
    info("  Step 1", 'FROM: AVIV_SENDER_EMAIL (.env)')
    info("  Step 2", 'SUBJECT: "דוח סוף יום"')
    info("  Step 3", "SINCE: today's date (DD-Mon-YYYY)")
    info("  Step 4", "Attachment: content-type application/pdf or application/octet-stream")
    info("  Step 5", 'Filename must start with "z_" and end with ".pdf"')
    print(f"  {BOLD}Amount extraction:{RESET}")
    info("  Source", "PDF only — NEVER trust email subject/filename amounts")
    info("  RTL regex", r'([\d,]+\.?\d*)\s*₪\s*:כ"הס')
    info("  LTR fallback", r'סה["׳]כ[:\s]+₪?\s*([\d,]+\.?\d*)')
    info("  Tool", "pdfplumber → page.extract_text() → regex match")
    print(f"  {BOLD}Z-report schedule:{RESET}")
    info("  is_z_expected()", "Sun-Fri: always; Saturday: only if last day of month")
    info("  check_missing_z_reports()", "Scans past 7 days, checks daily_sales for gaps")
    info("  Missing dates", "Registered as pending_fetches(agent='aviv_alerts', reason='Z report missing')")
    info("Duplicate detection", "Implicit — agent only searches today's emails")
    info("On failure", "add_pending_fetch('aviv_alerts', date, error) → retried on next run")
    info("On success", "resolve_pending_fetch('aviv_alerts', date)")
    info("DB target", "daily_sales table: source='aviv'")
    info("DB fields", "date, total_income, source")
    print()

    # --- ELECTRICITY ---
    print(f"  {BOLD}{CYAN}ELECTRICITY (IEC bills){RESET}")
    info("Auth method", "Gmail IMAP SSL (port 993) with App Password")
    info("Credentials", "GMAIL_ADDRESS + GMAIL_APP_PASSWORD from .env")
    print(f"  {BOLD}Email filter chain:{RESET}")
    info("  Step 1", 'Subject must contain contract "346412955"')
    info("  Step 2", 'Subject must contain "לתקופה" (billing period)')
    info("  Step 3", "Subject must NOT contain skip keywords:")
    info("          ", "שובר תשלום, קבלה, התראה בגין אי תשלום, הודעה על העברת חוב,")
    info("          ", "אישור החלפת לקוחות, אישור הצטרפות")
    print(f"  {BOLD}Date parsing:{RESET}")
    info("  Subject format", "לתקופה - DD/MM/YYYY - DD/MM/YYYY (END first, then START)")
    info("  Regex", r"לתקופה - (\d{2}/\d{2}/\d{4}) - (\d{2}/\d{2}/\d{4})")
    print(f"  {BOLD}Amount extraction:{RESET}")
    info("  Source", "PDF attachment via pdfplumber")
    info("  RTL regex", r'([\d,]+\.?\d*)\s+ןובשח תפוקתל מ"עמ ללוכ כ"הס')
    info("  PDF filename", r"^\d{4}-\d+_\d{8}_\d{6}\.pdf$ (IEC naming convention)")
    info("Correction detection", "is_correction = True when billing_days > 90")
    info("PDF storage", "Saved to data/electricity_bills/<filename>.pdf")
    info("Duplicate detection", "SELECT COUNT(*) WHERE category='electricity' AND pdf_filename=?")
    info("On failure", "add_pending_fetch('electricity', date, error) via BaseAgent")
    info("DB target", "expenses table: category='electricity', source='iec'")
    info("DB fields", "date, amount, description, period_start, period_end, billing_days, is_correction, pdf_filename")
    print()

    # --- EMPLOYEE HOURS ---
    print(f"  {BOLD}{CYAN}EMPLOYEE HOURS (monthly attendance){RESET}")
    info("Auth method", "Gmail IMAP SSL (port 993) with App Password")
    info("Credentials", "GMAIL_ADDRESS + GMAIL_APP_PASSWORD + AVIV_SENDER_EMAIL from .env")
    print(f"  {BOLD}Email filter chain:{RESET}")
    info("  Step 1", "FROM: AVIV_SENDER_EMAIL")
    info("  Step 2", 'SUBJECT: "נוכחות באקסל"')
    info("  Step 3", "SINCE: 1st of current month")
    info("  Step 4", 'Attachment: CSV filename starts with "דוח שעון נוכחות מפורט_XL_"')
    print(f"  {BOLD}CSV parsing:{RESET}")
    info("  Employee row", r"^\d+\s+(.+)$ → captures Hebrew name")
    info("  Summary row", "סה''כ שורות → last non-empty column = HH:MM total")
    info("  Time conversion", "HH:MM → decimal (e.g. 33:47 → 33.783)")
    info("  Encoding", "Tries: utf-8-sig, utf-8, cp1255, iso-8859-8")
    info("Name matching", "employees.name must match CSV name exactly")
    info("is_finalized", "Always True when saved from CSV (confirmed hours)")
    info("Schedule guard", "Only runs on days 1-5 of month; skips if already finalized")
    info("On failure", "add_pending_fetch('employee_hours', date, error) via BaseAgent")
    info("DB target", "employee_hours table via upsert_employee_hours()")
    info("DB fields", "employee_id, month, year, hours_worked, is_finalized")

    results["Agent Approach Report"] = "pass"


# ═══════════════════════════════════════════════════════════════════════════
# 11. DEEP SECURITY AUDIT
# ═══════════════════════════════════════════════════════════════════════════

def test_security():
    header(11, "DEEP SECURITY AUDIT")

    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    passed = 0
    total = 10

    # --- 1. .env not in git ---
    try:
        out = subprocess.run(
            ["git", "ls-files", ".env"],
            capture_output=True, text=True, cwd=project_root,
        ).stdout.strip()
        ok = out == ""
        passed += check("1. .env not tracked in git", ok,
                        "EXPOSED!" if not ok else "correctly gitignored")
    except Exception as e:
        check("1. .env not tracked in git", False, str(e))

    # --- 2. .gitignore coverage ---
    gitignore_path = os.path.join(project_root, ".gitignore")
    required_patterns = [".env", "*.db", "data/", "__pycache__"]
    if os.path.isfile(gitignore_path):
        with open(gitignore_path) as f:
            gitignore_content = f.read()
        missing = [p for p in required_patterns if p not in gitignore_content]
        ok = len(missing) == 0
        detail = f"missing: {', '.join(missing)}" if missing else "all present"
        passed += check("2. .gitignore coverage", ok, detail)
    else:
        check("2. .gitignore coverage", False, ".gitignore not found")

    # --- 3. No hardcoded credentials in source ---
    cred_patterns = [
        (re.compile(r'[a-zA-Z0-9_-]{50,}'), "long token/key"),
        (re.compile(r'\d{8,12}:AA[A-Za-z0-9_-]{30,}'), "Telegram bot token"),
        (re.compile(r'[a-z]{16}', re.IGNORECASE), None),  # check below
    ]
    # Load actual secrets from .env for comparison
    env_path = os.path.join(project_root, ".env")
    actual_secrets = []
    if os.path.isfile(env_path):
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if "=" in line and not line.startswith("#"):
                    val = line.split("=", 1)[1].strip().strip("'\"")
                    if len(val) >= 10:
                        actual_secrets.append(val)

    hardcoded_found = []
    py_files = []
    for dirpath, _, filenames in os.walk(project_root):
        if "__pycache__" in dirpath or ".git" in dirpath:
            continue
        for fn in filenames:
            if fn.endswith(".py"):
                full = os.path.join(dirpath, fn)
                rel = os.path.relpath(full, project_root)
                # Skip test files and .env itself
                if rel.startswith("tests/") or rel.startswith("scripts/debug"):
                    continue
                py_files.append((rel, full))

    for rel, full in py_files:
        with open(full) as f:
            content = f.read()
        for secret in actual_secrets:
            if secret in content:
                hardcoded_found.append(f"{rel}: contains actual secret value")
                break

    ok = len(hardcoded_found) == 0
    detail = "; ".join(hardcoded_found[:3]) if hardcoded_found else "clean"
    passed += check("3. No hardcoded credentials in .py", ok, detail)

    # --- 4. Flask auth enforced ---
    try:
        from dashboard.app import app
        client = app.test_client()
        protected_routes = ["/", "/fixed-expenses", "/employees", "/electricity-history"]
        auth_ok = True
        auth_details = []
        for route in protected_routes:
            resp = client.get(route)
            # Should redirect (302) or return non-200 without login
            if resp.status_code == 200:
                auth_ok = False
                auth_details.append(f"{route} returned 200 without auth")
        ok = auth_ok
        detail = "; ".join(auth_details) if auth_details else "all routes require login"
        passed += check("4. Flask auth enforced (no login)", ok, detail)
    except Exception as e:
        check("4. Flask auth enforced", False, str(e))

    # --- 5. DB file not in git ---
    try:
        out = subprocess.run(
            ["git", "ls-files", "*.db", "database/*.db"],
            capture_output=True, text=True, cwd=project_root,
        ).stdout.strip()
        ok = out == ""
        passed += check("5. DB file not tracked in git", ok,
                        f"TRACKED: {out}" if not ok else "clean")
    except Exception as e:
        check("5. DB file not tracked in git", False, str(e))

    # --- 6. Sensitive data folders not in git ---
    try:
        out = subprocess.run(
            ["git", "ls-files", "data/"],
            capture_output=True, text=True, cwd=project_root,
        ).stdout.strip()
        ok = out == ""
        passed += check("6. data/ folder not tracked in git", ok,
                        f"TRACKED: {out[:100]}" if not ok else "clean")
    except Exception as e:
        check("6. data/ folder not tracked", False, str(e))

    # --- 7. No credentials in git history (commit messages) ---
    try:
        out = subprocess.run(
            ["git", "log", "--all", "--oneline", "-20"],
            capture_output=True, text=True, cwd=project_root,
        ).stdout.lower()
        suspect_words = ["password", "token", "secret", "api_key", "apikey"]
        found_words = [w for w in suspect_words if w in out]
        ok = len(found_words) == 0
        if ok:
            passed += 1
        detail = f"found: {', '.join(found_words)}" if found_words else "clean"
        status = PASS if ok else WARN
        suffix = f"  {DIM}({detail}){RESET}"
        print(f"  {'[' + status + ']':<20s} 7. No credential keywords in commit messages{suffix}")
    except Exception as e:
        check("7. No credentials in commit messages", False, str(e))

    # --- 8. .env file permissions ---
    if os.path.isfile(env_path):
        file_stat = os.stat(env_path)
        mode = stat.S_IMODE(file_stat.st_mode)
        mode_str = oct(mode)
        # 0o600 = owner read/write only
        ok = mode <= 0o600
        if ok:
            passed += 1
        status = PASS if ok else WARN
        detail = f"mode={mode_str}" + ("" if ok else " (recommend chmod 600)")
        suffix = f"  {DIM}({detail}){RESET}"
        print(f"  {'[' + status + ']':<20s} 8. .env file permissions{suffix}")
    else:
        check("8. .env file permissions", False, ".env not found")

    # --- 9. Telegram token not hardcoded ---
    telegram_token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    token_found = []
    if telegram_token:
        for rel, full in py_files:
            with open(full) as f:
                if telegram_token in f.read():
                    token_found.append(rel)
    ok = len(token_found) == 0
    detail = f"FOUND IN: {', '.join(token_found)}" if token_found else "clean"
    passed += check("9. Telegram token not hardcoded", ok, detail)

    # --- 10. IMAP credentials not in source ---
    gmail_addr = os.getenv("GMAIL_ADDRESS", "")
    gmail_pass = os.getenv("GMAIL_APP_PASSWORD", "")
    imap_found = []
    for rel, full in py_files:
        with open(full) as f:
            content = f.read()
        if gmail_addr and gmail_addr in content:
            imap_found.append(f"{rel}: Gmail address")
        if gmail_pass and gmail_pass in content:
            imap_found.append(f"{rel}: App password")
    ok = len(imap_found) == 0
    detail = f"FOUND: {'; '.join(imap_found)}" if imap_found else "clean"
    passed += check("10. IMAP credentials not in source", ok, detail)

    # --- Final score ---
    print()
    color = GREEN if passed == total else (YELLOW if passed >= 7 else RED)
    print(f"  {BOLD}Security score: {color}{passed}/{total} checks passed{RESET}")

    results["Security Audit"] = "pass" if passed == total else ("warn" if passed >= 7 else "fail")


# ═══════════════════════════════════════════════════════════════════════════
# 12. SUMMARY
# ═══════════════════════════════════════════════════════════════════════════

def print_summary():
    header(12, "SUMMARY")

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
    test_pending_fetches()
    test_agent_approach()
    test_security()
    print_summary()
