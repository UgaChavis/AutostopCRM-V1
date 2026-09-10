from __future__ import annotations

from typing import Any


def delete_custom_template_state(
    service: Any,
    *,
    record: Any,
    templates: list[Any],
    settings: Any,
) -> None:
    """Delete an active template without exposing a dangling default reference."""

    document_type = record.document_type
    if settings.default_template_ids.get(document_type) != record.id:
        service._write_custom_templates(templates)
        return

    # Clear the reference first: both the intermediate state and a failed
    # compensation still leave a usable template store. Feed publication is
    # deferred until the two files form one coherent projection.
    settings.default_template_ids.pop(document_type, None)
    service._write_settings(settings, sync_change_feed=False)
    try:
        service._write_custom_templates(templates, sync_change_feed=False)
    except Exception:
        # A writer can report a late failure after its atomic replace. If the
        # intended bytes are already durable, acknowledge the completed delete
        # instead of inviting a retry of an operation that already happened.
        if service._read_custom_templates() == templates:
            service._sync_change_feed()
            return
        settings.default_template_ids[document_type] = record.id
        try:
            service._write_settings(settings, sync_change_feed=False)
        finally:
            service._sync_change_feed()
        raise
    service._sync_change_feed()
