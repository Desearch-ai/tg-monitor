import json
import sqlite3
import tempfile
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


class TgSyncUiWatchlistTests(unittest.TestCase):
    def test_watched_sources_uses_sources_from_live_source_watchlist_dict(self):
        watched = ui._watched_sources(
            {
                "source_watchlist": {
                    "mode": "configured_sources",
                    "count": 2,
                    "sources": [
                        {"id": "-1002564889965", "name": "☝️🤓 τhe nerds 🧠🥼", "type": "group"},
                        {"id": "-1002316424674", "name": "Cosmonauts 🚀 | macrocosmos", "type": "group"},
                    ],
                }
            },
            {"sources": []},
        )

        self.assertEqual(
            watched,
            [
                {"id": "-1002564889965", "name": "☝️🤓 τhe nerds 🧠🥼", "type": "group", "aliases": []},
                {"id": "-1002316424674", "name": "Cosmonauts 🚀 | macrocosmos", "type": "group", "aliases": []},
            ],
        )
        self.assertNotIn("configured_sources", {source["name"] for source in watched})
        self.assertNotIn("5", {source["id"] for source in watched})

    def test_watched_dialog_scope_returns_configured_dialogs_present_in_sqlite(self):
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
                    ("-1002564889965", "☝️🤓 τhe nerds 🧠🥼", "group", 10, 1, "Alice", "Signal", "2026-05-09T10:00:00+00:00", None),
                    ("-1002316424674", "Cosmonauts 🚀 | macrocosmos", "group", 20, 2, "Bob", "Macro", "2026-05-09T11:00:00+00:00", None),
                    ("-999", "Not watched", "group", 30, 3, "Cara", "Noise", "2026-05-09T12:00:00+00:00", None),
                ],
            )
            conn.commit()
            conn.close()

            app = ui.create_app(api_url="http://127.0.0.1:8765", db_path=db_path)
            route = app.router.match("/api/dialogs")
            original_status_payload = ui._status_payload
            ui._status_payload = lambda _app: {
                "source_watchlist": {
                    "mode": "configured_sources",
                    "count": 3,
                    "sources": [
                        {"id": "-1002564889965", "name": "☝️🤓 τhe nerds 🧠🥼", "type": "group"},
                        {"id": "-1002316424674", "name": "Cosmonauts 🚀 | macrocosmos", "type": "group"},
                        {"id": "-404", "name": "Configured but absent locally", "type": "group"},
                    ],
                }
            }
            try:
                status, _content_type, body, _headers = route.handler({"scope": "watched", "limit": "10"})
            finally:
                ui._status_payload = original_status_payload

        payload = json.loads(body)
        self.assertEqual(status, 200)
        self.assertEqual(payload["count"], 2)
        self.assertEqual([dialog["id"] for dialog in payload["dialogs"]], ["-1002316424674", "-1002564889965"])
        self.assertEqual(payload["dialogs"][1]["message_count"], 1)


class TgSyncUiSearchRegressionTests(unittest.TestCase):
    def _db_with_messages(self, tmp):
        db_path = f"{tmp}/monitor.db"
        conn = sqlite3.connect(db_path)
        conn.execute(
            """
            CREATE TABLE messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                account_id TEXT DEFAULT 'default',
                dialog_id TEXT,
                dialog_name TEXT,
                dialog_type TEXT,
                msg_id INTEGER,
                sender_id INTEGER,
                sender_name TEXT,
                text TEXT,
                date TEXT,
                reply_to_id INTEGER,
                UNIQUE(account_id, dialog_id, msg_id)
            )
            """
        )
        conn.executemany(
            """
            INSERT INTO messages
                (account_id, dialog_id, dialog_name, dialog_type, msg_id, sender_id, sender_name, text, date, reply_to_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                ("default", "-1001", "Nerds", "group", 100, 1, "Alice", "Bittensor search result", "2026-05-09T10:00:00+00:00", None),
                ("ops", "-1001", "Nerds", "group", 99, 2, "Bob", "Older desearch context", "2026-05-09T09:00:00+00:00", None),
            ],
        )
        conn.commit()
        conn.close()
        return db_path

    def test_search_api_returns_real_sqlite_results_with_actionable_payload(self):
        with tempfile.TemporaryDirectory() as tmp:
            app = ui.create_app(api_url="http://127.0.0.1:8765", db_path=self._db_with_messages(tmp))
            route = app.router.match("/api/search")

            status, _content_type, body, _headers = route.handler({"q": "bittensor", "limit": "10", "account": "default"})

        payload = json.loads(body)
        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["count"], 1)
        self.assertEqual(payload["messages"][0]["msg_id"], 100)
        self.assertEqual(payload["empty_reason"], None)

    def test_search_api_reports_blank_query_bad_limit_and_missing_db_instead_of_silent_blank(self):
        with tempfile.TemporaryDirectory() as tmp:
            app = ui.create_app(api_url="http://127.0.0.1:8765", db_path=f"{tmp}/missing.db")
            route = app.router.match("/api/search")

            status, _content_type, body, _headers = route.handler({"q": ""})
            self.assertEqual(status, 400)
            payload = json.loads(body)
            self.assertFalse(payload["ok"])
            self.assertEqual(payload["error_code"], "missing_query")
            self.assertIn("Enter", payload["hint"])

            status, _content_type, body, _headers = route.handler({"q": "bittensor", "limit": "0"})
            self.assertEqual(status, 400)
            self.assertEqual(json.loads(body)["error_code"], "bad_request")

            status, _content_type, body, _headers = route.handler({"q": "bittensor"})
            self.assertEqual(status, 503)
            self.assertEqual(json.loads(body)["error_code"], "db_unavailable")

    def test_search_api_reports_bad_sqlite_schema_instead_of_500_blank(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = f"{tmp}/bad.db"
            conn = sqlite3.connect(db_path)
            conn.execute("CREATE TABLE unrelated (id INTEGER PRIMARY KEY)")
            conn.commit()
            conn.close()

            app = ui.create_app(api_url="http://127.0.0.1:8765", db_path=db_path)
            route = app.router.match("/api/search")

            status, _content_type, body, _headers = route.handler({"q": "bittensor"})

        payload = json.loads(body)
        self.assertEqual(status, 503)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error_code"], "db_unavailable")
        self.assertIn("DB", payload["hint"])

    def test_recent_api_supports_bounded_older_message_cursors(self):
        with tempfile.TemporaryDirectory() as tmp:
            app = ui.create_app(api_url="http://127.0.0.1:8765", db_path=self._db_with_messages(tmp))
            route = app.router.match("/api/recent")

            status, _content_type, body, _headers = route.handler({"dialog": "-1001", "before_id": "100", "limit": "5", "account": "default"})

        payload = json.loads(body)
        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["older_cursor"]["before_id"], 100)
        self.assertEqual(payload["messages"], [])

    def test_index_html_contains_search_states_older_history_account_and_internal_bind_copy(self):
        html = ui.render_index_html(api_url="http://127.0.0.1:8765", db_path="monitor.db")

        self.assertIn('id="searchStatus"', html)
        self.assertIn("renderSearchResults", html)
        self.assertIn("Load older local messages", html)
        self.assertIn("Historical sync", html)
        self.assertIn('id="accountSelector"', html)
        self.assertIn("Single-account mode", html)
        self.assertIn("--allow-internal-bind", html)
        self.assertNotIn("/api/send", html)

    def test_parser_requires_explicit_internal_bind_opt_in_for_tailscale_hosts(self):
        self.assertEqual(ui.main(["--host", "0.0.0.0", "--port", "0"]), 2)
        parser = ui.build_parser()
        args = parser.parse_args(["--host", "0.0.0.0", "--allow-internal-bind"])
        self.assertTrue(args.allow_internal_bind)


if __name__ == "__main__":
    unittest.main()
