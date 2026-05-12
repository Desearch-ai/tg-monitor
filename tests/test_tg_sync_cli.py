import io
import json
import os
import sqlite3
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from tg_sync import cli


SCHEMA = """
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


class TgSyncCliTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self.db_path = Path(self.tmpdir.name) / "monitor.db"
        conn = sqlite3.connect(self.db_path)
        conn.execute(SCHEMA)
        conn.execute(
            """
            INSERT INTO messages
                (dialog_id, dialog_name, dialog_type, msg_id, sender_id, sender_name, text, date, reply_to_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ("-1001", "Nerds", "group", 10, 1, "Alice", "Bittensor search", "2026-05-09T10:00:00+00:00", None),
        )
        conn.commit()
        conn.close()

    def run_cli(self, argv):
        stdout = io.StringIO()
        stderr = io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = cli.main(argv)
        return code, stdout.getvalue(), stderr.getvalue()

    def test_search_json_outputs_machine_readable_payload(self):
        code, out, err = self.run_cli(["--db", str(self.db_path), "search", "bittensor", "--json"])

        self.assertEqual(code, 0, err)
        payload = json.loads(out)
        self.assertEqual(payload["count"], 1)
        self.assertEqual(payload["messages"][0]["msg_id"], 10)

    def test_no_send_reply_or_delete_commands_are_registered(self):
        parser = cli.build_parser()
        subparsers = next(action for action in parser._actions if action.__class__.__name__ == "_SubParsersAction")

        self.assertNotIn("send", subparsers.choices)
        self.assertNotIn("reply", subparsers.choices)
        self.assertNotIn("delete", subparsers.choices)

    def test_api_unavailable_status_has_safe_error_and_exit_code(self):
        code, out, err = self.run_cli(["--api-url", "http://127.0.0.1:9", "status", "--json"])

        self.assertEqual(code, cli.EXIT_API_UNAVAILABLE)
        payload = json.loads(out)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["api_url"], "http://127.0.0.1:9")
        self.assertIn("error", payload)

class TgSyncCliAccountAndBackfillTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self.config_path = Path(self.tmpdir.name) / "accounts.json"
        self.db_path = Path(self.tmpdir.name) / "default.db"
        conn = sqlite3.connect(self.db_path)
        conn.execute(SCHEMA)
        conn.commit()
        conn.close()
        self.old_env = os.environ.copy()
        os.environ["TG_SYNC_ACCOUNTS_CONFIG"] = str(self.config_path)

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self.old_env)

    def run_cli(self, argv):
        stdout = io.StringIO()
        stderr = io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = cli.main(argv)
        return code, stdout.getvalue(), stderr.getvalue()

    def test_accounts_add_list_switch_status_json(self):
        code, out, err = self.run_cli(["accounts", "add", "ops", "--session", "sessions/ops", "--db", str(self.db_path), "--json"])
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out)["account"]["id"], "ops")

        code, out, err = self.run_cli(["accounts", "switch", "ops", "--json"])
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out)["active_account"], "ops")

        code, out, err = self.run_cli(["accounts", "status", "--json"])
        self.assertEqual(code, 0, err)
        payload = json.loads(out)
        self.assertEqual(payload["active_account"], "ops")
        self.assertEqual(payload["account"]["session_path"], "sessions/ops")

        code, out, err = self.run_cli(["accounts", "list", "--json"])
        self.assertEqual(code, 0, err)
        self.assertIn("ops", [a["id"] for a in json.loads(out)["accounts"]])

    def test_sync_backfill_dry_run_is_bounded_and_scriptable(self):
        self.run_cli(["accounts", "add", "ops", "--session", "sessions/ops", "--db", str(self.db_path)])

        code, out, err = self.run_cli(["sync", "backfill", "--account", "ops", "--dialog", "-1001", "--limit", "25", "--before-id", "900", "--dry-run", "--json"])

        self.assertEqual(code, 0, err)
        payload = json.loads(out)
        self.assertTrue(payload["dry_run"])
        self.assertEqual(payload["account"]["id"], "ops")
        self.assertEqual(payload["request"]["limit"], 25)
        self.assertEqual(payload["request"]["before_id"], 900)
        self.assertEqual(payload["telegram_writes"], "forbidden")

    def test_sync_backfill_rejects_unbounded_or_malformed_requests(self):
        code, out, err = self.run_cli(["sync", "backfill", "--dialog", "-1001", "--limit", "5001", "--dry-run", "--json"])

        self.assertEqual(code, cli.EXIT_BAD_ARGS)
        payload = json.loads(out)
        self.assertFalse(payload["ok"])
        self.assertIn("limit", payload["error"])

if __name__ == "__main__":
    unittest.main()
