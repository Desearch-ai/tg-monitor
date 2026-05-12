import sqlite3
import tempfile
import unittest
from pathlib import Path

from tg_sync.store import ReadOnlyStore


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


class TgSyncStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self.db_path = Path(self.tmpdir.name) / "monitor.db"
        conn = sqlite3.connect(self.db_path)
        conn.execute(SCHEMA)
        conn.executemany(
            """
            INSERT INTO messages
                (dialog_id, dialog_name, dialog_type, msg_id, sender_id, sender_name, text, date, reply_to_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                ("-1001", "Nerds", "group", 10, 1, "Alice", "Root about Bittensor", "2026-05-09T10:00:00+00:00", None),
                ("-1001", "Nerds", "group", 11, 2, "Bob", "Parent reply", "2026-05-09T10:01:00+00:00", 10),
                ("-1001", "Nerds", "group", 12, 3, "Cara", "Child says desearch", "2026-05-09T10:02:00+00:00", 11),
                ("-1001", "Nerds", "group", 13, 4, "Dan", "Nearby context", "2026-05-09T10:03:00+00:00", None),
                ("-2002", "Updates", "channel", 20, 5, "Eve", "Bittensor channel", "2026-05-09T11:00:00+00:00", None),
            ],
        )
        conn.commit()
        conn.close()

    def test_list_dialogs_returns_counts_and_latest_date(self):
        dialogs = ReadOnlyStore(self.db_path).list_dialogs(limit=10)

        self.assertEqual(dialogs[0]["id"], "-2002")
        self.assertEqual(dialogs[1]["id"], "-1001")
        self.assertEqual(dialogs[1]["message_count"], 4)
        self.assertEqual(dialogs[1]["latest_date"], "2026-05-09T10:03:00+00:00")

    def test_search_messages_filters_without_mutating_db(self):
        store = ReadOnlyStore(self.db_path)
        results = store.search_messages("bittensor", dialog_type="group", limit=5)

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["msg_id"], 10)
        with self.assertRaises(sqlite3.OperationalError):
            with store.connect() as conn:
                conn.execute("INSERT INTO messages (dialog_id) VALUES ('x')")

    def test_thread_reconstructs_anchor_parents_replies_and_context(self):
        thread = ReadOnlyStore(self.db_path).get_thread("-1001", 11, context=1, max_depth=5)

        self.assertEqual(thread["anchor"]["msg_id"], 11)
        self.assertEqual([m["msg_id"] for m in thread["parents"]], [10])
        self.assertEqual([m["msg_id"] for m in thread["replies"]], [12])
        self.assertIn(10, [m["msg_id"] for m in thread["context"]])
        self.assertIn(12, [m["msg_id"] for m in thread["context"]])

class TgSyncStoreAccountScopeTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self.db_path = Path(self.tmpdir.name) / "monitor.db"
        conn = sqlite3.connect(self.db_path)
        conn.execute("""
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
        """)
        conn.executemany(
            """
            INSERT INTO messages
                (account_id, dialog_id, dialog_name, dialog_type, msg_id, sender_id, sender_name, text, date, reply_to_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                ("default", "-1001", "Nerds", "group", 10, 1, "Alice", "default bittensor", "2026-05-09T10:00:00+00:00", None),
                ("ops", "-1001", "Nerds", "group", 10, 2, "Bob", "ops bittensor", "2026-05-09T10:01:00+00:00", None),
            ],
        )
        conn.commit()
        conn.close()

    def test_account_scoped_search_does_not_mix_same_dialog_message_ids(self):
        default_results = ReadOnlyStore(self.db_path, account_id="default").search_messages("bittensor", limit=10)
        ops_results = ReadOnlyStore(self.db_path, account_id="ops").search_messages("bittensor", limit=10)

        self.assertEqual([m["sender"] for m in default_results], ["Alice"])
        self.assertEqual([m["sender"] for m in ops_results], ["Bob"])
        self.assertEqual(ops_results[0]["account_id"], "ops")

    def test_account_scoped_recent_and_thread_reads_do_not_mix_accounts(self):
        ops_store = ReadOnlyStore(self.db_path, account_id="ops")

        recent = ops_store.recent_messages(minutes=None, dialog_id="-1001", limit=10)
        thread = ops_store.get_thread("-1001", 10, context=2)

        self.assertEqual([m["sender"] for m in recent], ["Bob"])
        self.assertEqual(thread["account_id"], "ops")
        self.assertEqual(thread["anchor"]["sender"], "Bob")

if __name__ == "__main__":
    unittest.main()
