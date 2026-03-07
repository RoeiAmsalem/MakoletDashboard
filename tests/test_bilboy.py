"""
Tests for agents/bilboy.py.
All HTTP calls and DB writes are mocked — no real network or DB needed.
"""

import sys
import os
import unittest
from unittest.mock import patch, MagicMock, call

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from agents.bilboy import BilBoyAgent

MOCK_LOG_AGENT = "agents.base_agent.log_agent_run"
MOCK_SLEEP     = "agents.base_agent.time.sleep"
MOCK_INSERT    = "agents.bilboy.insert_expense"


# Sample API responses
BRANCHES = [{"branchId": "branch-1", "name": "Main Branch"}]

SUPPLIERS = {"suppliers": [
    {"id": 100, "name": "Supplier A"},
    {"id": 101, "name": "Supplier B"},
]}

HEADERS = [
    {
        "documentDate": "2025-03-01",
        "totalAmount": 1500.0,
        "supplierName": "Supplier A",
        "docNumber": "INV-001",
    },
    {
        "documentDate": "2025-03-05",
        "totalAmount": 800.0,
        "supplierName": "Supplier B",
        "docNumber": "INV-002",
    },
]


def _mock_get(url, **kwargs):
    resp = MagicMock()
    resp.status_code = 200
    if "/user/branches" in url:
        resp.json.return_value = BRANCHES
    elif "/customer/suppliers" in url:
        resp.json.return_value = SUPPLIERS
    elif "/customer/docs/headers" in url:
        resp.json.return_value = HEADERS
    else:
        resp.json.return_value = []
    resp.raise_for_status = MagicMock()
    return resp


class TestBilBoyFetchData(unittest.TestCase):
    def _make_agent(self):
        with patch.dict(os.environ, {"BILBOY_TOKEN": "test-token"}):
            return BilBoyAgent()

    def test_fetch_returns_records(self):
        agent = self._make_agent()
        with patch.object(agent._session, "get", side_effect=_mock_get):
            records = agent.fetch_data()
        self.assertEqual(len(records), 2)

    def test_record_fields(self):
        agent = self._make_agent()
        with patch.object(agent._session, "get", side_effect=_mock_get):
            records = agent.fetch_data()
        r = records[0]
        self.assertEqual(r["date"], "2025-03-01")
        self.assertAlmostEqual(r["amount"], 1500.0)
        self.assertIn("Supplier A", r["description"])
        self.assertIn("raw", r)

    def test_date_truncated_to_10_chars(self):
        """Ensure datetime strings like '2025-03-01T00:00:00' are trimmed."""
        headers_with_datetime = [
            {"documentDate": "2025-03-01T12:34:56", "totalAmount": 100.0}
        ]

        def mock_get_datetime(url, **kwargs):
            resp = MagicMock()
            resp.status_code = 200
            resp.raise_for_status = MagicMock()
            if "/user/branches" in url:
                resp.json.return_value = BRANCHES
            elif "/customer/suppliers" in url:
                resp.json.return_value = SUPPLIERS
            else:
                resp.json.return_value = headers_with_datetime
            return resp

        agent = self._make_agent()
        with patch.object(agent._session, "get", side_effect=mock_get_datetime):
            records = agent.fetch_data()
        self.assertEqual(records[0]["date"], "2025-03-01")

    def test_401_raises_permission_error(self):
        def mock_401(url, **kwargs):
            resp = MagicMock()
            resp.status_code = 401
            return resp

        agent = self._make_agent()
        with patch.object(agent._session, "get", side_effect=mock_401):
            with self.assertRaises(PermissionError) as ctx:
                agent.fetch_data()
        self.assertIn("token expired", str(ctx.exception).lower())

    def test_empty_branches_raises(self):
        def mock_empty(url, **kwargs):
            resp = MagicMock()
            resp.status_code = 200
            resp.raise_for_status = MagicMock()
            resp.json.return_value = []
            return resp

        agent = self._make_agent()
        with patch.object(agent._session, "get", side_effect=mock_empty):
            with self.assertRaises(ValueError):
                agent.fetch_data()

    def test_headers_wrapped_in_data_key(self):
        """API may return {"data": [...]} instead of a bare list."""
        def mock_wrapped(url, **kwargs):
            resp = MagicMock()
            resp.status_code = 200
            resp.raise_for_status = MagicMock()
            if "/user/branches" in url:
                resp.json.return_value = BRANCHES
            elif "/customer/suppliers" in url:
                resp.json.return_value = SUPPLIERS
            else:
                resp.json.return_value = {"data": HEADERS}
            return resp

        agent = self._make_agent()
        with patch.object(agent._session, "get", side_effect=mock_wrapped):
            records = agent.fetch_data()
        self.assertEqual(len(records), 2)


class TestBilBoySaveToDB(unittest.TestCase):
    def _make_agent(self):
        with patch.dict(os.environ, {"BILBOY_TOKEN": "test-token"}):
            return BilBoyAgent()

    @patch(MOCK_INSERT)
    def test_save_calls_insert_for_each_record(self, mock_insert):
        data = [
            {"date": "2025-03-01", "amount": 1500.0, "description": "Supplier A", "raw": {}},
            {"date": "2025-03-05", "amount": 800.0,  "description": "Supplier B", "raw": {}},
        ]
        self._make_agent().save_to_db(data)
        self.assertEqual(mock_insert.call_count, 2)

    @patch(MOCK_INSERT)
    def test_save_uses_goods_category(self, mock_insert):
        data = [{"date": "2025-03-01", "amount": 100.0, "description": "X", "raw": {}}]
        self._make_agent().save_to_db(data)
        _, kwargs = mock_insert.call_args
        self.assertEqual(kwargs.get("category") or mock_insert.call_args.args[1], "goods")

    @patch(MOCK_INSERT)
    def test_save_uses_bilboy_source(self, mock_insert):
        data = [{"date": "2025-03-01", "amount": 100.0, "description": "X", "raw": {}}]
        self._make_agent().save_to_db(data)
        call_kwargs = mock_insert.call_args.kwargs
        self.assertEqual(call_kwargs.get("source"), "bilboy")

    @patch(MOCK_INSERT)
    def test_save_empty_list_no_insert(self, mock_insert):
        self._make_agent().save_to_db([])
        mock_insert.assert_not_called()


class TestBilBoyRunIntegration(unittest.TestCase):
    """End-to-end run() via BaseAgent with all side effects mocked."""

    def _make_agent(self):
        with patch.dict(os.environ, {"BILBOY_TOKEN": "test-token"}):
            return BilBoyAgent()

    @patch(MOCK_SLEEP)
    @patch(MOCK_LOG_AGENT)
    @patch(MOCK_INSERT)
    def test_run_success(self, mock_insert, mock_log, mock_sleep):
        agent = self._make_agent()
        with patch.object(agent._session, "get", side_effect=_mock_get):
            result = agent.run()
        self.assertTrue(result["success"])
        self.assertEqual(len(result["data"]), 2)
        self.assertEqual(mock_insert.call_count, 2)
        mock_log.assert_called_once()
        self.assertEqual(mock_log.call_args.kwargs["status"], "success")

    @patch(MOCK_SLEEP)
    @patch(MOCK_LOG_AGENT)
    @patch(MOCK_INSERT)
    def test_run_401_fails_after_retries(self, mock_insert, mock_log, mock_sleep):
        def mock_401(url, **kwargs):
            resp = MagicMock()
            resp.status_code = 401
            return resp

        agent = self._make_agent()
        with patch.object(agent._session, "get", side_effect=mock_401):
            result = agent.run()
        self.assertFalse(result["success"])
        self.assertIn("token expired", result["error"].lower())
        mock_log.assert_called_once()
        self.assertEqual(mock_log.call_args.kwargs["status"], "failure")


if __name__ == "__main__":
    unittest.main()
