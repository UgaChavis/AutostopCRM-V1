"""Obsolete read failures cannot revoke newer constructor access or state."""

from __future__ import annotations

import os
import unittest

from tests import test_manager_structure_browser as support


class ManagerReadFreshnessBrowserTests(unittest.TestCase):
    setUpClass = classmethod(support.ManagerStructureBrowserTests.setUpClass.__func__)
    setUp = support.ManagerStructureBrowserTests.setUp
    read = support.ManagerStructureBrowserTests.read
    automotive_fixture = support.ManagerStructureBrowserTests.automotive_fixture

    def hold_next_read(self, status: int, payload: dict) -> None:
        self.page.evaluate(
            """({status, payload}) => {
                const original = window.fetch;
                let holdNext = true;
                window.heldOldRead = null;
                window.fetch = (url, options) => {
                    if (String(url) === '/api/manager_structure' && holdNext) {
                        holdNext = false;
                        return new Promise(resolve => {
                            window.heldOldRead = () => resolve(new Response(
                                JSON.stringify(payload),
                                {status, headers: {'Content-Type': 'application/json'}}
                            ));
                        });
                    }
                    return original(url, options);
                };
            }""",
            {"status": status, "payload": payload},
        )
        self.page.clock.fast_forward(5000)
        self.page.wait_for_function("window.heldOldRead !== null")

    def accept_newer_title(self, title: str) -> None:
        response = self.context.request.post(
            self.runtime.base_url + "/api/manager_structure/apply",
            headers={"X-Operator-Session": self.admin},
            data={
                "operation": "upsert_element",
                "element": {"id": "E1", "title": title},
                "expected_version": self.read()["version"],
                "idempotency_key": "read-freshness-" + os.urandom(8).hex(),
            },
        )
        self.assertTrue(response.ok, response.text())
        version = response.json()["data"]["version"]
        self.page.clock.fast_forward(5000)
        self.page.wait_for_function(
            "version => document.querySelector('#count').textContent.includes(`v${version}`)",
            arg=version,
        )
        self.assertEqual(self.page.locator('.node[data-id="E1"] .title-main').text_content(), title)

    def release_old_read(self) -> None:
        self.page.evaluate("window.heldOldRead()")
        self.page.wait_for_timeout(100)

    def assert_owner_access(self) -> None:
        self.assertIn("Владелец", self.page.locator("#mode").inner_text())
        self.assertFalse(self.page.locator("#canvasLoadError").is_visible())
        self.assertTrue(self.page.locator('[data-create="module"]').is_enabled())
        self.assertEqual(self.errors, [])

    def test_old_session_401_cannot_revoke_newly_accepted_owner_session(self):
        self.page.clock.install()
        self.automotive_fixture()
        self.hold_next_read(401, {"ok": False, "error": {"message": "Old session expired"}})
        newer = self.context.request.post(
            self.runtime.base_url + "/api/login_operator",
            data={"username": "admin", "password": "admin"},
        ).json()["data"]["session"]["token"]
        self.assertNotEqual(newer, self.admin)
        self.page.evaluate(
            """token => {
                localStorage.setItem('kanban-operator-session', token);
                window.dispatchEvent(new StorageEvent('storage', {
                    key: 'kanban-operator-session'
                }));
            }""",
            newer,
        )
        self.accept_newer_title("New owner read")
        before = self.read(newer)
        self.release_old_read()
        self.assert_owner_access()
        self.assertEqual(self.read(newer), before)

    def test_older_same_session_error_or_malformed_reply_keeps_latest_read(self):
        self.page.clock.install()
        self.automotive_fixture()
        for index, (status, payload) in enumerate(
            (
                (401, {"ok": False, "error": {"message": "Obsolete denied read"}}),
                (503, {"ok": False, "error": {"message": "Obsolete failed read"}}),
                (200, {"ok": True, "data": {"version": 999}}),
            )
        ):
            with self.subTest(status=status):
                self.hold_next_read(status, payload)
                self.accept_newer_title(f"Current read {index}")
                before = self.read()
                self.release_old_read()
                self.assert_owner_access()
                self.assertEqual(self.read(), before)


if __name__ == "__main__":
    unittest.main()
