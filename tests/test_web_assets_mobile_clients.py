"""Contract for mobile client-list navigation and profile selection."""

import unittest

from tests.source_path_support import ensure_source_path

ensure_source_path()

from minimal_kanban.web_assets import BOARD_WEB_APP_CONTRACT_TEXT as BOARD_WEB_APP_HTML  # noqa: E402, I001


class MobileClientsAssetsTests(unittest.TestCase):
    def test_mobile_more_clients_module_has_native_drilldown(self) -> None:
        self.assertIn("mobileMorePanel: ''", BOARD_WEB_APP_HTML)
        for element_id in (
            "mobileClientsPanel",
            "mobileClientsBackButton",
            "mobileClientsSearchInput",
            "mobileClientsMeta",
            "mobileClientsList",
            "mobileClientDetail",
        ):
            self.assertIn(element_id, BOARD_WEB_APP_HTML)
        self.assertIn("function openMobileClientsPanel()", BOARD_WEB_APP_HTML)
        self.assertIn("function renderMobileClientsPanel(preserveList = false)", BOARD_WEB_APP_HTML)
        self.assertIn("function loadMobileClients(", BOARD_WEB_APP_HTML)
        self.assertIn("function loadMobileClientProfile(clientId)", BOARD_WEB_APP_HTML)
        self.assertIn("data-mobile-client-id", BOARD_WEB_APP_HTML)
        self.assertIn("data-mobile-client-order-card", BOARD_WEB_APP_HTML)
        self.assertIn(
            "if (target === 'clients') return openMobileClientsPanel();", BOARD_WEB_APP_HTML
        )
        self.assertIn("els.mobileClientsSearchInput?.addEventListener('input'", BOARD_WEB_APP_HTML)
        self.assertIn(".mobile-client-row", BOARD_WEB_APP_HTML)
        self.assertIn(".mobile-client-detail", BOARD_WEB_APP_HTML)
        self.assertIn(".mobile-more-grid[hidden]", BOARD_WEB_APP_HTML)
