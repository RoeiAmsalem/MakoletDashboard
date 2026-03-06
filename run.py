"""
Entry point for the MakoletDashboard Flask app.

Run from the project root:
    python3 run.py

This ensures the project root is on sys.path before any dashboard
or database imports happen.
"""

import os
import sys

# Project root is the directory containing this file
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from dashboard.app import app  # noqa: E402

if __name__ == "__main__":
    port = int(os.getenv("DASHBOARD_PORT", 8080))
    app.run(host="0.0.0.0", port=port, debug=True)
