"""
Tests for agents/base_agent.py.
All DB and sleep calls are mocked so tests run fast.
"""

import sys
import os
import unittest
from unittest.mock import patch, MagicMock, call

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from agents.base_agent import BaseAgent, MAX_RETRIES


# ---------------------------------------------------------------------------
# Concrete subclasses for testing
# ---------------------------------------------------------------------------

class SuccessAgent(BaseAgent):
    name = "test_success"

    def fetch_data(self):
        return [{"item": 1}, {"item": 2}]

    def save_to_db(self, data):
        pass


class AlwaysFailAgent(BaseAgent):
    name = "test_fail"

    def fetch_data(self):
        raise RuntimeError("network error")

    def save_to_db(self, data):
        pass


class FailThenSucceedAgent(BaseAgent):
    """Fails on first N calls, then succeeds."""
    name = "test_flaky"

    def __init__(self, fail_times: int):
        super().__init__()
        self._fail_times = fail_times
        self._attempts = 0

    def fetch_data(self):
        self._attempts += 1
        if self._attempts <= self._fail_times:
            raise ConnectionError("transient error")
        return [{"ok": True}]

    def save_to_db(self, data):
        pass


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

MOCK_LOG = "agents.base_agent.log_agent_run"
MOCK_SLEEP = "agents.base_agent.time.sleep"


class TestBaseAgentSuccess(unittest.TestCase):
    @patch(MOCK_SLEEP)
    @patch(MOCK_LOG)
    def test_run_returns_success(self, mock_log, mock_sleep):
        result = SuccessAgent().run()
        self.assertTrue(result["success"])
        self.assertEqual(len(result["data"]), 2)
        self.assertIsNone(result["error"])

    @patch(MOCK_SLEEP)
    @patch(MOCK_LOG)
    def test_run_logs_success(self, mock_log, mock_sleep):
        SuccessAgent().run()
        mock_log.assert_called_once()
        kwargs = mock_log.call_args.kwargs
        self.assertEqual(kwargs["status"], "success")
        self.assertEqual(kwargs["records_fetched"], 2)

    @patch(MOCK_SLEEP)
    @patch(MOCK_LOG)
    def test_no_sleep_on_success(self, mock_log, mock_sleep):
        SuccessAgent().run()
        mock_sleep.assert_not_called()


class TestBaseAgentFailure(unittest.TestCase):
    @patch(MOCK_SLEEP)
    @patch(MOCK_LOG)
    def test_run_returns_failure(self, mock_log, mock_sleep):
        result = AlwaysFailAgent().run()
        self.assertFalse(result["success"])
        self.assertEqual(result["data"], [])
        self.assertIn("network error", result["error"])

    @patch(MOCK_SLEEP)
    @patch(MOCK_LOG)
    def test_retries_exactly_max_times(self, mock_log, mock_sleep):
        """sleep should be called MAX_RETRIES-1 times (between attempts)."""
        AlwaysFailAgent().run()
        self.assertEqual(mock_sleep.call_count, MAX_RETRIES - 1)

    @patch(MOCK_SLEEP)
    @patch(MOCK_LOG)
    def test_logs_failure_once_on_final_fail(self, mock_log, mock_sleep):
        AlwaysFailAgent().run()
        mock_log.assert_called_once()
        kwargs = mock_log.call_args.kwargs
        self.assertEqual(kwargs["status"], "failure")
        self.assertIn("network error", kwargs["error_message"])

    @patch(MOCK_SLEEP)
    @patch(MOCK_LOG)
    def test_notify_failure_called(self, mock_log, mock_sleep):
        agent = AlwaysFailAgent()
        with patch.object(agent, "_notify_failure") as mock_notify:
            agent.run()
            mock_notify.assert_called_once()
            self.assertIn("network error", mock_notify.call_args.args[0])


class TestBaseAgentRetrySuccess(unittest.TestCase):
    @patch(MOCK_SLEEP)
    @patch(MOCK_LOG)
    def test_succeeds_after_one_failure(self, mock_log, mock_sleep):
        result = FailThenSucceedAgent(fail_times=1).run()
        self.assertTrue(result["success"])
        self.assertEqual(mock_sleep.call_count, 1)

    @patch(MOCK_SLEEP)
    @patch(MOCK_LOG)
    def test_succeeds_on_last_attempt(self, mock_log, mock_sleep):
        result = FailThenSucceedAgent(fail_times=MAX_RETRIES - 1).run()
        self.assertTrue(result["success"])
        self.assertEqual(mock_sleep.call_count, MAX_RETRIES - 1)

    @patch(MOCK_SLEEP)
    @patch(MOCK_LOG)
    def test_fails_when_over_max_retries(self, mock_log, mock_sleep):
        result = FailThenSucceedAgent(fail_times=MAX_RETRIES).run()
        self.assertFalse(result["success"])


class TestBaseAgentNotifyFailure(unittest.TestCase):
    @patch(MOCK_SLEEP)
    @patch(MOCK_LOG)
    def test_whatsapp_called_if_available(self, mock_log, mock_sleep):
        mock_send = MagicMock()
        with patch.dict("sys.modules", {"notifications.whatsapp": MagicMock(send_alert=mock_send)}):
            AlwaysFailAgent().run()
        mock_send.assert_called_once()

    @patch(MOCK_SLEEP)
    @patch(MOCK_LOG)
    def test_no_crash_if_whatsapp_missing(self, mock_log, mock_sleep):
        """ImportError from missing whatsapp module must be silently swallowed."""
        with patch.dict("sys.modules", {"notifications": None, "notifications.whatsapp": None}):
            result = AlwaysFailAgent().run()
        self.assertFalse(result["success"])


if __name__ == "__main__":
    unittest.main()
