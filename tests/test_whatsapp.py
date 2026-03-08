"""
Tests for notifications/whatsapp.py (Telegram-based notifications).
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

def _mock_env(token="fake-bot-token", chat_id="123456"):
    return patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": token, "TELEGRAM_CHAT_ID": chat_id})


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
    @patch("notifications.whatsapp.requests.post")
    def test_skips_when_no_token(self, mock_post, _):
        with patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "", "TELEGRAM_CHAT_ID": "123"}):
            send_alert("test")
        mock_post.assert_not_called()

    @patch("notifications.whatsapp._is_send_window", return_value=True)
    @patch("notifications.whatsapp.requests.post")
    def test_skips_when_no_chat_id(self, mock_post, _):
        with patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "tok", "TELEGRAM_CHAT_ID": ""}):
            send_alert("test")
        mock_post.assert_not_called()

    @patch("notifications.whatsapp._is_send_window", return_value=True)
    @patch("notifications.whatsapp.requests.post")
    def test_skips_when_both_missing(self, mock_post, _):
        with patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "", "TELEGRAM_CHAT_ID": ""}):
            send_alert("test")
        mock_post.assert_not_called()


# ---------------------------------------------------------------------------
# send_alert — time window
# ---------------------------------------------------------------------------

class TestSendAlertTimeWindow(unittest.TestCase):
    @patch("notifications.whatsapp._is_send_window", return_value=False)
    @patch("notifications.whatsapp.requests.post")
    def test_does_not_send_outside_window(self, mock_post, _):
        with _mock_env():
            send_alert("test message")
        mock_post.assert_not_called()

    @patch("notifications.whatsapp._is_send_window", return_value=True)
    @patch("notifications.whatsapp.requests.post")
    def test_sends_inside_window(self, mock_post, _):
        mock_post.return_value = MagicMock(status_code=200)
        with _mock_env():
            send_alert("hello")
        mock_post.assert_called_once()


# ---------------------------------------------------------------------------
# send_alert — HTTP request construction
# ---------------------------------------------------------------------------

class TestSendAlertHTTPRequest(unittest.TestCase):
    @patch("notifications.whatsapp._is_send_window", return_value=True)
    @patch("notifications.whatsapp.requests.post")
    def test_url_contains_token(self, mock_post, _):
        mock_post.return_value = MagicMock(status_code=200)
        with _mock_env(token="my-bot-token"):
            send_alert("msg")
        url = mock_post.call_args.args[0]
        self.assertIn("my-bot-token", url)

    @patch("notifications.whatsapp._is_send_window", return_value=True)
    @patch("notifications.whatsapp.requests.post")
    def test_url_uses_telegram_api(self, mock_post, _):
        mock_post.return_value = MagicMock(status_code=200)
        with _mock_env():
            send_alert("msg")
        url = mock_post.call_args.args[0]
        self.assertIn("api.telegram.org/bot", url)

    @patch("notifications.whatsapp._is_send_window", return_value=True)
    @patch("notifications.whatsapp.requests.post")
    def test_payload_contains_chat_id(self, mock_post, _):
        mock_post.return_value = MagicMock(status_code=200)
        with _mock_env(chat_id="999"):
            send_alert("msg")
        payload = mock_post.call_args.kwargs.get("json", {})
        self.assertEqual(payload["chat_id"], "999")

    @patch("notifications.whatsapp._is_send_window", return_value=True)
    @patch("notifications.whatsapp.requests.post")
    def test_payload_contains_message(self, mock_post, _):
        mock_post.return_value = MagicMock(status_code=200)
        with _mock_env():
            send_alert("hello world")
        payload = mock_post.call_args.kwargs.get("json", {})
        self.assertEqual(payload["text"], "hello world")

    @patch("notifications.whatsapp._is_send_window", return_value=True)
    @patch("notifications.whatsapp.requests.post")
    def test_timeout_set(self, mock_post, _):
        mock_post.return_value = MagicMock(status_code=200)
        with _mock_env():
            send_alert("msg")
        kwargs = mock_post.call_args.kwargs
        self.assertIn("timeout", kwargs)
        self.assertGreater(kwargs["timeout"], 0)


# ---------------------------------------------------------------------------
# send_alert — error handling
# ---------------------------------------------------------------------------

class TestSendAlertErrorHandling(unittest.TestCase):
    @patch("notifications.whatsapp._is_send_window", return_value=True)
    @patch("notifications.whatsapp.requests.post")
    def test_no_exception_on_http_error(self, mock_post, _):
        import requests as req
        mock_post.side_effect = req.RequestException("timeout")
        with _mock_env():
            try:
                send_alert("msg")  # must not raise
            except Exception as exc:
                self.fail(f"send_alert raised unexpectedly: {exc}")

    @patch("notifications.whatsapp._is_send_window", return_value=True)
    @patch("notifications.whatsapp.requests.post")
    def test_no_exception_on_connection_error(self, mock_post, _):
        import requests as req
        mock_post.side_effect = req.exceptions.ConnectionError("refused")
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
