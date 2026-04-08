from __future__ import annotations

from ..logging_setup import close_logger, configure_logging
from .runner import run_agent_loop


def run() -> int:
    logger = configure_logging()
    try:
        return run_agent_loop(logger=logger)
    finally:
        close_logger(logger)
