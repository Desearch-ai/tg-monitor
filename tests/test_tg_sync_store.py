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


if __name__ == "__main__":
    unittest.main()
