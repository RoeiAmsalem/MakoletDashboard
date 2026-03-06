"""
Tests for notifications/whatsapp.py.
All HTTP calls and environment reads are mocked.
"""

import sys
import os
import unittest
from datetime import datetime
from unittest.mock import patch, MagicMock
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from notifications.whatsapp import format_agent_alert, send_alert, _is_send_window

_ISRAEL_TZ = ZoneInfo("Asia/Jerusalem")

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mock_env(phone="972501234567", api_key="abc123"):
    return patch.dict(os.environ, {"WHATSAPP_PHONE": phone, "WHATSAPP_API_KEY": api_key})


def _mock_time(hour: int):
    """Patch datetime.now inside whatsapp module to return the given Israel hour."""
    fake_dt = datetime(2024, 6, 1, hour, 0, 0, tzinfo=_ISRAEL_TZ)
    return patch("notifications.whatsapp.datetime")


# ---------------------------------------------------------------------------
# _is_send_window
# ---------------------------------------------------------------------------

class TestIsSendWindow(unittest.TestCase):
    def _patch_hour(self, hour: int):
        fake_dt = datetime(2024, 6, 1, hour, 0, 0, tzinfo=_ISRAEL_TZ)
        mock_dt = MagicMock()
        mock_dt.now.return_value = fake_dt
        return patch("notifications.whatsapp.datetime", mock_dt)

    def test_inside_window_morning(self):
        with self._patch_hour(8):
            self.assertTrue(_is_send_window())

    def test_inside_window_midday(self):
        with self._patch_hour(13):
            self.assertTrue(_is_send_window())

    def test_inside_window_last_valid_hour(self):
        with self._patch_hour(21):
            self.assertTrue(_is_send_window())

    def test_outside_window_midnight(self):
        with self._patch_hour(0):
            self.assertFalse(_is_send_window())

    def test_outside_window_early_morning(self):
        with self._patch_hour(7):
            self.assertFalse(_is_send_window())

    def test_outside_window_at_22(self):
        with self._patch_hour(22):
            self.assertFalse(_is_send_window())

    def test_outside_window_late_night(self):
        with self._patch_hour(23):
            self.assertFalse(_is_send_window())


# ---------------------------------------------------------------------------
# send_alert — credential checks
# ---------------------------------------------------------------------------

class TestSendAlertCredentials(unittest.TestCase):
    @patch("notifications.whatsapp._is_send_window", return_value=True)
    @patch("notifications.whatsapp.requests.get")
    def test_skips_when_no_phone(self, mock_get, _):
        with patch.dict(os.environ, {"WHATSAPP_PHONE": "", "WHATSAPP_API_KEY": "key"}):
            send_alert("test")
        mock_get.assert_not_called()

    @patch("notifications.whatsapp._is_send_window", return_value=True)
    @patch("notifications.whatsapp.requests.get")
    def test_skips_when_no_api_key(self, mock_get, _):
        with patch.dict(os.environ, {"WHATSAPP_PHONE": "972501234567", "WHATSAPP_API_KEY": ""}):
            send_alert("test")
        mock_get.assert_not_called()

    @patch("notifications.whatsapp._is_send_window", return_value=True)
    @patch("notifications.whatsapp.requests.get")
    def test_skips_when_both_missing(self, mock_get, _):
        with patch.dict(os.environ, {"WHATSAPP_PHONE": "", "WHATSAPP_API_KEY": ""}):
            send_alert("test")
        mock_get.assert_not_called()


# ---------------------------------------------------------------------------
# send_alert — time window
# ---------------------------------------------------------------------------

class TestSendAlertTimeWindow(unittest.TestCase):
    @patch("notifications.whatsapp._is_send_window", return_value=False)
    @patch("notifications.whatsapp.requests.get")
    def test_does_not_send_outside_window(self, mock_get, _):
        with _mock_env():
            send_alert("test message")
        mock_get.assert_not_called()

    @patch("notifications.whatsapp._is_send_window", return_value=True)
    @patch("notifications.whatsapp.requests.get")
    def test_sends_inside_window(self, mock_get, _):
        mock_get.return_value = MagicMock(status_code=200, raise_for_status=MagicMock())
        with _mock_env():
            send_alert("hello")
        mock_get.assert_called_once()


# ---------------------------------------------------------------------------
# send_alert — HTTP request construction
# ---------------------------------------------------------------------------

class TestSendAlertHTTPRequest(unittest.TestCase):
    @patch("notifications.whatsapp._is_send_window", return_value=True)
    @patch("notifications.whatsapp.requests.get")
    def test_url_contains_phone(self, mock_get, _):
        mock_get.return_value = MagicMock(status_code=200, raise_for_status=MagicMock())
        with _mock_env(phone="972501234567"):
            send_alert("msg")
        url = mock_get.call_args.args[0]
        self.assertIn("972501234567", url)

    @patch("notifications.whatsapp._is_send_window", return_value=True)
    @patch("notifications.whatsapp.requests.get")
    def test_url_contains_apikey(self, mock_get, _):
        mock_get.return_value = MagicMock(status_code=200, raise_for_status=MagicMock())
        with _mock_env(api_key="mykey"):
            send_alert("msg")
        url = mock_get.call_args.args[0]
        self.assertIn("mykey", url)

    @patch("notifications.whatsapp._is_send_window", return_value=True)
    @patch("notifications.whatsapp.requests.get")
    def test_message_is_url_encoded(self, mock_get, _):
        mock_get.return_value = MagicMock(status_code=200, raise_for_status=MagicMock())
        with _mock_env():
            send_alert("hello world & more")
        url = mock_get.call_args.args[0]
        self.assertIn("hello+world", url.replace("%20", "+").replace(" ", "+"))
        self.assertNotIn(" ", url)

    @patch("notifications.whatsapp._is_send_window", return_value=True)
    @patch("notifications.whatsapp.requests.get")
    def test_uses_callmebot_base_url(self, mock_get, _):
        mock_get.return_value = MagicMock(status_code=200, raise_for_status=MagicMock())
        with _mock_env():
            send_alert("msg")
        url = mock_get.call_args.args[0]
        self.assertIn("callmebot.com/whatsapp.php", url)

    @patch("notifications.whatsapp._is_send_window", return_value=True)
    @patch("notifications.whatsapp.requests.get")
    def test_timeout_set(self, mock_get, _):
        mock_get.return_value = MagicMock(status_code=200, raise_for_status=MagicMock())
        with _mock_env():
            send_alert("msg")
        kwargs = mock_get.call_args.kwargs
        self.assertIn("timeout", kwargs)
        self.assertGreater(kwargs["timeout"], 0)


# ---------------------------------------------------------------------------
# send_alert — error handling
# ---------------------------------------------------------------------------

class TestSendAlertErrorHandling(unittest.TestCase):
    @patch("notifications.whatsapp._is_send_window", return_value=True)
    @patch("notifications.whatsapp.requests.get")
    def test_no_exception_on_http_error(self, mock_get, _):
        import requests as req
        mock_get.side_effect = req.RequestException("timeout")
        with _mock_env():
            try:
                send_alert("msg")  # must not raise
            except Exception as exc:
                self.fail(f"send_alert raised unexpectedly: {exc}")

    @patch("notifications.whatsapp._is_send_window", return_value=True)
    @patch("notifications.whatsapp.requests.get")
    def test_no_exception_on_connection_error(self, mock_get, _):
        import requests as req
        mock_get.side_effect = req.exceptions.ConnectionError("refused")
        with _mock_env():
            try:
                send_alert("msg")
            except Exception as exc:
                self.fail(f"send_alert raised unexpectedly: {exc}")


# ---------------------------------------------------------------------------
# format_agent_alert
# ---------------------------------------------------------------------------

class TestFormatAgentAlert(unittest.TestCase):
    def _get_formatted(self, agent_name="bilboy", error="some error"):
        fake_dt = datetime(2024, 6, 1, 10, 30, 0, tzinfo=_ISRAEL_TZ)
        mock_dt = MagicMock()
        mock_dt.now.return_value = fake_dt
        with patch("notifications.whatsapp.datetime", mock_dt):
            return format_agent_alert(agent_name, error)

    def test_contains_store_name(self):
        msg = self._get_formatted()
        self.assertIn("מכולת אינשטיין", msg)

    def test_contains_agent_name(self):
        msg = self._get_formatted(agent_name="bilboy")
        self.assertIn("bilboy", msg)

    def test_contains_error(self):
        msg = self._get_formatted(error="connection refused")
        self.assertIn("connection refused", msg)

    def test_contains_date(self):
        msg = self._get_formatted()
        self.assertIn("01/06/2024", msg)

    def test_contains_time(self):
        msg = self._get_formatted()
        self.assertIn("10:30", msg)

    def test_contains_alert_emoji(self):
        msg = self._get_formatted()
        self.assertIn("\U0001f6a8", msg)


if __name__ == "__main__":
    unittest.main()
