"""
SQLite connection + CRUD functions for MakoletDashboard.

Usage:
    from database.db import init_db, get_connection
    init_db()
    conn = get_connection()
"""

import sqlite3
import os
from datetime import datetime, date
from database.models import create_tables, _migrate_expenses_columns

DB_PATH = os.path.join(os.path.dirname(__file__), "makolet.db")


# ---------------------------------------------------------------------------
# Connection
# ---------------------------------------------------------------------------

def get_connection() -> sqlite3.Connection:
    """Return a connection with row_factory set to sqlite3.Row."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(seed: bool = False):
    """
    Create all tables.
    Always seeds default fixed expenses if the table is empty.
    If seed=True, also insert one sample row per table (for testing/dev).
    """
    conn = get_connection()
    create_tables(conn)            # CREATE TABLE IF NOT EXISTS for all tables
    _migrate_expenses_columns(conn)  # ALTER TABLE to add new columns (idempotent)
    _seed_default_fixed_expenses(conn)
    if seed:
        _seed_sample_data(conn)
    conn.close()


# ---------------------------------------------------------------------------
# Default data seeding
# ---------------------------------------------------------------------------

def _seed_default_fixed_expenses(conn: sqlite3.Connection):
    """Insert default expense categories if the fixed_expenses table is empty."""
    count = conn.execute("SELECT COUNT(*) FROM fixed_expenses").fetchone()[0]
    if count > 0:
        return
    today = date.today().isoformat()
    defaults = [
        ("rent",   0.0, today, "שכירות"),
        ("arnona", 0.0, today, "ארנונה"),
    ]
    for category, amount, valid_from, notes in defaults:
        conn.execute(
            "INSERT INTO fixed_expenses (category, amount, valid_from, notes) VALUES (?, ?, ?, ?)",
            (category, amount, valid_from, notes),
        )
    conn.commit()


# ---------------------------------------------------------------------------
# Seed / sample data
# ---------------------------------------------------------------------------

def _seed_sample_data(conn: sqlite3.Connection):
    today = date.today().isoformat()
    cur = conn.cursor()

    # daily_sales
    cur.execute(
        "INSERT INTO daily_sales (date, total_income, source) VALUES (?, ?, ?)",
        (today, 5000.0, "aviv_alerts"),
    )

    # expenses
    cur.execute(
        "INSERT INTO expenses (date, category, amount, description, source) VALUES (?, ?, ?, ?, ?)",
        (today, "goods", 2000.0, "Sample goods invoice", "bilboy"),
    )

    # employees
    cur.execute(
        "INSERT INTO employees (name, hourly_rate, is_active) VALUES (?, ?, ?)",
        ("ישראל ישראלי", 40.0, 1),
    )
    employee_id = cur.lastrowid

    # employee_hours
    now = datetime.now()
    cur.execute(
        "INSERT INTO employee_hours (employee_id, month, year, hours_worked, is_finalized) VALUES (?, ?, ?, ?, ?)",
        (employee_id, now.month, now.year, 160.0, 0),
    )

    # fixed_expenses
    cur.execute(
        "INSERT INTO fixed_expenses (category, amount, valid_from, notes) VALUES (?, ?, ?, ?)",
        ("rent", 8000.0, "2024-01-01", "שכירות חנות"),
    )

    # agent_logs
    cur.execute(
        """INSERT INTO agent_logs
           (agent_name, run_date, status, records_fetched, duration_seconds)
           VALUES (?, ?, ?, ?, ?)""",
        ("seed", today, "success", 0, 0.0),
    )

    conn.commit()


# ---------------------------------------------------------------------------
# daily_sales
# ---------------------------------------------------------------------------

def insert_daily_sale(date: str, total_income: float, source: str) -> int:
    """Insert a daily sales record. Returns the new row id."""
    with get_connection() as conn:
        cur = conn.execute(
            "INSERT INTO daily_sales (date, total_income, source) VALUES (?, ?, ?)",
            (date, total_income, source),
        )
        return cur.lastrowid


def get_sales_by_month(month: int, year: int) -> list[sqlite3.Row]:
    with get_connection() as conn:
        return conn.execute(
            "SELECT * FROM daily_sales WHERE strftime('%m', date) = ? AND strftime('%Y', date) = ? ORDER BY date",
            (f"{month:02d}", str(year)),
        ).fetchall()


def get_total_income(month: int, year: int) -> float:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT COALESCE(SUM(total_income), 0) AS total FROM daily_sales "
            "WHERE strftime('%m', date) = ? AND strftime('%Y', date) = ?",
            (f"{month:02d}", str(year)),
        ).fetchone()
        return row["total"]


# ---------------------------------------------------------------------------
# expenses
# ---------------------------------------------------------------------------

def insert_expense(date: str, category: str, amount: float,
                   description: str = None, source: str = None) -> int:
    with get_connection() as conn:
        cur = conn.execute(
            "INSERT INTO expenses (date, category, amount, description, source) VALUES (?, ?, ?, ?, ?)",
            (date, category, amount, description, source),
        )
        return cur.lastrowid


def get_expenses_by_month(month: int, year: int,
                           category: str = None) -> list[sqlite3.Row]:
    with get_connection() as conn:
        if category:
            return conn.execute(
                "SELECT * FROM expenses WHERE strftime('%m', date) = ? AND strftime('%Y', date) = ? "
                "AND category = ? ORDER BY date",
                (f"{month:02d}", str(year), category),
            ).fetchall()
        return conn.execute(
            "SELECT * FROM expenses WHERE strftime('%m', date) = ? AND strftime('%Y', date) = ? ORDER BY date",
            (f"{month:02d}", str(year)),
        ).fetchall()


def get_total_expenses_by_category(month: int, year: int) -> dict:
    """Return {category: total_amount} for a given month/year."""
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT category, COALESCE(SUM(amount), 0) AS total FROM expenses "
            "WHERE strftime('%m', date) = ? AND strftime('%Y', date) = ? GROUP BY category",
            (f"{month:02d}", str(year)),
        ).fetchall()
        return {row["category"]: row["total"] for row in rows}


# ---------------------------------------------------------------------------
# employees
# ---------------------------------------------------------------------------

def insert_employee(name: str, hourly_rate: float) -> int:
    with get_connection() as conn:
        cur = conn.execute(
            "INSERT INTO employees (name, hourly_rate) VALUES (?, ?)",
            (name, hourly_rate),
        )
        return cur.lastrowid


def get_active_employees() -> list[sqlite3.Row]:
    with get_connection() as conn:
        return conn.execute(
            "SELECT * FROM employees WHERE is_active = 1 ORDER BY name"
        ).fetchall()


def update_employee_rate(employee_id: int, hourly_rate: float):
    with get_connection() as conn:
        conn.execute(
            "UPDATE employees SET hourly_rate = ? WHERE id = ?",
            (hourly_rate, employee_id),
        )


def deactivate_employee(employee_id: int):
    with get_connection() as conn:
        conn.execute(
            "UPDATE employees SET is_active = 0 WHERE id = ?",
            (employee_id,),
        )


def get_all_employees() -> list[sqlite3.Row]:
    """Return all employees (active first, then inactive), ordered by name."""
    with get_connection() as conn:
        return conn.execute(
            "SELECT * FROM employees ORDER BY is_active DESC, name"
        ).fetchall()


def toggle_employee_active(employee_id: int) -> bool:
    """Flip is_active for the employee. Returns the new state as bool."""
    with get_connection() as conn:
        row = conn.execute(
            "SELECT is_active FROM employees WHERE id = ?", (employee_id,)
        ).fetchone()
        if not row:
            raise ValueError(f"Employee {employee_id} not found")
        new_state = 0 if row["is_active"] else 1
        conn.execute(
            "UPDATE employees SET is_active = ? WHERE id = ?",
            (new_state, employee_id),
        )
    return bool(new_state)


def delete_employee(employee_id: int) -> None:
    """Hard-delete an employee and all their hours records."""
    with get_connection() as conn:
        conn.execute("DELETE FROM employee_hours WHERE employee_id = ?", (employee_id,))
        conn.execute("DELETE FROM employees WHERE id = ?", (employee_id,))


# ---------------------------------------------------------------------------
# employee_hours
# ---------------------------------------------------------------------------

def upsert_employee_hours(employee_id: int, month: int, year: int,
                           hours_worked: float, is_finalized: bool = False) -> int:
    """Insert or update hours for a given employee/month/year."""
    with get_connection() as conn:
        existing = conn.execute(
            "SELECT id FROM employee_hours WHERE employee_id = ? AND month = ? AND year = ?",
            (employee_id, month, year),
        ).fetchone()
        if existing:
            conn.execute(
                "UPDATE employee_hours SET hours_worked = ?, is_finalized = ? WHERE id = ?",
                (hours_worked, int(is_finalized), existing["id"]),
            )
            return existing["id"]
        cur = conn.execute(
            "INSERT INTO employee_hours (employee_id, month, year, hours_worked, is_finalized) VALUES (?, ?, ?, ?, ?)",
            (employee_id, month, year, hours_worked, int(is_finalized)),
        )
        return cur.lastrowid


def get_employee_hours(month: int, year: int) -> list[sqlite3.Row]:
    with get_connection() as conn:
        return conn.execute(
            """SELECT eh.*, e.name, e.hourly_rate
               FROM employee_hours eh
               JOIN employees e ON e.id = eh.employee_id
               WHERE eh.month = ? AND eh.year = ?""",
            (month, year),
        ).fetchall()


def get_total_salary_cost(month: int, year: int) -> float:
    """Sum of hours_worked * hourly_rate for all employees in month/year."""
    with get_connection() as conn:
        row = conn.execute(
            """SELECT COALESCE(SUM(eh.hours_worked * e.hourly_rate), 0) AS total
               FROM employee_hours eh
               JOIN employees e ON e.id = eh.employee_id
               WHERE eh.month = ? AND eh.year = ?""",
            (month, year),
        ).fetchone()
        return row["total"]


# ---------------------------------------------------------------------------
# fixed_expenses
# ---------------------------------------------------------------------------

def upsert_fixed_expense(category: str, amount: float, valid_from: str,
                          valid_until: str = None, notes: str = None) -> int:
    with get_connection() as conn:
        cur = conn.execute(
            "INSERT INTO fixed_expenses (category, amount, valid_from, valid_until, notes) VALUES (?, ?, ?, ?, ?)",
            (category, amount, valid_from, valid_until, notes),
        )
        return cur.lastrowid


def get_active_fixed_expenses(as_of_date: str = None) -> list[sqlite3.Row]:
    """Return fixed expenses active on as_of_date (default: today)."""
    as_of_date = as_of_date or date.today().isoformat()
    with get_connection() as conn:
        return conn.execute(
            """SELECT * FROM fixed_expenses
               WHERE valid_from <= ?
               AND (valid_until IS NULL OR valid_until >= ?)
               ORDER BY category""",
            (as_of_date, as_of_date),
        ).fetchall()


def get_total_fixed_expenses(as_of_date: str = None) -> float:
    rows = get_active_fixed_expenses(as_of_date)
    return sum(row["amount"] for row in rows)


def get_all_fixed_expenses() -> list[sqlite3.Row]:
    """Return all fixed expense rows regardless of validity dates."""
    with get_connection() as conn:
        return conn.execute(
            "SELECT * FROM fixed_expenses ORDER BY category"
        ).fetchall()


def update_fixed_expense_amount(expense_id: int, amount: float) -> None:
    with get_connection() as conn:
        conn.execute(
            "UPDATE fixed_expenses SET amount = ? WHERE id = ?",
            (amount, expense_id),
        )


def insert_fixed_expense(category: str, amount: float) -> int:
    """Insert a new fixed expense with valid_from = today. Returns new row id."""
    return upsert_fixed_expense(
        category=category,
        amount=amount,
        valid_from=date.today().isoformat(),
    )


def delete_fixed_expense(expense_id: int) -> None:
    with get_connection() as conn:
        conn.execute("DELETE FROM fixed_expenses WHERE id = ?", (expense_id,))


# ---------------------------------------------------------------------------
# agent_logs
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# electricity bills
# ---------------------------------------------------------------------------

def get_electricity_bills() -> list[sqlite3.Row]:
    """Return all electricity expenses newest first."""
    with get_connection() as conn:
        return conn.execute(
            "SELECT * FROM expenses WHERE category='electricity' ORDER BY date DESC"
        ).fetchall()


def get_electricity_monthly_estimate() -> float | None:
    """Latest non-correction bill ÷ 2. Returns None if no bills exist."""
    with get_connection() as conn:
        row = conn.execute(
            """SELECT amount FROM expenses
               WHERE category='electricity' AND (is_correction=0 OR is_correction IS NULL)
               ORDER BY date DESC LIMIT 1"""
        ).fetchone()
    if row is None:
        return None
    return round(row["amount"] / 2, 2)


def get_electricity_estimate_for_month(year: int, month: int) -> dict | None:
    """
    Smart prorated electricity estimate for a target month.

    Step 1 — find a bill whose period covers (overlaps) the target month.
    Step 2 — if none, try the same month last year.
    Step 3 — return None if no historical data.

    Returns:
        {"estimate": float, "is_estimate": bool, "overlap_days": int, "billing_days": int}
        or None.
    """
    import calendar

    def _prorate(row, ms: date, me: date) -> dict:
        ps = date.fromisoformat(row["period_start"])
        pe = date.fromisoformat(row["period_end"])
        overlap_start = max(ps, ms)
        overlap_end   = min(pe, me)
        overlap_days  = max((overlap_end - overlap_start).days + 1, 0)
        billing_days  = row["billing_days"] or max((pe - ps).days, 1)
        return {
            "estimate":     round(row["amount"] * overlap_days / billing_days, 2),
            "overlap_days": overlap_days,
            "billing_days": billing_days,
        }

    def _find_covering_bill(conn, y: int, m: int):
        m_start = date(y, m, 1).isoformat()
        m_end   = date(y, m, calendar.monthrange(y, m)[1]).isoformat()
        return conn.execute(
            """SELECT * FROM expenses
               WHERE category='electricity'
                 AND (is_correction=0 OR is_correction IS NULL)
                 AND period_start IS NOT NULL AND period_end IS NOT NULL
                 AND period_start <= ? AND period_end >= ?
               ORDER BY date DESC LIMIT 1""",
            (m_end, m_start),
        ).fetchone(), date(y, m, 1), date(y, m, calendar.monthrange(y, m)[1])

    with get_connection() as conn:
        row, ms, me = _find_covering_bill(conn, year, month)
        if row:
            result = _prorate(row, ms, me)
            result["is_estimate"] = False
            return result

        row, ms, me = _find_covering_bill(conn, year - 1, month)
        if row:
            result = _prorate(row, ms, me)
            result["is_estimate"] = True
            return result

    return None


def log_agent_run(agent_name: str, run_date: str, status: str,
                  records_fetched: int = 0, error_message: str = None,
                  duration_seconds: float = None) -> int:
    with get_connection() as conn:
        cur = conn.execute(
            """INSERT INTO agent_logs
               (agent_name, run_date, status, records_fetched, error_message, duration_seconds)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (agent_name, run_date, status, records_fetched, error_message, duration_seconds),
        )
        return cur.lastrowid


def get_agent_logs(agent_name: str = None, limit: int = 50) -> list[sqlite3.Row]:
    with get_connection() as conn:
        if agent_name:
            return conn.execute(
                "SELECT * FROM agent_logs WHERE agent_name = ? ORDER BY created_at DESC LIMIT ?",
                (agent_name, limit),
            ).fetchall()
        return conn.execute(
            "SELECT * FROM agent_logs ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()


def get_last_agent_run(agent_name: str) -> sqlite3.Row | None:
    with get_connection() as conn:
        return conn.execute(
            "SELECT * FROM agent_logs WHERE agent_name = ? ORDER BY created_at DESC LIMIT 1",
            (agent_name,),
        ).fetchone()


# ---------------------------------------------------------------------------
# Profit calculation helper
# ---------------------------------------------------------------------------

def calculate_estimated_profit(month: int, year: int) -> dict:
    """
    Return a dict with all components + estimated profit for the given month.
    Applies a days-elapsed ratio to fixed expenses.
    """
    import calendar
    today = date.today()
    days_in_month = calendar.monthrange(year, month)[1]

    if year == today.year and month == today.month:
        days_passed = today.day
    elif date(year, month, 1) < today.replace(day=1):
        days_passed = days_in_month  # past month — full
    else:
        days_passed = 0  # future

    ratio = days_passed / days_in_month if days_in_month else 0

    income = get_total_income(month, year)
    expenses_by_cat = get_total_expenses_by_category(month, year)
    goods = expenses_by_cat.get("goods", 0)
    electricity = expenses_by_cat.get("electricity", 0)

    fixed_total = get_total_fixed_expenses()
    fixed_prorated = fixed_total * ratio

    salary = get_total_salary_cost(month, year)

    profit = income - goods - electricity - fixed_prorated - salary

    # is_finalized if all active employees have finalized hours this month
    hours_rows = get_employee_hours(month, year)
    is_finalized = bool(hours_rows) and all(row["is_finalized"] for row in hours_rows)

    return {
        "month": month,
        "year": year,
        "days_passed": days_passed,
        "days_in_month": days_in_month,
        "ratio": ratio,
        "income": income,
        "goods": goods,
        "electricity": electricity,
        "fixed_prorated": fixed_prorated,
        "salary": salary,
        "profit": profit,
        "is_finalized": is_finalized,
    }
