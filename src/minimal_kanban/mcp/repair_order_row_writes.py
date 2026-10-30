"""Register repair-order table replacement tools."""

from .payloads import JsonEnvelope, RepairOrderRowPayload


def register_repair_order_row_writes(
    server, board_api, scoped_description, write_tool_annotations, relay_board_call
) -> None:
    @server.tool(
        name="replace_repair_order_works",
        description=scoped_description(
            "Replace the full Works table of a repair order on the current AutoStop CRM board."
        ),
        annotations=write_tool_annotations("Replace Repair Order Works"),
        structured_output=True,
    )
    def replace_repair_order_works(
        card_id: str,
        rows: list[RepairOrderRowPayload],
        actor_name: str | None = None,
        expected_updated_at: str | None = None,
    ) -> JsonEnvelope:
        return relay_board_call(
            "replace_repair_order_works",
            lambda: board_api.replace_repair_order_works(
                card_id=card_id,
                rows=[row.model_dump() for row in rows],
                actor_name=actor_name,
                expected_updated_at=expected_updated_at,
            ),
        )

    @server.tool(
        name="replace_repair_order_materials",
        description=scoped_description(
            "Replace the full Materials table of a repair order on the current AutoStop CRM board."
        ),
        annotations=write_tool_annotations("Replace Repair Order Materials"),
        structured_output=True,
    )
    def replace_repair_order_materials(
        card_id: str,
        rows: list[RepairOrderRowPayload],
        actor_name: str | None = None,
        expected_updated_at: str | None = None,
    ) -> JsonEnvelope:
        return relay_board_call(
            "replace_repair_order_materials",
            lambda: board_api.replace_repair_order_materials(
                card_id=card_id,
                rows=[row.model_dump() for row in rows],
                actor_name=actor_name,
                expected_updated_at=expected_updated_at,
            ),
        )
