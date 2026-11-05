"""ISO dates survive repair-order form rehydration and ordinary saves."""

import json
import os
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from tests.browser_ui_support import UiBrowserTestCase


class RepairOrderIsoBaselineBrowserTests(UiBrowserTestCase):
    def make_order(self, label, values):
        client = self.runtime.service.create_client({"display_name": "SYNTHETIC ISO " + label})[
            "client"
        ]["id"]
        card = self.runtime.service.create_card(
            {"title": "SYNTHETIC ISO ORDER " + label, "client_id": client}
        )["card"]["id"]
        self.runtime.service.update_card(
            {
                "card_id": card,
                "repair_order": {
                    **{key: value for key, value in values.items() if key != "closed_at"},
                    "works": [{"name": "SYNTHETIC WORK", "quantity": "1", "price": "100"}],
                },
            }
        )
        return card

    def open_order(self, card):
        self.load_board()
        self.page.locator("#repairOrdersButton").click()
        self.page.locator(f'#repairOrdersList [data-open-repair-order-card="{card}"]').click()
        self.page.wait_for_function("state.repairOrderWriteReady && !state.repairOrderLoading")

    def save(self):
        with self.page.expect_response("**/api/update_repair_order") as response:
            self.page.locator("#repairOrderSaveButton").click()
        self.assertEqual(response.value.status, 200)
        self.page.wait_for_function("!state.repairOrderMutationRequest")

    def test_save_edit_switch_and_defaults(self):
        a = {
            "date": "2026-11-04T05:30:42.123+07:00",
            "opened_at": "2026-11-03T22:30:39.456+00:00",
            "closed_at": "2026-11-06T11:12:13.789Z",
        }
        b = {
            "date": "2026-09-01T01:02:03.123Z",
            "opened_at": "2026-09-02T04:05:06.456+03:00",
            "closed_at": "2026-09-03T07:08:09.789-04:00",
        }
        card_a, card_b = self.make_order("A", a), self.make_order("B", b)
        self.open_order(card_a)
        self.assertEqual(self.page.locator("#repairOrderDate").input_value(), a["date"])
        self.assertEqual(self.page.locator("#repairOrderOpenedAt").input_value(), a["opened_at"])
        date_differences = self.page.evaluate("""() => {
          const before = JSON.parse(state.repairOrderInitialPayloadKey);
          const now = repairOrderDirtyComparisonPayload();
          return ['date', 'opened_at', 'closed_at'].filter(key => before[key] !== now[key]);
        }""")
        self.assertEqual(date_differences, [])
        values = self.page.evaluate("() => readRepairOrderFromForm()")
        for field, value in a.items():
            if field == "closed_at":
                continue
            self.assertEqual(values[field], value)
        self.assertEqual(
            self.page.evaluate(
                """value => {
              applyRepairOrderToForm({...state.activeCard.repair_order, closed_at: value});
              const result = readRepairOrderFromForm().closed_at;
              applyRepairOrderToForm(state.activeCard.repair_order);
              return result;
            }""",
                a["closed_at"],
            ),
            a["closed_at"],
        )
        for _ in range(2):
            self.save()
            saved = self.runtime.service.get_card({"card_id": card_a})["card"]["repair_order"]
            self.assertEqual(saved["date"], a["date"])
            self.assertEqual(saved["opened_at"], a["opened_at"])
        edited_iso = "2026-11-04T05:31:42.123+07:00"
        self.page.locator("#repairOrderDate").fill(edited_iso)
        self.assertTrue(self.page.evaluate("repairOrderModalHasUnsavedChanges()"))
        self.save()
        saved = self.runtime.service.get_card({"card_id": card_a})["card"]["repair_order"]
        self.assertEqual(saved["date"], edited_iso)
        self.assertEqual(
            (
                datetime.fromisoformat(saved["date"]) - datetime.fromisoformat(a["date"])
            ).total_seconds(),
            60,
        )
        self.assertEqual(saved["opened_at"], a["opened_at"])
        edited_opened = "2026-11-03T22:31:39.456+00:00"
        self.page.locator("#repairOrderOpenedAt").fill(edited_opened)
        self.save()
        saved = self.runtime.service.get_card({"card_id": card_a})["card"]["repair_order"]
        self.assertEqual(saved["date"], edited_iso)
        self.assertEqual(saved["opened_at"], edited_opened)

        self.open_order(card_b)
        values = self.page.evaluate("() => readRepairOrderFromForm()")
        for field, value in b.items():
            if field == "closed_at":
                continue
            self.assertEqual(values[field], value)
        self.assertEqual(
            self.page.evaluate(
                """value => {
              applyRepairOrderToForm({...state.activeCard.repair_order, closed_at: value});
              const result = readRepairOrderFromForm().closed_at;
              applyRepairOrderToForm(state.activeCard.repair_order);
              return result;
            }""",
                b["closed_at"],
            ),
            b["closed_at"],
        )
        self.assertEqual(
            self.page.evaluate(
                """value => {
              applyRepairOrderToForm({...state.activeCard.repair_order, closed_at: value});
              els.repairOrderClosedAt.value = '6.3.2026 2:3:4';
              const result = readRepairOrderFromForm().closed_at;
              applyRepairOrderToForm(state.activeCard.repair_order);
              return result;
            }""",
                b["closed_at"],
            ),
            "06.03.2026 02:03:04",
        )
        self.save()
        saved = self.runtime.service.get_card({"card_id": card_b})["card"]["repair_order"]
        self.assertEqual(saved["date"], b["date"])
        self.assertEqual(saved["opened_at"], b["opened_at"])

        defaults = self.page.evaluate("""() => {
          const order = {...state.activeCard.repair_order, date: '', opened_at: '', closed_at: ''};
          applyRepairOrderToForm(order);
          const values = readRepairOrderFromForm();
          return {date: values.date, opened: values.opened_at, closed: values.closed_at,
            expectedDate: repairOrderCanonicalDateValue(els.repairOrderDate.value),
            expectedOpened: repairOrderCanonicalDateValue(els.repairOrderOpenedAt.value)};
        }""")
        self.assertEqual(defaults["date"], defaults["expectedDate"])
        self.assertEqual(defaults["opened"], defaults["expectedOpened"])
        self.assertTrue(defaults["date"])
        self.assertTrue(defaults["opened"])
        self.assertEqual(defaults["closed"], "")
        fallback = self.page.evaluate(
            """iso => {
          applyRepairOrderToForm({...state.activeCard.repair_order, date: iso, opened_at: '', closed_at: ''});
          const values = readRepairOrderFromForm();
          return {date: values.date, opened: values.opened_at};
        }""",
            b["date"],
        )
        self.assertEqual(fallback["date"], b["date"])
        self.assertEqual(fallback["opened"], b["date"])

    def test_late_a_response_does_not_replace_b_baselines(self):
        a = {
            "date": "2026-11-01T01:02:03.123Z",
            "opened_at": "2026-11-01T04:05:06.123+07:00",
        }
        b = {
            "date": "2026-11-02T07:08:09.456Z",
            "opened_at": "2026-11-02T10:11:12.456-04:00",
        }
        card_a, card_b = self.make_order("LATE A", a), self.make_order("LATE B", b)
        held = []

        def delay_a(route):
            if route.request.post_data_json.get("card_id") == card_a:
                held.append(route)
            else:
                route.continue_()

        self.load_board()
        self.page.route("**/api/get_repair_order", delay_a)
        self.page.locator("#repairOrdersButton").click()
        self.page.locator(f'#repairOrdersList [data-open-repair-order-card="{card_a}"]').click()
        self.page.wait_for_timeout(200)
        self.assertEqual(len(held), 1)
        self.page.locator(f'#repairOrdersList [data-open-repair-order-card="{card_b}"]').click()
        self.page.wait_for_function(
            "id => state.activeCard?.id === id && state.repairOrderWriteReady", arg=card_b
        )
        with self.page.expect_response("**/api/get_repair_order"):
            held.pop().continue_()
        self.page.wait_for_timeout(100)
        self.assertEqual(self.page.evaluate("state.activeCard?.id"), card_b)
        values = self.page.evaluate("() => readRepairOrderFromForm()")
        self.assertEqual(values["date"], b["date"])
        self.assertEqual(values["opened_at"], b["opened_at"])
        self.save()
        saved = self.runtime.service.get_card({"card_id": card_b})["card"]["repair_order"]
        self.assertEqual(saved["date"], b["date"])
        self.assertEqual(saved["opened_at"], b["opened_at"])

    def test_payment_rehydrates_dates_without_losing_the_original(self):
        values = {
            "date": "2026-11-04T05:30:42.123+07:00",
            "opened_at": "2026-11-05T14:21:39.456-04:00",
        }
        card = self.make_order("PAYMENT", values)
        self.open_order(card)
        self.page.locator("#repairOrderPaymentsButton").click()
        self.page.locator("#repairOrderPaymentsModal.is-open").wait_for()
        self.page.locator("#repairOrderPaymentAmount").fill("1")
        with self.page.expect_response("**/api/update_repair_order") as response:
            self.page.locator("#repairOrderPaymentAddButton").click()
        self.assertEqual(response.value.status, 200)
        self.page.wait_for_function("!state.repairOrderMutationRequest")
        saved = self.runtime.service.get_card({"card_id": card})["card"]["repair_order"]
        self.assertEqual(len(saved["payments"]), 1)
        current = self.page.evaluate("() => readRepairOrderFromForm()")
        for field, expected in values.items():
            self.assertEqual(saved[field], expected)
            self.assertEqual(current[field], expected)

    def test_invalid_iso_blocks_writes_but_not_reopening_or_closed_at(self):
        values = {
            "date": "2026-11-04T05:30:42.123+07:00",
            "opened_at": "2026-11-05T14:21:39.456-04:00",
        }
        card = self.make_order("INVALID", values)
        self.open_order(card)
        for invalid in (
            "2026",
            "2026-",
            "2026-02-30",
            "0001-01-01T00:00:00+14:00",
            "9999-12-31T23:59:59-14:00",
            "2026-11-04T05:30",
            "2026-11-04T05:30+0760",
            "04.11.2026 05:30",
        ):
            with self.subTest(invalid=invalid):
                self.page.locator("#repairOrderDate").fill(invalid)
                before = self.writes.count("update_repair_order")
                self.page.locator("#repairOrderSaveButton").click()
                self.assertIn(
                    "ПРОВЕРЬТЕ ДАТУ И ВРЕМЯ", self.page.locator("#statusLine").inner_text()
                )
                self.assertIn(
                    "ПРОВЕРЬТЕ ДАТУ И ВРЕМЯ",
                    self.page.locator("#repairOrderDate").evaluate(
                        "input => input.validationMessage"
                    ),
                )
                self.assertEqual(self.writes.count("update_repair_order"), before)
                self.assertFalse(self.page.evaluate("Boolean(state.repairOrderMutationRequest)"))
                self.assertEqual(self.page.evaluate("() => requireRepairOrderCardId()"), "")
        self.assertEqual(
            self.runtime.service.get_card({"card_id": card})["card"]["repair_order"]["date"],
            values["date"],
        )

        self.page.evaluate("""() => applyRepairOrderToForm({
          ...state.activeCard.repair_order, date: '04.11.2026 05:30',
        })""")
        self.page.locator("#repairOrderDate").fill("202")
        before = self.writes.count("update_repair_order")
        self.page.locator("#repairOrderSaveButton").click()
        self.assertIn("ПРОВЕРЬТЕ ДАТУ И ВРЕМЯ", self.page.locator("#statusLine").inner_text())
        self.assertEqual(self.writes.count("update_repair_order"), before)
        self.page.evaluate("() => applyRepairOrderToForm(state.activeCard.repair_order)")
        self.page.locator("#repairOrderDate").fill("2026-")

        self.page.locator("#repairOrderPaymentsButton").click()
        self.page.locator("#repairOrderPaymentsModal.is-open").wait_for()
        self.page.locator("#repairOrderPaymentAmount").fill("1")
        before = self.writes.count("update_repair_order")
        self.page.locator("#repairOrderPaymentAddButton").click()
        self.assertEqual(self.writes.count("update_repair_order"), before)
        self.assertFalse(self.page.evaluate("Boolean(state.repairOrderMutationRequest)"))
        self.assertEqual(self.page.locator("#repairOrderPaymentAmount").input_value(), "1")
        self.assertIn(
            "ВЕРНИТЕСЬ К ЗАКАЗ-НАРЯДУ", self.page.locator("#repairOrderPaymentsMeta").inner_text()
        )
        self.page.once("dialog", lambda dialog: dialog.accept())
        self.page.locator('#repairOrderPaymentsModal [data-close="repair-order-payments"]').click()

        self.page.route(
            "**/api/preview_repair_order_reopen",
            lambda route: route.fulfill(json={"ok": True, "data": {"payroll_reversals": []}}),
        )
        self.page.once("dialog", lambda dialog: dialog.dismiss())
        self.page.evaluate("els.repairOrderStatus.dataset.status = 'closed'")
        with self.page.expect_request("**/api/preview_repair_order_reopen"):
            self.page.evaluate("() => toggleRepairOrderStatus()")
        self.page.wait_for_function("!state.repairOrderMutationRequest")
        self.page.evaluate("""() => {
          els.repairOrderStatus.dataset.status = 'open';
          syncRepairOrderEditingState(readRepairOrderFromForm());
        }""")
        self.page.locator("#repairOrderDate").fill(values["date"])
        self.assertEqual(
            self.page.locator("#repairOrderDate").evaluate("input => input.validationMessage"), ""
        )
        self.page.locator("#repairOrderClosedAt").fill("2026-")
        self.save()
        self.assertEqual(
            self.runtime.service.get_card({"card_id": card})["card"]["repair_order"]["date"],
            values["date"],
        )

    def test_unchanged_malformed_legacy_iso_is_not_rewritten(self):
        legacy = "2026-02-30"
        card = self.make_order("MALFORMED LEGACY", {"date": legacy, "opened_at": "2026-11-04"})
        self.open_order(card)
        self.assertEqual(self.page.locator("#repairOrderDate").input_value(), legacy)
        self.save()
        saved = self.runtime.service.get_card({"card_id": card})["card"]["repair_order"]
        self.assertEqual(saved["date"], legacy)

    def test_switching_orders_clears_date_validity(self):
        original = "2026-11-04T05:30:42.123+07:00"
        first = self.make_order("VALIDITY A", {"date": original, "opened_at": original})
        second = self.make_order("VALIDITY B", {"date": original, "opened_at": original})
        self.open_order(first)
        self.page.locator("#repairOrderDate").fill("2026-")
        self.page.locator("#repairOrderSaveButton").click()
        self.assertTrue(
            self.page.locator("#repairOrderDate").evaluate("input => input.validationMessage")
        )
        self.page.once("dialog", lambda dialog: dialog.accept())
        self.page.locator('#repairOrderModal [data-close="repair-order"]').click()
        self.assertTrue(self.page.locator("#repairOrderModal").is_hidden())
        self.page.locator(f'#repairOrdersList [data-open-repair-order-card="{second}"]').click()
        self.page.wait_for_function(
            "id => state.activeCard?.id === id && state.repairOrderWriteReady", arg=second
        )
        self.assertEqual(
            self.page.locator("#repairOrderDate").evaluate("input => input.validationMessage"), ""
        )
        self.save()
        saved = self.runtime.service.get_card({"card_id": second})["card"]["repair_order"]
        self.assertEqual(saved["date"], original)

    def test_common_offset_formats_survive_manual_edit(self):
        original = "2026-11-04T05:30:42.123+07:00"
        card = self.make_order("OFFSETS", {"date": original, "opened_at": original})
        self.open_order(card)
        for edited in ("2026-11-04T05:31+07", "2026-11-04T05:32+0700", "2026-11-04T05:33Z"):
            with self.subTest(edited=edited):
                self.page.locator("#repairOrderDate").fill(edited)
                self.save()
                saved = self.runtime.service.get_card({"card_id": card})["card"]["repair_order"]
                self.assertEqual(saved["date"], edited)
                self.assertEqual(saved["opened_at"], original)

    def test_naive_iso_to_dotted_requires_a_real_calendar_date(self):
        original = "2026-11-04T05:30"
        card = self.make_order("NAIVE TO DOTTED", {"date": original, "opened_at": original})
        self.open_order(card)
        self.page.locator("#repairOrderDate").fill("31.02.26 10:00")
        before = self.writes.count("update_repair_order")
        self.page.locator("#repairOrderSaveButton").click()
        self.assertIn("ПРОВЕРЬТЕ ДАТУ И ВРЕМЯ", self.page.locator("#statusLine").inner_text())
        self.assertEqual(self.writes.count("update_repair_order"), before)
        self.page.locator("#repairOrderDate").fill("29.02.24 10:00")
        self.save()
        saved = self.runtime.service.get_card({"card_id": card})["card"]["repair_order"]
        self.assertEqual(saved["date"], "29.02.2024 10:00")
        self.assertEqual(saved["opened_at"], original)

    def test_print_gate_skips_invalid_draft_only_while_order_form_is_open(self):
        original = "2026-11-04T05:30:42.123+07:00"
        card = self.make_order("PRINT GUARD", {"date": original, "opened_at": original})
        self.open_order(card)
        self.page.locator("#repairOrderDate").fill("2026-")
        before = (
            self.writes.count("get_repair_order_print_workspace"),
            self.writes.count("create_card"),
        )
        self.page.locator("#repairOrderPrintButton").click()
        self.assertIn("ПРОВЕРЬТЕ ДАТУ И ВРЕМЯ", self.page.locator("#statusLine").inner_text())
        self.assertEqual(
            (
                self.writes.count("get_repair_order_print_workspace"),
                self.writes.count("create_card"),
            ),
            before,
        )
        self.assertFalse(self.page.locator("#repairOrderPrintModal").is_visible())
        self.page.once("dialog", lambda dialog: dialog.accept())
        self.page.locator('#repairOrderModal [data-close="repair-order"]').click()
        self.assertTrue(self.page.locator("#repairOrderModal").is_hidden())
        self.page.evaluate("id => openCardWorkspace(id)", card)
        self.page.wait_for_function(
            "id => state.activeCard?.id === id && state.activeCardIsFull", arg=card
        )
        self.assertEqual(self.page.locator("#repairOrderDate").input_value(), "2026-")
        self.page.route(
            "**/api/get_repair_order_print_workspace",
            lambda route: route.fulfill(
                status=503, json={"ok": False, "error": {"message": "SYNTHETIC"}}
            ),
        )
        with self.page.expect_request("**/api/get_repair_order_print_workspace"):
            self.page.evaluate(
                "() => { void invokeBoardModule('printing', 'printRepairOrderDraft', []); }"
            )

    def test_full_iso_is_visible_and_keyboard_navigation_works(self):
        iso = "2026-11-04T05:30:42.123456+07:00"
        card = self.make_order("LAYOUT", {"date": iso, "opened_at": iso})
        self.open_order(card)
        for width in (1440, 1024):
            with self.subTest(width=width):
                self.page.set_viewport_size({"width": width, "height": 900})
                info = self.page.evaluate("""() => {
                  const input = els.repairOrderDate, style = getComputedStyle(input);
                  const canvas = document.createElement('canvas'), ctx = canvas.getContext('2d');
                  ctx.font = style.font;
                  const a = input.getBoundingClientRect();
                  const client = els.repairOrderClient.getBoundingClientRect();
                  const vehicle = els.repairOrderVehicle.getBoundingClientRect();
                  return {available: input.clientWidth - parseFloat(style.paddingLeft) - parseFloat(style.paddingRight),
                    text: ctx.measureText(input.value).width, bottom: a.bottom,
                    clientTop: client.top, vehicleTop: vehicle.top,
                    clientRight: client.right, vehicleLeft: vehicle.left};
                }""")
                self.assertGreaterEqual(info["available"], info["text"])
                self.assertLessEqual(info["bottom"], info["clientTop"])
                self.assertLessEqual(info["bottom"], info["vehicleTop"])
                self.assertLessEqual(info["clientRight"], info["vehicleLeft"])
                self.page.locator("#repairOrderDate").focus()
                self.page.keyboard.press("Tab")
                self.assertEqual(
                    self.page.evaluate("document.activeElement.id"), "repairOrderOpenedAt"
                )

    def test_empty_opened_at_uses_original_iso_date_in_another_timezone(self):
        from minimal_kanban.models import business_timezone, clear_business_timezone_cache

        environment = patch.dict(os.environ, {"AUTOSTOPCRM_BUSINESS_TIMEZONE": "Asia/Krasnoyarsk"})
        environment.start()
        self.addCleanup(clear_business_timezone_cache)
        self.addCleanup(environment.stop)
        clear_business_timezone_cache()
        self.assertEqual(business_timezone().key, "Asia/Krasnoyarsk")

        iso = "2026-11-04T05:30:42.123+07:00"
        card = self.make_order("FALLBACK", {"date": iso})
        state_file = Path(self.runtime.temp_dir.name) / "state.json"
        state = json.loads(state_file.read_text(encoding="utf-8"))
        next(item for item in state["cards"] if item["id"] == card)["repair_order"]["opened_at"] = (
            ""
        )
        temporary = state_file.with_name("synthetic-swap.json")
        temporary.write_text(json.dumps(state), encoding="utf-8")
        os.replace(temporary, state_file)
        original = self.runtime.service.get_card({"card_id": card})["card"]["repair_order"]
        self.assertEqual(original["opened_at"], "")

        context = self.browser.new_context(
            viewport={"width": 1440, "height": 960},
            timezone_id="America/New_York",
            extra_http_headers=self.runtime.auth_headers,
        )
        self.addCleanup(context.close)
        context.add_init_script(
            "localStorage.setItem('kanban-operator-session', "
            + json.dumps(self.session_token)
            + ");"
        )
        self.page = context.new_page()
        self.page.on("pageerror", lambda error: self.page_errors.append(str(error)))
        self.open_order(card)
        self.assertEqual(
            self.page.evaluate("Intl.DateTimeFormat().resolvedOptions().timeZone"),
            "America/New_York",
        )
        current = self.page.evaluate("() => readRepairOrderFromForm()")
        self.assertEqual(current["date"], iso)
        self.assertEqual(current["opened_at"], iso)
        self.save()
        saved = self.runtime.service.get_card({"card_id": card})["card"]["repair_order"]
        self.assertEqual(saved["date"], iso)
        self.assertEqual(saved["opened_at"], iso)
        self.assertEqual(datetime.fromisoformat(saved["opened_at"]), datetime.fromisoformat(iso))

    def test_date_only_does_not_shift_in_form_or_journal(self):
        date = "2026-11-04"
        card = self.make_order("DATE ONLY", {"date": date, "opened_at": date})
        client = self.runtime.service.get_card({"card_id": card})["card"]["client_id"]
        for timezone in ("America/New_York", "Asia/Krasnoyarsk"):
            with self.subTest(timezone=timezone):
                context = self.browser.new_context(
                    viewport={"width": 1440, "height": 960},
                    timezone_id=timezone,
                    extra_http_headers=self.runtime.auth_headers,
                )
                self.addCleanup(context.close)
                context.add_init_script(
                    "localStorage.setItem('kanban-operator-session', "
                    + json.dumps(self.session_token)
                    + ");"
                )
                self.page = context.new_page()
                self.page.on("pageerror", lambda error: self.page_errors.append(str(error)))
                self.load_board()
                self.page.locator("#repairOrdersButton").click()
                row = self.page.locator(f'#repairOrdersList [data-open-repair-order-card="{card}"]')
                row.scroll_into_view_if_needed()
                self.assertEqual(row.locator(".repair-orders-row__opened").inner_text(), "04.11.26")
                row.click()
                self.page.wait_for_function(
                    "state.repairOrderWriteReady && !state.repairOrderLoading"
                )
                self.assertEqual(self.page.locator("#repairOrderDate").input_value(), date)
                self.assertEqual(self.page.locator("#repairOrderOpenedAt").input_value(), date)
                self.save()
                saved = self.runtime.service.get_card({"card_id": card})["card"]["repair_order"]
                self.assertEqual(saved["date"], date)
                self.assertEqual(saved["opened_at"], date)
                self.page.locator('#repairOrderModal [data-close="repair-order"]').click()
                self.page.locator('#repairOrdersModal [data-close="repair-orders"]').click()
                self.page.locator("#clientsButton").click()
                self.page.locator("#clientsSearchInput").fill("SYNTHETIC ISO DATE ONLY")
                self.page.wait_for_function(
                    "id => state.clientsLoaded && state.clients.some(client => client.id === id && client.stats)",
                    arg=client,
                )
                client_row = self.page.locator(f'#clientsList [data-client-id="{client}"]')
                self.assertIn("04.11.26", client_row.inner_text())
                client_row.click()
                self.page.wait_for_function(
                    "id => state.clientsActiveProfile?.client?.id === id", arg=client
                )
                orders = self.page.locator("#clientOrdersList")
                self.assertIn("04.11.26", orders.inner_text())
                self.page.evaluate("""() => {
                  state.clientsActiveProfile.repair_orders[0].opened_at = '2026-11-04<img src=x onerror=alert(1)>';
                  renderClientProfile(state.clientsActiveProfile);
                }""")
                self.assertIn("<img src=x onerror=alert(1)>", orders.inner_text())
                self.assertEqual(orders.locator("img").count(), 0)
                self.page.locator('#clientsModal [data-close="clients"]').click()
                self.page.set_viewport_size({"width": 390, "height": 844})
                self.page.click('[data-mobile-view="more"]')
                self.page.click('[data-mobile-open="clients"]')
                mobile_row = self.page.locator(
                    f'#mobileClientsList [data-mobile-client-id="{client}"]'
                )
                self.assertIn("04.11.26", mobile_row.inner_text())
                mobile_row.click()
                self.page.wait_for_function(
                    "id => state.clientsActiveProfile?.client?.id === id", arg=client
                )
                self.assertIn("04.11.26", self.page.locator("#mobileClientDetail").inner_text())
