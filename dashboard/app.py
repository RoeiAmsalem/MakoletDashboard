"""
Flask dashboard server for MakoletDashboard.

Routes:
    GET  /login                    → login page
    POST /login                    → authenticate
    GET  /logout                   → log out + redirect to /login
    GET  /                         → home page (מסך בית - estimated profit)
    GET  /fixed-expenses           → fixed expenses management
    GET  /employees                → employees & monthly hours
    GET  /api/summary              → current month KPIs + estimated profit
    GET  /api/history              → last 6 months profit breakdown
    GET  /api/fixed-expenses       → all fixed expenses
    PUT  /api/fixed-expenses/<id>  → update expense amount
    GET  /api/employees            → all employees with current-month hours
    PUT  /api/employees/<id>       → update hourly rate + upsert monthly hours

Roles:
    admin  → full access, edit buttons visible
    viewer → read-only, edit buttons hidden
"""

import os
import sys

# Ensure project root is on sys.path regardless of where Python is invoked from.
# __file__ is always dashboard/app.py, so two dirnames up is the project root.
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from datetime import date
from dateutil.relativedelta import relativedelta

import re

from flask import Flask, abort, jsonify, redirect, render_template, request, send_from_directory, url_for
from flask_login import (
    LoginManager,
    UserMixin,
    current_user,
    login_required,
    login_user,
    logout_user,
)
from dotenv import load_dotenv

from database.db import (
    calculate_estimated_profit,
    delete_employee,
    delete_fixed_expense,
    get_all_employees,
    get_all_fixed_expenses,
    get_electricity_bills,
    get_electricity_monthly_estimate,
    get_employee_hours,
    init_db,
    insert_employee,
    insert_fixed_expense,
    toggle_employee_active,
    update_employee_rate,
    update_fixed_expense_amount,
    upsert_employee_hours,
)

_ELEC_BILLS_DIR = os.path.join(_PROJECT_ROOT, "data", "electricity_bills")

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "dev-secret-key")

# Ensure DB tables exist on startup
init_db()

# ---------------------------------------------------------------------------
# Flask-Login setup
# ---------------------------------------------------------------------------

login_manager = LoginManager(app)
login_manager.login_view = "login"          # redirect here when not authenticated
login_manager.login_message = ""            # suppress default English flash


class User(UserMixin):
    def __init__(self, user_id: str, role: str):
        self.id = user_id          # Flask-Login uses .id as the session key
        self.username = user_id
        self.role = role           # "admin" | "viewer"

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"


def _build_users() -> dict:
    """Read credentials from .env and return {username: {password, role}}."""
    users = {}
    admin_u = os.getenv("ADMIN_USERNAME", "")
    admin_p = os.getenv("ADMIN_PASSWORD", "")
    viewer_u = os.getenv("VIEWER_USERNAME", "")
    viewer_p = os.getenv("VIEWER_PASSWORD", "")
    if admin_u and admin_p:
        users[admin_u] = {"password": admin_p, "role": "admin"}
    if viewer_u and viewer_p:
        users[viewer_u] = {"password": viewer_p, "role": "viewer"}
    return users


USERS = _build_users()


@login_manager.user_loader
def load_user(user_id: str):
    data = USERS.get(user_id)
    if data:
        return User(user_id, data["role"])
    return None


# ---------------------------------------------------------------------------
# Auth routes
# ---------------------------------------------------------------------------

@app.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("index"))

    error = None
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        user_data = USERS.get(username)
        if user_data and user_data["password"] == password:
            user = User(username, user_data["role"])
            login_user(user, remember=True)
            return redirect(request.args.get("next") or url_for("index"))
        error = "שם משתמש או סיסמה שגויים"

    return render_template("login.html", error=error)


@app.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("login"))


# ---------------------------------------------------------------------------
# Page routes
# ---------------------------------------------------------------------------

@app.route("/")
@login_required
def index():
    return render_template("index.html")


@app.route("/fixed-expenses")
@login_required
def fixed_expenses():
    return render_template("fixed_expenses.html")


@app.route("/employees")
@login_required
def employees():
    return render_template("employees.html")


@app.route("/electricity-history")
@login_required
def electricity_history():
    return render_template("electricity_history.html")


# ---------------------------------------------------------------------------
# API routes
# ---------------------------------------------------------------------------

@app.route("/api/summary")
@login_required
def api_summary():
    """Return current month KPIs and estimated profit."""
    today = date.today()
    data = calculate_estimated_profit(today.month, today.year)
    return jsonify(data)


@app.route("/api/fixed-expenses", methods=["GET"])
@login_required
def api_fixed_expenses_list():
    rows = get_all_fixed_expenses()
    return jsonify([dict(r) for r in rows])


@app.route("/api/fixed-expenses", methods=["POST"])
@login_required
def api_fixed_expenses_create():
    body     = request.get_json(force=True)
    category = (body.get("category") or "").strip()
    amount   = body.get("amount", 0)
    if not category:
        return jsonify({"error": "category required"}), 400
    new_id = insert_fixed_expense(category, float(amount))
    return jsonify({"ok": True, "id": new_id}), 201


@app.route("/api/fixed-expenses/<int:expense_id>", methods=["PUT"])
@login_required
def api_fixed_expenses_update(expense_id: int):
    body = request.get_json(force=True)
    amount = body.get("amount")
    if amount is None or not isinstance(amount, (int, float)):
        return jsonify({"error": "amount required"}), 400
    update_fixed_expense_amount(expense_id, float(amount))
    return jsonify({"ok": True, "id": expense_id, "amount": float(amount)})


@app.route("/api/fixed-expenses/<int:expense_id>", methods=["DELETE"])
@login_required
def api_fixed_expenses_delete(expense_id: int):
    delete_fixed_expense(expense_id)
    return jsonify({"ok": True})


@app.route("/api/employees", methods=["GET"])
@login_required
def api_employees_list():
    today = date.today()
    employees  = get_all_employees()
    hours_rows = get_employee_hours(today.month, today.year)
    hours_by_emp = {r["employee_id"]: r for r in hours_rows}

    result = []
    for emp in employees:
        h = hours_by_emp.get(emp["id"])
        result.append({
            "id":           emp["id"],
            "name":         emp["name"],
            "hourly_rate":  emp["hourly_rate"],
            "is_active":    bool(emp["is_active"]),
            "hours_worked": h["hours_worked"] if h else None,
            "is_finalized": bool(h["is_finalized"]) if h else False,
            "hours_row_id": h["id"] if h else None,
        })
    return jsonify(result)


@app.route("/api/employees", methods=["POST"])
@login_required
def api_employees_create():
    body        = request.get_json(force=True)
    name        = (body.get("name") or "").strip()
    hourly_rate = body.get("hourly_rate", 0)
    if not name:
        return jsonify({"error": "name required"}), 400
    new_id = insert_employee(name, float(hourly_rate))
    return jsonify({"ok": True, "id": new_id}), 201


@app.route("/api/employees/<int:employee_id>", methods=["DELETE"])
@login_required
def api_employees_delete(employee_id: int):
    delete_employee(employee_id)
    return jsonify({"ok": True})


@app.route("/api/employees/<int:employee_id>/toggle", methods=["POST"])
@login_required
def api_employees_toggle(employee_id: int):
    try:
        new_state = toggle_employee_active(employee_id)
    except ValueError as e:
        return jsonify({"error": str(e)}), 404
    return jsonify({"ok": True, "is_active": new_state})


@app.route("/api/employees/<int:employee_id>", methods=["PUT"])
@login_required
def api_employees_update(employee_id: int):
    body = request.get_json(force=True)
    hourly_rate  = body.get("hourly_rate")
    hours_worked = body.get("hours_worked")

    if hourly_rate is not None:
        update_employee_rate(employee_id, float(hourly_rate))

    if hours_worked is not None:
        today = date.today()
        upsert_employee_hours(
            employee_id=employee_id,
            month=today.month,
            year=today.year,
            hours_worked=float(hours_worked),
            is_finalized=True,
        )

    return jsonify({"ok": True})


@app.route("/api/electricity/bills")
@login_required
def api_electricity_bills():
    rows = get_electricity_bills()
    return jsonify([dict(r) for r in rows])


@app.route("/api/electricity/estimate")
@login_required
def api_electricity_estimate():
    estimate = get_electricity_monthly_estimate()
    return jsonify({"monthly_estimate": estimate})


@app.route("/api/electricity/pdf/<filename>")
@login_required
def api_electricity_pdf(filename):
    """Serve an IEC bill PDF. Validates filename to prevent path traversal."""
    if not re.match(r'^[\w\-]+\.pdf$', filename, re.ASCII):
        abort(400)
    if not os.path.isdir(_ELEC_BILLS_DIR):
        abort(404)
    return send_from_directory(_ELEC_BILLS_DIR, filename)


@app.route("/api/history")
@login_required
def api_history():
    """Return profit breakdown for the last 6 months (oldest → newest)."""
    today = date.today()
    months = []
    for i in range(5, -1, -1):
        target = today - relativedelta(months=i)
        row = calculate_estimated_profit(target.month, target.year)
        label = date(target.year, target.month, 1).strftime("%-m/%Y")
        row["label"] = label
        months.append(row)
    return jsonify(months)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    port = int(os.getenv("DASHBOARD_PORT", 8080))
    app.run(debug=True, port=port)
