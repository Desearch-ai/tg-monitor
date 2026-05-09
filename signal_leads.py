"""Keyword/context signal matching and Growth-App-safe lead-candidate export.

This module is intentionally read-only: it reads the local tg-monitor SQLite DB
and emits a review artifact. It does not call Telegram, Growth App, or /send.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

SCHEMA_VERSION = "lead-candidates/v1"
DEFAULT_CONFIG_PATH = "monitor_rules.json"

DEFAULT_CONFIG: dict[str, Any] = {
    "source_watchlist": [],
    "keyword_rules": [
        {
            "id": "desearch-intent",
            "keywords": [
                "desearch",
                "decentralized search",
                "search api",
                "web search api",
                "social search",
            ],
            "reason": "Message references search/data infrastructure where Desearch may be relevant.",
            "confidence": 0.72,
            "suggested_product_service": "Desearch API",
        },
        {
            "id": "bittensor-sn22",
            "keywords": ["bittensor", "subnet", "sn22", "tao", "miners", "validators"],
            "reason": "Message is in the Bittensor/Subnet context that Desearch SN22 serves.",
            "confidence": 0.68,
            "suggested_product_service": "Subnet 22 / Desearch intelligence",
        },
        {
            "id": "builder-buying-intent",
            "keywords": ["need api", "looking for", "dataset", "real-time data", "low latency", "evaluation tooling"],
            "reason": "Message may indicate builder/operator need for data, APIs, or tooling.",
            "confidence": 0.58,
            "suggested_product_service": "Desearch developer tooling",
        },
    ],
    "lead_export": {
        "default_minutes": 24 * 60,
        "default_limit": 500,
        "include_dms": False,
        "context_chars": 220,
        "approval_status": "needs_review",
    },
}


def parse_dt(value: str | datetime) -> datetime:
    if isinstance(value, datetime):
        dt = value
    else:
        if value.endswith("Z"):
            value = value[:-1] + "+00:00"
        dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def _split_env_list(value: str | None) -> list[str]:
    if not value:
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


def load_config(path: str | Path | None = None, environ: dict[str, str] | None = None) -> dict[str, Any]:
    """Load monitor rules config, with env source-watchlist compatibility.

    Runtime may provide either TG_MONITOR_CONFIG or the legacy TG_WATCH_GROUPS
    / newer TG_WATCH_SOURCES comma-separated env var. If neither is present,
    the source watchlist stays empty, meaning existing all-dialog ingestion and
    export behavior remain compatible.
    """
    env = environ if environ is not None else os.environ
    config_path = Path(path or env.get("TG_MONITOR_CONFIG") or DEFAULT_CONFIG_PATH)
    config = deepcopy(DEFAULT_CONFIG)

    if config_path.exists():
        raw = json.loads(config_path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError(f"{config_path} must contain a JSON object")
        if "sources" in raw and "source_watchlist" not in raw:
            raw["source_watchlist"] = raw.pop("sources")
        config = _deep_merge(config, raw)

    env_sources = _split_env_list(env.get("TG_WATCH_SOURCES") or env.get("TG_WATCH_GROUPS"))
    if env_sources:
        config["source_watchlist"] = [{"id": item, "name": item, "enabled": True} for item in env_sources]

    config["source_watchlist"] = _normalize_sources(config.get("source_watchlist", []))
    config["keyword_rules"] = _normalize_rules(config.get("keyword_rules", []))
    config["lead_export"] = _deep_merge(DEFAULT_CONFIG["lead_export"], config.get("lead_export", {}))
    return config


def _normalize_sources(sources: Iterable[dict[str, Any]] | None) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for source in sources or []:
        if not source or source.get("enabled", True) is False:
            continue
        sid = str(source.get("id") or source.get("dialog_id") or source.get("username") or "").strip()
        name = str(source.get("name") or source.get("title") or sid).strip()
        aliases = [str(a).strip().lower() for a in source.get("aliases", []) if str(a).strip()]
        normalized.append(
            {
                "id": sid,
                "name": name,
                "type": source.get("type"),
                "aliases": aliases,
            }
        )
    return normalized


def _normalize_rules(rules: Iterable[dict[str, Any]] | None) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for idx, rule in enumerate(rules or []):
        if not rule or rule.get("enabled", True) is False:
            continue
        keywords = [str(k).strip() for k in rule.get("keywords", []) if str(k).strip()]
        if not keywords:
            continue
        confidence = float(rule.get("confidence", 0.5))
        normalized.append(
            {
                "id": str(rule.get("id") or f"rule-{idx + 1}"),
                "keywords": keywords,
                "reason": str(rule.get("reason") or "Matched configured Telegram signal keyword."),
                "confidence": max(0.0, min(1.0, confidence)),
                "suggested_product_service": str(rule.get("suggested_product_service") or rule.get("product") or "Review manually"),
            }
        )
    return normalized


def watchlist_summary(config: dict[str, Any]) -> dict[str, Any]:
    sources = config.get("source_watchlist") or []
    return {
        "mode": "configured_sources" if sources else "all_non_dm_dialogs",
        "count": len(sources),
        "sources": [
            {k: v for k, v in {"id": s.get("id"), "name": s.get("name"), "type": s.get("type")}.items() if v}
            for s in sources
        ],
    }


def source_matches_watchlist(row: dict[str, Any], sources: list[dict[str, Any]]) -> bool:
    if not sources:
        return True
    row_id = str(row.get("dialog_id") or "")
    row_name = str(row.get("dialog_name") or row.get("dialog") or "").lower()
    row_type = row.get("dialog_type") or row.get("type")
    for source in sources:
        sid = str(source.get("id") or "")
        sname = str(source.get("name") or "").lower()
        stype = source.get("type")
        aliases = source.get("aliases") or []
        id_match = bool(sid and sid == row_id)
        name_match = bool(sname and sname == row_name)
        alias_match = row_name in aliases or any(alias and alias in row_name for alias in aliases)
        type_match = not stype or stype == row_type
        if type_match and (id_match or name_match or alias_match):
            return True
    return False


def fetch_rows(
    db_path: str | Path,
    *,
    minutes: int,
    include_dms: bool = False,
    limit: int = 500,
    now: str | datetime | None = None,
) -> list[dict[str, Any]]:
    current = parse_dt(now or datetime.now(timezone.utc))
    since = current - timedelta(minutes=minutes)
    query = """
        SELECT dialog_id, dialog_name, dialog_type, msg_id, sender_id, sender_name, text, date, reply_to_id
        FROM messages
        WHERE date >= ?
    """
    params: list[Any] = [since.isoformat()]
    if not include_dms:
        query += " AND dialog_type != 'dm'"
    query += " ORDER BY date ASC LIMIT ?"
    params.append(limit)

    conn = sqlite3.connect(db_path)
    rows = conn.execute(query, params).fetchall()
    conn.close()
    return [
        {
            "dialog_id": str(r[0]),
            "dialog_name": r[1],
            "dialog_type": r[2],
            "msg_id": int(r[3]),
            "sender_id": r[4],
            "sender_name": r[5],
            "text": r[6] or "",
            "date": r[7],
            "reply_to_id": int(r[8]) if r[8] is not None else None,
        }
        for r in rows
    ]


def _keyword_pattern(keyword: str) -> re.Pattern[str]:
    escaped = re.escape(keyword).replace(r"\ ", r"\s+")
    return re.compile(rf"(?<![\w]){escaped}(?![\w])", re.IGNORECASE)


def matched_keywords(text: str, keywords: Iterable[str]) -> list[str]:
    found: list[str] = []
    for keyword in keywords:
        if _keyword_pattern(keyword).search(text or ""):
            found.append(keyword.lower())
    return found


def context_excerpt(text: str, keywords: Iterable[str], chars: int) -> str:
    clean = " ".join((text or "").split())
    if not clean:
        return ""
    first_span: tuple[int, int] | None = None
    for keyword in keywords:
        match = _keyword_pattern(keyword).search(clean)
        if match and (first_span is None or match.start() < first_span[0]):
            first_span = match.span()
    if not first_span or len(clean) <= chars:
        return clean[:chars]
    half = max(20, chars // 2)
    start = max(0, first_span[0] - half)
    end = min(len(clean), first_span[1] + half)
    excerpt = clean[start:end].strip()
    if start > 0:
        excerpt = "…" + excerpt
    if end < len(clean):
        excerpt += "…"
    return excerpt


def _telegram_ref(dialog_id: str, msg_id: int) -> str | None:
    if dialog_id.startswith("-100"):
        return f"https://t.me/c/{dialog_id[4:]}/{msg_id}"
    return None


def _surrounding_context(rows: list[dict[str, Any]], index: int) -> list[dict[str, Any]]:
    current = rows[index]
    context: list[dict[str, Any]] = []
    neighbors: list[tuple[str, dict[str, Any]]] = []

    for other_index in range(index - 1, -1, -1):
        if rows[other_index].get("dialog_id") == current.get("dialog_id"):
            neighbors.append(("previous", rows[other_index]))
            break
    for other_index in range(index + 1, len(rows)):
        if rows[other_index].get("dialog_id") == current.get("dialog_id"):
            neighbors.append(("next", rows[other_index]))
            break

    for label, other in neighbors:
        context.append(
            {
                "position": label,
                "msg_id": other["msg_id"],
                "author_name": other.get("sender_name"),
                "timestamp": other.get("date"),
                "text_excerpt": context_excerpt(other.get("text") or "", [], 180),
            }
        )
    return context


def build_candidates(rows: list[dict[str, Any]], config: dict[str, Any]) -> list[dict[str, Any]]:
    sources = config.get("source_watchlist") or []
    rules = config.get("keyword_rules") or []
    export_config = config.get("lead_export") or {}
    chars = int(export_config.get("context_chars", 220))
    approval_status = str(export_config.get("approval_status", "needs_review"))
    filtered_rows = [row for row in rows if source_matches_watchlist(row, sources)]

    candidates: list[dict[str, Any]] = []
    seen: set[tuple[str, int, str]] = set()
    for index, row in enumerate(filtered_rows):
        text = row.get("text") or ""
        for rule in rules:
            matches = matched_keywords(text, rule["keywords"])
            if not matches:
                continue
            key = (row["dialog_id"], int(row["msg_id"]), rule["id"])
            if key in seen:
                continue
            seen.add(key)
            confidence = min(0.95, float(rule["confidence"]) + (0.05 * max(0, len(matches) - 1)))
            candidates.append(
                {
                    "candidate_id": f"tg:{row['dialog_id']}:{row['msg_id']}:{rule['id']}",
                    "source": {
                        "id": row["dialog_id"],
                        "name": row.get("dialog_name"),
                        "type": row.get("dialog_type"),
                    },
                    "message_reference": {
                        "dialog_id": row["dialog_id"],
                        "msg_id": int(row["msg_id"]),
                        "local_ref": f"{row['dialog_id']}:{row['msg_id']}",
                        "telegram_url": _telegram_ref(row["dialog_id"], int(row["msg_id"])),
                        "reply_to_id": row.get("reply_to_id"),
                        "timestamp": row.get("date"),
                    },
                    "author": {
                        "id": row.get("sender_id"),
                        "name": row.get("sender_name"),
                    },
                    "rule_id": rule["id"],
                    "matched_keywords": matches,
                    "context_excerpt": context_excerpt(text, matches, chars),
                    "surrounding_context": _surrounding_context(filtered_rows, index),
                    "reason": rule["reason"],
                    "confidence": round(confidence, 2),
                    "suggested_product_service": rule["suggested_product_service"],
                    "approval_status": approval_status,
                }
            )
    return candidates


def export_lead_candidates(
    db_path: str | Path,
    *,
    config: dict[str, Any] | None = None,
    config_path: str | Path | None = None,
    minutes: int | None = None,
    limit: int | None = None,
    now: str | datetime | None = None,
    include_dms: bool | None = None,
) -> dict[str, Any]:
    resolved_config = _deep_merge(load_config(config_path), config or {})
    resolved_config["source_watchlist"] = _normalize_sources(resolved_config.get("source_watchlist", []))
    resolved_config["keyword_rules"] = _normalize_rules(resolved_config.get("keyword_rules", []))
    export_config = resolved_config.get("lead_export") or {}
    resolved_minutes = int(minutes if minutes is not None else export_config.get("default_minutes", 24 * 60))
    resolved_limit = int(limit if limit is not None else export_config.get("default_limit", 500))
    resolved_include_dms = bool(include_dms if include_dms is not None else export_config.get("include_dms", False))
    current = parse_dt(now or datetime.now(timezone.utc))
    rows = fetch_rows(
        db_path,
        minutes=resolved_minutes,
        include_dms=resolved_include_dms,
        limit=resolved_limit,
        now=current,
    )
    candidates = build_candidates(rows, resolved_config)
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": current.isoformat(),
        "window": {
            "minutes": resolved_minutes,
            "since": (current - timedelta(minutes=resolved_minutes)).isoformat(),
        },
        "source_watchlist": watchlist_summary(resolved_config),
        "keyword_rules": [
            {
                "id": rule["id"],
                "keywords": rule["keywords"],
                "suggested_product_service": rule["suggested_product_service"],
                "confidence": rule["confidence"],
            }
            for rule in resolved_config.get("keyword_rules", [])
        ],
        "candidate_count": len(candidates),
        "candidates": candidates,
        "growth_app_import_notes": {
            "approval_required": True,
            "approval_status_field": "approval_status",
            "side_effects": "none_read_only_artifact",
            "next_step": "Review candidates in #tg-alerts or Growth App import tooling before any contact.",
        },
    }


def write_lead_candidate_artifact(
    db_path: str | Path,
    output_path: str | Path,
    **kwargs: Any,
) -> dict[str, Any]:
    payload = export_lead_candidates(db_path, **kwargs)
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return payload
