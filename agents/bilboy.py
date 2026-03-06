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
from database.db import insert_expense

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

    def _get(self, path: str, **params) -> dict | list:
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
        # branches is a list; take the first one
        first = branches[0] if isinstance(branches, list) else branches
        return str(first.get("branchId") or first.get("id") or first.get("branch_id", ""))

    def _get_invoice_headers(self, branch_id: str) -> list[dict]:
        """
        Fetch document headers for the past 30 days.
        Returns a flat list of invoice dicts.
        """
        today = date.today()
        from_date = (today - timedelta(days=30)).isoformat()
        to_date = today.isoformat()

        raw = self._get(
            "/customer/docs/headers",
            branchId=branch_id,
            fromDate=from_date,
            toDate=to_date,
        )
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
        branch_id = self._get_branch_id()
        self.logger.info("[bilboy] Using branch_id=%s", branch_id)

        invoices = self._get_invoice_headers(branch_id)
        self.logger.info("[bilboy] Fetched %d invoice headers", len(invoices))

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
        """Insert each invoice as an expense with category='goods'."""
        for record in data:
            insert_expense(
                date=record["date"],
                category="goods",
                amount=record["amount"],
                description=record["description"],
                source="bilboy",
            )
        self.logger.info("[bilboy] Saved %d expense records to DB", len(data))


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
