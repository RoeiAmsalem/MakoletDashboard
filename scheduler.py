"""
Nightly scheduler for MakoletDashboard agents.

Schedule (Asia/Jerusalem timezone):
    02:00 every night — run bilboy + aviv_alerts always,
                        run employee_hours only on days 1-5 of the month
                        AND only if not already finalized for this month.

On startup: all applicable agents run once immediately for testing.
"""

import logging
import time
from datetime import date

from apscheduler.schedulers.blocking import BlockingScheduler

from agents.bilboy import BilBoyAgent
from agents.aviv_alerts import AvivAlertsAgent
from agents.employee_hours import EmployeeHoursAgent
from database.db import init_db, get_connection

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

def nightly_job():
    """Main nightly routine: always run bilboy + aviv, conditionally run employee_hours."""
    today = date.today()
    logger.info("=== Nightly job started (%s) ===", today.isoformat())

    _run_agent(BilBoyAgent())
    _run_agent(AvivAlertsAgent())

    # employee_hours: only on days 1-5 AND not yet finalized
    if today.day <= 5:
        if _is_month_finalized(today.month, today.year):
            logger.info(
                "[scheduler] employee_hours skipped — already finalized for %d/%d",
                today.month, today.year,
            )
        else:
            _run_agent(EmployeeHoursAgent())
    else:
        logger.info(
            "[scheduler] employee_hours skipped — today is day %d (only runs on days 1-5)",
            today.day,
        )

    logger.info("=== Nightly job complete ===")


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

    logger.info("Scheduler started — nightly job at 02:00 Asia/Jerusalem. Press Ctrl+C to stop.")
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("Scheduler stopped.")
