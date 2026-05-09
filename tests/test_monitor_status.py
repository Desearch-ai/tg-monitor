import importlib
import os
import sqlite3
import sys
import tempfile
import types
import unittest
from pathlib import Path


def install_dependency_stubs():
    dotenv = types.ModuleType("dotenv")
    dotenv.load_dotenv = lambda: None
    sys.modules["dotenv"] = dotenv

    telethon = types.ModuleType("telethon")

    class TelegramClient:
        def __init__(self, *args, **kwargs):
            pass

    telethon.TelegramClient = TelegramClient
    sys.modules["telethon"] = telethon

    tl = types.ModuleType("telethon.tl")
    tl_types = types.ModuleType("telethon.tl.types")

    class Channel:
        broadcast = False

    class Chat:
        pass

    class User:
        pass

    class PeerChannel:
        def __init__(self, channel_id):
            self.channel_id = channel_id

    class PeerChat:
        def __init__(self, chat_id):
            self.chat_id = chat_id

    class PeerUser:
        def __init__(self, user_id):
            self.user_id = user_id

    class MessageReplyHeader:
        reply_to_msg_id = None

    tl_types.Channel = Channel
    tl_types.Chat = Chat
    tl_types.User = User
    tl_types.PeerChannel = PeerChannel
    tl_types.PeerChat = PeerChat
    tl_types.PeerUser = PeerUser
    tl_types.MessageReplyHeader = MessageReplyHeader
    sys.modules["telethon.tl"] = tl
    sys.modules["telethon.tl.types"] = tl_types

    aiohttp = types.ModuleType("aiohttp")
    web = types.ModuleType("aiohttp.web")

    class RouteTableDef:
        def get(self, _path):
            return lambda func: func

        def post(self, _path):
            return lambda func: func

    web.RouteTableDef = RouteTableDef
    web.Application = object
    web.AppRunner = object
    web.TCPSite = object
    web.json_response = lambda payload, status=200: {"payload": payload, "status": status}
    aiohttp.web = web
    sys.modules["aiohttp"] = aiohttp
    sys.modules["aiohttp.web"] = web


class MonitorStatusPayloadTests(unittest.TestCase):
    def setUp(self):
        install_dependency_stubs()
        os.environ.setdefault("TG_API_ID", "1")
        os.environ.setdefault("TG_API_HASH", "test_hash")
        os.environ.setdefault("TG_PHONE", "+10000000000")
        os.environ.pop("TG_WATCH_GROUPS", None)
        os.environ.pop("TG_WATCH_SOURCES", None)
        os.environ.pop("TG_MONITOR_CONFIG", None)
        sys.modules.pop("monitor", None)
        self.monitor = importlib.import_module("monitor")
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self.monitor.DB_PATH = str(Path(self.tmpdir.name) / "monitor.db")
        self.monitor.init_db()

    def test_status_payload_is_safe_before_telegram_ready(self):
        self.monitor._telegram_ready = False
        self.monitor._telegram_status = "starting"

        payload = self.monitor.build_status_payload()

        self.assertEqual(payload["status"], "starting")
        self.assertFalse(payload["telegram_ready"])
        self.assertEqual(payload["total_messages"], 0)
        self.assertEqual(payload["by_type"], {})
        self.assertEqual(payload["source_watchlist"], {"mode": "all_non_dm_dialogs", "count": 0, "sources": []})

    def test_status_payload_keeps_counts_and_additive_ready_field(self):
        conn = sqlite3.connect(self.monitor.DB_PATH)
        conn.executemany(
            """
            INSERT INTO messages
                (dialog_id, dialog_name, dialog_type, msg_id, sender_id, sender_name, text, date, reply_to_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                ("-1", "Group", "group", 1, 10, "A", "hello", "2026-05-09T00:00:00+00:00", None),
                ("-2", "Channel", "channel", 2, 20, "B", "world", "2026-05-09T00:00:01+00:00", None),
                ("-2", "Channel", "channel", 3, 20, "B", "again", "2026-05-09T00:00:02+00:00", None),
            ],
        )
        conn.commit()
        conn.close()
        self.monitor._telegram_ready = True
        self.monitor._telegram_status = "running"

        payload = self.monitor.build_status_payload()

        self.assertEqual(payload["status"], "running")
        self.assertTrue(payload["telegram_ready"])
        self.assertEqual(payload["total_messages"], 3)
        self.assertEqual(payload["by_type"], {"channel": 2, "group": 1})

    def test_status_payload_surfaces_configured_source_watchlist(self):
        os.environ["TG_WATCH_SOURCES"] = "-1001,-1002"

        payload = self.monitor.build_status_payload()

        self.assertEqual(payload["source_watchlist"]["mode"], "configured_sources")
        self.assertEqual(payload["source_watchlist"]["count"], 2)
        self.assertEqual([s["id"] for s in payload["source_watchlist"]["sources"]], ["-1001", "-1002"])


if __name__ == "__main__":
    unittest.main()
