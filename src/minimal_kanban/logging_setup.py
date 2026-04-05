from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler

from .config import APP_NAME, get_log_file, get_logs_dir, get_mcp_startup_log_file


def close_logger(logger: logging.Logger) -> None:
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()


def _build_formatter() -> logging.Formatter:
    return logging.Formatter(
        "%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        "%Y-%m-%d %H:%M:%S",
    )


def _configure_file_logger(name: str, log_file, *, level: int = logging.INFO) -> logging.Logger:
    get_logs_dir().mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger(name)
    logger.setLevel(level)
    close_logger(logger)
    logger.propagate = False

    formatter = _build_formatter()
    file_handler = RotatingFileHandler(log_file, maxBytes=1_000_000, backupCount=3, encoding="utf-8")
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)
    return logger


def _configure_stream_fallback_logger(name: str, *, level: int = logging.INFO, warning_message: str = "") -> logging.Logger:
    logger = logging.getLogger(name)
    logger.setLevel(level)
    close_logger(logger)
    logger.propagate = False

    stream_handler = logging.StreamHandler(sys.stderr)
    stream_handler.setFormatter(_build_formatter())
    logger.addHandler(stream_handler)
    if warning_message:
        logger.warning(warning_message)
    return logger


def configure_logging() -> logging.Logger:
    try:
        return _configure_file_logger(APP_NAME, get_log_file(), level=logging.INFO)
    except Exception as exc:  # pragma: no cover - defensive fallback
        return _configure_stream_fallback_logger(
            APP_NAME,
            level=logging.INFO,
            warning_message=f"Не удалось настроить файловый лог приложения, включён stderr fallback: {exc}",
        )


def configure_mcp_startup_logger() -> logging.Logger:
    try:
        return _configure_file_logger(f"{APP_NAME}.mcp.startup", get_mcp_startup_log_file(), level=logging.DEBUG)
    except Exception as exc:  # pragma: no cover - defensive fallback
        return _configure_stream_fallback_logger(
            f"{APP_NAME}.mcp.startup",
            level=logging.DEBUG,
            warning_message=f"Не удалось настроить MCP startup log, включён stderr fallback: {exc}",
        )
