from __future__ import annotations

import unittest

from scripts import j1_forward_cold_archive as archive


class ColdArchiveLogicalPaths(unittest.TestCase):
    def test_production_cold_graph_is_typed_even_beneath_controller_root(self):
        root = "/var/lib/autostop-crm-forward"
        for area in ("state", "attempts", "origins", "launch-permits"):
            with self.subTest(area=area):
                self.assertTrue(archive.control_path(root + "/" + area + "/record.json", root))
        for suffix in (
            "cold/v1/epochs/epoch/epoch.json",
            "cold/v1/epochs/epoch/journals/op/state.json",
            "cold-controller-launch/op/state.json",
            "cold-requests/op/request.json",
            "state-extra/record.json",
            "",
        ):
            with self.subTest(suffix=suffix):
                self.assertFalse(archive.control_path(root + "/" + suffix, root))
        self.assertFalse(
            archive.control_path("/etc/autostop-crm-forward/cold/v1/authority.json", root)
        )


if __name__ == "__main__":
    unittest.main()
