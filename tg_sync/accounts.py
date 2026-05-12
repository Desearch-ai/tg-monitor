"""Account registry for bounded Telegram sync operations.

The registry stores only local runtime metadata: account id/label, session path,
DB path, and where credentials are expected to come from. Telegram API secrets,
phone auth codes, and session files stay outside source/control JSON.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping

from .config import resolve_db_path

DEFAULT_ACCOUNT_ID = "default"
DEFAULT_CREDENTIALS_SOURCE = "env:TG_API_ID,TG_API_HASH,TG_PHONE"
CONFIG_ENV = "TG_SYNC_ACCOUNTS_CONFIG"
ACTIVE_ENV = "TG_SYNC_ACCOUNT"
SESSION_ENV = "TG_SESSION_PATH"


@dataclass(frozen=True)
class Account:
    id: str
    label: str
    credentials_source: str
    session_path: str
    db_path: Path
    active: bool = False

    def to_json(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "label": self.label,
            "credentials_source": self.credentials_source,
            "session_path": self.session_path,
            "db_path": str(self.db_path),
            "active": self.active,
        }


class AccountRegistry:
    def __init__(self, path: str | Path | None = None, environ: Mapping[str, str] | None = None):
        self.environ = environ or os.environ
        self.path = Path(path or self.environ.get(CONFIG_ENV) or _default_config_path()).expanduser()

    def list_accounts(self) -> list[Account]:
        data = self._load()
        accounts = data.get("accounts") or {}
        active_id = str(data.get("active_account") or DEFAULT_ACCOUNT_ID)
        if not accounts:
            return [self._default_account(active=True)]
        result = []
        for account_id, raw in sorted(accounts.items()):
            result.append(self._account_from_raw(account_id, raw, active_id == account_id))
        if DEFAULT_ACCOUNT_ID not in accounts:
            result.insert(0, self._default_account(active=active_id == DEFAULT_ACCOUNT_ID))
        return result

    def active_account(self, account_id: str | None = None) -> Account:
        wanted = account_id or self.environ.get(ACTIVE_ENV) or self._load().get("active_account") or DEFAULT_ACCOUNT_ID
        for account in self.list_accounts():
            if account.id == wanted:
                return Account(**{**asdict(account), "active": True})
        raise ValueError(f"unknown account: {wanted}")

    def add_account(
        self,
        account_id: str,
        *,
        label: str | None = None,
        session_path: str | None = None,
        db_path: str | Path | None = None,
        credentials_source: str | None = None,
    ) -> Account:
        account_id = _validate_account_id(account_id)
        data = self._load()
        accounts = dict(data.get("accounts") or {})
        accounts[account_id] = {
            "label": label or account_id,
            "session_path": session_path or _default_session_path(account_id, self.environ),
            "db_path": str(resolve_db_path(str(db_path) if db_path else _default_db_path(account_id, self.environ))),
        }
        if credentials_source:
            accounts[account_id]["credentials_source"] = credentials_source
        data["accounts"] = accounts
        data.setdefault("active_account", account_id if account_id != DEFAULT_ACCOUNT_ID else DEFAULT_ACCOUNT_ID)
        self._save(data)
        return self._account_from_raw(account_id, accounts[account_id], data.get("active_account") == account_id)

    def switch(self, account_id: str) -> Account:
        account = self.active_account(account_id)
        data = self._load()
        data["active_account"] = account.id
        self._save(data)
        return Account(**{**asdict(account), "active": True})

    def _account_from_raw(self, account_id: str, raw: Mapping[str, Any], active: bool) -> Account:
        return Account(
            id=str(account_id),
            label=str(raw.get("label") or account_id),
            credentials_source=str(raw.get("credentials_source") or DEFAULT_CREDENTIALS_SOURCE),
            session_path=str(raw.get("session_path") or _default_session_path(account_id, self.environ)),
            db_path=resolve_db_path(str(raw.get("db_path") or _default_db_path(account_id, self.environ))),
            active=active,
        )

    def _default_account(self, active: bool = False) -> Account:
        return Account(
            id=DEFAULT_ACCOUNT_ID,
            label="Default Telegram account",
            credentials_source=DEFAULT_CREDENTIALS_SOURCE,
            session_path=str(self.environ.get(SESSION_ENV) or "user_session"),
            db_path=resolve_db_path(None),
            active=active,
        )

    def _load(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"active_account": self.environ.get(ACTIVE_ENV) or DEFAULT_ACCOUNT_ID, "accounts": {}}
        with self.path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
        if not isinstance(payload, dict):
            raise ValueError(f"invalid account registry: {self.path}")
        payload.setdefault("accounts", {})
        return payload

    def _save(self, payload: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        safe_payload = {"active_account": payload.get("active_account") or DEFAULT_ACCOUNT_ID, "accounts": payload.get("accounts") or {}}
        self.path.write_text(json.dumps(safe_payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def resolve_runtime_account(environ: Mapping[str, str] | None = None, account_id: str | None = None) -> Account:
    env = environ or os.environ
    return AccountRegistry(env.get(CONFIG_ENV), environ=env).active_account(account_id or env.get(ACTIVE_ENV))


def _default_config_path() -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME", "~/.config")).expanduser() / "tg-monitor" / "accounts.json"


def _default_session_path(account_id: str, environ: Mapping[str, str]) -> str:
    if account_id == DEFAULT_ACCOUNT_ID:
        return str(environ.get(SESSION_ENV) or "user_session")
    return str(Path("sessions") / account_id)


def _default_db_path(account_id: str, environ: Mapping[str, str]) -> str:
    if account_id == DEFAULT_ACCOUNT_ID:
        return str(environ.get("TG_MONITOR_DB") or environ.get("DB_PATH") or "monitor.db")
    return str(Path("db") / f"{account_id}.db")


def _validate_account_id(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}", value or ""):
        raise ValueError("account id must be 1-64 chars: letters, numbers, dot, underscore, dash")
    return value
