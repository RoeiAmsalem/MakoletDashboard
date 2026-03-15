"""
SQL CREATE TABLE statements for MakoletDashboard.
Import and call create_tables(conn) to initialize the schema.
"""

CREATE_DAILY_SALES = """
CREATE TABLE IF NOT EXISTS daily_sales (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    date        TEXT    NOT NULL,           -- YYYY-MM-DD
    total_income REAL   NOT NULL,
    source      TEXT    NOT NULL,           -- e.g. 'aviv_alerts'
    created_at  TEXT    NOT NULL DEFAULT (datetime('now'))
);
"""

CREATE_EXPENSES = """
CREATE TABLE IF NOT EXISTS expenses (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    date        TEXT    NOT NULL,           -- YYYY-MM-DD
    category    TEXT    NOT NULL,           -- goods|electricity|arnona|rent|salary|vat|insurance|internet
    amount      REAL    NOT NULL,
    description TEXT,
    source      TEXT,                       -- e.g. 'bilboy', 'electricity', 'manual'
    created_at  TEXT    NOT NULL DEFAULT (datetime('now'))
);
"""

CREATE_EMPLOYEES = """
CREATE TABLE IF NOT EXISTS employees (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT    NOT NULL,
    hourly_rate REAL    NOT NULL,
    is_active   INTEGER NOT NULL DEFAULT 1, -- 1=active, 0=inactive
    created_at  TEXT    NOT NULL DEFAULT (datetime('now')),
    deleted_at  TEXT    DEFAULT NULL
);
"""

CREATE_EMPLOYEE_HOURS = """
CREATE TABLE IF NOT EXISTS employee_hours (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    employee_id  INTEGER NOT NULL REFERENCES employees(id),
    month        INTEGER NOT NULL,          -- 1-12
    year         INTEGER NOT NULL,
    hours_worked REAL    NOT NULL,
    is_finalized INTEGER NOT NULL DEFAULT 0,-- 0=estimated, 1=final
    created_at   TEXT    NOT NULL DEFAULT (datetime('now'))
);
"""

CREATE_FIXED_EXPENSES = """
CREATE TABLE IF NOT EXISTS fixed_expenses (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    category    TEXT    NOT NULL,           -- rent|arnona|insurance|internet|vat
    amount      REAL    NOT NULL,
    valid_from  TEXT    NOT NULL,           -- YYYY-MM-DD
    valid_until TEXT,                       -- NULL = still active
    notes       TEXT
);
"""

CREATE_AGENT_LOGS = """
CREATE TABLE IF NOT EXISTS agent_logs (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_name       TEXT    NOT NULL,
    run_date         TEXT    NOT NULL,      -- YYYY-MM-DD
    status           TEXT    NOT NULL,      -- 'success' | 'failure'
    records_fetched  INTEGER DEFAULT 0,
    error_message    TEXT,
    duration_seconds REAL,
    created_at       TEXT    NOT NULL DEFAULT (datetime('now'))
);
"""

CREATE_EMPLOYEE_MONTHLY_HOURS = """
CREATE TABLE IF NOT EXISTS employee_monthly_hours (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    employee_name   TEXT    NOT NULL,
    month           TEXT    NOT NULL,           -- YYYY-MM
    total_hours     REAL    NOT NULL,
    total_salary    REAL    NOT NULL,
    uploaded_at     TEXT    DEFAULT (datetime('now')),
    UNIQUE(employee_name, month)
);
"""

CREATE_EMPLOYEE_RATE_HISTORY = """
CREATE TABLE IF NOT EXISTS employee_rate_history (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    employee_id     INTEGER NOT NULL,
    hourly_rate     REAL    NOT NULL,
    effective_from  TEXT    NOT NULL,
    effective_to    TEXT,
    created_at      TEXT    DEFAULT (datetime('now'))
);
"""

CREATE_PENDING_FETCHES = """
CREATE TABLE IF NOT EXISTS pending_fetches (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    agent           TEXT    NOT NULL,
    date            TEXT    NOT NULL,           -- YYYY-MM-DD
    reason          TEXT,
    attempts        INTEGER DEFAULT 0,
    created_at      TEXT    DEFAULT (datetime('now')),
    last_attempt_at TEXT,
    resolved_at     TEXT,                       -- NULL = still pending
    UNIQUE(agent, date)
);
"""

ALL_TABLES = [
    CREATE_DAILY_SALES,
    CREATE_EXPENSES,
    CREATE_EMPLOYEES,
    CREATE_EMPLOYEE_HOURS,
    CREATE_FIXED_EXPENSES,
    CREATE_AGENT_LOGS,
    CREATE_EMPLOYEE_MONTHLY_HOURS,
    CREATE_EMPLOYEE_RATE_HISTORY,
    CREATE_PENDING_FETCHES,
]


def create_tables(conn):
    """Create all tables. Safe to call multiple times (IF NOT EXISTS)."""
    cursor = conn.cursor()
    for statement in ALL_TABLES:
        cursor.execute(statement)
    conn.commit()
    _migrate_expenses_columns(conn)


def _migrate_expenses_columns(conn):
    """Add extra columns to expenses if they don't exist yet."""
    new_cols = [
        # electricity columns
        ("is_correction", "BOOLEAN DEFAULT 0"),
        ("pdf_filename",  "TEXT"),
        ("period_start",  "TEXT"),
        ("period_end",    "TEXT"),
        ("billing_days",  "INTEGER"),
        # bilboy columns
        ("ref_number",        "TEXT"),
        ("total_without_vat", "REAL"),
        ("doc_type",          "INTEGER"),
        ("doc_type_name",     "TEXT"),
    ]
    for col, definition in new_cols:
        try:
            conn.execute(f"ALTER TABLE expenses ADD COLUMN {col} {definition}")
            conn.commit()
        except Exception:
            pass  # Column already exists
    _cleanup_duplicate_electricity(conn)
    _migrate_daily_sales_columns(conn)
    _migrate_employees_columns(conn)


def _migrate_daily_sales_columns(conn):
    """Add pdf_path column to daily_sales if it doesn't exist yet."""
    try:
        conn.execute("ALTER TABLE daily_sales ADD COLUMN pdf_path TEXT")
        conn.commit()
    except Exception:
        pass  # Column already exists


def _migrate_employees_columns(conn):
    """Add shift and deleted_at columns to employees if they don't exist yet."""
    for col, definition in [
        ("shift", "TEXT DEFAULT ''"),
        ("deleted_at", "TEXT DEFAULT NULL"),
    ]:
        try:
            conn.execute(f"ALTER TABLE employees ADD COLUMN {col} {definition}")
            conn.commit()
        except Exception:
            pass  # Column already exists
    _seed_employee_rate_history(conn)


def _seed_employee_rate_history(conn):
    """Seed employee_rate_history for any employees that don't have an entry yet."""
    employees = conn.execute("SELECT id, hourly_rate, created_at FROM employees").fetchall()
    for emp in employees:
        exists = conn.execute(
            "SELECT id FROM employee_rate_history WHERE employee_id = ?", (emp[0],)
        ).fetchone()
        if not exists:
            from_date = emp[2][:10] if emp[2] else "2026-01-01"
            conn.execute(
                "INSERT INTO employee_rate_history (employee_id, hourly_rate, effective_from) VALUES (?, ?, ?)",
                (emp[0], emp[1], from_date),
            )
    conn.commit()


def _cleanup_duplicate_electricity(conn):
    """Remove duplicate electricity expenses (same pdf_filename, keep lowest id)."""
    conn.execute("""
        DELETE FROM expenses
        WHERE category = 'electricity'
          AND pdf_filename IS NOT NULL
          AND id NOT IN (
              SELECT MIN(id)
              FROM expenses
              WHERE category = 'electricity'
                AND pdf_filename IS NOT NULL
              GROUP BY pdf_filename
          )
    """)
    conn.commit()
