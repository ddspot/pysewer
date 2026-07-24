# SPDX-FileCopyrightText: 2023 Helmholtz Centre for Environmental Research (UFZ)
# SPDX-License-Identifier: GPL-3.0-only

import datetime
import logging
import os
from logging.handlers import RotatingFileHandler

logger = logging.getLogger(__name__)


# configure the root logger
def configure_logging():
    log_formatter = logging.Formatter(
        "%(asctime)s %(levelname)s %(funcName)s(%(lineno)d) %(message)s"
    )
    # get the the current timestamp to create a unique file for this run
    timestamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    log_filename = f"logs/test_logs_{timestamp}.log"

    # Create the logs directory if it doesn't exist
    os.makedirs(os.path.dirname(log_filename), exist_ok=True)

    # create a rotating file handler
    log_handler = RotatingFileHandler(
        log_filename, maxBytes=5 * 1024 * 1024, backupCount=2, encoding=None, delay=0
    )
    log_handler.setFormatter(log_formatter)

    # get the root logger and add the roating handler to it
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.DEBUG)
    root_logger.addHandler(log_handler)


# pytest hook to configure logging before running tests
def pytest_configure(config):
    configure_logging()


def pytest_terminal_summary(terminalreporter, exitstatus, config):
    # Log warnings
    warnings = terminalreporter.stats.get("warnings", [])
    for warning in warnings:
        logger.warning(warning.message)

    # Log short test summary info
    summary = terminalreporter.stats
    for key in summary:
        if key in [
            "passed",
            "failed",
            "error",
            "skipped",
            "deselected",
            "xfailed",
            "xpassed",
        ]:
            logger.info(f"{key.upper()}: {len(summary[key])}")


def pytest_runtest_logreport(report):
    if report.failed:
        logger.error(
            f"Test {report.nodeid} failed with the following error: {report.longrepr}"
        )
    elif report.outcome == "skipped":
        logger.warning(f"Test {report.nodeid} was skipped: {report.longrepr}")
