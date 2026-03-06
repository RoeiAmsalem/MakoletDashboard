"""
Flask dashboard server for MakoletDashboard.

Routes:
    GET /               → home page (מסך בית - estimated profit)
    GET /api/summary    → current month KPIs + estimated profit
    GET /api/history    → last 6 months profit breakdown
"""

import os
from datetime import date
from dateutil.relativedelta import relativedelta

from flask import Flask, jsonify, render_template
from dotenv import load_dotenv

from database.db import calculate_estimated_profit, init_db

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "dev-secret-key")

# Ensure DB tables exist on startup
init_db()


# ---------------------------------------------------------------------------
# Page routes
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    return render_template("index.html")


# ---------------------------------------------------------------------------
# API routes
# ---------------------------------------------------------------------------

@app.route("/api/summary")
def api_summary():
    """Return current month KPIs and estimated profit."""
    today = date.today()
    data = calculate_estimated_profit(today.month, today.year)
    return jsonify(data)


@app.route("/api/history")
def api_history():
    """Return profit breakdown for the last 6 months (oldest → newest)."""
    today = date.today()
    months = []
    for i in range(5, -1, -1):
        target = today - relativedelta(months=i)
        row = calculate_estimated_profit(target.month, target.year)
        # Add a human-readable Hebrew month label
        label = date(target.year, target.month, 1).strftime("%-m/%Y")
        row["label"] = label
        months.append(row)
    return jsonify(months)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    port = int(os.getenv("DASHBOARD_PORT", 5000))
    app.run(debug=True, port=port)
