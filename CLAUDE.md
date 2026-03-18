# Makolet Dashboard - מערכת ניהול כספי למכולת

## Overview

Automated financial management system for a small grocery store (מכולת).
Agents fetch data nightly from APIs and email, store in SQLite, and display via a Flask dashboard.
Notifications go via Telegram (primary) and WhatsApp (fallback).

---

## Architecture

```
scheduler.py (APScheduler, Asia/Jerusalem)
├── agents/bilboy.py           → goods invoices via BilBoy REST API
├── agents/aviv_alerts.py      → daily sales via Gmail IMAP + PDF extraction
├── agents/electricity.py      → electricity bills via Gmail IMAP + PDF
├── agents/employee_hours.py   → attendance CSV via Gmail IMAP
└── agents/base_agent.py       → abstract base: retry logic, pending fetches, alerts
         ↓
    database/db.py (SQLite, raw sql, no ORM)
         ↓
    dashboard/app.py (Flask + Flask-Login, Chart.js frontend)
         ↓
    notifications/whatsapp.py (Telegram primary, WhatsApp fallback)
```

---

## Directory Structure

```
MakoletDashboard/
├── CLAUDE.md
├── README.md
├── requirements.txt
├── run.py                         ← Flask entry point
├── scheduler.py                   ← APScheduler nightly orchestrator
├── .env                           ← secrets (not in git)
├── .gitignore
│
├── agents/
│   ├── __init__.py
│   ├── base_agent.py              ← ABC with retry, pending fetches, notifications
│   ├── bilboy.py                  ← BilBoy REST API agent (token auth, auto-refresh)
│   ├── aviv_alerts.py             ← Gmail IMAP → Z-report PDFs → daily sales
│   ├── electricity.py             ← Gmail IMAP → IEC bill PDFs → expenses
│   ├── employee_hours.py          ← Gmail IMAP → attendance CSV → hours
│   └── parse_attendance_csv.py    ← CSV parser helper for employee_hours
│
├── database/
│   ├── __init__.py
│   ├── db.py                      ← SQLite connection + all CRUD functions (~650 LOC)
│   ├── models.py                  ← CREATE TABLE statements + migrations
│   └── makolet.db                 ← SQLite file (generated, not in git)
│
├── dashboard/
│   ├── __init__.py
│   ├── app.py                     ← Flask server with auth (~1400 LOC)
│   ├── templates/
│   │   ├── index.html             ← Home dashboard with KPI cards + charts
│   │   ├── login.html
│   │   ├── sales.html             ← Daily sales view
│   │   ├── goods.html             ← BilBoy documents table
│   │   ├── electricity_history.html
│   │   ├── employees.html         ← Staff management + CSV upload
│   │   └── fixed_expenses.html
│   └── static/
│       ├── css/style.css          ← RTL-aware styling
│       ├── js/charts.js           ← Chart.js integration
│       └── makolet_logo.{png,jpg}
│
├── notifications/
│   ├── __init__.py
│   └── whatsapp.py                ← Telegram + WhatsApp (Green API) alerts
│
├── scripts/                       ← Maintenance & backfill utilities
│   ├── backfill_aviv.py
│   ├── backfill_bilboy.py
│   ├── bilboy_verify.py
│   ├── bilboy_deep_audit.py
│   ├── load_historical_electricity.py
│   ├── import_z_pdfs.py
│   ├── migrate_db.py
│   ├── debug_electricity_pdf.py
│   ├── debug_historical_email.py
│   └── deep_test.py
│
└── tests/
    ├── __init__.py
    ├── test_base_agent.py
    ├── test_bilboy.py
    ├── test_aviv_alerts.py
    ├── test_electricity.py
    ├── test_employee_hours.py
    ├── test_db.py
    └── test_whatsapp.py
```

---

## Tech Stack

| Component | Technology | Notes |
|-----------|-----------|-------|
| Data fetching | Gmail IMAP + REST APIs | No browser scraping currently used |
| PDF parsing | pdfplumber | Z-reports, electricity bills |
| Backend | Flask + Flask-Login | Role-based auth (admin/viewer) |
| Database | SQLite (raw sql) | No ORM, parameterized queries, sqlite3.Row |
| Frontend | HTML + Chart.js | Vanilla JS, RTL layout, no build tools |
| Scheduling | APScheduler | BlockingScheduler, Asia/Jerusalem timezone |
| Notifications | Telegram (primary) | WhatsApp via Green API as fallback |

---

## Data Sources

### 1. Aviv POS (Daily Sales) — `agents/aviv_alerts.py`
- **Method:** Gmail IMAP — searches for emails from `AVIV_SENDER_EMAIL` with subject "דוח סוף יום"
- **Data:** PDF attachment (filename starting with "z_") → extracts total income via pdfplumber
- **Schedule:** Nightly at 02:00; expected Sun–Fri, Saturday only on month-end
- **Credentials:** `GMAIL_ADDRESS`, `GMAIL_APP_PASSWORD`, `AVIV_SENDER_EMAIL`

### 2. BilBoy (Goods/Invoices) — `agents/bilboy.py`
- **Method:** REST API at `https://app.billboy.co.il:5050/api`
- **Data:** All document types (deliveries, invoices, credits, returns, receipts)
- **Auth:** Bearer token with auto-refresh on 401. Token requires initial manual OTP
- **Schedule:** Nightly at 02:00 + Saturday full-month reconciliation at 02:30
- **Credentials:** `BILBOY_TOKEN`

### 3. Electricity (IEC Bills) — `agents/electricity.py`
- **Method:** Gmail IMAP — emails from `noreplys@iec.co.il` with contract number in subject
- **Data:** Bill amount, billing period, PDF attachment
- **Features:** Marks multi-month bills as corrections, calculates prorated monthly estimates
- **Credentials:** `GMAIL_ADDRESS`, `GMAIL_APP_PASSWORD`

### 4. Employee Hours — `agents/employee_hours.py`
- **Method:** Gmail IMAP — emails with subject "נוכחות באקסל"
- **Data:** Hebrew tab-separated CSV with attendance data
- **Schedule:** Days 1–5 of month only, skipped if already finalized
- **Features:** Fuzzy name matching between CSV and DB employees
- **Credentials:** `GMAIL_ADDRESS`, `GMAIL_APP_PASSWORD`

### 5. Municipality (Arnona) — NOT IMPLEMENTED
- Planned but not yet built

---

## Database Schema (9 tables)

### `daily_sales` — Z-report income
```sql
id, date, total_income, source, pdf_path, created_at
```

### `expenses` — All expense records
```sql
id, date, category, amount, description, source, created_at,
-- electricity columns:
is_correction, pdf_filename, period_start, period_end, billing_days,
-- bilboy columns:
ref_number, total_without_vat, doc_type, doc_type_name, paid
```
Categories: `goods` | `electricity` | `arnona` | `rent` | `salary` | `vat` | `insurance` | `internet`

### `employees` — Staff database
```sql
id, name, hourly_rate, is_active, shift, created_at, deleted_at
```

### `employee_hours` — Monthly hours per employee (by ID)
```sql
id, employee_id, month, year, hours_worked, is_finalized, created_at
```

### `employee_monthly_hours` — CSV-uploaded attendance (by name)
```sql
id, employee_name, month, total_hours, total_salary, uploaded_at
-- UNIQUE(employee_name, month)
```

### `employee_rate_history` — Historical hourly rates
```sql
id, employee_id, hourly_rate, effective_from, effective_to, created_at
```

### `fixed_expenses` — Manual recurring costs
```sql
id, category, amount, valid_from, valid_until, notes
```

### `agent_logs` — Agent execution history
```sql
id, agent_name, run_date, status, records_fetched, error_message, duration_seconds, created_at
```

### `pending_fetches` — Failed fetch retry tracking
```sql
id, agent, date, reason, attempts, created_at, last_attempt_at, resolved_at
-- UNIQUE(agent, date)
```

---

## Agent Base Class

All agents inherit from `BaseAgent` (ABC) and must implement:
```python
class MyAgent(BaseAgent):
    name = "my_agent"                   # unique agent identifier
    def fetch_data(self) -> list: ...   # fetch from external source
    def save_to_db(self, data) -> None: ... # persist to database
```

`BaseAgent.run()` provides:
- Up to 3 retry attempts with 5-second delays
- Automatic `agent_logs` recording (success/failure + timing)
- `pending_fetches` tracking for failed dates (retried on next run)
- Telegram/WhatsApp alerts on failure, recovery, and exhausted retries

Optional override: `fetch_data_for_date(target_date)` for date-specific fetching.

---

## Scheduler Jobs

| Job | Schedule | Description |
|-----|----------|-------------|
| `nightly_job` | Daily 02:00 | Runs bilboy + aviv_alerts always; employee_hours on days 1–5 if not finalized |
| `saturday_reconciliation` | Saturday 02:30 | Full-month BilBoy delete+re-insert reconciliation |

On startup, `nightly_job` runs once immediately for testing.
After each nightly run, a Hebrew summary is sent via Telegram.

---

## Notifications

**Primary:** Telegram via Bot API (`TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`)
**Fallback:** WhatsApp via Green API (`GREENAPI_INSTANCE_ID`, `GREENAPI_TOKEN`, `WHATSAPP_PHONE`)

Alerts sent for:
- Agent failure after 3 retries
- Pending fetch recovery / exhaustion
- Missing Z-reports (checked nightly, past 7 days)
- Nightly summary (always)
- Saturday reconciliation results

---

## Environment Variables (.env)

```env
# Gmail IMAP (used by aviv_alerts, electricity, employee_hours)
GMAIL_ADDRESS=
GMAIL_APP_PASSWORD=           # Google App Password (not regular password)

# Aviv
AVIV_SENDER_EMAIL=            # Email address that sends Z-reports

# BilBoy API
BILBOY_TOKEN=                 # Bearer token (manual OTP setup, auto-refresh)

# Telegram (primary notifications)
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=

# WhatsApp / Green API (fallback)
WHATSAPP_PHONE=972XXXXXXXXX
GREENAPI_INSTANCE_ID=
GREENAPI_API_URL=
GREENAPI_TOKEN=

# Flask
FLASK_SECRET_KEY=
DASHBOARD_PORT=8080
```

---

## Running the Project

```bash
# Install dependencies
pip install -r requirements.txt

# Configure environment
cp .env.example .env   # edit with real credentials

# Run the dashboard
python run.py

# Run the scheduler (blocks, runs agents nightly)
python scheduler.py
```

---

## Running Tests

```bash
python -m pytest tests/
```

Tests cover all agents, database CRUD, base agent retry logic, and notification routing.

---

## Development Guidelines

- **Agent pattern:** Always inherit from `BaseAgent`, implement `fetch_data()` and `save_to_db()`
- **Database:** Use `get_connection()` context manager, parameterized queries only, no ORM
- **Migrations:** Add new columns via `ALTER TABLE` with try/except in `models.py` (idempotent)
- **Credentials:** Never hardcode — always from `.env` via `os.getenv()`
- **Error handling:** Agents should raise exceptions; `BaseAgent.run()` handles retries and logging
- **Frontend:** RTL layout (Hebrew), vanilla JS + Chart.js, no build process
- **Dates:** Store as ISO format `YYYY-MM-DD` in DB, display as `DD/MM/YYYY` in UI
- **Language:** Code in English, UI and notifications in Hebrew

---

## Not Yet Implemented

- `agents/municipality.py` — Arnona (property tax) agent
- CI/CD pipeline
- Docker / deployment automation
- `.env.example` template file
