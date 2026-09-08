"""Derived order files are prepared before, but published only after, state commit."""

import uuid
from pathlib import Path


def publish_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(content, encoding="utf-8")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


class RepairOrderArtifactsMixin:
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
