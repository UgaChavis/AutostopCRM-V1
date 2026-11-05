"""Client profile writes keep the revision of the displayed source profile."""

from tests.browser_ui_support import UiBrowserTestCase


class ClientProfileCasBrowserTests(UiBrowserTestCase):
    def open_client(self, client_id):
        self.page.locator(f'#clientsList [data-client-id="{client_id}"]').click()
        self.page.wait_for_function(
            "id => state.clientsDesktopProfile?.client?.id === id", arg=client_id
        )

    def save_client_profile(self):
        self.page.wait_for_function("els.clientSaveButton && !els.clientSaveButton.disabled")
        self.page.evaluate("async () => { await saveClientProfile(); }")

    def test_conflict_keeps_draft_and_does_not_repeat(self):
        other = self.runtime.service.create_client({"display_name": "SYNTHETIC OTHER CLIENT"})[
            "client"
        ]["id"]
        client_id = self.create_client_and_open("SYNTHETIC CAS CLIENT")
        version = self.page.evaluate("state.clientsDesktopProfile.client.updated_at")
        attempts = []

        def reject(route):
            attempts.append(route.request.post_data_json)
            route.fulfill(
                status=409,
                json={
                    "ok": False,
                    "error": {"code": "client_update_conflict", "message": "SYNTHETIC CONFLICT"},
                },
            )

        self.page.route("**/api/update_client", reject)
        self.page.locator("#clientCommentInput").fill("SYNTHETIC UNSAVED")
        self.save_client_profile()
        self.page.wait_for_function("!state.clientMutationRequest")
        self.assertEqual(len(attempts), 1)
        self.assertEqual(attempts[0]["client_id"], client_id)
        self.assertEqual(attempts[0]["expected_updated_at"], version)
        self.assertEqual(
            self.page.locator("#clientCommentInput").input_value(), "SYNTHETIC UNSAVED"
        )
        self.assertFalse(self.page.locator("#clientSaveButton").is_disabled())
        self.assertIn("SYNTHETIC CONFLICT", self.page.locator("#statusLine").inner_text())
        self.page.once("dialog", lambda dialog: dialog.dismiss())
        self.page.locator(f'#clientsList [data-client-id="{other}"]').click()
        self.assertEqual(self.page.evaluate("state.clientsActiveId"), client_id)
        self.assertEqual(
            self.page.locator("#clientCommentInput").input_value(), "SYNTHETIC UNSAVED"
        )
        self.page.once("dialog", lambda dialog: dialog.accept())
        self.open_client(other)
        self.assertEqual(len(attempts), 1)

    def test_create_does_not_send_a_revision(self):
        self.load_board()
        self.page.locator("#clientsButton").click()
        self.page.locator("#clientNewButton").click()
        self.page.locator("#clientDisplayNameInput").fill("SYNTHETIC NEW CLIENT")
        attempts = []

        def reject(route):
            attempts.append(route.request.post_data_json)
            route.fulfill(status=422, json={"ok": False, "error": {"message": "SYNTHETIC"}})

        self.page.route("**/api/create_client", reject)
        self.save_client_profile()
        self.page.wait_for_function("!state.clientMutationRequest")
        self.assertEqual(len(attempts), 1)
        self.assertNotIn("expected_updated_at", attempts[0])
        self.assertEqual(
            self.page.locator("#clientDisplayNameInput").input_value(), "SYNTHETIC NEW CLIENT"
        )

    def test_missing_source_revision_stops_before_write(self):
        self.create_client_and_open("SYNTHETIC MISSING VERSION")
        self.page.evaluate("state.clientsDraftRevision.version = ''")
        self.page.locator("#clientCommentInput").fill("SYNTHETIC DRAFT")
        self.save_client_profile()
        self.page.wait_for_function("!state.clientMutationRequest")
        self.assertNotIn("update_client", self.writes)
        self.assertEqual(self.page.locator("#clientCommentInput").input_value(), "SYNTHETIC DRAFT")
        self.assertIn(
            "ВЕРСИЯ КЛИЕНТА НЕ ПОДТВЕРЖДЕНА", self.page.locator("#statusLine").inner_text()
        )

    def test_uncertain_write_keeps_revision_after_switching_clients(self):
        first = self.runtime.service.create_client({"display_name": "SYNTHETIC CLIENT A"})[
            "client"
        ]["id"]
        second = self.runtime.service.create_client({"display_name": "SYNTHETIC CLIENT B"})[
            "client"
        ]["id"]
        self.load_board()
        self.page.locator("#clientsButton").click()
        self.open_client(first)
        original = self.page.evaluate("state.clientsDesktopProfile.client.updated_at")
        attempts = []

        def fail_write(route):
            attempts.append(route.request.post_data_json)
            route.fulfill(status=503, json={"ok": False, "error": {"message": "SYNTHETIC"}})

        def fail_read(route):
            route.fulfill(status=503, json={"ok": False, "error": {"message": "SYNTHETIC"}})

        self.page.route("**/api/update_client", fail_write)
        self.page.route("**/api/get_client?**", fail_read)
        self.page.locator("#clientCommentInput").fill("SYNTHETIC UNKNOWN")
        self.save_client_profile()
        self.page.wait_for_function(
            "state.clientProfileSaveCheckpoint?.uncertain && !state.clientMutationRequest"
        )
        self.assertTrue(
            self.page.evaluate("state.clientProfileSaveCheckpoint.uncertain"),
            (attempts, self.page.locator("#statusLine").inner_text()),
        )
        self.page.unroute("**/api/get_client?**", fail_read)
        self.page.once("dialog", lambda dialog: dialog.accept())
        self.open_client(second)
        self.open_client(first)
        self.page.locator("#clientCommentInput").fill("SYNTHETIC UNKNOWN")
        self.save_client_profile()
        self.page.wait_for_function("!state.clientMutationRequest")
        self.assertEqual(len(attempts), 1)
        self.assertEqual(attempts[0]["expected_updated_at"], original)
        self.assertEqual(
            self.page.evaluate("state.clientProfileSaveCheckpoint.expectedUpdatedAt"), original
        )
        self.assertEqual(
            self.page.evaluate("state.clientProfileSaveCheckpoint.payload"),
            {
                key: value
                for key, value in attempts[0].items()
                if key not in {"client_id", "expected_updated_at"}
            },
        )
        self.assertTrue(self.page.evaluate("state.clientProfileSaveCheckpoint.uncertain"))
        self.assertEqual(
            self.page.locator("#clientCommentInput").input_value(), "SYNTHETIC UNKNOWN"
        )

    def test_confirmed_uncertain_write_rebases_new_draft_before_next_save(self):
        client_id = self.create_client_and_open("SYNTHETIC REBASE CLIENT")
        first_version = self.page.evaluate("state.clientsDesktopProfile.client.updated_at")
        attempts = []
        confirmed_versions = []

        def lose_write(route):
            attempts.append(route.request.post_data_json)
            response = route.fetch()
            self.assertEqual(response.status, 200)
            confirmed_versions.append(response.json()["data"]["client"]["updated_at"])
            route.abort("failed")

        def fail_read(route):
            route.fulfill(status=503, json={"ok": False, "error": {"message": "SYNTHETIC"}})

        self.page.route("**/api/update_client", lose_write)
        self.page.route("**/api/get_client?**", fail_read)
        self.page.locator("#clientCommentInput").fill("SYNTHETIC FIRST DRAFT")
        self.save_client_profile()
        self.page.wait_for_function(
            "state.clientProfileSaveCheckpoint?.uncertain && !state.clientMutationRequest"
        )
        self.assertEqual(attempts[0]["expected_updated_at"], first_version)
        self.assertNotEqual(confirmed_versions[0], first_version)

        self.page.unroute("**/api/get_client?**", fail_read)
        self.page.locator("#clientCommentInput").fill("SYNTHETIC SECOND DRAFT")
        self.save_client_profile()
        self.page.wait_for_function(
            "!state.clientProfileSaveCheckpoint.uncertain && !state.clientMutationRequest"
        )
        self.assertEqual(len(attempts), 1)
        self.assertEqual(
            self.page.locator("#clientCommentInput").input_value(), "SYNTHETIC SECOND DRAFT"
        )
        self.assertEqual(
            self.page.evaluate("state.clientsDesktopProfile.client.updated_at"),
            first_version,
        )
        self.assertEqual(
            self.page.evaluate("state.clientsDraftRevision.version"), confirmed_versions[0]
        )
        self.assertEqual(
            self.page.evaluate("state.clientProfileSaveCheckpoint.expectedUpdatedAt"),
            confirmed_versions[0],
        )

        self.page.unroute("**/api/update_client", lose_write)
        with self.page.expect_request("**/api/update_client") as request:
            self.save_client_profile()
        self.assertEqual(request.value.post_data_json["expected_updated_at"], confirmed_versions[0])
        self.page.wait_for_function("!state.clientMutationRequest")
        self.assertEqual(
            self.runtime.service.get_client({"client_id": client_id})["client"]["comment"],
            "SYNTHETIC SECOND DRAFT",
        )

    def test_readback_with_missing_version_cannot_release_checkpoint(self):
        self.create_client_and_open("SYNTHETIC INVALID READBACK")
        attempts = []

        def lose_write(route):
            attempts.append(route.request.post_data_json)
            self.assertEqual(route.fetch().status, 200)
            route.abort("failed")

        def omit_version(route):
            payload = route.fetch().json()
            payload["data"]["client"]["updated_at"] = ""
            route.fulfill(json=payload)

        self.page.route("**/api/update_client", lose_write)
        self.page.route("**/api/get_client?**", omit_version)
        self.page.locator("#clientCommentInput").fill("SYNTHETIC FIRST DRAFT")
        self.save_client_profile()
        self.page.wait_for_function(
            "state.clientProfileSaveCheckpoint?.uncertain && !state.clientMutationRequest"
        )
        self.page.locator("#clientCommentInput").fill("SYNTHETIC SECOND DRAFT")
        self.save_client_profile()
        self.page.wait_for_function("!state.clientMutationRequest")
        self.assertTrue(self.page.evaluate("state.clientProfileSaveCheckpoint.uncertain"))
        self.assertEqual(len(attempts), 1)
        self.assertEqual(
            self.page.locator("#clientCommentInput").input_value(), "SYNTHETIC SECOND DRAFT"
        )

    def test_rebased_draft_conflicts_if_another_operator_updates_after_readback(self):
        client_id = self.create_client_and_open("SYNTHETIC CONFLICT CLIENT")
        attempts = []

        def lose_write(route):
            attempts.append(route.request.post_data_json)
            self.assertEqual(route.fetch().status, 200)
            route.abort("failed")

        def fail_read(route):
            route.fulfill(status=503, json={"ok": False, "error": {"message": "SYNTHETIC"}})

        self.page.route("**/api/update_client", lose_write)
        self.page.route("**/api/get_client?**", fail_read)
        self.page.locator("#clientCommentInput").fill("SYNTHETIC FIRST DRAFT")
        self.save_client_profile()
        self.page.wait_for_function(
            "state.clientProfileSaveCheckpoint?.uncertain && !state.clientMutationRequest"
        )

        self.page.unroute("**/api/get_client?**", fail_read)
        self.page.locator("#clientCommentInput").fill("SYNTHETIC SECOND DRAFT")
        self.save_client_profile()
        self.page.wait_for_function(
            "!state.clientProfileSaveCheckpoint.uncertain && !state.clientMutationRequest"
        )
        rebased_version = self.page.evaluate("state.clientProfileSaveCheckpoint.expectedUpdatedAt")
        self.assertEqual(len(attempts), 1)

        remote = self.runtime.service.update_client(
            {"client_id": client_id, "display_name": "SYNTHETIC REMOTE UPDATE"}
        )["client"]
        self.assertNotEqual(remote["updated_at"], rebased_version)
        self.page.unroute("**/api/update_client", lose_write)
        with self.page.expect_response("**/api/update_client") as response:
            self.save_client_profile()
        self.assertEqual(response.value.status, 409)
        self.assertEqual(
            response.value.request.post_data_json["expected_updated_at"], rebased_version
        )
        self.page.wait_for_function("!state.clientMutationRequest")
        current = self.runtime.service.get_client({"client_id": client_id})["client"]
        self.assertEqual(current["display_name"], "SYNTHETIC REMOTE UPDATE")
        self.assertEqual(current["comment"], "SYNTHETIC FIRST DRAFT")
        self.assertEqual(
            self.page.locator("#clientCommentInput").input_value(), "SYNTHETIC SECOND DRAFT"
        )
        self.assertEqual(self.writes.count("update_client"), 2)
