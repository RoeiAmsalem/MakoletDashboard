"""
BaseAgent - abstract base class for all MakoletDashboard agents.

Every agent must inherit from BaseAgent and implement:
    fetch_data() -> list
    save_to_db(data: list)

The run() method handles retry logic, timing, and DB logging automatically.
"""

import time
import logging
from abc import ABC, abstractmethod
from datetime import date

from database.db import log_agent_run

logger = logging.getLogger(__name__)

MAX_RETRIES = 3
RETRY_DELAY_SECONDS = 5  # wait between retries


class BaseAgent(ABC):
    """
    Abstract base for all data-fetching agents.

    Subclasses must implement fetch_data() and save_to_db().
    Call run() to execute with automatic retry + logging.
    """

    # Override in subclass with a meaningful name, e.g. "bilboy"
    name: str = "base"

    def __init__(self):
        self.logger = logging.getLogger(self.__class__.__name__)

    # ------------------------------------------------------------------
    # Abstract interface — subclasses must implement these
    # ------------------------------------------------------------------

    @abstractmethod
    def fetch_data(self) -> list:
        """
        Fetch raw data from external source (API / email / scraping).
        Returns a list of records (dicts or domain objects).
        Raise an exception on failure — BaseAgent will handle retries.
        """

    @abstractmethod
    def save_to_db(self, data: list) -> None:
        """
        Persist the records returned by fetch_data() to the database.
        Raise an exception on failure.
        """

    # ------------------------------------------------------------------
    # run() — the main entry point, called by scheduler / run_all_agents
    # ------------------------------------------------------------------

    def run(self) -> dict:
        """
        Execute the agent with up to MAX_RETRIES attempts.

        Returns:
            {
                "success": bool,
                "data": list,       # populated on success
                "error": str | None # populated on final failure
            }
        """
        today = date.today().isoformat()
        start_time = time.monotonic()
        last_error: str | None = None

        for attempt in range(1, MAX_RETRIES + 1):
            try:
                self.logger.info("[%s] Attempt %d/%d", self.name, attempt, MAX_RETRIES)
                data = self.fetch_data()
                self.save_to_db(data)

                duration = time.monotonic() - start_time
                log_agent_run(
                    agent_name=self.name,
                    run_date=today,
                    status="success",
                    records_fetched=len(data) if data else 0,
                    duration_seconds=round(duration, 2),
                )
                self.logger.info(
                    "[%s] Success — %d records in %.2fs",
                    self.name, len(data) if data else 0, duration,
                )
                return {"success": True, "data": data, "error": None}

            except Exception as exc:  # noqa: BLE001
                last_error = str(exc)
                self.logger.warning(
                    "[%s] Attempt %d failed: %s", self.name, attempt, last_error
                )
                if attempt < MAX_RETRIES:
                    self.logger.info(
                        "[%s] Retrying in %ds...", self.name, RETRY_DELAY_SECONDS
                    )
                    time.sleep(RETRY_DELAY_SECONDS)

        # All attempts exhausted
        duration = time.monotonic() - start_time
        log_agent_run(
            agent_name=self.name,
            run_date=today,
            status="failure",
            records_fetched=0,
            error_message=last_error,
            duration_seconds=round(duration, 2),
        )
        self.logger.error("[%s] Failed after %d attempts: %s", self.name, MAX_RETRIES, last_error)
        self._notify_failure(last_error)

        return {"success": False, "data": [], "error": last_error}

    # ------------------------------------------------------------------
    # Failure notification (WhatsApp in Step 7, plain log for now)
    # ------------------------------------------------------------------

    def _notify_failure(self, error_message: str) -> None:
        """
        Alert that the agent has failed.
        Step 7 will replace this with a real WhatsApp message via CallMeBot.
        """
        msg = (
            f"[ALERT] Agent '{self.name}' failed after {MAX_RETRIES} retries.\n"
            f"Error: {error_message}"
        )
        # Print so it's visible in logs / terminal even before WhatsApp is wired
        print(msg)
        self.logger.error(msg)

        # Step 7 hook — import lazily so the project works before
        # notifications/whatsapp.py exists.
        try:
            from notifications.whatsapp import send_alert  # noqa: PLC0415
            send_alert(msg)
        except ImportError:
            pass  # whatsapp module not yet available
