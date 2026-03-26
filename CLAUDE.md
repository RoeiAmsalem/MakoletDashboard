# 🏪 Makolet Dashboard - מערכת ניהול כספי למכולת

## סקירה כללית
מערכת אוטומטית שמוציאה נתונים כספיים ממספר אתרים כל לילה,
שומרת אותם ב-DB, ומציגה דשבורד ויזואלי עם השוואות חודשיות.

---

## ארכיטקטורה - Multi-Agent System

```
Orchestrator (scheduler.py)
├── 🤖 agents/aviv_pos.py        → מכירות יומיות מאביב קופות
├── 🤖 agents/bilboy.py          → חשבוניות סחורה מ-Bilboy
├── 🤖 agents/electricity.py     → חשבון חשמל מחברת חשמל ישראל
└── 🤖 agents/municipality.py   → ארנונה מפורטל העירייה
         ↓
    📦 database/db.py (SQLite)
         ↓
    🌐 dashboard/app.py (Flask + HTML/JS)
         ↓
    📱 notifications/whatsapp.py (התראות כשסוכן נכשל)
```

---

## מבנה תיקיות

```
makolet-dashboard/
├── CLAUDE.md                  ← הקובץ הזה
├── README.md
├── requirements.txt
├── .env                       ← סיסמאות ומפתחות (לא ב-git!)
├── .env.example               ← תבנית ל-.env
├── .gitignore
│
├── agents/                    ← סוכני ה-scraping
│   ├── __init__.py
│   ├── base_agent.py          ← מחלקת בסיס לכל הסוכנים
│   ├── aviv_pos.py            ← סוכן אביב קופות
│   ├── bilboy.py              ← סוכן Bilboy
│   ├── electricity.py         ← סוכן חברת חשמל
│   └── municipality.py        ← סוכן ארנונה
│
├── database/
│   ├── __init__.py
│   ├── db.py                  ← חיבור ל-SQLite + פונקציות CRUD
│   ├── models.py              ← הגדרת טבלאות
│   └── makolet.db             ← קובץ ה-DB (לא ב-git!)
│
├── dashboard/
│   ├── app.py                 ← Flask server
│   ├── templates/
│   │   └── index.html         ← דשבורד ראשי
│   └── static/
│       ├── css/style.css
│       └── js/charts.js       ← גרפים עם Chart.js
│
├── notifications/
│   ├── __init__.py
│   └── whatsapp.py            ← שליחת הודעות WhatsApp (Twilio/CallMeBot)
│
├── scheduler.py               ← מנהל התזמון הלילי (cron)
└── run_all_agents.py          ← הרצה ידנית של כל הסוכנים
```

---

## Stack טכנולוגי

| רכיב | טכנולוגיה | סיבה |
|------|-----------|------|
| Scraping | Playwright (Python) | עובד טוב עם אתרים ישראלים, תומך JS |
| Backend | Flask | פשוט, Python, מספיק לדשבורד |
| Database | SQLite | אין צורך בשרת DB נפרד |
| Frontend | HTML + Chart.js | Python-friendly, ללא build process |
| תזמון | APScheduler | ספריית Python, ללא צורך ב-cron מערכת |
| התראות | CallMeBot API | WhatsApp בחינם, ללא Twilio |

---

## מקורות נתונים

### 1. אביב קופות (מכירות)
- **URL:** להשלים
- **נתון:** סך מכירות יומי / חודשי
- **תדירות:** כל לילה ב-02:00
- **credentials:** `.env` → `AVIV_USERNAME`, `AVIV_PASSWORD`

### 2. Bilboy (סחורה)
- **URL:** https://www.bilboy.co.il
- **נתון:** סך חשבוניות לחודש
- **תדירות:** כל לילה ב-02:30
- **credentials:** `.env` → `BILBOY_USERNAME`, `BILBOY_PASSWORD`

### 3. חברת חשמל ישראל
- **URL:** https://www.iec.co.il
- **נתון:** חשבון חודשי אחרון
- **תדירות:** פעם בחודש ב-1 לחודש
- **credentials:** `.env` → `ELECTRIC_USERNAME`, `ELECTRIC_PASSWORD`

### 4. עירייה - ארנונה
- **URL:** להשלים (תלוי עיר)
- **נתון:** תשלום ארנונה חודשי
- **תדירות:** פעם ברבעון
- **credentials:** `.env` → `MUNICIPALITY_USERNAME`, `MUNICIPALITY_PASSWORD`

---

## סכמת DB

### טבלה: `daily_sales` (מכירות יומיות)
```sql
id, date, total_income, source, created_at
```

### טבלה: `expenses` (הוצאות)
```sql
id, date, category, amount, description, source, created_at
```
**קטגוריות:** `goods` | `electricity` | `arnona` | `rent` | `salary` | `vat` | `insurance` | `internet`

### טבלה: `agent_logs` (לוג סוכנים)
```sql
id, agent_name, run_date, status, records_fetched, error_message, duration_seconds, created_at
```

### טבלה: `fixed_expenses` (הוצאות קבועות ידניות)
```sql
id, category, amount, valid_from, valid_until, notes
```
לקטגוריות שאין להן אתר: שכירות, משכורות, ביטוח, אינטרנט, מע"מ

---

## מחלקת בסיס לסוכנים (base_agent.py)

כל סוכן **חייב** לרשת מ-`BaseAgent` ולממש:
```python
class BaseAgent:
    def run(self) -> dict:       # מחזיר {"success": bool, "data": [...], "error": str}
    def login(self) -> bool      # מתחבר לאתר
    def fetch_data(self) -> list # מושך את הנתונים
    def save_to_db(self, data)   # שומר ב-DB
```

---

## התראות WhatsApp

שימוש ב-**CallMeBot** (חינמי, ללא Twilio):
- הגדרה: https://www.callmebot.com/blog/free-api-whatsapp-messages/
- `.env` → `WHATSAPP_PHONE`, `WHATSAPP_API_KEY`

**מתי נשלחת הודעה:**
- סוכן נכשל לאחר 3 ניסיונות
- נתון חריג (למשל חשמל גבוה מהרגיל ב-30%)
- סיכום לילי (אופציונלי)

---

## משתני סביבה (.env)

```env
# אביב קופות
AVIV_USERNAME=
AVIV_PASSWORD=

# Bilboy
BILBOY_USERNAME=
BILBOY_PASSWORD=

# חברת חשמל
ELECTRIC_USERNAME=
ELECTRIC_PASSWORD=

# עירייה
MUNICIPALITY_USERNAME=
MUNICIPALITY_PASSWORD=

# WhatsApp התראות
WHATSAPP_PHONE=972XXXXXXXXX
WHATSAPP_API_KEY=

# כללי
FLASK_SECRET_KEY=
DASHBOARD_PORT=5000
```

---

## סדר פיתוח מומלץ

1. **[שלב 1]** `database/` - DB + models
2. **[שלב 2]** `agents/base_agent.py` - מחלקת בסיס
3. **[שלב 3]** `agents/bilboy.py` - סוכן ראשון (הכי חשוב)
4. **[שלב 4]** `agents/aviv_pos.py` - סוכן קופה
5. **[שלב 5]** `dashboard/` - דשבורד Flask
6. **[שלב 6]** `notifications/whatsapp.py` - התראות
7. **[שלב 7]** `scheduler.py` - תזמון לילי
8. **[שלב 8]** `agents/electricity.py` + `agents/municipality.py`
9. **[שלב 9]** Deploy לVPS

---

## הנחיות לסוכנים (חשוב!)

- **תמיד** השתמש ב-`playwright` עם `headless=True` בפרודקשן
- **תמיד** המתן לטעינת אלמנטים עם `wait_for_selector` - לא `sleep`
- **תמיד** תפוס exceptions ודווח ל-`agent_logs`
- **תמיד** נסה שוב 3 פעמים לפני כישלון סופי
- **אל תשמור** credentials בקוד - רק מ-`.env`
- **אל תשמור** screenshots אלא אם יש שגיאה (לצורך debug)

---

## הרצת הפרויקט

```bash
# התקנת dependencies
pip install -r requirements.txt
playwright install chromium

# הגדרת סביבה
cp .env.example .env
# ערוך את .env עם הפרטים האמיתיים

# הרצת הדשבורד
python dashboard/app.py

# הרצה ידנית של כל הסוכנים
python run_all_agents.py

# הפעלת scheduler (ירוץ כל הזמן)
python scheduler.py
```

---

## VPS Deployment

- **OS:** Ubuntu 22.04
- **הרצה רציפה:** systemd services (`makolet` + `makolet-scheduler`)
- **לוגים:** `/var/log/makolet/`
- **גיבוי DB:** cron job יומי ל-backup של `makolet.db`

**Deploy command (always restart BOTH services):**
```bash
ssh makolet "cd /opt/makolet-dashboard && git pull origin main && systemctl restart makolet && systemctl restart makolet-scheduler"
```
