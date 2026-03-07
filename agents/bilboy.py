"""
BilBoy agent - fetches goods invoices from the BilBoy API and saves them
to the expenses table with category='goods'.

API flow:
    GET /user/branches          → pick first branch
    GET /customer/suppliers     → list of supplier IDs
    GET /customer/docs/headers  → invoice list (filtered to today's date range)

Auth: Bearer token from BILBOY_TOKEN in .env
      Token is obtained manually via OTP and renewed when expired (401).
"""

import os
from datetime import date, timedelta

import requests
from dotenv import load_dotenv

from agents.base_agent import BaseAgent
from database.db import get_connection, insert_expense

load_dotenv()

API_BASE = "https://app.billboy.co.il:5050/api"


class BilBoyAgent(BaseAgent):
    name = "bilboy"

    def __init__(self):
        super().__init__()
        self._token = os.getenv("BILBOY_TOKEN", "")
        self._session = requests.Session()
        self._session.headers.update(
            {"Authorization": f"Bearer {self._token}"}
        )

    # ------------------------------------------------------------------
    # Internal API helpers
    # ------------------------------------------------------------------

    def _get(self, path: str, params=None) -> dict | list:
        """GET helper that raises PermissionError on 401."""
        url = f"{API_BASE}{path}"
        resp = self._session.get(url, params=params, timeout=30)
        if resp.status_code == 401:
            raise PermissionError("BilBoy token expired")
        resp.raise_for_status()
        return resp.json()

    def _get_branch(self) -> tuple[str, list[str]]:
        """Return (branch_id, supplier_ids) from the first branch."""
        branches = self._get("/user/branches")
        if not branches:
            raise ValueError("No branches returned from BilBoy API")
        first = branches[0] if isinstance(branches, list) else branches
        branch_id = str(first.get("branchId") or first.get("id") or first.get("branch_id", ""))
        supplier_ids = [str(s) for s in (first.get("suppliers") or [])]
        return branch_id, supplier_ids

    def _get_invoice_headers(self, branch_id: str, supplier_ids: list[str],
                             from_date: str | None = None,
                             to_date: str | None = None) -> list[dict]:
        """
        Fetch document headers for a date range (default: yesterday only).
        Returns a flat list of invoice dicts.
        """
        if from_date is None or to_date is None:
            yesterday = date.today() - timedelta(days=1)
            from_date = from_date or yesterday.isoformat()
            to_date = to_date or yesterday.isoformat()

        # API requires branches and suppliers as repeated query params
        params = [("branches", branch_id), ("fromDate", from_date), ("toDate", to_date)]
        for sid in supplier_ids:
            params.append(("suppliers", sid))

        raw = self._get("/customer/docs/headers", params=params)
        # API may return a list directly or wrapped in a key
        if isinstance(raw, list):
            return raw
        return raw.get("data") or raw.get("docs") or raw.get("headers") or []

    # ------------------------------------------------------------------
    # BaseAgent interface
    # ------------------------------------------------------------------

    def fetch_data(self) -> list[dict]:
        """
        Run the BilBoy API flow and return a list of invoice records.

        Each record:
            {
                "date": "YYYY-MM-DD",
                "amount": float,
                "description": str,
                "raw": dict        # full API object kept for debugging
            }
        """
        branch_id, supplier_ids = self._get_branch()
        self.logger.info("[bilboy] Using branch_id=%s (%d suppliers)", branch_id, len(supplier_ids))

        all_invoices = self._get_invoice_headers(branch_id, supplier_ids)
        self.logger.info("[bilboy] Fetched %d invoice headers", len(all_invoices))

        # Filter out franchise fees (זיכיונות המכולת)
        invoices = []
        skipped = 0
        for inv in all_invoices:
            supplier = inv.get("supplierName") or inv.get("description") or ""
            if "זיכיונות המכולת" in supplier:
                skipped += 1
                continue
            invoices.append(inv)
        if skipped:
            self.logger.info("[bilboy] Skipped %d invoice(s) from זיכיונות המכולת", skipped)

        records = []
        for inv in invoices:
            # Normalise date field (API uses various key names)
            raw_date = (
                inv.get("documentDate")
                or inv.get("date")
                or inv.get("docDate")
                or date.today().isoformat()
            )
            # Normalise amount field
            amount = float(
                inv.get("totalAmount")
                or inv.get("total")
                or inv.get("amount")
                or 0
            )
            description = (
                inv.get("supplierName")
                or inv.get("description")
                or inv.get("docNumber")
                or "BilBoy invoice"
            )
            records.append(
                {
                    "date": str(raw_date)[:10],  # ensure YYYY-MM-DD
                    "amount": amount,
                    "description": description,
                    "raw": inv,
                }
            )
        return records

    def save_to_db(self, data: list[dict]) -> None:
        """Insert each invoice as an expense with category='goods', skipping duplicates."""
        saved = 0
        for record in data:
            if self._is_duplicate(record):
                continue
            insert_expense(
                date=record["date"],
                category="goods",
                amount=record["amount"],
                description=record["description"],
                source="bilboy",
            )
            saved += 1
        self.logger.info("[bilboy] Saved %d expense records to DB (%d skipped as duplicates)",
                         saved, len(data) - saved)

    @staticmethod
    def _is_duplicate(record: dict) -> bool:
        with get_connection() as conn:
            count = conn.execute(
                "SELECT COUNT(*) FROM expenses WHERE date=? AND source='bilboy' AND amount=? AND description=?",
                (record["date"], record["amount"], record["description"]),
            ).fetchone()[0]
        return count > 0


# ---------------------------------------------------------------------------
# Manual run entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import logging

    logging.basicConfig(level=logging.INFO)
    result = BilBoyAgent().run()
    if result["success"]:
        print(f"Success: {len(result['data'])} invoices saved.")
    else:
        print(f"Failed: {result['error']}")
