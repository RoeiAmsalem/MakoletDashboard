"""
Aviv POS live status scraper — uses Playwright to scrape bi-aviv.web.app/status.

Logs in via form, navigates to /status, scrapes:
  - amount: today's ₪ total
  - transactions: transaction count
  - last_updated: timestamp from the status card

Stores in live_sales table (upsert by date). Runs every 5 minutes via scheduler.
"""

import logging
import os
import re
import sqlite3
import sys
from datetime import date, datetime

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("aviv_live")

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "database", "makolet.db")
STATUS_URL = "https://bi-aviv.web.app/status"


def init_live_sales_table():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS live_sales (
            date TEXT PRIMARY KEY,
            amount REAL,
            transactions INTEGER,
            last_updated TEXT,
            fetched_at TEXT
        )
    """)
    conn.commit()
    conn.close()


def scrape():
    from playwright.sync_api import sync_playwright

    logger.info("Starting Playwright...")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_context().new_page()

        # Navigate to /status — will redirect to /sign-in
        logger.info("Navigating to %s...", STATUS_URL)
        page.goto(STATUS_URL, wait_until="networkidle", timeout=30000)
        page.wait_for_timeout(2000)

        # If redirected to sign-in, fill the form
        if "sign-in" in page.url:
            logger.info("On login page — filling credentials...")
            inputs = page.locator("input")
            if inputs.count() >= 2:
                inputs.nth(0).fill("S33834")
                inputs.nth(1).fill("S33834")
            checkbox = page.locator("input[type='checkbox']")
            if checkbox.count() > 0:
                checkbox.first.check()
            login_btn = page.locator("button", has_text="התחברות")
            if login_btn.count() == 0:
                login_btn = page.locator("button[type='submit']")
            login_btn.first.click()
            logger.info("Clicked login, waiting...")
            page.wait_for_timeout(5000)
            logger.info("Post-login URL: %s", page.url)

        # Navigate to /status — try sidebar link first, then direct URL
        if "status" not in page.url:
            logger.info("On %s — looking for status link in sidebar...", page.url)
            # Try clicking sidebar link with text "תשקיף סניף" or "Online" or "status"
            status_link = page.locator("a[href*='status']")
            if status_link.count() == 0:
                status_link = page.locator("text=Online")
            if status_link.count() > 0:
                logger.info("Found status link, clicking...")
                status_link.first.click()
                page.wait_for_timeout(3000)
            else:
                logger.info("No status link found, trying direct navigation...")
                page.goto(STATUS_URL, wait_until="networkidle", timeout=30000)
                page.wait_for_timeout(3000)

        logger.info("Current URL: %s", page.url)
        debug_text = page.inner_text("body")
        logger.info("Page text (%d chars):\n%s", len(debug_text), debug_text[:800])

        # Wait for status page content
        try:
            page.wait_for_selector("text=תאריך עדכון אחרון", timeout=15000)
        except Exception:
            # Fallback: wait for any ₪ on whatever page we're on
            logger.warning("'תאריך עדכון אחרון' not found, falling back to ₪ wait...")
            page.wait_for_selector("text=₪", timeout=10000)
        page.wait_for_timeout(2000)

        raw_text = page.inner_text("body")
        logger.info("Page text (%d chars):\n%s", len(raw_text), raw_text[:600])

        amount = 0.0
        transactions = 0
        last_updated = ""

        if "תאריך עדכון אחרון" in raw_text:
            # /status page: "תאריך עדכון אחרון\n11:43 26/03/26\n₪4,127\n(51)\n..."
            ts_match = re.search(r"תאריך עדכון אחרון\s*\n\s*(\d{1,2}:\d{2}\s+\d{2}/\d{2}/\d{2})", raw_text)
            if ts_match:
                last_updated = ts_match.group(1).strip()
            amt_match = re.search(r"תאריך עדכון אחרון.*?₪\s?([\d,]+(?:\.\d+)?)", raw_text, re.DOTALL)
            if amt_match:
                amount = float(amt_match.group(1).replace(",", ""))
            tx_match = re.search(r"תאריך עדכון אחרון.*?₪[\d,]+(?:\.\d+)?\s*\n\s*\((\d+)\)", raw_text, re.DOTALL)
            if tx_match:
                transactions = int(tx_match.group(1))
        else:
            # /dashboard fallback: "מכירות\n69.3%\n₪4,127\n...\n51\n51\n₪81\nעסקאות"
            amt_match = re.search(r"מכירות\s*\n.*?\n\s*₪\s?([\d,]+(?:\.\d+)?)", raw_text, re.DOTALL)
            if amt_match:
                amount = float(amt_match.group(1).replace(",", ""))
            # Transactions: first standalone number before "עסקאות"
            tx_match = re.search(r"(\d+)\s*\n\s*\d+\s*\n\s*₪[\d,]+\s*\n\s*עסקאות", raw_text)
            if tx_match:
                transactions = int(tx_match.group(1))
            last_updated = datetime.now().strftime("%H:%M %d/%m/%y")

        logger.info("Scraped: amount=₪%.2f, tx=%d, last_updated=%s",
                     amount, transactions, last_updated)

        browser.close()

    return {
        "date": date.today().isoformat(),
        "amount": amount,
        "transactions": transactions,
        "last_updated": last_updated,
        "fetched_at": datetime.now().isoformat(),
    }


def save(data: dict):
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        "INSERT OR REPLACE INTO live_sales (date, amount, transactions, last_updated, fetched_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (data["date"], data["amount"], data["transactions"], data["last_updated"], data["fetched_at"]),
    )
    conn.commit()
    conn.close()
    logger.info("Saved to live_sales for date=%s", data["date"])


def run_aviv_live():
    """Entry point for scheduler — init table, scrape, save."""
    import pytz
    il_tz = pytz.timezone('Asia/Jerusalem')
    now = datetime.now(il_tz)
    start = now.replace(hour=6, minute=30, second=0, microsecond=0)
    end = now.replace(hour=23, minute=5, second=0, microsecond=0)
    if not (start <= now <= end):
        return  # outside store hours, skip silently
    try:
        init_live_sales_table()
        data = scrape()
        save(data)
        logger.info("Aviv live: ₪%.2f (%d tx)", data["amount"], data["transactions"])
    except Exception as e:
        logger.error("Aviv live scrape failed: %s", e)


if __name__ == "__main__":
    try:
        init_live_sales_table()
        data = scrape()
        save(data)
        print(f"\nResult:")
        print(f"  Date:         {data['date']}")
        print(f"  Amount:       ₪{data['amount']:,.2f}")
        print(f"  Transactions: {data['transactions']}")
        print(f"  Last updated: {data['last_updated']}")
        print(f"  Fetched at:   {data['fetched_at']}")
    except Exception as e:
        logger.error("Scrape failed: %s", e, exc_info=True)
        sys.exit(1)
