"""
BilBoy agent - fetches ALL document types from the BilBoy API and saves them
to the expenses table with category='goods'.

Document types:
    2 = תעודת משלוח (delivery note)
    3 = חשבונית (invoice)
    4 = חשבונית זיכוי (credit invoice)
    5 = תעודת החזרה (return note) — negative amount
    7 = קבלה (receipt)

API flow:
    GET /user/branches          → pick first branch
    GET /customer/suppliers     → list of supplier IDs
    GET /customer/docs/headers  → document list (filtered to date range)

Auth: Bearer token from BILBOY_TOKEN in .env
      Token is obtained manually via OTP and renewed when expired (401).
"""

import os
from datetime import date, datetime, timedelta
from pathlib import Path

import requests
from dotenv import load_dotenv

from agents.base_agent import BaseAgent
from database.db import get_connection, add_pending_fetch, resolve_pending_fetch

load_dotenv()

API_BASE = "https://app.billboy.co.il:5050/api"

DOC_TYPE_NAMES = {
    2: "תעודת משלוח",
    3: "חשבונית",
    4: "חשבונית זיכוי",
    5: "תעודת החזרה",
    7: "קבלה",
}


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

    def _get_doc_headers(self, branch_id: str, suppliers_csv: str,
                         from_date: str | None = None,
                         to_date: str | None = None) -> list[dict]:
        """
        Fetch document headers for a date range (default: yesterday only).
        Returns ALL document types — no type filtering.
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
        Core fetching logic. Used by both fetch_data() and fetch_data_for_date().
        Fetches ALL document types (invoices, delivery notes, returns, etc.).
        """
        branch_id = self._get_branch_id()
        self.logger.info("[bilboy] Using branch_id=%s", branch_id)

        suppliers_csv, skipped_names = self._get_supplier_ids(branch_id)
        if skipped_names:
            self.logger.info("[bilboy] Filtered out %d franchise supplier(s)", len(skipped_names))
        if not suppliers_csv:
            self.logger.warning("[bilboy] No supplier IDs found")
            return []

        docs = self._get_doc_headers(branch_id, suppliers_csv,
                                     from_date=from_date, to_date=to_date)
        self.logger.info("[bilboy] Fetched %d documents from API", len(docs))

        records = []
        self._skip_zikayon = 0
        self._skip_zeros = 0
        for doc in docs:
            raw_date = doc.get("date") or doc.get("documentDate") or date.today().isoformat()
            amount = float(doc.get("totalWithVat") or doc.get("totalAmount") or doc.get("amount") or 0)
            total_without_vat = float(doc.get("totalWithoutVat") or 0)
            supplier = doc.get("supplierName") or ""
            ref_number = str(doc.get("refNumber") or doc.get("number") or "").lstrip("0") or "0"
            doc_type = doc.get("type")
            doc_type_name = DOC_TYPE_NAMES.get(doc_type, str(doc_type))
            description = supplier or ref_number or "BilBoy document"

            # Skip franchise docs
            if "זיכיונות המכולת" in supplier:
                self._skip_zikayon += 1
                continue

            # Skip zero-amount docs (delivery notes with no value)
            if amount == 0 and total_without_vat == 0:
                self._skip_zeros += 1
                continue

            records.append(
                {
                    "date": str(raw_date)[:10],  # ensure YYYY-MM-DD
                    "amount": amount,
                    "total_without_vat": total_without_vat,
                    "description": description,
                    "ref_number": ref_number,
                    "doc_type": doc_type,
                    "doc_type_name": doc_type_name,
                    "raw": doc,
                }
            )

        # Deduplicate within batch (same date + ref_number + doc_type)
        seen = set()
        deduped = []
        self._skip_dupes = 0
        for r in records:
            key = (r["date"], r["ref_number"], r["doc_type"])
            if key in seen:
                self._skip_dupes += 1
                continue
            seen.add(key)
            deduped.append(r)
        records = deduped

        if self._skip_zikayon or self._skip_zeros or self._skip_dupes:
            self.logger.info(
                "[bilboy] Filtered: %d zikayon, %d zero-amount, %d batch-dupes → %d records kept",
                self._skip_zikayon, self._skip_zeros, self._skip_dupes, len(records),
            )
        return records

    def fetch_data(self) -> list[dict]:
        """
        Fetch documents for the full current month.
        The nightly reconciliation in scheduler.py does a full replace
        (delete + re-insert) so late-arriving invoices are always caught.
        """
        today = date.today()
        from_date = date(today.year, today.month, 1).isoformat()
        to_date = today.isoformat()
        records = self._fetch_invoices(from_date=from_date, to_date=to_date)
        self.logger.info("[bilboy] Fetched %d documents for %s to %s",
                         len(records), from_date, to_date)
        return records

    def fetch_data_for_date(self, target_date: str) -> list[dict]:
        """
        Fetch documents for a specific date (used for pending retries).
        """
        return self._fetch_invoices(from_date=target_date, to_date=target_date)

    def save_to_db(self, data: list[dict]) -> None:
        """Insert each document as an expense with category='goods', skipping duplicates.
        Also updates existing rows that have amount=0 (e.g. delivery notes filled in later)."""
        saved = 0
        updated = 0
        saved_dates = set()
        for record in data:
            if self._is_duplicate(record):
                # Try to update if existing row has amount=0 and new data has a real amount
                if record.get("ref_number") and record.get("amount"):
                    if self._update_zero_amount(record):
                        updated += 1
                        saved_dates.add(record["date"])
                continue
            self._insert_bilboy_expense(record)
            saved += 1
            saved_dates.add(record["date"])
        # Resolve any pending fetches for dates we successfully saved
        for d in saved_dates:
            resolve_pending_fetch("bilboy", d)
        self.logger.info("[bilboy] Saved %d, updated %d zero-amount rows (%d skipped as duplicates)",
                         saved, updated, len(data) - saved - updated)

    @staticmethod
    def _update_zero_amount(record: dict) -> bool:
        """Update an existing row that has amount=0 with new data from the API."""
        with get_connection() as conn:
            cur = conn.execute(
                """UPDATE expenses SET amount=?, total_without_vat=?, doc_type_name=?,
                   description=?
                   WHERE ref_number=? AND category='goods' AND amount=0""",
                (
                    record["amount"],
                    record.get("total_without_vat"),
                    record.get("doc_type_name"),
                    record.get("description"),
                    record["ref_number"],
                ),
            )
            return cur.rowcount > 0

    @staticmethod
    def _is_duplicate(record: dict) -> bool:
        """Check by ref_number first (reliable), fall back to old key."""
        with get_connection() as conn:
            if record.get("ref_number"):
                count = conn.execute(
                    "SELECT COUNT(*) FROM expenses "
                    "WHERE source='bilboy' AND ref_number=? AND date=?",
                    (record["ref_number"], record["date"]),
                ).fetchone()[0]
                if count > 0:
                    return True
            # Fallback for rows without ref_number
            count = conn.execute(
                "SELECT COUNT(*) FROM expenses "
                "WHERE date=? AND source='bilboy' AND amount=? AND description=?",
                (record["date"], record["amount"], record["description"]),
            ).fetchone()[0]
        return count > 0

    @staticmethod
    def _insert_bilboy_expense(record: dict) -> int:
        """Insert a bilboy expense with all document fields."""
        with get_connection() as conn:
            cur = conn.execute(
                """INSERT INTO expenses
                   (date, category, amount, description, source,
                    ref_number, total_without_vat, doc_type, doc_type_name)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    record["date"],
                    "goods",
                    record["amount"],
                    record["description"],
                    "bilboy",
                    record.get("ref_number"),
                    record.get("total_without_vat"),
                    record.get("doc_type"),
                    record.get("doc_type_name"),
                ),
            )
            return cur.lastrowid


# ---------------------------------------------------------------------------
# Manual run entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import logging
    import sys

    logging.basicConfig(level=logging.INFO)
    dry_run = "--dry-run" in sys.argv

    if dry_run:
        print("=== DRY RUN — no DB changes ===\n")
        agent = BilBoyAgent()
        today = date.today()
        from_date = date(today.year, today.month, 1).isoformat()
        to_date = today.isoformat()
        records = agent._fetch_invoices(from_date=from_date, to_date=to_date)
        total_fetched = len(records) + agent._skip_zikayon + agent._skip_zeros + agent._skip_dupes
        total_amount = sum(r["amount"] for r in records)
        total_nv = sum(r["total_without_vat"] for r in records)

        print(f"Date range: {from_date} to {to_date}")
        print(f"Total fetched from API: {total_fetched}")
        print(f"  would_skip_zikayon:   {agent._skip_zikayon}")
        print(f"  would_skip_zeros:     {agent._skip_zeros}")
        print(f"  would_skip_dupes:     {agent._skip_dupes}")
        print(f"  would_insert:         {len(records)}")
        print(f"  total (with VAT):     ₪{total_amount:,.2f}")
        print(f"  total (without VAT):  ₪{total_nv:,.2f}")
        print()

        # Breakdown by doc type
        by_type: dict[str, list] = {}
        for r in records:
            key = r.get("doc_type_name") or str(r.get("doc_type"))
            by_type.setdefault(key, []).append(r)
        print("--- BY DOC TYPE ---")
        for tn, docs in sorted(by_type.items()):
            t = sum(d["amount"] for d in docs)
            print(f"  {tn}: {len(docs)} docs, ₪{t:,.2f}")

        # Top 10 suppliers
        by_sup: dict[str, float] = {}
        for r in records:
            by_sup[r["description"]] = by_sup.get(r["description"], 0) + r["amount"]
        print("\n--- TOP 10 SUPPLIERS ---")
        for sup, amt in sorted(by_sup.items(), key=lambda x: -x[1])[:10]:
            print(f"  ₪{amt:>10,.2f}  {sup}")
    else:
        result = BilBoyAgent().run()
        if result["success"]:
            print(f"Success: {len(result['data'])} documents saved.")
        else:
            print(f"Failed: {result['error']}")
