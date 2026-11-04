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
        self.assertEqual(
            self.page.locator("#repairOrderDate").input_value(),
            self.page.locator("#repairOrderOpenedAt").input_value(),
        )
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
        self.page.locator("#repairOrderDate").fill("04.01.2026 09:03:04")
        self.assertTrue(self.page.evaluate("repairOrderModalHasUnsavedChanges()"))
        self.save()
        saved = self.runtime.service.get_card({"card_id": card_a})["card"]["repair_order"]
        self.assertEqual(saved["date"], "04.01.2026 09:03:04")
        self.assertEqual(saved["opened_at"], a["opened_at"])
        self.page.locator("#repairOrderOpenedAt").fill("05.02.2026 11:22:33")
        self.save()
        saved = self.runtime.service.get_card({"card_id": card_a})["card"]["repair_order"]
        self.assertEqual(saved["date"], "04.01.2026 09:03:04")
        self.assertEqual(saved["opened_at"], "05.02.2026 11:22:33")

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
