from __future__ import annotations

import json
import threading
from logging import Logger
from pathlib import Path
from uuid import uuid4

from .config import get_app_data_dir, get_settings_file
from .settings_models import IntegrationSettings
from .storage.file_lock import ProcessFileLock
from .storage.limited_io import read_text_limited

SETTINGS_FILE_MAX_BYTES = 1 * 1024 * 1024


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"Unsupported JSON constant: {value}")


class SettingsStore:
    def __init__(self, settings_file: Path | None = None, logger: Logger | None = None) -> None:
        self._settings_file = settings_file or get_settings_file()
        self._logger = logger
        self._lock = threading.RLock()
        self._process_lock = ProcessFileLock(self._settings_file.with_suffix(".lock"))
        get_app_data_dir().mkdir(parents=True, exist_ok=True)
        self._settings_file.parent.mkdir(parents=True, exist_ok=True)
        if not self._settings_file.exists():
            with self._process_lock.acquire():
                self._write_settings(IntegrationSettings.defaults())

    @property
    def path(self) -> Path:
        return self._settings_file

    def read(self) -> IntegrationSettings:
        with self._lock:
            with self._process_lock.acquire():
                payload = self._read_payload()
                settings = IntegrationSettings.from_dict(payload)
                if payload != settings.to_dict():
                    self._write_settings(settings)
                return settings

    def write(self, settings: IntegrationSettings) -> None:
        with self._lock:
            with self._process_lock.acquire():
                self._write_settings(settings)

    def reset(self) -> IntegrationSettings:
        defaults = IntegrationSettings.defaults()
        self.write(defaults)
        return defaults

    def _read_payload(self) -> dict:
        if not self._settings_file.exists():
            return IntegrationSettings.defaults().to_dict()
        try:
            payload = json.loads(
                self._read_settings_text(),
                parse_constant=_reject_json_constant,
            )
        except (OSError, json.JSONDecodeError, UnicodeDecodeError, ValueError, RecursionError):
            backup = self._corrupted_settings_backup_path()
            self._log_warning(
                "Файл настроек поврежден, создаётся резервная копия %s и используются значения по умолчанию.",
                backup.name,
            )
            self._settings_file.replace(backup)
            defaults = IntegrationSettings.defaults()
            self._write_settings(defaults)
            return defaults.to_dict()
        if not isinstance(payload, dict):
            backup = self._corrupted_settings_backup_path()
            self._log_warning(
                "Файл настроек содержит некорректный формат, создаётся резервная копия %s и используются значения по умолчанию.",
                backup.name,
            )
            self._settings_file.replace(backup)
            defaults = IntegrationSettings.defaults()
            self._write_settings(defaults)
            return defaults.to_dict()
        return payload

    def _read_settings_text(self) -> str:
        return read_text_limited(
            self._settings_file,
            max_bytes=SETTINGS_FILE_MAX_BYTES,
            label="settings file",
        )

    def _corrupted_settings_backup_path(self) -> Path:
        backup = self._settings_file.with_suffix(".corrupted.json")
        if not backup.exists():
            return backup
        stem = self._settings_file.with_suffix("").name
        for index in range(2, 1000):
            candidate = self._settings_file.with_name(f"{stem}.corrupted-{index}.json")
            if not candidate.exists():
                return candidate
        return self._settings_file.with_name(f"{stem}.corrupted-{uuid4().hex}.json")

    def _write_settings(self, settings: IntegrationSettings) -> None:
        payload = json.dumps(settings.to_dict(), ensure_ascii=True, indent=2, allow_nan=False)
        if len(payload.encode("utf-8")) > SETTINGS_FILE_MAX_BYTES:
            raise ValueError("settings file is too large")
        temp_file = self._settings_file.with_name(f".{self._settings_file.name}.{uuid4().hex}.tmp")
        try:
            temp_file.write_text(payload, encoding="utf-8")
            temp_file.replace(self._settings_file)
        finally:
            temp_file.unlink(missing_ok=True)

    def _log_warning(self, message: str, *args) -> None:
        if self._logger is not None:
            self._logger.warning(message, *args)
