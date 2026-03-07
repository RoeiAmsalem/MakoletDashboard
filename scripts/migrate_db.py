"""
Manual DB migration script.

Run this once after pulling new code that adds columns or tables:
    python3 scripts/migrate_db.py

It is safe to run multiple times — all migrations are idempotent.
"""

import os
import sys

# Ensure project root is on path regardless of where this is executed from.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database.db import init_db, DB_PATH

if __name__ == "__main__":
    print(f"Migrating database: {DB_PATH}")
    init_db()
    print("Done — all tables and columns are up to date.")
