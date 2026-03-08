"""
Tests for agents/aviv_alerts.py

Mocks:
  - imaplib.IMAP4_SSL  → no real network calls
  - pdfplumber.open    → no real PDF parsing
  - database.db.insert_daily_sale → no real DB writes
"""

import io
import sys
import types
from datetime import date
from unittest.mock import MagicMock, patch, call

import pytest

# ---------------------------------------------------------------------------
# Helpers to build minimal fake email bytes
# ---------------------------------------------------------------------------

import email as _email_module
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email.mime.text import MIMEText
from email import encoders


def _build_raw_email(pdf_bytes: bytes, filename: str = "z_report.pdf") -> bytes:
    """Construct a minimal RFC822 email with a PDF attachment."""
    msg = MIMEMultipart()
    msg["From"] = "avivpost@avivpos.co.il"
    msg["Subject"] = "דוח סוף יום"

    part = MIMEBase("application", "pdf")
    part.set_payload(pdf_bytes)
    encoders.encode_base64(part)
    part.add_header("Content-Disposition", "attachment", filename=filename)
    msg.attach(part)
    return msg.as_bytes()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def env_vars(monkeypatch):
    """Inject dummy credentials so the agent can be instantiated."""
    monkeypatch.setenv("GMAIL_ADDRESS", "test@gmail.com")
    monkeypatch.setenv("GMAIL_APP_PASSWORD", "app-password")
    monkeypatch.setenv("AVIV_SENDER_EMAIL", "avivpost@avivpos.co.il")


@pytest.fixture()
def agent():
    from agents.aviv_alerts import AvivAlertsAgent
    return AvivAlertsAgent()


# ---------------------------------------------------------------------------
# _extract_total_from_pdf
# ---------------------------------------------------------------------------

class TestExtractTotalFromPdf:
    def test_parses_standard_line(self, agent):
        fake_page = MagicMock()
        fake_page.extract_text.return_value = 'סה"כ: ₪ 12377.92'
        fake_pdf = MagicMock()
        fake_pdf.pages = [fake_page]
        fake_pdf.__enter__ = lambda s: s
        fake_pdf.__exit__ = MagicMock(return_value=False)

        with patch("pdfplumber.open", return_value=fake_pdf):
            result = agent._extract_total_from_pdf(b"fake-pdf-bytes")

        assert result == 12377.92

    def test_parses_amount_with_commas(self, agent):
        fake_page = MagicMock()
        fake_page.extract_text.return_value = 'סה"כ: ₪ 1,234.56'
        fake_pdf = MagicMock()
        fake_pdf.pages = [fake_page]
        fake_pdf.__enter__ = lambda s: s
        fake_pdf.__exit__ = MagicMock(return_value=False)

        with patch("pdfplumber.open", return_value=fake_pdf):
            result = agent._extract_total_from_pdf(b"fake-pdf-bytes")

        assert result == 1234.56

    def test_returns_none_when_pattern_not_found(self, agent):
        fake_page = MagicMock()
        fake_page.extract_text.return_value = "No matching line here"
        fake_pdf = MagicMock()
        fake_pdf.pages = [fake_page]
        fake_pdf.__enter__ = lambda s: s
        fake_pdf.__exit__ = MagicMock(return_value=False)

        with patch("pdfplumber.open", return_value=fake_pdf):
            result = agent._extract_total_from_pdf(b"fake-pdf-bytes")

        assert result is None

    def test_finds_total_on_second_page(self, agent):
        page1 = MagicMock()
        page1.extract_text.return_value = "Some header text"
        page2 = MagicMock()
        page2.extract_text.return_value = 'סה"כ: ₪ 5000.00'
        fake_pdf = MagicMock()
        fake_pdf.pages = [page1, page2]
        fake_pdf.__enter__ = lambda s: s
        fake_pdf.__exit__ = MagicMock(return_value=False)

        with patch("pdfplumber.open", return_value=fake_pdf):
            result = agent._extract_total_from_pdf(b"fake-pdf-bytes")

        assert result == 5000.0


# ---------------------------------------------------------------------------
# fetch_data
# ---------------------------------------------------------------------------

class TestFetchData:
    def _make_imap_mock(self, msg_ids: list[bytes], raw_email: bytes | None):
        """Build a fake IMAP4_SSL instance."""
        mock_imap = MagicMock()
        mock_imap.login.return_value = ("OK", [])
        mock_imap.select.return_value = ("OK", [])
        mock_imap.logout.return_value = ("OK", [])

        if msg_ids:
            mock_imap.search.return_value = ("OK", [b" ".join(msg_ids)])
            if raw_email is not None:
                mock_imap.fetch.return_value = ("OK", [(b"1", raw_email)])
            else:
                mock_imap.fetch.return_value = ("OK", [(b"1", b"")])
        else:
            mock_imap.search.return_value = ("OK", [b""])

        return mock_imap

    def test_returns_empty_when_no_email_today(self, agent):
        mock_imap = self._make_imap_mock(msg_ids=[], raw_email=None)
        with patch("imaplib.IMAP4_SSL", return_value=mock_imap):
            result = agent.fetch_data()
        assert result == []

    def test_returns_record_on_success(self, agent):
        pdf_bytes = b"fake-pdf"
        raw_email = _build_raw_email(pdf_bytes, filename="z_daily.pdf")
        mock_imap = self._make_imap_mock(msg_ids=[b"1"], raw_email=raw_email)

        fake_page = MagicMock()
        fake_page.extract_text.return_value = 'סה"כ: ₪ 9999.00'
        fake_pdf = MagicMock()
        fake_pdf.pages = [fake_page]
        fake_pdf.__enter__ = lambda s: s
        fake_pdf.__exit__ = MagicMock(return_value=False)

        with patch("imaplib.IMAP4_SSL", return_value=mock_imap), \
             patch("pdfplumber.open", return_value=fake_pdf):
            result = agent.fetch_data()

        assert len(result) == 1
        assert result[0]["total_income"] == 9999.0
        assert result[0]["source"] == "aviv"
        assert result[0]["date"] == date.today().isoformat()

    def test_raises_when_no_pdf_attachment(self, agent):
        # Email with no z_ attachment
        msg = MIMEMultipart()
        msg["From"] = "avivpost@avivpos.co.il"
        msg["Subject"] = "דוח סוף יום"
        msg.attach(MIMEText("body text"))
        raw_email = msg.as_bytes()

        mock_imap = self._make_imap_mock(msg_ids=[b"1"], raw_email=raw_email)
        with patch("imaplib.IMAP4_SSL", return_value=mock_imap):
            with pytest.raises(ValueError, match="no z_\\*.pdf attachment"):
                agent.fetch_data()

    def test_raises_when_total_not_parseable(self, agent):
        pdf_bytes = b"fake-pdf"
        raw_email = _build_raw_email(pdf_bytes, filename="z_report.pdf")
        mock_imap = self._make_imap_mock(msg_ids=[b"1"], raw_email=raw_email)

        fake_page = MagicMock()
        fake_page.extract_text.return_value = "No total here"
        fake_pdf = MagicMock()
        fake_pdf.pages = [fake_page]
        fake_pdf.__enter__ = lambda s: s
        fake_pdf.__exit__ = MagicMock(return_value=False)

        with patch("imaplib.IMAP4_SSL", return_value=mock_imap), \
             patch("pdfplumber.open", return_value=fake_pdf):
            with pytest.raises(ValueError, match="could not parse"):
                agent.fetch_data()

    def test_uses_latest_email_when_multiple_found(self, agent):
        """When multiple emails match, the last one (msg_id b'2') is used."""
        pdf_bytes = b"fake-pdf"
        raw_email = _build_raw_email(pdf_bytes, filename="z_report.pdf")
        mock_imap = self._make_imap_mock(msg_ids=[b"1", b"2"], raw_email=raw_email)

        fake_page = MagicMock()
        fake_page.extract_text.return_value = 'סה"כ: ₪ 100.00'
        fake_pdf = MagicMock()
        fake_pdf.pages = [fake_page]
        fake_pdf.__enter__ = lambda s: s
        fake_pdf.__exit__ = MagicMock(return_value=False)

        with patch("imaplib.IMAP4_SSL", return_value=mock_imap), \
             patch("pdfplumber.open", return_value=fake_pdf):
            agent.fetch_data()

        # fetch should have been called with msg_id b"2" (the last one)
        mock_imap.fetch.assert_called_once_with(b"2", "(RFC822)")


# ---------------------------------------------------------------------------
# save_to_db
# ---------------------------------------------------------------------------

class TestSaveToDb:
    def test_calls_insert_daily_sale(self, agent):
        data = [{"date": "2026-03-06", "total_income": 1234.56, "source": "aviv"}]
        with patch("agents.aviv_alerts.insert_daily_sale") as mock_insert:
            agent.save_to_db(data)
        mock_insert.assert_called_once_with(
            date="2026-03-06",
            total_income=1234.56,
            source="aviv",
        )

    def test_does_nothing_for_empty_list(self, agent):
        with patch("agents.aviv_alerts.insert_daily_sale") as mock_insert:
            agent.save_to_db([])
        mock_insert.assert_not_called()


# ---------------------------------------------------------------------------
# RTL PDF extraction
# ---------------------------------------------------------------------------

class TestExtractTotalRTL:
    """Tests for RTL PDF text (pdfplumber visual order)."""

    def test_parses_rtl_total(self, agent):
        fake_page = MagicMock()
        fake_page.extract_text.return_value = '20295.85 ₪ :כ"הס'
        fake_pdf = MagicMock()
        fake_pdf.pages = [fake_page]
        fake_pdf.__enter__ = lambda s: s
        fake_pdf.__exit__ = MagicMock(return_value=False)

        with patch("pdfplumber.open", return_value=fake_pdf):
            result = agent._extract_total_from_pdf(b"fake-pdf-bytes")

        assert result == 20295.85

    def test_parses_rtl_with_commas(self, agent):
        fake_page = MagicMock()
        fake_page.extract_text.return_value = '1,234.56 ₪ :כ"הס'
        fake_pdf = MagicMock()
        fake_pdf.pages = [fake_page]
        fake_pdf.__enter__ = lambda s: s
        fake_pdf.__exit__ = MagicMock(return_value=False)

        with patch("pdfplumber.open", return_value=fake_pdf):
            result = agent._extract_total_from_pdf(b"fake-pdf-bytes")

        assert result == 1234.56

    def test_rtl_preferred_over_ltr(self, agent):
        """When both patterns exist, RTL match is found first."""
        fake_page = MagicMock()
        fake_page.extract_text.return_value = '20295.85 ₪ :כ"הס\nסה"כ: ₪ 999.00'
        fake_pdf = MagicMock()
        fake_pdf.pages = [fake_page]
        fake_pdf.__enter__ = lambda s: s
        fake_pdf.__exit__ = MagicMock(return_value=False)

        with patch("pdfplumber.open", return_value=fake_pdf):
            result = agent._extract_total_from_pdf(b"fake-pdf-bytes")

        assert result == 20295.85


# ---------------------------------------------------------------------------
# is_z_expected
# ---------------------------------------------------------------------------

class TestIsZExpected:
    def test_sunday_expected(self):
        from agents.aviv_alerts import is_z_expected
        # 2026-03-08 is Sunday
        assert is_z_expected(date(2026, 3, 8)) is True

    def test_weekday_expected(self):
        from agents.aviv_alerts import is_z_expected
        # Monday through Friday
        assert is_z_expected(date(2026, 3, 9)) is True   # Mon
        assert is_z_expected(date(2026, 3, 13)) is True  # Fri

    def test_saturday_not_expected(self):
        from agents.aviv_alerts import is_z_expected
        # 2026-03-07 is Saturday, not last day of month
        assert is_z_expected(date(2026, 3, 7)) is False

    def test_saturday_last_day_of_month_expected(self):
        from agents.aviv_alerts import is_z_expected
        # Find a Saturday that's the last day of a month
        # 2026-01-31 is Saturday
        assert is_z_expected(date(2026, 1, 31)) is True

    def test_saturday_not_last_day(self):
        from agents.aviv_alerts import is_z_expected
        # 2026-03-14 is Saturday, not last day
        assert is_z_expected(date(2026, 3, 14)) is False


# ---------------------------------------------------------------------------
# check_missing_z_reports
# ---------------------------------------------------------------------------

class TestCheckMissingZReports:
    def test_no_missing_when_all_present(self):
        from agents.aviv_alerts import check_missing_z_reports
        today = date(2026, 3, 8)  # Sunday
        # Mock: all expected days have records
        mock_conn = MagicMock()
        mock_conn.__enter__ = lambda s: s
        mock_conn.__exit__ = MagicMock(return_value=False)
        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = (1,)  # always found
        mock_conn.execute.return_value = mock_cursor

        with patch("agents.aviv_alerts.date") as mock_date, \
             patch("agents.aviv_alerts.get_connection", return_value=mock_conn):
            mock_date.today.return_value = today
            mock_date.side_effect = lambda *a, **kw: date(*a, **kw)
            missing = check_missing_z_reports()

        assert missing == []

    def test_detects_missing_day(self):
        from agents.aviv_alerts import check_missing_z_reports
        today = date(2026, 3, 8)  # Sunday

        mock_conn = MagicMock()
        mock_conn.__enter__ = lambda s: s
        mock_conn.__exit__ = MagicMock(return_value=False)

        def fake_execute(sql, params):
            cursor = MagicMock()
            d = params[0]
            # Mar 7 is Saturday (skip), Mar 2 missing, rest present
            if d == "2026-03-02":
                cursor.fetchone.return_value = (0,)
            else:
                cursor.fetchone.return_value = (1,)
            return cursor

        mock_conn.execute.side_effect = fake_execute

        with patch("agents.aviv_alerts.date") as mock_date, \
             patch("agents.aviv_alerts.get_connection", return_value=mock_conn):
            mock_date.today.return_value = today
            mock_date.side_effect = lambda *a, **kw: date(*a, **kw)
            missing = check_missing_z_reports()

        assert "2026-03-02" in missing
