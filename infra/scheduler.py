import logging
import time

import schedule

from execution.trade_executor import build_default_executor


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
LOG = logging.getLogger(__name__)


def run_bot() -> None:
    executor = build_default_executor()
    executor.run_once()


def main() -> None:
    schedule.every().day.at("15:45").do(run_bot)
    LOG.info("Scheduler started. Waiting for jobs...")

    while True:
        schedule.run_pending()
        time.sleep(1)


if __name__ == "__main__":
    main()
