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
