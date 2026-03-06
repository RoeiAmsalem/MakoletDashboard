"""
Flask dashboard server for MakoletDashboard.

Routes:
    GET  /login          → login page
    POST /login          → authenticate
    GET  /logout         → log out + redirect to /login
    GET  /               → home page (מסך בית - estimated profit)
    GET  /api/summary    → current month KPIs + estimated profit
    GET  /api/history    → last 6 months profit breakdown

Roles:
    admin  → full access, edit buttons visible
    viewer → read-only, edit buttons hidden
"""

import os
from datetime import date
from dateutil.relativedelta import relativedelta

from flask import Flask, jsonify, redirect, render_template, request, url_for
from flask_login import (
    LoginManager,
    UserMixin,
    current_user,
    login_required,
    login_user,
    logout_user,
)
from dotenv import load_dotenv

from database.db import calculate_estimated_profit, init_db

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
    port = int(os.getenv("DASHBOARD_PORT", 5000))
    app.run(debug=True, port=port)
