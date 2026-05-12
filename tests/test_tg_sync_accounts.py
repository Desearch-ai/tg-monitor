import json
import os
import tempfile
import unittest
from pathlib import Path

from tg_sync.accounts import AccountRegistry, resolve_runtime_account


class TgSyncAccountRegistryTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self.config_path = Path(self.tmpdir.name) / "accounts.json"

    def test_default_account_is_backward_compatible_without_config(self):
        registry = AccountRegistry(self.config_path)

        active = registry.active_account()

        self.assertEqual(active.id, "default")
        self.assertEqual(active.session_path, "user_session")
        self.assertEqual(str(active.db_path), "monitor.db")
        self.assertEqual(active.credentials_source, "env:TG_API_ID,TG_API_HASH,TG_PHONE")

    def test_add_and_switch_persists_active_account_without_secrets(self):
        registry = AccountRegistry(self.config_path)
        registry.add_account("ops", label="Ops Sync", session_path="sessions/ops", db_path="db/ops.db")
        registry.switch("ops")

        reloaded = AccountRegistry(self.config_path)
        payload = json.loads(self.config_path.read_text())

        self.assertEqual(reloaded.active_account().id, "ops")
        self.assertEqual(reloaded.active_account().label, "Ops Sync")
        self.assertNotIn("api_hash", json.dumps(payload).lower())
        self.assertNotIn("phone", json.dumps(payload).lower())

    def test_runtime_account_prefers_explicit_env_active_account(self):
        registry = AccountRegistry(self.config_path)
        registry.add_account("ops", session_path="sessions/ops", db_path="db/ops.db")
        account = resolve_runtime_account({"TG_SYNC_ACCOUNTS_CONFIG": str(self.config_path), "TG_SYNC_ACCOUNT": "ops"})

        self.assertEqual(account.id, "ops")
        self.assertEqual(account.session_path, "sessions/ops")
