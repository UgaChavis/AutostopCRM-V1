from __future__ import annotations

import logging
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from minimal_kanban.logging_setup import _prepare_logger, close_logger  # noqa: E402


class LoggingSetupTests(unittest.TestCase):
    def test_prepare_logger_closes_old_handlers_and_resets_state(self) -> None:
        logger_name = f"autostopcrm.tests.logging_setup.{id(self)}"
        logger = logging.getLogger(logger_name)
        previous_level = logger.level
        previous_propagate = logger.propagate

        with tempfile.TemporaryDirectory() as temp_dir:
            old_handler = logging.FileHandler(Path(temp_dir) / "previous.log", encoding="utf-8")
            logger.addHandler(old_handler)
            logger.setLevel(logging.DEBUG)
            logger.propagate = True

            try:
                configured = _prepare_logger(logger_name, logging.WARNING)

                self.assertIs(configured, logger)
                self.assertEqual(logger.level, logging.WARNING)
                self.assertFalse(logger.propagate)
                self.assertNotIn(old_handler, logger.handlers)
                self.assertIsNone(old_handler.stream)
            finally:
                close_logger(logger)
                logger.setLevel(previous_level)
                logger.propagate = previous_propagate


if __name__ == "__main__":
    unittest.main()
