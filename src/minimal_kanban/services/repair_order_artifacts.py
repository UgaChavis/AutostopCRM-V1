"""Derived order files are prepared before, but published only after, state commit."""

import uuid
from pathlib import Path

from ..models import REPAIR_ORDER_FILE_RETENTION_LIMIT, business_timezone
from .errors import ServiceError


def publish_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(content, encoding="utf-8", newline="\n")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


class RepairOrderArtifactsMixin:
    @staticmethod
    def _repair_order_file_signature(path):
        try:
            stat = path.stat()
            return (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)
        except OSError:
            return None

    def _ensure_repair_order_text_file(self, card, *, force=False):
        path = self._repair_order_text_path(card)
        cache = getattr(self, "_repair_order_verified_files", None)
        if cache is None:
            cache = self._repair_order_verified_files = {}
        token = (
            card.updated_at,
            card.created_at,
            card.heading(),
            card.repair_order.to_storage_dict(),
            str(business_timezone()),
        )
        signature = self._repair_order_file_signature(path)
        entry = (token, str(path), signature)
        if not force and signature is not None and cache.get(card.id) == entry:
            return path
        content = self._render_repair_order_text(card, path)
        if not force and signature is not None:
            try:
                if self._read_repair_order_text_file(path).replace("\r\n", "\n") == content:
                    if self._repair_order_file_signature(path) == signature:
                        if card.id not in cache and len(cache) >= REPAIR_ORDER_FILE_RETENTION_LIMIT:
                            cache.pop(next(iter(cache)))
                        cache[card.id] = entry
                    return path
            except (OSError, ServiceError):
                pass
        cache.pop(card.id, None)
        publish_text(path, content)
        self._cleanup_repair_order_text_files(card, keep_path=path)
        return path

    def _prepare_repair_order_artifacts(self, bundle, cards):
        from .bundle_draft import BundleDraft

        if not isinstance(bundle, BundleDraft):
            return []
        previous = {card.id: card for card in bundle.source["cards"]}
        prepared = []
        for card in cards:
            before = previous.get(card.id)
            if before is card or not self._card_has_repair_order(card):
                continue
            if before is not None and (
                before.updated_at == card.updated_at
                and before.created_at == card.created_at
                and before.heading() == card.heading()
                and before.repair_order == card.repair_order
            ):
                continue
            path = self._repair_order_text_path(card)
            prepared.append((card, path, self._render_repair_order_text(card, path)))
        return prepared

    def _publish_repair_order_artifacts(self, prepared):
        for card, path, content in prepared:
            try:
                publish_text(path, content)
                self._cleanup_repair_order_text_files(card, keep_path=path)
            except Exception:
                # These files are derivatives. Reads regenerate from authoritative
                # card state; an IO error cannot turn a committed payment into failure.
                self._logger.exception("post_commit_repair_order_file_failed card_id=%s", card.id)
