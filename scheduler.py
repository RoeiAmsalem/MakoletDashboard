"""
Nightly scheduler for MakoletDashboard agents.

Schedule (Asia/Jerusalem timezone):
    02:00 every night — run bilboy + aviv_alerts always,
                        run employee_hours only on days 1-5 of the month
                        AND only if not already finalized for this month.
    Saturday 02:00    — full-month BilBoy reconciliation (separate job).

On startup: all applicable agents run once immediately for testing.
"""

import logging
import time
from datetime import date

from apscheduler.schedulers.blocking import BlockingScheduler

from agents.bilboy import BilBoyAgent
from agents.aviv_alerts import AvivAlertsAgent, check_missing_z_reports
from agents.employee_hours import EmployeeHoursAgent
from database.db import init_db, get_connection, get_total_income, get_total_expenses_by_category
from notifications.whatsapp import send_alert

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("scheduler")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _is_month_finalized(month: int, year: int) -> bool:
    """Return True if at least one employee_hours row is finalized this month."""
    with get_connection() as conn:
        count = conn.execute(
            "SELECT COUNT(*) FROM employee_hours WHERE month = ? AND year = ? AND is_finalized = 1",
            (month, year),
        ).fetchone()[0]
    return count > 0


def _run_agent(agent_instance) -> dict:
    """Run a single agent, log timing, and return the result dict."""
    name = agent_instance.name
    logger.info("[scheduler] Starting agent: %s", name)
    start = time.monotonic()
    result = agent_instance.run()
    elapsed = time.monotonic() - start

    if result["success"]:
        logger.info(
            "[scheduler] %s finished — %d records in %.2fs",
            name, len(result.get("data") or []), elapsed,
        )
    else:
        logger.error(
            "[scheduler] %s FAILED after %.2fs — %s",
            name, elapsed, result.get("error"),
        )
    return result


# ---------------------------------------------------------------------------
# Nightly job
# ---------------------------------------------------------------------------

def _build_nightly_summary(today: date, results: dict, missing_z: list[str],
                           employee_status: str) -> str:
    """Build the Hebrew Telegram summary message."""
    day_names = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    date_str = today.strftime("%d/%m/%Y")
    month = today.month
    year = today.year

    lines = [f"\U0001f319 \u05d3\u05d5\u05d7 \u05dc\u05d9\u05dc\u05d4 \u2014 {date_str}", ""]

    # BilBoy
    bb = results.get("bilboy", {})
    bb_ok = bb.get("success", False)
    bb_count = len(bb.get("data") or [])
    goods_total = get_total_expenses_by_category(month, year).get("goods", 0)
    bb_icon = "\u2705" if bb_ok else "\u274c"
    lines.append(
        f"\U0001f4e6 BilBoy: {bb_icon} {bb_count} "
        f"\u05d7\u05e9\u05d1\u05d5\u05e0\u05d9\u05d5\u05ea \u05d7\u05d3\u05e9\u05d5\u05ea"
        f" | \u05e1\u05d4\u05f4\u05db {_format_month(today)} \u20aa{goods_total:,.0f}"
    )

    # Aviv
    av = results.get("aviv_alerts", {})
    av_ok = av.get("success", False)
    av_count = len(av.get("data") or [])
    sales_total = get_total_income(month, year)
    av_icon = "\u2705" if av_ok else "\u274c"
    if av_count > 0:
        av_text = f"{av_count} \u05e0\u05e9\u05de\u05e8\u05d5"
    else:
        av_text = "\u05dc\u05d0 \u05e0\u05de\u05e6\u05d0 \u05d4\u05d9\u05d5\u05dd"
    lines.append(
        f"\U0001f9fe Z-Report: {av_icon} {av_text}"
        f" | {_format_month(today)} \u05d4\u05db\u05e0\u05e1\u05d5\u05ea \u20aa{sales_total:,.0f}"
    )

    # Employee hours
    lines.append(f"\U0001f477 \u05e9\u05e2\u05d5\u05ea \u05e2\u05d5\u05d1\u05d3\u05d9\u05dd: {employee_status}")

    # Missing Z-reports
    lines.append("")
    if missing_z:
        formatted = ", ".join(
            date.fromisoformat(d).strftime("%d/%m") for d in missing_z
        )
        lines.append(f"\u26a0\ufe0f \u05d7\u05e1\u05e8\u05d9\u05dd Z-Reports: {formatted}")
    else:
        lines.append("\u2705 \u05d0\u05d9\u05df Z-Reports \u05d7\u05e1\u05e8\u05d9\u05dd")

    return "\n".join(lines)


def _format_month(d: date) -> str:
    """Return Hebrew month name for display."""
    months = {
        1: "\u05d9\u05e0\u05d5\u05d0\u05e8", 2: "\u05e4\u05d1\u05e8\u05d5\u05d0\u05e8",
        3: "\u05de\u05e8\u05e5", 4: "\u05d0\u05e4\u05e8\u05d9\u05dc",
        5: "\u05de\u05d0\u05d9", 6: "\u05d9\u05d5\u05e0\u05d9",
        7: "\u05d9\u05d5\u05dc\u05d9", 8: "\u05d0\u05d5\u05d2\u05d5\u05e1\u05d8",
        9: "\u05e1\u05e4\u05d8\u05de\u05d1\u05e8", 10: "\u05d0\u05d5\u05e7\u05d8\u05d5\u05d1\u05e8",
        11: "\u05e0\u05d5\u05d1\u05de\u05d1\u05e8", 12: "\u05d3\u05e6\u05de\u05d1\u05e8",
    }
    return months.get(d.month, "")


def _send_missing_z_alert(missing_date: str) -> None:
    """Send an immediate Telegram alert for a missing Z-report."""
    d = date.fromisoformat(missing_date)
    day_names_he = {
        0: "Monday", 1: "Tuesday", 2: "Wednesday",
        3: "Thursday", 4: "Friday", 5: "Saturday", 6: "Sunday",
    }
    day_name = day_names_he.get(d.weekday(), "")
    msg = (
        f"\u26a0\ufe0f Z-Report \u05d7\u05e1\u05e8!\n"
        f"\u05ea\u05d0\u05e8\u05d9\u05da: {d.strftime('%d/%m/%Y')} ({day_name})\n"
        f"\u05d4\u05e1\u05d5\u05db\u05df \u05dc\u05d0 \u05de\u05e6\u05d0 \u05d0\u05ea "
        f"\u05e7\u05d5\u05d1\u05e5 \u05d4-Z \u05e9\u05dc \u05d0\u05de\u05e9.\n"
        f"\u05d1\u05d3\u05d5\u05e7 \u05d0\u05ea \u05de\u05d9\u05d9\u05dc avivpost@avivpos.co.il"
    )
    send_alert(msg, force=True)


def nightly_job():
    """Main nightly routine: always run bilboy + aviv, conditionally run employee_hours."""
    today = date.today()
    logger.info("=== Nightly job started (%s) ===", today.isoformat())

    results = {}
    results["bilboy"] = _run_agent(BilBoyAgent())
    results["aviv_alerts"] = _run_agent(AvivAlertsAgent())

    # Check for missing Z-reports in the past 7 days
    missing = check_missing_z_reports()
    if missing:
        for d in missing:
            logger.warning("[scheduler] Missing Z-report for %s", d)
            _send_missing_z_alert(d)
    else:
        logger.info("[scheduler] No missing Z-reports in the past 7 days")

    # employee_hours: only on days 1-5 AND not yet finalized
    employee_status = ""
    if today.day <= 5:
        if _is_month_finalized(today.month, today.year):
            logger.info(
                "[scheduler] employee_hours skipped — already finalized for %d/%d",
                today.month, today.year,
            )
            employee_status = "\u2705 \u05db\u05d1\u05e8 \u05e1\u05d5\u05e4\u05d9"
        else:
            eh_result = _run_agent(EmployeeHoursAgent())
            if eh_result["success"]:
                employee_status = f"\u2705 {len(eh_result.get('data') or [])} \u05e8\u05e9\u05d5\u05de\u05d5\u05ea"
            else:
                employee_status = "\u274c \u05e0\u05db\u05e9\u05dc"
    else:
        logger.info(
            "[scheduler] employee_hours skipped — today is day %d (only runs on days 1-5)",
            today.day,
        )
        employee_status = f"\u23ed\ufe0f \u05d3\u05d5\u05dc\u05d2 (\u05d9\u05d5\u05dd {today.day})"

    # Send nightly summary via Telegram
    summary = _build_nightly_summary(today, results, missing, employee_status)
    logger.info("[scheduler] Sending nightly summary via Telegram")
    send_alert(summary, force=True)

    logger.info("=== Nightly job complete ===")


# ---------------------------------------------------------------------------
# Saturday reconciliation job
# ---------------------------------------------------------------------------

def saturday_reconciliation():
    """
    Full-month BilBoy reconciliation — runs every Saturday.

    Fetches ALL documents from BilBoy API for the current month and compares
    against DB.  Inserts missing docs and deletes DB rows that no longer
    exist in the API (BilBoy is the source of truth).

    Key is (date, ref_number) for rows that have ref_number,
    falling back to (date, amount, description) for legacy rows.
    """
    today = date.today()
    from_date = date(today.year, today.month, 1).isoformat()
    to_date = today.isoformat()
    logger.info("=== Saturday reconciliation started (%s to %s) ===", from_date, to_date)

    try:
        agent = BilBoyAgent()
        api_records = agent._fetch_invoices(from_date=from_date, to_date=to_date)
        logger.info("[reconciliation] API returned %d documents", len(api_records))
    except Exception as exc:
        logger.error("[reconciliation] Failed to fetch from API: %s", exc)
        send_alert(
            f"\u274c \u05d4\u05ea\u05d0\u05de\u05d4 \u05e9\u05d1\u05d5\u05e2\u05d9\u05ea BilBoy \u05e0\u05db\u05e9\u05dc\u05d4: {exc}",
            force=True,
        )
        return

    # Build API lookup by ref_number (primary) and legacy key (fallback)
    api_by_ref: dict[tuple, dict] = {}
    api_by_legacy: dict[tuple, dict] = {}
    for r in api_records:
        ref = r.get("ref_number") or ""
        if ref:
            api_by_ref[(r["date"], ref)] = r
        api_by_legacy[(r["date"], r["amount"], r["description"])] = r

    # Build DB lookup
    with get_connection() as conn:
        db_rows = conn.execute(
            "SELECT id, date, amount, description, ref_number FROM expenses "
            "WHERE category='goods' AND source='bilboy' "
            "AND date >= ? AND date <= ?",
            (from_date, to_date),
        ).fetchall()

    db_by_ref: dict[tuple, list[int]] = {}
    db_by_legacy: dict[tuple, list[int]] = {}
    db_matched_ids = set()

    for row in db_rows:
        row_id = row[0]
        ref = row[4] or ""
        if ref:
            key = (row[1], ref)
            db_by_ref.setdefault(key, []).append(row_id)
        legacy_key = (row[1], float(row[2]), row[3])
        db_by_legacy.setdefault(legacy_key, []).append(row_id)

    # --- Match: find DB rows that exist in API ---
    for key in db_by_ref:
        if key in api_by_ref:
            for rid in db_by_ref[key]:
                db_matched_ids.add(rid)

    for key in db_by_legacy:
        if key in api_by_legacy:
            for rid in db_by_legacy[key]:
                db_matched_ids.add(rid)

    # --- Find API records not in DB ---
    inserted_count = 0
    inserted_amount = 0.0
    for r in api_records:
        ref = r.get("ref_number") or ""
        # Check if already in DB by ref_number
        if ref and (r["date"], ref) in db_by_ref:
            continue
        # Check by legacy key
        legacy_key = (r["date"], r["amount"], r["description"])
        if legacy_key in db_by_legacy:
            continue
        # Not in DB — insert
        agent._insert_bilboy_expense(r)
        inserted_count += 1
        inserted_amount += r["amount"]
        logger.info("[reconciliation] Inserted: %s | %s | %s | %.2f",
                     r["date"], r.get("doc_type_name", ""), r["description"], r["amount"])

    # --- Delete: DB rows not matched to any API record ---
    all_db_ids = {row[0] for row in db_rows}
    orphan_ids = all_db_ids - db_matched_ids
    deleted_count = 0
    deleted_amount = 0.0
    if orphan_ids:
        with get_connection() as conn:
            for row in db_rows:
                if row[0] in orphan_ids:
                    conn.execute("DELETE FROM expenses WHERE id = ?", (row[0],))
                    deleted_count += 1
                    deleted_amount += float(row[2])
                    logger.info("[reconciliation] Deleted id=%d: %s | %s | %.2f",
                                 row[0], row[1], row[3], float(row[2]))

    # Final month total
    goods_total = get_total_expenses_by_category(today.month, today.year).get("goods", 0)

    # --- Telegram summary ---
    if inserted_count == 0 and deleted_count == 0:
        msg = (
            "\U0001f504 \u05d4\u05ea\u05d0\u05de\u05d4 \u05e9\u05d1\u05d5\u05e2\u05d9\u05ea \u2014 BilBoy\n"
            f"\U0001f4c5 {date(today.year, today.month, 1).strftime('%d/%m/%Y')} "
            f"\u05e2\u05d3 {today.strftime('%d/%m/%Y')}\n\n"
            f"\u2705 \u05d4\u05db\u05dc \u05de\u05e1\u05d5\u05e0\u05db\u05e8\u05df \u2014 "
            f"\u05d0\u05d9\u05df \u05d4\u05e4\u05e8\u05e9\u05d9\u05dd"
        )
    else:
        lines = [
            f"\U0001f504 \u05d4\u05ea\u05d0\u05de\u05d4 \u05e9\u05d1\u05d5\u05e2\u05d9\u05ea \u2014 BilBoy",
            f"\U0001f4c5 {date(today.year, today.month, 1).strftime('%d/%m/%Y')} "
            f"\u05e2\u05d3 {today.strftime('%d/%m/%Y')}",
            "",
        ]
        if inserted_count:
            lines.append(
                f"\u2705 \u05e0\u05d5\u05e1\u05e4\u05d5: {inserted_count} \u05de\u05e1\u05de\u05db\u05d9\u05dd"
                f" | \u20aa{inserted_amount:,.2f}"
            )
        if deleted_count:
            lines.append(
                f"\U0001f5d1\ufe0f \u05e0\u05de\u05d7\u05e7\u05d5: {deleted_count} \u05de\u05e1\u05de\u05db\u05d9\u05dd"
                f" | \u20aa{deleted_amount:,.2f}"
            )
        lines.append(
            f"\U0001f4b0 \u05e1\u05d4\u05f4\u05db {_format_month(today)} "
            f"\u05d0\u05d7\u05e8\u05d9 \u05d4\u05ea\u05d0\u05de\u05d4: \u20aa{goods_total:,.2f}"
        )
        msg = "\n".join(lines)

    send_alert(msg, force=True)
    logger.info(
        "=== Saturday reconciliation complete — %d inserted, %d deleted ===",
        inserted_count, deleted_count,
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    init_db()
    logger.info("Database initialised.")

    # Run immediately on startup so we can verify everything works
    logger.info("Running startup pass of all agents...")
    nightly_job()

    scheduler = BlockingScheduler(timezone="Asia/Jerusalem")
    scheduler.add_job(
        nightly_job,
        trigger="cron",
        hour=2,
        minute=0,
        id="nightly_agents",
        name="Nightly agents (02:00 IL)",
        replace_existing=True,
    )
    scheduler.add_job(
        saturday_reconciliation,
        trigger="cron",
        day_of_week="sat",
        hour=2,
        minute=30,
        id="saturday_reconciliation",
        name="Saturday BilBoy reconciliation (02:30 IL)",
        replace_existing=True,
    )

    logger.info("Scheduler started — nightly 02:00, reconciliation Sat 02:30. Press Ctrl+C to stop.")
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("Scheduler stopped.")
