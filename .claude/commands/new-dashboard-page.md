# New Dashboard Page

Create a new dashboard page for MakoletDashboard following the design system below.

The page name and purpose are: $ARGUMENTS

---

## 1. Design System

### Layout
- **RTL throughout**: every HTML element must have `dir="rtl"` on `<html>` and `direction: rtl` in CSS.
- Hebrew is the primary language. All labels, headings, and UI copy must be in Hebrew.

### Color Palette
| Token | Hex | Usage |
|-------|-----|-------|
| `--bg` | `#0f172a` | Page background (dark navy) |
| `--surface` | `#1e293b` | Navbar, sidebar, secondary surfaces |
| `--card` | `#ffffff` | Card backgrounds |
| `--card-border` | `#e2e8f0` | Card borders |
| `--profit` | `#22c55e` | Positive numbers, profit, income |
| `--loss` | `#ef4444` | Negative numbers, loss, expenses |
| `--neutral` | `#3b82f6` | Info, neutral metrics, links |
| `--text-primary` | `#1e293b` | Main body text (on white cards) |
| `--text-secondary` | `#64748b` | Labels, captions |
| `--text-light` | `#f8fafc` | Text on dark backgrounds |

Define all tokens as CSS variables on `:root`.

### Typography
- Font stack: `'Segoe UI', 'Arial', sans-serif` — clean and Hebrew-compatible.
- No Google Fonts (avoid network dependency).
- Card titles: `0.85rem`, uppercase, `--text-secondary`.
- KPI values: `2rem`, bold, colored by type (profit/loss/neutral).
- Table headers: `0.8rem`, uppercase, `--text-secondary`.

### Card Component
Every card must use this exact style pattern:
```css
.card {
    background: var(--card);
    border: 1px solid var(--card-border);
    border-radius: 12px;
    padding: 1.5rem;
    box-shadow: 0 1px 3px rgba(0,0,0,0.08), 0 1px 2px rgba(0,0,0,0.06);
}
```

---

## 2. Page Structure

Build the page in this exact order top to bottom:

### A. Navbar
```html
<nav class="navbar">
    <!-- Right side: store name + page title -->
    <!-- Left side: current month label (dynamic, from JS) -->
</nav>
```
- Background: `--surface`, text: `--text-light`.
- Store name: "מכולת" or the relevant section title.
- Current month: rendered by JS as `new Date().toLocaleDateString('he-IL', { month: 'long', year: 'numeric' })`.

### B. Summary Cards Row (KPI Cards)
- A responsive row of 3–4 `.kpi-card` elements inside `.cards-grid`.
- Each card shows: a Hebrew label, a `₪` value (or unit), and a colored indicator.
- Color-code the value: green for income/profit, red for expenses/loss, blue for neutral counts.
- Grid: `grid-template-columns: repeat(auto-fit, minmax(200px, 1fr))`.

### C. Chart Section
- One or two `<canvas>` elements wrapped in `.card.chart-card`.
- Always use **Chart.js** (loaded from the existing `/static/js/charts.js` or CDN).
- Chart colors must use the palette tokens (convert hex to rgba for fills).
- Include a chart title above the canvas in Hebrew.

### D. Data Table
- A `.card` containing a `<table class="data-table">`.
- `<thead>` with Hebrew column names.
- `<tbody>` populated by JS from the API response.
- Zebra striping: odd rows `#f8fafc`, hover: `#f1f5f9`.
- Money columns: right-align, prepend `₪`.
- Dates: format with `toLocaleDateString('he-IL')`.

---

## 3. Mandatory Requirements for Every Page

### Flask API Integration
- Define a matching Flask route in `dashboard/app.py` that returns JSON.
- The HTML page fetches from that endpoint using `fetch('/api/<endpoint>')`.
- Always wrap the fetch in `async/await` with a `try/catch`.

### Loading State
Every section that loads data must show a spinner while fetching:
```html
<div class="loading" id="<section>-loading">
    <div class="spinner"></div>
    <span>טוען נתונים...</span>
</div>
```
```css
.spinner {
    width: 32px; height: 32px;
    border: 3px solid #e2e8f0;
    border-top-color: var(--neutral);
    border-radius: 50%;
    animation: spin 0.8s linear infinite;
}
@keyframes spin { to { transform: rotate(360deg); } }
```
Hide the loading div and show the content once data arrives.

### Money Formatting
Use a single helper function for all monetary values — never format inline:
```js
function formatMoney(amount) {
    return '₪\u202F' + Number(amount).toLocaleString('he-IL', {
        minimumFractionDigits: 2,
        maximumFractionDigits: 2
    });
}
```

### Responsive / Mobile
- Use CSS Grid with `auto-fit` / `minmax` — no fixed pixel widths on layout containers.
- Navbar collapses gracefully on small screens (stack vertically below 600px).
- Table: wrap in `overflow-x: auto` so it scrolls horizontally on mobile.
- Cards stack to single column on screens < 600px.

### Error State
If the API call fails, show an error message inside the section:
```js
container.innerHTML = '<p class="error-msg">שגיאה בטעינת הנתונים. נסה שוב מאוחר יותר.</p>';
```
```css
.error-msg { color: var(--loss); text-align: center; padding: 2rem; }
```

---

## 4. File Checklist

When creating a new page, produce **all** of the following:

- [ ] `dashboard/templates/<page-name>.html` — the full page
- [ ] Flask route in `dashboard/app.py`: `@app.route('/<page-name>')` + `@app.route('/api/<page-name>')`
- [ ] Any new CSS in `dashboard/static/css/style.css` (add, don't overwrite)
- [ ] Any new JS in `dashboard/static/js/charts.js` if chart logic is reusable

---

## 5. HTML Skeleton

Use this as the starting point for every new page:

```html
<!DOCTYPE html>
<html lang="he" dir="rtl">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title><!-- Page title in Hebrew --> - מכולת דשבורד</title>
    <link rel="stylesheet" href="{{ url_for('static', filename='css/style.css') }}">
    <style>
        /* Page-specific styles only — shared styles live in style.css */
    </style>
</head>
<body>
    <nav class="navbar">
        <div class="navbar-brand">
            <span class="store-name">מכולת</span>
            <span class="page-title"><!-- Hebrew page name --></span>
        </div>
        <div class="navbar-meta">
            <span id="current-month"></span>
        </div>
    </nav>

    <main class="main-content">

        <!-- KPI Cards -->
        <section class="cards-grid" id="kpi-section">
            <div class="loading" id="kpi-loading">
                <div class="spinner"></div>
                <span>טוען נתונים...</span>
            </div>
            <!-- Cards injected by JS -->
        </section>

        <!-- Chart -->
        <section class="card chart-card">
            <h2 class="section-title"><!-- Chart title --></h2>
            <div class="loading" id="chart-loading">
                <div class="spinner"></div>
                <span>טוען גרף...</span>
            </div>
            <canvas id="main-chart" style="display:none"></canvas>
        </section>

        <!-- Data Table -->
        <section class="card">
            <h2 class="section-title"><!-- Table title --></h2>
            <div style="overflow-x: auto">
                <table class="data-table">
                    <thead>
                        <tr>
                            <!-- Hebrew column headers -->
                        </tr>
                    </thead>
                    <tbody id="table-body">
                        <tr>
                            <td colspan="99">
                                <div class="loading">
                                    <div class="spinner"></div>
                                    <span>טוען...</span>
                                </div>
                            </td>
                        </tr>
                    </tbody>
                </table>
            </div>
        </section>

    </main>

    <script src="https://cdn.jsdelivr.net/npm/chart.js@4"></script>
    <script src="{{ url_for('static', filename='js/charts.js') }}"></script>
    <script>
        // Set current month in navbar
        document.getElementById('current-month').textContent =
            new Date().toLocaleDateString('he-IL', { month: 'long', year: 'numeric' });

        function formatMoney(amount) {
            return '₪\u202F' + Number(amount).toLocaleString('he-IL', {
                minimumFractionDigits: 2,
                maximumFractionDigits: 2
            });
        }

        async function loadData() {
            try {
                const res = await fetch('/api/<!-- endpoint -->');
                if (!res.ok) throw new Error(`HTTP ${res.status}`);
                const data = await res.json();
                renderKpis(data);
                renderChart(data);
                renderTable(data);
            } catch (err) {
                console.error(err);
                document.getElementById('kpi-section').innerHTML =
                    '<p class="error-msg">שגיאה בטעינת הנתונים. נסה שוב מאוחר יותר.</p>';
            }
        }

        function renderKpis(data) {
            // Remove loading spinner, inject .kpi-card elements
        }

        function renderChart(data) {
            // Hide chart-loading, show canvas, init Chart.js
        }

        function renderTable(data) {
            // Populate #table-body with <tr> rows
        }

        loadData();
    </script>
</body>
</html>
```
