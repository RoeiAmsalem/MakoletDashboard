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
from database.db import get_connection, insert_expense, add_pending_fetch, resolve_pending_fetch

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

    def _get_branch_id(self) -> str:
        branches = self._get("/user/branches")
        if not branches:
            raise ValueError("No branches returned from BilBoy API")
        first = branches[0] if isinstance(branches, list) else branches
        return str(first.get("branchId") or first.get("id") or first.get("branch_id", ""))

    def _get_supplier_ids(self, branch_id: str) -> tuple[list[str], list[str]]:
        """
        Fetch all suppliers for a branch.
        Returns (all_ids_csv, skip_ids) where skip_ids are franchise suppliers.
        Filters out זיכיונות המכולת at the supplier level.
        """
        raw = self._get("/customer/suppliers", params={
            "customerBranchId": branch_id,
            "all": "true",
        })
        suppliers = raw.get("suppliers") if isinstance(raw, dict) else raw
        if not suppliers:
            return "", []

        keep_ids = []
        skip_names = []
        for s in suppliers:
            name = s.get("title") or s.get("name") or s.get("supplierName") or ""
            sid = str(s.get("id") or s.get("supplierId") or "")
            if "זיכיונות המכולת" in name:
                skip_names.append(name)
                continue
            if sid:
                keep_ids.append(sid)
        return ",".join(keep_ids), skip_names

    def _get_invoice_headers(self, branch_id: str, suppliers_csv: str,
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

        raw = self._get("/customer/docs/headers", params={
            "suppliers": suppliers_csv,
            "branches": branch_id,
            "from": f"{from_date}T00:00:00",
            "to": f"{to_date}T00:00:00",
        })
        # API may return a list directly or wrapped in a key
        if isinstance(raw, list):
            return raw
        return raw.get("data") or raw.get("docs") or raw.get("headers") or []

    # ------------------------------------------------------------------
    # BaseAgent interface
    # ------------------------------------------------------------------

    def _fetch_invoices(self, from_date: str = None, to_date: str = None) -> list[dict]:
        """
        Core invoice fetching logic. Used by both fetch_data() and fetch_data_for_date().
        """
        branch_id = self._get_branch_id()
        self.logger.info("[bilboy] Using branch_id=%s", branch_id)

        suppliers_csv, skipped_names = self._get_supplier_ids(branch_id)
        if skipped_names:
            self.logger.info("[bilboy] Filtered out %d franchise supplier(s)", len(skipped_names))
        if not suppliers_csv:
            self.logger.warning("[bilboy] No supplier IDs found")
            return []

        invoices = self._get_invoice_headers(branch_id, suppliers_csv,
                                              from_date=from_date, to_date=to_date)
        self.logger.info("[bilboy] Fetched %d invoice headers", len(invoices))

        records = []
        for inv in invoices:
            raw_date = inv.get("date") or inv.get("documentDate") or date.today().isoformat()
            amount = float(inv.get("totalWithVat") or inv.get("totalAmount") or inv.get("amount") or 0)
            supplier = inv.get("supplierName") or ""
            ref_number = str(inv.get("refNumber") or inv.get("number") or "")
            description = supplier or ref_number or "BilBoy invoice"
            records.append(
                {
                    "date": str(raw_date)[:10],  # ensure YYYY-MM-DD
                    "amount": amount,
                    "description": description,
                    "raw": inv,
                }
            )
        return records

    def fetch_data(self) -> list[dict]:
        """
        Run the BilBoy API flow for yesterday (default date range).
        """
        return self._fetch_invoices()

    def fetch_data_for_date(self, target_date: str) -> list[dict]:
        """
        Fetch invoices for a specific date (used for pending retries).
        """
        return self._fetch_invoices(from_date=target_date, to_date=target_date)

    def save_to_db(self, data: list[dict]) -> None:
        """Insert each invoice as an expense with category='goods', skipping duplicates."""
        saved = 0
        saved_dates = set()
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
            saved_dates.add(record["date"])
        # Resolve any pending fetches for dates we successfully saved
        for d in saved_dates:
            resolve_pending_fetch("bilboy", d)
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
