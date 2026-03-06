"""
Basic tests for database/db.py.
Uses a temporary in-memory DB so nothing touches makolet.db.
"""

import sqlite3
import sys
import os
import unittest
from datetime import date
from unittest.mock import patch

# Make project root importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import database.db as db
from database.models import create_tables


# ---------------------------------------------------------------------------
# Helper: patch DB_PATH to use an in-memory database per test
# ---------------------------------------------------------------------------

class InMemoryDB(unittest.TestCase):
    """Base class that wires db.get_connection() to a shared in-memory DB."""

    def setUp(self):
        self._conn = sqlite3.connect(":memory:")
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        create_tables(self._conn)

        # Patch get_connection so all db functions use our in-memory conn
        self._patcher = patch("database.db.get_connection", return_value=self._conn)
        self._patcher.start()

    def tearDown(self):
        self._patcher.stop()
        self._conn.close()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestCreateTables(InMemoryDB):
    def test_all_tables_exist(self):
        tables = {
            row[0]
            for row in self._conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        expected = {
            "daily_sales", "expenses", "employees",
            "employee_hours", "fixed_expenses", "agent_logs",
        }
        self.assertTrue(expected.issubset(tables))


class TestDailySales(InMemoryDB):
    def test_insert_and_query(self):
        today = date.today().isoformat()
        db.insert_daily_sale(today, 1234.5, "test")
        rows = db.get_sales_by_month(date.today().month, date.today().year)
        self.assertEqual(len(rows), 1)
        self.assertAlmostEqual(rows[0]["total_income"], 1234.5)

    def test_get_total_income(self):
        today = date.today().isoformat()
        db.insert_daily_sale(today, 1000.0, "test")
        db.insert_daily_sale(today, 500.0, "test")
        total = db.get_total_income(date.today().month, date.today().year)
        self.assertAlmostEqual(total, 1500.0)

    def test_no_sales_returns_zero(self):
        total = db.get_total_income(1, 2000)
        self.assertEqual(total, 0.0)


class TestExpenses(InMemoryDB):
    def test_insert_and_query(self):
        today = date.today().isoformat()
        db.insert_expense(today, "goods", 800.0, "BilBoy invoice", "bilboy")
        rows = db.get_expenses_by_month(date.today().month, date.today().year)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["category"], "goods")

    def test_filter_by_category(self):
        today = date.today().isoformat()
        db.insert_expense(today, "goods", 100.0)
        db.insert_expense(today, "electricity", 200.0)
        goods_rows = db.get_expenses_by_month(
            date.today().month, date.today().year, category="goods"
        )
        self.assertEqual(len(goods_rows), 1)

    def test_totals_by_category(self):
        today = date.today().isoformat()
        db.insert_expense(today, "goods", 300.0)
        db.insert_expense(today, "goods", 200.0)
        db.insert_expense(today, "electricity", 150.0)
        totals = db.get_total_expenses_by_category(
            date.today().month, date.today().year
        )
        self.assertAlmostEqual(totals["goods"], 500.0)
        self.assertAlmostEqual(totals["electricity"], 150.0)


class TestEmployees(InMemoryDB):
    def test_insert_and_query(self):
        emp_id = db.insert_employee("Test Worker", 50.0)
        self.assertIsInstance(emp_id, int)
        employees = db.get_active_employees()
        self.assertEqual(len(employees), 1)
        self.assertEqual(employees[0]["name"], "Test Worker")

    def test_deactivate(self):
        emp_id = db.insert_employee("Temp Worker", 30.0)
        db.deactivate_employee(emp_id)
        self.assertEqual(len(db.get_active_employees()), 0)

    def test_update_rate(self):
        emp_id = db.insert_employee("Worker", 40.0)
        db.update_employee_rate(emp_id, 60.0)
        employees = db.get_active_employees()
        self.assertAlmostEqual(employees[0]["hourly_rate"], 60.0)


class TestEmployeeHours(InMemoryDB):
    def setUp(self):
        super().setUp()
        self.emp_id = db.insert_employee("Worker", 50.0)

    def test_upsert_insert(self):
        db.upsert_employee_hours(self.emp_id, 1, 2025, 160.0)
        rows = db.get_employee_hours(1, 2025)
        self.assertEqual(len(rows), 1)
        self.assertAlmostEqual(rows[0]["hours_worked"], 160.0)

    def test_upsert_update(self):
        db.upsert_employee_hours(self.emp_id, 1, 2025, 160.0)
        db.upsert_employee_hours(self.emp_id, 1, 2025, 180.0, is_finalized=True)
        rows = db.get_employee_hours(1, 2025)
        self.assertEqual(len(rows), 1)
        self.assertAlmostEqual(rows[0]["hours_worked"], 180.0)
        self.assertEqual(rows[0]["is_finalized"], 1)

    def test_salary_cost(self):
        db.upsert_employee_hours(self.emp_id, 1, 2025, 100.0)
        cost = db.get_total_salary_cost(1, 2025)
        self.assertAlmostEqual(cost, 5000.0)  # 100h * 50/h


class TestFixedExpenses(InMemoryDB):
    def test_insert_and_get_active(self):
        db.upsert_fixed_expense("rent", 8000.0, "2024-01-01")
        rows = db.get_active_fixed_expenses()
        self.assertEqual(len(rows), 1)
        self.assertAlmostEqual(rows[0]["amount"], 8000.0)

    def test_expired_not_returned(self):
        db.upsert_fixed_expense("rent", 8000.0, "2020-01-01", valid_until="2020-12-31")
        rows = db.get_active_fixed_expenses()
        self.assertEqual(len(rows), 0)

    def test_total_fixed(self):
        db.upsert_fixed_expense("rent", 8000.0, "2024-01-01")
        db.upsert_fixed_expense("internet", 200.0, "2024-01-01")
        total = db.get_total_fixed_expenses()
        self.assertAlmostEqual(total, 8200.0)


class TestAgentLogs(InMemoryDB):
    def test_log_success(self):
        today = date.today().isoformat()
        db.log_agent_run("bilboy", today, "success", records_fetched=5, duration_seconds=2.3)
        logs = db.get_agent_logs()
        self.assertEqual(len(logs), 1)
        self.assertEqual(logs[0]["status"], "success")

    def test_log_failure(self):
        today = date.today().isoformat()
        db.log_agent_run("electricity", today, "failure", error_message="Timeout")
        last = db.get_last_agent_run("electricity")
        self.assertEqual(last["status"], "failure")
        self.assertEqual(last["error_message"], "Timeout")

    def test_filter_by_agent(self):
        today = date.today().isoformat()
        db.log_agent_run("bilboy", today, "success")
        db.log_agent_run("electricity", today, "success")
        logs = db.get_agent_logs(agent_name="bilboy")
        self.assertEqual(len(logs), 1)
        self.assertEqual(logs[0]["agent_name"], "bilboy")


class TestCalculateEstimatedProfit(InMemoryDB):
    def test_profit_structure(self):
        today = date.today()
        result = db.calculate_estimated_profit(today.month, today.year)
        for key in ("income", "goods", "electricity", "fixed_prorated",
                    "salary", "profit", "is_finalized", "ratio"):
            self.assertIn(key, result)

    def test_profit_math(self):
        today = date.today()
        m, y = today.month, today.year
        db.insert_daily_sale(today.isoformat(), 10000.0, "test")
        db.insert_expense(today.isoformat(), "goods", 3000.0)
        emp_id = db.insert_employee("Worker", 50.0)
        db.upsert_employee_hours(emp_id, m, y, 100.0)  # 5000 cost

        result = db.calculate_estimated_profit(m, y)
        # profit = 10000 - 3000 - 0 (electricity) - 0 (fixed) - 5000 = 2000
        self.assertAlmostEqual(result["profit"], 2000.0, places=1)


if __name__ == "__main__":
    unittest.main()
