import unittest

from tg_sync import ui


class TgSyncUiTests(unittest.TestCase):
    def test_index_html_exposes_read_only_banner_and_disabled_compose(self):
        html = ui.render_index_html(api_url="http://127.0.0.1:8765", db_path="monitor.db")

        self.assertIn("Read-only mode", html)
        self.assertIn("disabled", html)
        self.assertIn("Manual-gated future action", html)
        self.assertIn("POST /send", html)

    def test_create_app_does_not_register_send_routes(self):
        app = ui.create_app(api_url="http://127.0.0.1:8765", db_path="monitor.db")
        routes = {route.resource.canonical for route in app.router.routes()}

        self.assertNotIn("/send", routes)
        self.assertNotIn("/api/send", routes)
        self.assertIn("/api/status", routes)
        self.assertIn("/api/search", routes)


if __name__ == "__main__":
    unittest.main()
