# CLAUDE.md - MakoletDashboard

This file provides guidance to Claude Code when working with code in this repository.
Read this file completely before writing any code.

---

## Project Overview

מערכת דשבורד אוטומטית לניהול כספי של מכולת.
**צבא סוכנים** שמושכים נתונים כל לילה ממקורות שונים → DB → דשבורד ויזואלי.

---

## Architecture - Multi-Agent System

```
Orchestrator (scheduler.py)
├── 🤖 agents/aviv_alerts.py     → קורא מייל יומי → הכנסות קופה
├── 🤖 agents/bilboy.py          → API של BilBoy → הוצאות סחורה
└── 🤖 agents/electricity.py     → scraping חברת חשמל → חשבון חשמל
         ↓
    📦 database/db.py (SQLite)
         ↓
    🌐 dashboard/app.py (Flask + HTML/Chart.js)
         ↓
    📱 notifications/whatsapp.py (CallMeBot - התראות כשסוכן נכשל)
```

---

## Stack

| רכיב | טכנולוגיה |
|------|-----------|
| Scraping | Playwright (Python) |
| מייל | imaplib (Python built-in) |
| Backend | Flask |
| Database | SQLite |
| Frontend | HTML + Chart.js |
| תזמון | APScheduler |
| התראות | CallMeBot (WhatsApp) |

---

## Directory Structure

```
MakoletDashboard/
├── CLAUDE.md
├── README.md
├── requirements.txt
├── .env                        ← סיסמאות (לא ב-git!)
├── .env.example
├── .gitignore
│
├── agents/
│   ├── __init__.py
│   ├── base_agent.py           ← מחלקת בסיס לכל הסוכנים
│   ├── aviv_alerts.py          ← קורא מייל מאביב התראות
│   ├── bilboy.py               ← API של BilBoy (JWT token)
│   └── electricity.py          ← scraping חברת חשמל
│
├── database/
│   ├── __init__.py
│   ├── db.py                   ← חיבור + פונקציות CRUD
│   ├── models.py               ← הגדרת טבלאות
│   └── makolet.db              ← קובץ DB (לא ב-git!)
│
├── dashboard/
│   ├── app.py                  ← Flask server
│   ├── templates/
│   │   ├── index.html          ← מסך בית - רווח משוער שוטף
│   │   ├── employees.html      ← ניהול עובדים ותעריפים
│   │   └── history.html        ← השוואות חודשיות
│   └── static/
│       ├── css/style.css
│       └── js/charts.js
│
├── notifications/
│   ├── __init__.py
│   └── whatsapp.py
│
├── scheduler.py                ← תזמון לילי
└── run_all_agents.py           ← הרצה ידנית של כל הסוכנים
```

---

## Data Sources

### 1. BilBoy (סחורה) - agents/bilboy.py ✅ כבר נכתב
- **שיטה:** REST API עם JWT Token
- **API Base:** `https://app.billboy.co.il:5050/api`
- **Flow:** GET /user/branches → GET /customer/suppliers → GET /customer/docs/headers
- **Auth:** Bearer token מ-`.env` → `BILBOY_TOKEN`
- **⚠️ חשוב:** Login הוא OTP לטלפון - לא ניתן לאוטומציה. הטוקן נשמר ב-.env ומחודש ידנית כשפג.

### 2. אביב התראות (הכנסות קופה) - agents/aviv_alerts.py
- **שיטה:** קריאת מייל יומי דרך IMAP
- **credentials:** `GMAIL_ADDRESS`, `GMAIL_APP_PASSWORD`, `AVIV_SENDER_EMAIL`

### 3. חברת חשמל - agents/electricity.py
- **שיטה:** Playwright scraping
- **URL:** https://www.iec.co.il
- **credentials:** `ELECTRIC_USERNAME`, `ELECTRIC_PASSWORD`

### 4. עובדים (ידני)
- Aviv BI נותן שעות בלבד (אפליקציה בטלפון - אין API)
- הזנה ידנית חודשית בדשבורד
- תעריף לכל עובד מוגדר בדשבורד

---

## Database Schema

### daily_sales
```sql
id, date, total_income, source, created_at
```

### expenses
```sql
id, date, category, amount, description, source, created_at
```
categories: `goods` | `electricity` | `arnona` | `rent` | `salary` | `vat` | `insurance` | `internet`

### employees
```sql
id, name, hourly_rate, is_active, created_at
```

### employee_hours
```sql
id, employee_id, month, year, hours_worked, is_finalized, created_at
```

### fixed_expenses
```sql
id, category, amount, valid_from, valid_until, notes
```

### agent_logs
```sql
id, agent_name, run_date, status, records_fetched, error_message, duration_seconds, created_at
```

---

## Estimated Profit Logic (מסך הבית)

```python
def calculate_estimated_profit(month, year):
    ratio = days_passed / days_in_month

    income   = sum(daily_sales)           # מאביב התראות
    goods    = sum(bilboy_invoices)        # מ-BilBoy
    electric = last_electric_bill         # מחברת חשמל
    fixed    = (rent + arnona + insurance + internet + vat) * ratio
    salaries = sum(hours * hourly_rate)   # שעות שהוזנו × תעריף

    return income - goods - electric - fixed - salaries
```

מוצג עם תווית "משוער" + ירוק/אדום.
כשמזינים שעות סופיות → הופך ל"סופי".

---

## Base Agent Pattern

כל סוכן חייב לרשת מ-BaseAgent:
```python
class BaseAgent:
    def run(self) -> dict:       # {"success": bool, "data": [...], "error": str}
    def fetch_data(self) -> list
    def save_to_db(self, data)
    # retry אוטומטי 3 פעמים לפני כישלון
    # כל כישלון → agent_logs + WhatsApp alert
```

---

## Agent Rules (חשוב!)

- Playwright תמיד עם `headless=True` בפרודקשן
- המתן לאלמנטים עם `wait_for_selector` - לא `time.sleep`
- תפוס exceptions ושמור ב-`agent_logs`
- נסה 3 פעמים לפני כישלון סופי
- אל תשמור credentials בקוד - רק מ-`.env`
- שמור screenshots רק כשיש שגיאה (debug)

---

## Environment Variables (.env)

```env
# BilBoy
BILBOY_TOKEN=                   # JWT token - מחדשים ידנית כשפג

# Gmail - אביב התראות
GMAIL_ADDRESS=
GMAIL_APP_PASSWORD=             # App Password של Google!
AVIV_SENDER_EMAIL=              # כתובת המייל של אביב

# חברת חשמל
ELECTRIC_USERNAME=
ELECTRIC_PASSWORD=

# WhatsApp התראות (CallMeBot)
WHATSAPP_PHONE=972XXXXXXXXX
WHATSAPP_API_KEY=

# Flask
FLASK_SECRET_KEY=
DASHBOARD_PORT=5000
```

---

## Development Order

1. **[שלב 1]** `database/` - models + db.py + כל הטבלאות
2. **[שלב 2]** `agents/base_agent.py` - מחלקת בסיס + retry
3. **[שלב 3]** `agents/bilboy.py` - כבר קיים, לשלב עם DB
4. **[שלב 4]** `agents/aviv_alerts.py` - קריאת מייל
5. **[שלב 5]** `dashboard/` - Flask + מסך בית עם רווח משוער
6. **[שלב 6]** `dashboard/employees.html` - ניהול עובדים
7. **[שלב 7]** `notifications/whatsapp.py`
8. **[שלב 8]** `scheduler.py`
9. **[שלב 9]** `agents/electricity.py`
10. **[שלב 10]** Deploy לVPS

---

## Running the Project

```bash
# התקנה
pip install -r requirements.txt
playwright install chromium

# הגדרה
cp .env.example .env

# הרצת דשבורד
python dashboard/app.py

# הרצה ידנית של סוכנים
python run_all_agents.py

# תזמון לילי
python scheduler.py
```

---

## Git & GitHub Workflow

- **Repository:** https://github.com/RoeiAmsalem/MakoletDashboard
- **Branch:** `main`
- **User:** Roei Amsalem (roei_amsalem@example.com)

**Commit format:**
- `feat:` פיצ'ר חדש
- `fix:` תיקון באג
- `refactor:` שיפור קוד
- `docs:` תיעוד

**Workflow:** השלם לוגיקה → commit → push → המשך

---

## Context Window Monitor

שני scripts ב-`~/.claude/`:
- `ctxstats` - snapshot חד פעמי
- `ctxwatch` - live עם progress bar (מתרענן כל 3 שניות)

```bash
# בטרמינל נפרד
ctxwatch
```

---

## Notes

- עדכן קובץ זה ככל שהפרויקט מתפתח
- תעד החלטות ארכיטקטורה חשובות
- שמור תיעוד ברמת "תמונה כללית" - לא פרטים קטנים
