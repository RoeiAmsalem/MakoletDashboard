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
    created_at  TEXT    NOT NULL DEFAULT (datetime('now'))
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

ALL_TABLES = [
    CREATE_DAILY_SALES,
    CREATE_EXPENSES,
    CREATE_EMPLOYEES,
    CREATE_EMPLOYEE_HOURS,
    CREATE_FIXED_EXPENSES,
    CREATE_AGENT_LOGS,
]


def create_tables(conn):
    """Create all tables. Safe to call multiple times (IF NOT EXISTS)."""
    cursor = conn.cursor()
    for statement in ALL_TABLES:
        cursor.execute(statement)
    conn.commit()
    _migrate_expenses_columns(conn)


def _migrate_expenses_columns(conn):
    """Add electricity-specific columns to expenses if they don't exist yet."""
    new_cols = [
        ("is_correction", "BOOLEAN DEFAULT 0"),
        ("pdf_filename",  "TEXT"),
        ("period_start",  "TEXT"),
        ("period_end",    "TEXT"),
        ("billing_days",  "INTEGER"),
    ]
    for col, definition in new_cols:
        try:
            conn.execute(f"ALTER TABLE expenses ADD COLUMN {col} {definition}")
            conn.commit()
        except Exception:
            pass  # Column already exists
    _cleanup_duplicate_electricity(conn)


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
