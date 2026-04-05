from __future__ import annotations

import json
from logging import Logger
from pathlib import Path
import threading

from .config import get_app_data_dir, get_settings_file
from .settings_models import IntegrationSettings
from .storage.file_lock import ProcessFileLock


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
            payload = json.loads(self._settings_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            backup = self._settings_file.with_suffix(".corrupted.json")
            self._log_warning(
                "Файл настроек поврежден, создаётся резервная копия %s и используются значения по умолчанию.",
                backup.name,
            )
            if backup.exists():
                backup.unlink()
            self._settings_file.replace(backup)
            defaults = IntegrationSettings.defaults()
            self._write_settings(defaults)
            return defaults.to_dict()
        if not isinstance(payload, dict):
            self._log_warning("Файл настроек содержит некорректный формат, используются значения по умолчанию.")
            defaults = IntegrationSettings.defaults()
            self._write_settings(defaults)
            return defaults.to_dict()
        return payload

    def _write_settings(self, settings: IntegrationSettings) -> None:
        payload = json.dumps(settings.to_dict(), ensure_ascii=True, indent=2)
        temp_file = self._settings_file.with_suffix(".tmp")
        temp_file.write_text(payload, encoding="utf-8")
        temp_file.replace(self._settings_file)

    def _log_warning(self, message: str, *args) -> None:
        if self._logger is not None:
            self._logger.warning(message, *args)
