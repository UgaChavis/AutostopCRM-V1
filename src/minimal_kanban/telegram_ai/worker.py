from __future__ import annotations

import logging
import time
from typing import Any

from ..logging_setup import close_logger, configure_logging
from ..mcp.client import BoardApiClient
from .audit import TelegramAIAuditService
from .auth import TelegramAuthService
from .config import TelegramAIConfig, load_config, redact_secret
from .context import CRMContextBuilder
from .crm_tools import CRMToolRegistry
from .memory import TelegramAIConversationMemory
from .models import DownloadedAttachment
from .normalizer import normalize_update
from .openai_client import TelegramAIOpenAIClient
from .orchestrator import TelegramAIOrchestrator
from .state import TelegramAIStateStore
from .telegram_client import TelegramApiError, TelegramBotClient


def run() -> int:
    logger = configure_logging()
    try:
        config = load_config()
        worker = TelegramAIWorker(config, logger=logger)
        worker.run_forever()
        return 0
    finally:
        close_logger(logger)


class TelegramAIWorker:
    def __init__(self, config: TelegramAIConfig, *, logger: logging.Logger | None = None) -> None:
        self._config = config
        self._logger = logger or logging.getLogger(__name__)
        self._state = TelegramAIStateStore(config.state_file)

    def run_forever(self) -> None:
        self._config.data_dir.mkdir(parents=True, exist_ok=True)
        self._config.downloads_dir.mkdir(parents=True, exist_ok=True)
        if not self._config.enabled:
            self._logger.info("telegram_ai.disabled enabled=false")
            self._sleep_forever()
            return
        if not self._config.bot_token:
            self._logger.warning("telegram_ai.safe_disabled reason=missing_bot_token")
            self._sleep_forever()
            return
        if not self._config.owner_ids:
            self._logger.warning("telegram_ai.safe_disabled reason=missing_owner_ids")
            self._sleep_forever()
            return
        if not self._config.openai_api_key:
            self._logger.warning("telegram_ai.safe_disabled reason=missing_openai_api_key")
            self._sleep_forever()
            return

        telegram = TelegramBotClient(self._config)
        audit = TelegramAIAuditService(
            self._config.audit_file,
            enabled=self._config.audit_enabled,
        )
        memory = TelegramAIConversationMemory(
            self._config.conversation_file,
            limit=self._config.conversation_memory_limit,
        )
        board_api = BoardApiClient(
            self._config.crm_api_base_url,
            bearer_token=self._config.crm_api_bearer_token,
            logger=self._logger,
            default_source="telegram_ai",
        )
        model_client = TelegramAIOpenAIClient(self._config)
        orchestrator = TelegramAIOrchestrator(
            auth=TelegramAuthService(self._config),
            model_client=model_client,
            context_builder=CRMContextBuilder(board_api),
            tool_registry=CRMToolRegistry(
                board_api,
                actor_name="TELEGRAM_AI",
                max_batch_cards=self._config.max_batch_cards,
                image_analyzer=model_client.analyze_image,
            ),
            audit=audit,
            memory=memory,
        )
        self._logger.info(
            "telegram_ai.started crm_api=%s model=%s owner_ids=%s token=%s",
            self._config.crm_api_base_url,
            self._config.model,
            len(self._config.owner_ids),
            redact_secret(self._config.bot_token),
        )
        self._poll_loop(telegram, orchestrator)

    def _poll_loop(self, telegram: TelegramBotClient, orchestrator: TelegramAIOrchestrator) -> None:
        while True:
            state = self._state.read()
            last_update_id = _int_or_none(state.get("last_update_id"))
            offset = last_update_id + 1 if last_update_id is not None else None
            try:
                updates = telegram.get_updates(
                    offset=offset,
                    timeout_seconds=self._config.telegram_poll_timeout_seconds,
                )
                for update in updates:
                    update_id = _int_or_none(update.get("update_id"))
                    self._handle_update(telegram, orchestrator, update)
                    if update_id is not None:
                        self._state.update(last_update_id=update_id, last_seen_at=time.time())
            except TelegramApiError as exc:
                self._logger.warning("telegram_ai.telegram_error error=%s", exc)
                time.sleep(5)
            except Exception as exc:  # pragma: no cover - long-running safety net
                self._logger.exception("telegram_ai.loop_error error=%s", exc)
                time.sleep(5)

    def _handle_update(
        self,
        telegram: TelegramBotClient,
        orchestrator: TelegramAIOrchestrator,
        update: dict[str, Any],
    ) -> None:
        normalized = normalize_update(update)
        if normalized is None:
            return
        downloaded = self._download_attachments(telegram, normalized.attachments)
        response = orchestrator.handle(normalized, downloaded_attachments=downloaded)
        telegram.send_message(
            chat_id=normalized.chat_id,
            text=response,
            reply_to_message_id=normalized.message_id,
        )

    def _download_attachments(
        self, telegram: TelegramBotClient, attachments
    ) -> list[DownloadedAttachment]:
        downloaded: list[DownloadedAttachment] = []
        for attachment in attachments:
            if attachment.kind not in {"voice", "photo"}:
                continue
            content, file_path = telegram.download_file(attachment.file_id)
            downloaded.append(
                DownloadedAttachment(
                    attachment=attachment,
                    content=content,
                    file_path=file_path,
                )
            )
        return downloaded

    def _sleep_forever(self) -> None:
        while True:
            time.sleep(60)


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
