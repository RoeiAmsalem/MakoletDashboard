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

from datetime import date, datetime
from dateutil.relativedelta import relativedelta

import re

from flask import Flask, abort, jsonify, make_response, redirect, render_template, request, send_file, send_from_directory, session, url_for
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
    get_active_employees,
    get_all_daily_sales,
    get_all_employees,
    get_daily_sales_by_month,
    get_all_fixed_expenses,
    get_electricity_bills,
    get_electricity_monthly_estimate,
    get_employee_hours,
    get_employee_monthly_hours,
    get_total_monthly_salary,
    init_db,
    insert_employee,
    insert_fixed_expense,
    toggle_employee_active,
    update_employee_rate,
    update_fixed_expense_amount,
    upsert_employee_hours,
    upsert_employee_monthly_hours,
)

_ELEC_BILLS_DIR = os.path.join(_PROJECT_ROOT, "data", "electricity_bills")
_Z_PDFS_DIR = os.path.join(_PROJECT_ROOT, "data", "z_pdfs")


def _get_rate_for_month(conn, employee_id, month_str, fallback_rate):
    """Look up the hourly rate effective during a given YYYY-MM month.
    Falls back to the employee's current hourly_rate if no history found."""
    # month_str is "YYYY-MM"; derive first and last day
    first_day = month_str + "-01"
    import calendar
    y, m = int(month_str[:4]), int(month_str[5:7])
    last_day = f"{month_str}-{calendar.monthrange(y, m)[1]:02d}"
    row = conn.execute(
        "SELECT hourly_rate FROM employee_rate_history "
        "WHERE employee_id = ? AND effective_from <= ? "
        "AND (effective_to IS NULL OR effective_to >= ?) "
        "ORDER BY effective_from DESC LIMIT 1",
        (employee_id, last_day, first_day),
    ).fetchone()
    return row["hourly_rate"] if row else fallback_rate


def rematch_employee(name, hourly_rate):
    """After an employee is created or updated, find any unmatched rows in
    employee_monthly_hours where total_salary=0 and the name fuzzy-matches,
    then calculate and update their salary using the historical rate."""
    from database.db import get_connection
    conn = get_connection()
    unmatched = conn.execute(
        "SELECT id, employee_name, month, total_hours FROM employee_monthly_hours WHERE total_salary = 0"
    ).fetchall()

    # Look up employee_id for rate history
    emp_row = conn.execute(
        "SELECT id FROM employees WHERE LOWER(TRIM(name)) = ?", (name.strip().lower(),)
    ).fetchone()
    emp_id = emp_row["id"] if emp_row else None

    updated = []
    for row in unmatched:
        csv_name = row["employee_name"].strip().lower()
        db_name = name.strip().lower()
        if db_name in csv_name or csv_name in db_name:
            rate = _get_rate_for_month(conn, emp_id, row["month"], hourly_rate) if emp_id else hourly_rate
            salary = row["total_hours"] * rate
            conn.execute(
                "UPDATE employee_monthly_hours SET total_salary = ?, employee_name = ? WHERE id = ? AND total_salary = 0",
                (salary, name, row["id"]),
            )
            updated.append({"month": row["month"], "hours": row["total_hours"], "salary": salary})

    conn.commit()
    conn.close()
    return updated

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "dev-secret-key")
app.jinja_env.globals["now"] = datetime.now

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

HEBREW_MONTHS = {
    1: 'ינואר', 2: 'פברואר', 3: 'מרץ', 4: 'אפריל', 5: 'מאי', 6: 'יוני',
    7: 'יולי', 8: 'אוגוסט', 9: 'ספטמבר', 10: 'אוקטובר', 11: 'נובמבר', 12: 'דצמבר',
}


def _parse_month_param() -> date:
    """Parse selected month from URL param, session, or default to current month.

    Priority:
        1. ?month=YYYY-MM in URL → use it AND save to session
        2. session['selected_month'] → use it
        3. default → current month
    """
    raw = request.args.get("month", "")
    if raw and re.match(r'^\d{4}-\d{2}$', raw):
        try:
            y, m = int(raw[:4]), int(raw[5:7])
            if 1 <= m <= 12:
                session["selected_month"] = raw
                return date(y, m, 1)
        except ValueError:
            pass

    # Fall back to session
    saved = session.get("selected_month", "")
    if saved and re.match(r'^\d{4}-\d{2}$', saved):
        try:
            y, m = int(saved[:4]), int(saved[5:7])
            if 1 <= m <= 12:
                return date(y, m, 1)
        except ValueError:
            pass

    today = date.today()
    return date(today.year, today.month, 1)


def _month_context() -> dict:
    """Return template context dict with month switcher variables."""
    selected = _parse_month_param()
    today_first = date(date.today().year, date.today().month, 1)

    selected_month = selected.strftime("%Y-%m")
    prev_month = (selected - relativedelta(months=1)).strftime("%Y-%m")
    next_first = selected + relativedelta(months=1)
    next_month = next_first.strftime("%Y-%m") if next_first <= today_first else None
    month_display = f"{HEBREW_MONTHS[selected.month]} {selected.year}"

    return {
        "selected_month": selected_month,
        "prev_month": prev_month,
        "next_month": next_month,
        "month_display": month_display,
    }


@app.route("/")
@login_required
def index():
    return render_template("index.html", **_month_context())


@app.route("/fixed-expenses")
@login_required
def fixed_expenses():
    return render_template("fixed_expenses.html", **_month_context())


@app.route("/employees")
@login_required
def employees():
    return render_template("employees.html", **_month_context())


@app.route("/goods")
@login_required
def goods():
    return render_template("goods.html", **_month_context())


@app.route("/sales")
@login_required
def sales():
    return render_template("sales.html", **_month_context())


@app.route("/electricity-history")
@login_required
def electricity_history():
    return render_template("electricity_history.html", **_month_context())


# ---------------------------------------------------------------------------
# API routes
# ---------------------------------------------------------------------------

@app.route("/api/summary")
@login_required
def api_summary():
    """Return KPIs and estimated profit for ?month=YYYY-MM (default: current)."""
    selected = _parse_month_param()
    data = calculate_estimated_profit(selected.month, selected.year)
    # Count unmatched employees (total_salary=0 in employee_monthly_hours)
    month_str = selected.strftime("%Y-%m")
    from database.db import get_connection
    with get_connection() as conn:
        unmatched = conn.execute(
            "SELECT COUNT(*) FROM employee_monthly_hours WHERE month = ? AND total_salary = 0",
            (month_str,),
        ).fetchone()[0]
    data["unmatched_employees"] = unmatched
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
    selected = _parse_month_param()
    month_num = selected.month
    year_num = selected.year
    month_str = selected.strftime("%Y-%m")

    from database.db import get_connection

    # Auto-rematch: scan unmatched rows against active employees before returning
    active_emps = get_active_employees()
    if active_emps:
        with get_connection() as conn:
            unmatched_rows = conn.execute(
                "SELECT id, employee_name, month, total_hours FROM employee_monthly_hours "
                "WHERE total_salary = 0"
            ).fetchall()
        for row in unmatched_rows:
            csv_name = row["employee_name"].strip().lower()
            for emp in active_emps:
                db_name = emp["name"].strip().lower()
                if db_name in csv_name or csv_name in db_name:
                    with get_connection() as conn:
                        rate = _get_rate_for_month(conn, emp["id"], row["month"], emp["hourly_rate"])
                        salary = row["total_hours"] * rate
                        conn.execute(
                            "UPDATE employee_monthly_hours SET total_salary = ?, employee_name = ? WHERE id = ? AND total_salary = 0",
                            (salary, emp["name"], row["id"]),
                        )
                    break

    # Fetch all monthly_hours rows for this month
    with get_connection() as conn:
        all_monthly = conn.execute(
            "SELECT employee_name, total_hours, total_salary FROM employee_monthly_hours "
            "WHERE month = ?",
            (month_str,),
        ).fetchall()
    monthly_hours = [{"name": r[0], "hours": r[1], "salary": r[2]} for r in all_monthly]
    total_csv_count = len(monthly_hours)

    employees = get_all_employees()
    hours_rows = get_employee_hours(month_num, year_num)
    hours_by_emp = {r["employee_id"]: r for r in hours_rows}

    # Also fetch deleted employees for past-month logic
    with get_connection() as conn:
        deleted_emps = conn.execute(
            "SELECT id, name, hourly_rate, shift, deleted_at FROM employees "
            "WHERE is_active = 0 AND deleted_at IS NOT NULL"
        ).fetchall()

    # Build a lookup from monthly_hours by name for merging
    matched_monthly_names = set()

    result = []
    for emp in employees:
        h = hours_by_emp.get(emp["id"])
        emp_name_lower = emp["name"].strip().lower()

        # Try to find matching monthly_hours entry
        mh_match = None
        for mh in monthly_hours:
            mh_name_lower = mh["name"].strip().lower()
            if emp_name_lower in mh_name_lower or mh_name_lower in emp_name_lower:
                mh_match = mh
                matched_monthly_names.add(mh["name"])
                break

        result.append({
            "id":           emp["id"],
            "name":         emp["name"],
            "hourly_rate":  emp["hourly_rate"],
            "shift":        emp["shift"] if "shift" in emp.keys() else "",
            "is_active":    bool(emp["is_active"]),
            "hours_worked": mh_match["hours"] if mh_match else (h["hours_worked"] if h else None),
            "salary":       mh_match["salary"] if mh_match else None,
            "is_finalized": True if mh_match else (bool(h["is_finalized"]) if h else False),
            "hours_row_id": h["id"] if h else None,
        })

    # For monthly_hours not matched to any employee card, check deleted employees
    unmatched = []
    past_employees = []

    for mh in monthly_hours:
        if mh["name"] in matched_monthly_names:
            continue

        mh_name_lower = mh["name"].strip().lower()

        # Check if a deleted employee matches this name
        deleted_match = None
        for de in deleted_emps:
            de_name_lower = de["name"].strip().lower()
            if de_name_lower in mh_name_lower or mh_name_lower in de_name_lower:
                deleted_match = de
                break

        if deleted_match:
            # Parse deleted_at month
            deleted_at_month = deleted_match["deleted_at"][:7]  # "YYYY-MM"
            if month_str < deleted_at_month:
                # Selected month is before deletion → employee was active this month
                # Add as a normal employee card
                matched_monthly_names.add(mh["name"])
                result.append({
                    "id":           deleted_match["id"],
                    "name":         deleted_match["name"],
                    "hourly_rate":  deleted_match["hourly_rate"],
                    "shift":        deleted_match["shift"] if deleted_match["shift"] else "",
                    "is_active":    True,  # was active during this month
                    "hours_worked": mh["hours"],
                    "salary":       mh["salary"],
                    "is_finalized": True,
                    "hours_row_id": None,
                    "was_active":   True,  # historical flag
                })
            else:
                # Deleted before/during this month → show as past employee
                past_employees.append(mh)
        elif mh["salary"] == 0:
            unmatched.append(mh)
        else:
            # Has salary but no employee record at all → past employee
            past_employees.append(mh)

    matched_count = sum(1 for e in result if e["hours_worked"] is not None) + len(past_employees)

    return jsonify({
        "employees": result,
        "unmatched": unmatched,
        "monthly_hours": monthly_hours,
        "past_employees": past_employees,
        "matched_count": matched_count,
        "total_csv_count": total_csv_count,
    })


@app.route("/api/employees", methods=["POST"])
@login_required
def api_employees_create():
    body        = request.get_json(force=True)
    name        = (body.get("name") or "").strip()
    hourly_rate = body.get("hourly_rate", 0)
    shift       = (body.get("shift") or "").strip()
    if not name:
        return jsonify({"error": "name required"}), 400
    if hourly_rate <= 0:
        return jsonify({"error": "hourly_rate must be > 0"}), 400
    new_id = insert_employee(name, float(hourly_rate), shift=shift)
    # Seed initial rate history entry
    from database.db import get_connection
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO employee_rate_history (employee_id, hourly_rate, effective_from) "
            "VALUES (?, ?, date('now'))",
            (new_id, float(hourly_rate)),
        )
    rematched = rematch_employee(name, float(hourly_rate))
    return jsonify({"ok": True, "id": new_id, "rematched": rematched}), 201


@app.route("/api/employees/<int:employee_id>", methods=["DELETE"])
@login_required
def api_employees_delete(employee_id: int):
    # Soft-delete: set active=0 + deleted_at so employee_monthly_hours data stays intact
    from database.db import get_connection
    with get_connection() as conn:
        conn.execute(
            "UPDATE employees SET is_active = 0, deleted_at = datetime('now') WHERE id = ?",
            (employee_id,),
        )
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
    new_name     = body.get("name")
    new_shift    = body.get("shift")

    from database.db import get_connection

    # Update name and/or shift if provided
    if new_name is not None or new_shift is not None:
        with get_connection() as conn:
            if new_name is not None and new_shift is not None:
                conn.execute("UPDATE employees SET name = ?, shift = ? WHERE id = ?",
                             (new_name.strip(), new_shift.strip(), employee_id))
            elif new_name is not None:
                conn.execute("UPDATE employees SET name = ? WHERE id = ?",
                             (new_name.strip(), employee_id))
            else:
                conn.execute("UPDATE employees SET shift = ? WHERE id = ?",
                             (new_shift.strip(), employee_id))

    if hourly_rate is not None:
        # Close current rate history entry and insert new one
        with get_connection() as conn:
            conn.execute(
                "UPDATE employee_rate_history SET effective_to = date('now') "
                "WHERE employee_id = ? AND effective_to IS NULL",
                (employee_id,),
            )
            conn.execute(
                "INSERT INTO employee_rate_history (employee_id, hourly_rate, effective_from) "
                "VALUES (?, ?, date('now'))",
                (employee_id, float(hourly_rate)),
            )
        update_employee_rate(employee_id, float(hourly_rate))

    if hours_worked is not None:
        selected = _parse_month_param()
        upsert_employee_hours(
            employee_id=employee_id,
            month=selected.month,
            year=selected.year,
            hours_worked=float(hours_worked),
            is_finalized=True,
        )

    # Rematch unmatched employee_monthly_hours rows
    rematched = []
    if hourly_rate is not None:
        with get_connection() as conn:
            emp = conn.execute("SELECT name FROM employees WHERE id = ?", (employee_id,)).fetchone()
        if emp:
            rematched = rematch_employee(emp["name"], float(hourly_rate))

    return jsonify({"ok": True, "rematched": rematched})


@app.route("/api/employees/upload-csv", methods=["POST"])
@login_required
def api_employees_upload_csv():
    # deprecated — use automatic EmployeeHoursAgent instead
    """Accept CSV file upload, parse attendance, match to DB employees, save monthly hours."""
    if not current_user.is_admin:
        return jsonify({"error": "admin only"}), 403

    import tempfile
    from agents.parse_attendance_csv import parse_attendance_csv

    f = request.files.get("file")
    if not f:
        return jsonify({"error": "no file uploaded"}), 400

    # Parse CSV
    try:
        csv_bytes = f.read()
        parsed = parse_attendance_csv(csv_bytes)
    except Exception as e:
        return jsonify({"error": f"CSV parse error: {e}"}), 400

    if not parsed:
        return jsonify({"error": "no employee data found in CSV"}), 400

    # Determine month from request or default to current
    month_str = request.form.get("month", "")
    if not re.match(r'^\d{4}-\d{2}$', month_str):
        today = date.today()
        month_str = today.strftime("%Y-%m")

    # Get all active employees from DB
    db_employees = get_active_employees()

    matched = []
    unmatched = []

    for entry in parsed:
        csv_name = entry["name"].strip()
        csv_name_lower = csv_name.lower()

        # Find matching employee: db_name in csv_name OR csv_name in db_name
        match = None
        for emp in db_employees:
            db_name = emp["name"].strip()
            db_name_lower = db_name.lower()
            if db_name_lower in csv_name_lower or csv_name_lower in db_name_lower:
                match = emp
                break

        if match:
            salary = round(entry["hours"] * match["hourly_rate"], 2)
            upsert_employee_monthly_hours(
                employee_name=match["name"],
                month=month_str,
                total_hours=entry["hours"],
                total_salary=salary,
            )
            # Also update employee_hours table for compatibility
            month_num = int(month_str.split("-")[1])
            year_num = int(month_str.split("-")[0])
            upsert_employee_hours(
                employee_id=match["id"],
                month=month_num,
                year=year_num,
                hours_worked=entry["hours"],
                is_finalized=True,
            )
            matched.append({
                "name": match["name"],
                "csv_name": csv_name,
                "hours": entry["hours"],
                "raw_hours": entry["raw_hours"],
                "hourly_rate": match["hourly_rate"],
                "salary": salary,
            })
        else:
            unmatched.append(csv_name)

    total_salary = sum(m["salary"] for m in matched)

    return jsonify({
        "matched": matched,
        "unmatched": unmatched,
        "month": month_str,
        "total_salary": round(total_salary, 2),
    })


@app.route("/api/employees/summary")
@login_required
def api_employees_summary():
    """Return total salary for a given month from employee_monthly_hours."""
    month_str = request.args.get("month", "")
    if not re.match(r'^\d{4}-\d{2}$', month_str):
        today = date.today()
        month_str = today.strftime("%Y-%m")
    total = get_total_monthly_salary(month_str)
    rows = get_employee_monthly_hours(month_str)
    return jsonify({
        "month": month_str,
        "total_salary": total,
        "employees": [dict(r) for r in rows],
    })


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


@app.route("/api/sales")
@login_required
def api_sales_list():
    """Return daily_sales records for the selected month (newest first)."""
    selected = _parse_month_param()
    rows = get_daily_sales_by_month(selected.month, selected.year)
    return jsonify([dict(r) for r in rows])


@app.route("/api/sales/pdf/<date_str>")
@login_required
def api_sales_pdf(date_str):
    """Serve a Z-report PDF for the given date. Validates date format."""
    if not re.match(r'^\d{4}-\d{2}-\d{2}$', date_str):
        abort(400)
    filename = f"z_{date_str}.pdf"
    pdf_path = os.path.join(_Z_PDFS_DIR, filename)
    if not os.path.isfile(pdf_path):
        abort(404)
    response = make_response(send_file(
        pdf_path,
        mimetype="application/pdf",
        as_attachment=False,
        download_name=filename,
    ))
    response.headers["Content-Disposition"] = f'inline; filename="{filename}"'
    response.headers["X-Frame-Options"] = "SAMEORIGIN"
    return response


@app.route("/api/sales/pdf-image/<date_str>/<int:page>")
@login_required
def api_sales_pdf_image(date_str, page):
    """Render a Z-report PDF page as PNG using PyMuPDF."""
    import fitz

    if not re.match(r'^\d{4}-\d{2}-\d{2}$', date_str):
        abort(400)
    pdf_path = os.path.join(_Z_PDFS_DIR, f"z_{date_str}.pdf")
    if not os.path.isfile(pdf_path):
        abort(404)
    doc = fitz.open(pdf_path)
    if page < 1 or page > len(doc):
        doc.close()
        abort(404)
    pg = doc[page - 1]
    mat = fitz.Matrix(2, 2)
    pix = pg.get_pixmap(matrix=mat)
    img_bytes = pix.tobytes("png")
    doc.close()
    response = make_response(img_bytes)
    response.headers["Content-Type"] = "image/png"
    response.headers["Cache-Control"] = "public, max-age=3600"
    return response


@app.route("/api/history")
@login_required
def api_history():
    """Return profit breakdown for the last 6 months relative to ?month= (oldest → newest)."""
    selected = _parse_month_param()
    months = []
    for i in range(5, -1, -1):
        target = selected - relativedelta(months=i)
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
