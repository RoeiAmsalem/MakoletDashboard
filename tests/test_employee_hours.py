"""
Tests for agents/employee_hours.py

Covers:
  - _hhmm_to_hours()    decimal conversion
  - parse_hours_csv()   full CSV parsing
  - fetch_data()        mocked IMAP + attachment
  - save_to_db()        mocked DB
"""

import io
import csv
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email import encoders
from unittest.mock import MagicMock, patch, call

import pytest

from agents.employee_hours import (
    _hhmm_to_hours,
    parse_hours_csv,
    CSV_FILENAME_PREFIX,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_csv_bytes(rows: list[list[str]], encoding: str = "utf-8") -> bytes:
    buf = io.StringIO()
    writer = csv.writer(buf)
    for row in rows:
        writer.writerow(row)
    return buf.getvalue().encode(encoding)


def _build_email_with_csv(csv_bytes: bytes, filename: str) -> bytes:
    msg = MIMEMultipart()
    msg["From"] = "avivpost@avivpos.co.il"
    msg["Subject"] = "נוכחות באקסל"
    part = MIMEBase("application", "octet-stream")
    part.set_payload(csv_bytes)
    encoders.encode_base64(part)
    part.add_header("Content-Disposition", "attachment", filename=filename)
    msg.attach(part)
    return msg.as_bytes()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def env_vars(monkeypatch):
    monkeypatch.setenv("GMAIL_ADDRESS", "test@gmail.com")
    monkeypatch.setenv("GMAIL_APP_PASSWORD", "app-password")
    monkeypatch.setenv("AVIV_SENDER_EMAIL", "avivpost@avivpos.co.il")


@pytest.fixture()
def agent():
    from agents.employee_hours import EmployeeHoursAgent
    return EmployeeHoursAgent()


# ---------------------------------------------------------------------------
# _hhmm_to_hours
# ---------------------------------------------------------------------------

class TestHhmmToHours:
    def test_exact_hours(self):
        assert _hhmm_to_hours("40:00") == pytest.approx(40.0)

    def test_with_minutes(self):
        assert _hhmm_to_hours("33:47") == pytest.approx(33 + 47 / 60, rel=1e-4)

    def test_zero(self):
        assert _hhmm_to_hours("0:00") == pytest.approx(0.0)

    def test_invalid_raises(self):
        with pytest.raises(ValueError):
            _hhmm_to_hours("3347")


# ---------------------------------------------------------------------------
# parse_hours_csv
# ---------------------------------------------------------------------------

class TestParseHoursCsv:

    SAMPLE_ROWS = [
        ["382 רועי אמסלם", "", "", "", "", "", ""],
        ["2026-01-01", "08:00", "17:00", "", "", "", "9:00"],
        ["2026-01-02", "08:00", "16:00", "", "", "", "8:00"],
        [f"סה''כ שורות  2", "", "", "", "", "17:00", ""],   # last non-empty = 17:00
        ["383 שמעון כהן", "", "", "", "", "", ""],
        ["2026-01-01", "09:00", "18:30", "", "", "", "9:30"],
        [f"סה''כ שורות  1", "", "", "", "", "9:30", ""],
        ["", "", "", "", "", "26:30", ""],                   # grand total row (ignored)
    ]

    def test_parses_two_employees(self):
        csv_bytes = _make_csv_bytes(self.SAMPLE_ROWS)
        result = parse_hours_csv(csv_bytes)
        assert len(result) == 2

    def test_strips_id_from_name(self):
        csv_bytes = _make_csv_bytes(self.SAMPLE_ROWS)
        result = parse_hours_csv(csv_bytes)
        names = [r["name"] for r in result]
        assert "רועי אמסלם" in names
        assert "שמעון כהן" in names
        # ID numbers must NOT appear in names
        for name in names:
            assert not name[0].isdigit()

    def test_hours_decimal_conversion(self):
        csv_bytes = _make_csv_bytes(self.SAMPLE_ROWS)
        result = parse_hours_csv(csv_bytes)
        roei  = next(r for r in result if r["name"] == "רועי אמסלם")
        shimon = next(r for r in result if r["name"] == "שמעון כהן")
        assert roei["hours"]   == pytest.approx(17.0)
        assert shimon["hours"] == pytest.approx(9.5)

    def test_empty_csv_returns_empty_list(self):
        result = parse_hours_csv(b"")
        assert result == []

    def test_no_summary_row_employee_skipped(self):
        # Employee row but no "סה''כ שורות" row
        rows = [
            ["999 בלי סיכום", "", ""],
            ["2026-01-01", "08:00", "17:00"],
            # no summary row
        ]
        result = parse_hours_csv(_make_csv_bytes(rows))
        assert result == []

    def test_grand_total_row_ignored(self):
        """Row with empty first column must not produce a record."""
        rows = [
            ["", "", "", "", "", "284:13", ""],
        ]
        result = parse_hours_csv(_make_csv_bytes(rows))
        assert result == []

    def test_utf8_bom_encoding(self):
        rows = [
            ["100 יעל לוי", ""],
            [f"סה''כ שורות  5", "", "", "", "", "20:00"],
        ]
        csv_bytes = _make_csv_bytes(rows, encoding="utf-8-sig")
        result = parse_hours_csv(csv_bytes)
        assert len(result) == 1
        assert result[0]["name"] == "יעל לוי"


# ---------------------------------------------------------------------------
# fetch_data
# ---------------------------------------------------------------------------

class TestFetchData:

    def _make_imap(self, msg_ids, raw_email):
        mock = MagicMock()
        mock.login.return_value = ("OK", [])
        mock.select.return_value = ("OK", [])
        mock.logout.return_value = ("OK", [])
        if msg_ids:
            mock.search.return_value = ("OK", [b" ".join(msg_ids)])
            mock.fetch.return_value = ("OK", [(b"1", raw_email)])
        else:
            mock.search.return_value = ("OK", [b""])
        return mock

    def test_returns_empty_when_no_email(self, agent):
        mock = self._make_imap([], None)
        with patch("imaplib.IMAP4_SSL", return_value=mock):
            result = agent.fetch_data()
        assert result == []

    def test_returns_records_on_success(self, agent):
        rows = [
            ["200 דנה בן דוד", ""],
            [f"סה''כ שורות  10", "", "", "", "", "35:30"],
        ]
        csv_bytes  = _make_csv_bytes(rows)
        filename   = f"{CSV_FILENAME_PREFIX}2026-03.csv"
        raw_email  = _build_email_with_csv(csv_bytes, filename)
        mock       = self._make_imap([b"1"], raw_email)

        with patch("imaplib.IMAP4_SSL", return_value=mock):
            result = agent.fetch_data()

        assert len(result) == 1
        assert result[0]["name"]  == "דנה בן דוד"
        assert result[0]["hours"] == pytest.approx(35.5)

    def test_raises_when_no_csv_attachment(self, agent):
        msg = MIMEMultipart()
        msg["From"] = "avivpost@avivpos.co.il"
        msg["Subject"] = "נוכחות באקסל"
        raw_email = msg.as_bytes()
        mock = self._make_imap([b"1"], raw_email)

        with patch("imaplib.IMAP4_SSL", return_value=mock):
            with pytest.raises(ValueError, match="no CSV attachment"):
                agent.fetch_data()

    def test_uses_latest_email(self, agent):
        rows = [["100 א ב", ""], [f"סה''כ שורות  1", "", "", "", "", "10:00"]]
        filename  = f"{CSV_FILENAME_PREFIX}test.csv"
        raw_email = _build_email_with_csv(_make_csv_bytes(rows), filename)
        mock      = self._make_imap([b"1", b"2"], raw_email)

        with patch("imaplib.IMAP4_SSL", return_value=mock):
            agent.fetch_data()

        mock.fetch.assert_called_once_with(b"2", "(RFC822)")


# ---------------------------------------------------------------------------
# save_to_db
# ---------------------------------------------------------------------------

class TestSaveToDb:

    def _make_db_rows(self, employees):
        """Build fake sqlite3.Row-like objects from a list of dicts."""
        rows = []
        for e in employees:
            row = MagicMock()
            row.__getitem__ = lambda self, k, e=e: e[k]
            rows.append(row)
        return rows

    def test_saves_matched_employee(self, agent):
        db_rows = [
            {"id": 1, "name": "רועי אמסלם"},
            {"id": 2, "name": "שמעון כהן"},
        ]
        fake_rows = self._make_db_rows(db_rows)

        mock_conn = MagicMock()
        mock_conn.__enter__ = lambda s: s
        mock_conn.__exit__  = MagicMock(return_value=False)
        mock_conn.execute.return_value.fetchall.return_value = fake_rows

        data = [{"name": "רועי אמסלם", "hours": 33.78}]

        with patch("agents.employee_hours.get_connection", return_value=mock_conn), \
             patch("agents.employee_hours.upsert_employee_hours") as mock_upsert:
            agent.save_to_db(data)

        mock_upsert.assert_called_once()
        call_kwargs = mock_upsert.call_args
        assert call_kwargs.kwargs["employee_id"] == 1
        assert call_kwargs.kwargs["hours_worked"] == pytest.approx(33.78)
        assert call_kwargs.kwargs["is_finalized"] is True

    def test_skips_unknown_employee(self, agent):
        fake_rows = self._make_db_rows([{"id": 1, "name": "רועי אמסלם"}])

        mock_conn = MagicMock()
        mock_conn.__enter__ = lambda s: s
        mock_conn.__exit__  = MagicMock(return_value=False)
        mock_conn.execute.return_value.fetchall.return_value = fake_rows

        data = [{"name": "לא קיים בכלל", "hours": 10.0}]

        with patch("agents.employee_hours.get_connection", return_value=mock_conn), \
             patch("agents.employee_hours.upsert_employee_hours") as mock_upsert:
            agent.save_to_db(data)

        mock_upsert.assert_not_called()

    def test_does_nothing_for_empty_list(self, agent):
        mock_conn = MagicMock()
        mock_conn.__enter__ = lambda s: s
        mock_conn.__exit__  = MagicMock(return_value=False)
        mock_conn.execute.return_value.fetchall.return_value = []

        with patch("agents.employee_hours.get_connection", return_value=mock_conn), \
             patch("agents.employee_hours.upsert_employee_hours") as mock_upsert:
            agent.save_to_db([])

        mock_upsert.assert_not_called()
