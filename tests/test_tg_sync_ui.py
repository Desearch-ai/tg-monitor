import unittest

from tg_sync import ui


class TgSyncUiTests(unittest.TestCase):
    def test_index_html_exposes_four_workspace_lanes_without_compose_placeholders(self):
        html = ui.render_index_html(api_url="http://127.0.0.1:8765", db_path="monitor.db")

        for label in ("Home / Sync Dashboard", "Chats / Sources", "Search / Research", "Thread / Export"):
            self.assertIn(label, html)
        self.assertIn("Read-only local workspace", html)
        self.assertIn("Local SQLite search", html)
        self.assertNotIn("Manual-gated future action", html)
        self.assertNotIn("Send disabled", html)
        self.assertNotIn("Reply disabled", html)
        self.assertNotIn("Delete disabled", html)
        self.assertNotIn("/api/send", html)
        self.assertNotIn("POST /send", html)

    def test_create_app_does_not_register_send_routes(self):
        app = ui.create_app(api_url="http://127.0.0.1:8765", db_path="monitor.db")
        routes = {route.resource.canonical for route in app.router.routes()}

        self.assertNotIn("/send", routes)
        self.assertNotIn("/api/send", routes)
        self.assertIn("/api/status", routes)
        self.assertIn("/api/dashboard", routes)
        self.assertIn("/api/search", routes)


class TgSyncUiDashboardTests(unittest.TestCase):
    def test_store_summary_counts_types_and_latest_activity(self):
        import sqlite3
        import tempfile

        schema = """
        CREATE TABLE messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            dialog_id TEXT,
            dialog_name TEXT,
            dialog_type TEXT,
            msg_id INTEGER,
            sender_id INTEGER,
            sender_name TEXT,
            text TEXT,
            date TEXT,
            reply_to_id INTEGER,
            UNIQUE(dialog_id, msg_id)
        )
        """
        with tempfile.TemporaryDirectory() as tmp:
            db_path = f"{tmp}/monitor.db"
            conn = sqlite3.connect(db_path)
            conn.execute(schema)
            conn.executemany(
                """
                INSERT INTO messages
                    (dialog_id, dialog_name, dialog_type, msg_id, sender_id, sender_name, text, date, reply_to_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    ("-1001", "Nerds", "group", 10, 1, "Alice", "Root", "2026-05-09T10:00:00+00:00", None),
                    ("-1001", "Nerds", "group", 11, 2, "Bob", "Reply", "2026-05-09T10:01:00+00:00", 10),
                    ("-2002", "Updates", "channel", 20, 3, "Cara", "News", "2026-05-09T11:00:00+00:00", None),
                ],
            )
            conn.commit()
            conn.close()

            summary = ui.build_store_summary(db_path)

        self.assertEqual(summary["total_messages"], 3)
        self.assertEqual(summary["by_type"], {"channel": 1, "group": 2})
        self.assertEqual(summary["latest_message_at"], "2026-05-09T11:00:00+00:00")
        self.assertEqual(summary["source_count"], 2)
        self.assertEqual(summary["recent_activity"][0]["msg_id"], 20)


if __name__ == "__main__":
    unittest.main()
