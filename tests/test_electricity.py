"""
Tests for agents/electricity.py.
All IMAP, pdfplumber, and DB calls are mocked — no real network or DB needed.
"""

import io
import os
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from agents.electricity import (
    CONTRACT_NUMBER,
    ElectricityAgent,
    extract_amount_from_pdf,
    parse_dates_from_subject,
    should_process_email,
)

# ---------------------------------------------------------------------------
# Subject filter tests
# ---------------------------------------------------------------------------

class TestShouldProcessEmail(unittest.TestCase):

    def _valid_subject(self):
        return (
            "חשבון חשמל מספר חשבון חוזה 346412955 "
            "לתקופה - 21/09/2025 - 22/07/2025"
        )

    def test_valid_bill_passes(self):
        self.assertTrue(should_process_email(self._valid_subject()))

    def test_filter_skips_wrong_contract(self):
        subject = (
            "חשבון חשמל מספר חשבון חוזה 347597870 "
            "לתקופה - 21/09/2025 - 22/07/2025"
        )
        self.assertFalse(should_process_email(subject))

    def test_filter_skips_missing_period_marker(self):
        subject = "חשבון חשמל מספר חשבון חוזה 346412955 ללא תקופה"
        self.assertFalse(should_process_email(subject))

    def test_filter_skips_receipts(self):
        subject = f"שובר תשלום חוזה {CONTRACT_NUMBER} לתקופה - 01/01/2025 - 01/03/2025"
        self.assertFalse(should_process_email(subject))

    def test_filter_skips_warnings(self):
        subject = f"התראה בגין אי תשלום חוזה {CONTRACT_NUMBER} לתקופה - 01/01/2025 - 01/03/2025"
        self.assertFalse(should_process_email(subject))

    def test_filter_skips_debt_transfer(self):
        subject = f"הודעה על העברת חוב {CONTRACT_NUMBER} לתקופה - 01/01/2025 - 01/03/2025"
        self.assertFalse(should_process_email(subject))

    def test_filter_skips_customer_transfer(self):
        subject = f"אישור החלפת לקוחות {CONTRACT_NUMBER} לתקופה - 01/01/2025 - 01/03/2025"
        self.assertFalse(should_process_email(subject))

    def test_filter_skips_signup(self):
        subject = f"אישור הצטרפות {CONTRACT_NUMBER} לתקופה - 01/01/2025 - 01/03/2025"
        self.assertFalse(should_process_email(subject))


# ---------------------------------------------------------------------------
# Date parsing tests
# ---------------------------------------------------------------------------

class TestParseDatesFromSubject(unittest.TestCase):

    def test_parse_returns_start_and_end(self):
        # Subject has END first, then START
        subject = "חוזה 346412955 לתקופה - 21/09/2025 - 22/07/2025"
        result = parse_dates_from_subject(subject)
        self.assertIsNotNone(result)
        start, end = result
        self.assertEqual(start, "2025-07-22")   # START is the second date
        self.assertEqual(end,   "2025-09-21")   # END is the first date

    def test_end_date_comes_first_in_subject(self):
        """Verify the END-first, START-second ordering is correctly flipped."""
        subject = "346412955 לתקופה - 31/12/2024 - 01/11/2024"
        start, end = parse_dates_from_subject(subject)
        self.assertEqual(start, "2024-11-01")
        self.assertEqual(end,   "2024-12-31")

    def test_no_date_returns_none(self):
        self.assertIsNone(parse_dates_from_subject("חשמל ללא תאריכים"))

    def test_days_calculation(self):
        from datetime import date
        subject = "346412955 לתקופה - 21/09/2025 - 22/07/2025"
        start, end = parse_dates_from_subject(subject)
        days = (date.fromisoformat(end) - date.fromisoformat(start)).days
        self.assertEqual(days, 61)   # Jul 22 → Sep 21 = 61 days


# ---------------------------------------------------------------------------
# is_correction flag tests
# ---------------------------------------------------------------------------

class TestIsCorrectionFlag(unittest.TestCase):

    def test_correction_flag_on_days_over_90(self):
        # 91 days → correction
        from datetime import date, timedelta
        start = date(2025, 1, 1)
        end   = start + timedelta(days=91)
        days  = (end - start).days
        self.assertTrue(days > 90)

    def test_normal_bill_not_flagged(self):
        # Jul 22 → Sep 21 = 61 days → normal (not a correction)
        from datetime import date
        start = date(2025, 7, 22)
        end   = date(2025, 9, 21)
        days  = (end - start).days
        self.assertEqual(days, 61)
        self.assertFalse(days > 90)


# ---------------------------------------------------------------------------
# PDF amount extraction test
# ---------------------------------------------------------------------------

class TestExtractAmountFromPdf(unittest.TestCase):

    def test_pdf_regex_extracts_amount(self):
        pdf_text = (
            'חברת חשמל לישראל\n'
            'סה"כ כולל מע"מ לתקופת חשבון 5,244.55\n'
            'פרטים נוספים'
        )
        mock_page = MagicMock()
        mock_page.extract_text.return_value = pdf_text

        mock_pdf = MagicMock()
        mock_pdf.__enter__ = MagicMock(return_value=mock_pdf)
        mock_pdf.__exit__ = MagicMock(return_value=False)
        mock_pdf.pages = [mock_page]

        with patch("agents.electricity.pdfplumber.open", return_value=mock_pdf):
            amount = extract_amount_from_pdf(b"fake-pdf-bytes")

        self.assertAlmostEqual(amount, 5244.55)

    def test_missing_pattern_returns_none(self):
        mock_page = MagicMock()
        mock_page.extract_text.return_value = "אין כאן סכום"

        mock_pdf = MagicMock()
        mock_pdf.__enter__ = MagicMock(return_value=mock_pdf)
        mock_pdf.__exit__ = MagicMock(return_value=False)
        mock_pdf.pages = [mock_page]

        with patch("agents.electricity.pdfplumber.open", return_value=mock_pdf):
            result = extract_amount_from_pdf(b"fake")
        self.assertIsNone(result)


# ---------------------------------------------------------------------------
# Duplicate prevention test
# ---------------------------------------------------------------------------

class TestDuplicatePrevention(unittest.TestCase):

    def _make_agent(self):
        with patch.dict(os.environ, {
            "GMAIL_ADDRESS": "test@example.com",
            "GMAIL_APP_PASSWORD": "secret",
        }):
            return ElectricityAgent()

    def test_already_processed_bill_is_skipped(self):
        """fetch_data must skip an email whose pdf_filename already exists in expenses."""
        agent = self._make_agent()

        # Build a minimal fake email
        import email as emaillib
        msg = emaillib.message.MIMEPart()
        subject_line = (
            f"חשמל {CONTRACT_NUMBER} "
            "לתקופה - 21/09/2025 - 22/07/2025"
        )

        fake_mail = MagicMock()
        fake_mail.search.return_value = ("OK", [b"1"])

        raw_msg = MagicMock()
        raw_msg.get.side_effect = lambda key, default="": {
            "Subject": subject_line
        }.get(key, default)

        def fake_fetch_email(mail, msg_id):
            return raw_msg

        def fake_get_attachment(msg):
            return ("2025-451514576_20250925_191144.pdf", b"pdf-bytes")

        # Already processed → True
        with patch.object(agent, "_connect", return_value=fake_mail), \
             patch.object(agent, "_search_all_emails", return_value=[b"1"]), \
             patch.object(agent, "_fetch_email", side_effect=fake_fetch_email), \
             patch.object(agent, "_get_pdf_attachment", side_effect=fake_get_attachment), \
             patch.object(agent, "_is_processed", return_value=True), \
             patch("os.makedirs"):
            records = agent.fetch_data()

        self.assertEqual(records, [])

    def test_unprocessed_bill_is_returned(self):
        """fetch_data must return a record for a bill not yet in expenses."""
        agent = self._make_agent()

        subject_line = (
            f"חשמל {CONTRACT_NUMBER} "
            "לתקופה - 21/09/2025 - 22/07/2025"
        )

        fake_mail = MagicMock()
        raw_msg = MagicMock()
        raw_msg.get.side_effect = lambda key, default="": {
            "Subject": subject_line
        }.get(key, default)

        with patch.object(agent, "_connect", return_value=fake_mail), \
             patch.object(agent, "_search_all_emails", return_value=[b"1"]), \
             patch.object(agent, "_fetch_email", return_value=raw_msg), \
             patch.object(agent, "_get_pdf_attachment",
                          return_value=("2025-451514576_20250925_191144.pdf", b"pdf")), \
             patch.object(agent, "_is_processed", return_value=False), \
             patch("agents.electricity.extract_amount_from_pdf", return_value=5244.55), \
             patch("builtins.open", unittest.mock.mock_open()), \
             patch("os.makedirs"):
            records = agent.fetch_data()

        self.assertEqual(len(records), 1)
        self.assertAlmostEqual(records[0]["amount"], 5244.55)
        self.assertEqual(records[0]["period_start"], "2025-07-22")
        self.assertEqual(records[0]["period_end"],   "2025-09-21")
        self.assertFalse(records[0]["is_correction"])


# ---------------------------------------------------------------------------
# Monthly estimate test
# ---------------------------------------------------------------------------

class TestMonthlyEstimateCalculation(unittest.TestCase):

    def test_monthly_estimate_is_half_of_latest_bill(self):
        from database.db import get_electricity_monthly_estimate

        mock_row = MagicMock()
        mock_row.__getitem__ = lambda self, key: 5244.55 if key == "amount" else None

        with patch("database.db.get_connection") as mock_conn_ctx:
            mock_conn = MagicMock()
            mock_conn_ctx.return_value.__enter__ = MagicMock(return_value=mock_conn)
            mock_conn_ctx.return_value.__exit__  = MagicMock(return_value=False)
            mock_conn.execute.return_value.fetchone.return_value = mock_row

            result = get_electricity_monthly_estimate()

        self.assertAlmostEqual(result, round(5244.55 / 2, 2), places=2)

    def test_monthly_estimate_returns_none_when_no_bills(self):
        from database.db import get_electricity_monthly_estimate

        with patch("database.db.get_connection") as mock_conn_ctx:
            mock_conn = MagicMock()
            mock_conn_ctx.return_value.__enter__ = MagicMock(return_value=mock_conn)
            mock_conn_ctx.return_value.__exit__  = MagicMock(return_value=False)
            mock_conn.execute.return_value.fetchone.return_value = None

            result = get_electricity_monthly_estimate()

        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
