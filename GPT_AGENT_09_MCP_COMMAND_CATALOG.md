# AutoStop CRM MCP Command Catalog

Current connector scope: one current AutoStop CRM / Minimal Kanban board only.

## Bootstrap And Runtime
- `get_connector_identity()`  
  Returns connector identity, board scope, resource URL, API base URL.
- `ping_connector()`  
  Fast reachability check for the current connector and local API.
- `bootstrap_context(include_archived=false, event_limit=25)`  
  Primary bootstrap call before writes. Returns identity, board context, wall preview, recommended write flow.
- `get_runtime_status()`  
  Runtime diagnostics: connector identity, board API health, counts, visibility.

## Agent Runtime And Tasks
- `agent_status(run_limit=10)`  
  Agent availability, heartbeat, queue totals, scheduled totals, recent runs.
- `agent_runs(limit=20)`  
  Recent agent runs.
- `agent_actions(limit=100, run_id?, task_id?)`  
  Operational log and tool actions by run or task.
- `agent_tasks(limit=50, status?)`  
  Queue tasks. `status` can contain `pending`, `running`, `completed`, `failed`.
- `agent_scheduled_tasks()`  
  Scheduled tasks with scope, period, next run and status.
- `agent_enqueue_task(task_text, mode="manual", card_id?, card_title?, requested_by?, actor_name?)`  
  Queue one manual task. Optional card binding.
- `save_agent_scheduled_task(name, prompt, task_id?, scope_type="all_cards", scope_column?, scope_column_label?, scope_card_id?, scope_card_label?, schedule_type="once", interval_value=1, interval_unit="minute", active=true)`  
  Create or update a scheduled task.
- `delete_agent_scheduled_task(task_id)`  
  Delete one scheduled task.
- `pause_agent_scheduled_task(task_id)`  
  Pause one scheduled task.
- `resume_agent_scheduled_task(task_id)`  
  Resume one scheduled task.
- `run_agent_scheduled_task(task_id)`  
  Queue an immediate run of one scheduled task.
- `set_card_ai_autofill(card_id, enabled?, prompt?, actor_name?)`  
  Enable, disable or update 4-hour card autofill follow-up.

## Board Read Tools
- `list_columns()`  
  Current columns.
- `get_cards(include_archived=false)`  
  Card list for the current board.
- `get_card(card_id)`  
  Full card, including `vehicle_profile` and compact view data.
- `get_card_context(card_id, event_limit=20, include_repair_order_text=true)`  
  Focused card context: card, recent events, attachments, repair-order text, board context.
- `get_board_snapshot(archive_limit?)`  
  Full board snapshot with columns/cards/stickies.
- `get_board_context()`  
  Current board totals and textual context.
- `review_board(stale_hours?, overload_threshold?, priority_limit?, recent_event_limit?)`  
  Operational summary with alerts and priority cards.
- `get_board_content(include_archived=true, event_limit=20)`  
  Full board content section from GPT wall.
- `get_board_events(include_archived=true, event_limit=20)`  
  Board event section from GPT wall.
- `get_gpt_wall(include_archived=true, event_limit?)`  
  Full wall snapshot for agent review.
- `get_card_log(card_id)`  
  Card event log.
- `list_archived_cards(limit=10)`  
  Archived cards only.
- `search_cards(query?, include_archived=false, column?, tag?, indicator?, status?, limit=20)`  
  Search cards inside the current board.
- `list_overdue_cards(include_archived=false)`  
  Overdue cards.

## Board Write Tools
- `create_column(label, actor_name?)`
- `rename_column(column_id, label, actor_name?)`
- `delete_column(column_id, actor_name?)`
- `create_sticky(text, deadline, x=0, y=0, actor_name?)`
- `update_sticky(sticky_id, text?, deadline?, actor_name?)`
- `move_sticky(sticky_id, x, y, actor_name?)`
- `delete_sticky(sticky_id, actor_name?)`
- `create_card(title, deadline?, vehicle="", description="", column?, tags?, vehicle_profile?, actor_name?)`
- `update_card(card_id, vehicle?, title?, description?, tags?, deadline?, vehicle_profile?, actor_name?)`
- `set_card_deadline(card_id, deadline, actor_name?)`
- `set_card_indicator(card_id, indicator, actor_name?)`
- `move_card(card_id, column, before_card_id?, actor_name?)`
- `bulk_move_cards(card_ids, column, actor_name?)`
- `archive_card(card_id, actor_name?)`
- `restore_card(card_id, column?, actor_name?)`

## Vehicle And Repair-Order Tools
- `autofill_vehicle_data(raw_text="", image_base64?, image_filename?, image_mime_type?, vehicle_profile?, vehicle?, title?, description?)`  
  Normalized vehicle-profile draft.
- `list_repair_orders(limit?, status?, query?, sort_by?, sort_dir?)`
- `get_repair_order(card_id)`
- `get_repair_order_text(card_id)`
- `update_repair_order(card_id, repair_order, actor_name?)`
- `set_repair_order_status(card_id, status, actor_name?)`
- `replace_repair_order_works(card_id, rows, actor_name?)`
- `replace_repair_order_materials(card_id, rows, actor_name?)`
- `autofill_repair_order(card_id, overwrite=false, actor_name?)`

## Cashbox Tools
- `list_cashboxes(limit?)`
- `get_cashbox(cashbox_id, transaction_limit?)`
- `create_cashbox(name, actor_name?)`
- `delete_cashbox(cashbox_id, actor_name?)`
- `create_cash_transaction(cashbox_id, direction, amount, note="", actor_name?)`

## Board Settings
- `update_board_settings(board_scale, actor_name?)`

## Safe Usage Notes
- Always start with `bootstrap_context()`.
- Confirm the target board and ids before write calls.
- Prefer `get_card_context(card_id)` before card-level edits.
- Use `set_card_ai_autofill()` for 4-hour follow-up instead of raw `agent_enqueue_task()` when the goal is card accompaniment.
- Use scheduled-task tools for `once`, `interval`, `on_create`; do not emulate schedules manually.
