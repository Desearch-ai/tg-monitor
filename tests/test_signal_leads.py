import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

import signal_leads


class LeadCandidateExportTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self.db_path = Path(self.tmpdir.name) / "monitor.db"
        conn = sqlite3.connect(self.db_path)
        conn.execute(
            """
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
                reply_to_id INTEGER
            )
            """
        )
        rows = [
            ("-1001", "Builders", "group", 10, 100, "Alice", "Anyone using decentralized search APIs?", "2026-05-09T10:00:00+00:00", None),
            ("-1001", "Builders", "group", 11, 101, "Bob", "Desearch could fit if it has low latency web results", "2026-05-09T10:01:00+00:00", 10),
            ("-1002", "Subnet founders", "channel", 20, 200, "Carol", "SN22 miners need better evaluation tooling", "2026-05-09T10:02:00+00:00", None),
            ("-1003", "Ignored", "group", 30, 300, "Dan", "desearch mention outside watchlist", "2026-05-09T10:03:00+00:00", None),
        ]
        conn.executemany(
            """
            INSERT INTO messages
                (dialog_id, dialog_name, dialog_type, msg_id, sender_id, sender_name, text, date, reply_to_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
        conn.commit()
        conn.close()

    def test_configured_multi_source_keyword_context_export(self):
        config = {
            "source_watchlist": [
                {"id": "-1001", "name": "Builders", "type": "group"},
                {"id": "-1002", "name": "Subnet founders", "type": "channel"},
            ],
            "keyword_rules": [
                {
                    "id": "desearch-api-intent",
                    "keywords": ["desearch", "decentralized search", "search api"],
                    "reason": "Developer is discussing search infrastructure that Desearch can help with.",
                    "confidence": 0.82,
                    "suggested_product_service": "Desearch API",
                },
                {
                    "id": "sn22-tooling",
                    "keywords": ["sn22", "miners", "evaluation tooling"],
                    "reason": "Subnet operator discussion relevant to SN22 tooling.",
                    "confidence": 0.75,
                    "suggested_product_service": "Subnet 22 / validator tooling",
                },
            ],
            "lead_export": {"approval_status": "needs_review", "context_chars": 80},
        }

        payload = signal_leads.export_lead_candidates(
            self.db_path,
            config=config,
            minutes=24 * 60,
            now="2026-05-09T11:00:00+00:00",
        )

        self.assertEqual(payload["schema_version"], "lead-candidates/v1")
        self.assertEqual(payload["source_watchlist"]["mode"], "configured_sources")
        self.assertEqual(payload["source_watchlist"]["count"], 2)
        refs = {candidate["message_reference"]["local_ref"] for candidate in payload["candidates"]}
        self.assertEqual(refs, {"-1001:10", "-1001:11", "-1002:20"})

        candidate = next(c for c in payload["candidates"] if c["message_reference"]["local_ref"] == "-1001:11")
        self.assertEqual(candidate["source"], {"id": "-1001", "name": "Builders", "type": "group"})
        self.assertEqual(candidate["author"], {"id": 101, "name": "Bob"})
        self.assertIn("desearch", candidate["matched_keywords"])
        self.assertIn("low latency web results", candidate["context_excerpt"])
        self.assertEqual(candidate["approval_status"], "needs_review")
        self.assertEqual(candidate["rule_id"], "desearch-api-intent")
        self.assertEqual(candidate["suggested_product_service"], "Desearch API")
        self.assertGreaterEqual(candidate["confidence"], 0.82)
        self.assertTrue(candidate["reason"])
        self.assertTrue(candidate["surrounding_context"])

    def test_write_artifact_uses_growth_app_safe_schema_without_side_effects(self):
        output_path = Path(self.tmpdir.name) / "lead-candidates.json"
        config = {
            "source_watchlist": [{"id": "-1001", "name": "Builders"}],
            "keyword_rules": [{"id": "desearch", "keywords": ["desearch"], "suggested_product_service": "Desearch API"}],
        }

        payload = signal_leads.write_lead_candidate_artifact(
            self.db_path,
            output_path,
            config=config,
            minutes=24 * 60,
            now="2026-05-09T11:00:00+00:00",
        )

        written = json.loads(output_path.read_text())
        self.assertEqual(written, payload)
        self.assertIn("growth_app_import_notes", written)
        self.assertEqual(written["growth_app_import_notes"]["side_effects"], "none_read_only_artifact")
        self.assertEqual(len(written["candidates"]), 1)


if __name__ == "__main__":
    unittest.main()
