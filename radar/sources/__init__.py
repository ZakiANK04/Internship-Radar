"""Source registry: every live connector the Scout agent can run."""

from __future__ import annotations

import json
import re
from typing import Any, Optional

from .. import config, db
from . import academic, ats, boards, research
from .base import Context, RawOffer, Source

# Source types that need no code: a Recruitee board, a WordPress lab site, a feed, or a listing page.
GENERIC_TYPES = ("recruitee", "wordpress", "rss", "page")

__all__ = [
    "Context",
    "RawOffer",
    "Source",
    "load_sources",
    "seed_sources_registry",
    "list_sources_status",
    "set_source_enabled",
    "add_custom_source",
    "delete_custom_source",
]


def _builtin_named_sources() -> dict[str, Source]:
    all_builtin = [*research.all_sources(), *boards.all_sources(), *academic.all_sources()]
    return {s.name: s for s in all_builtin}


def _source_from_dict(entry: dict[str, Any]) -> Optional[Source]:
    source_id = entry.get("source_id") or ""
    kind = entry.get("kind") or "company"
    ats_type = entry.get("ats")
    name = entry.get("label") or source_id
    group = entry.get("group_name") or ""
    cfg = entry.get("config") or {}
    if isinstance(cfg, str):
        try:
            cfg = json.loads(cfg)
        except Exception:
            cfg = {}

    builtins = _builtin_named_sources()
    if source_id in builtins:
        builtin = builtins[source_id]
        return builtin

    token = cfg.get("token") or source_id.split(":", 1)[-1]
    if ats_type in GENERIC_TYPES:
        return academic.from_config(ats_type, token, name, cfg)
    if ats_type == "greenhouse":
        return ats.greenhouse(token, name)
    elif ats_type == "lever":
        return ats.lever(token, name)
    elif ats_type == "lever_eu":
        return ats.lever(token, name, host="api.eu.lever.co")
    elif ats_type == "ashby":
        return ats.ashby(token, name)
    elif ats_type == "smartrecruiters":
        return ats.smartrecruiters(token, name)
    elif ats_type == "workday":
        return ats.workday(
            token,
            cfg.get("pod", "wd3"),
            cfg.get("site", "careers"),
            name,
            mena=bool(cfg.get("mena")),
        )
    return None


def seed_sources_registry() -> int:
    """Ensure all built-in sources and boards.json entries are registered in SQLite.
    Preserves existing user enable/disable toggles and custom sources.
    """
    existing = {row["source_id"]: row for row in db.rows("SELECT * FROM sources_registry")}
    stamp = db.now()
    to_insert = []

    # 1. Register built-in research and board sources
    for src in [*research.all_sources(), *boards.all_sources(), *academic.all_sources()]:
        if src.name not in existing:
            to_insert.append((
                src.name,
                src.kind,
                src.group or "Built-in",
                src.label,
                "builtin",
                json.dumps({}),
                1,
                0,
                stamp,
                stamp,
            ))

    # 2. Register boards from boards.json
    try:
        registry = json.loads((config.SEED_DIR / "boards.json").read_text(encoding="utf-8"))
    except Exception:
        registry = []

    group_map = {
        "greenhouse": "Greenhouse",
        "lever": "Lever",
        "lever_eu": "Lever (EU)",
        "ashby": "Ashby",
        "smartrecruiters": "SmartRecruiters",
        "workday": "Workday",
    }

    for b in registry:
        kind = b.get("ats")
        token = b.get("token")
        name = b.get("name")
        mena = bool(b.get("mena"))
        if kind == "workday" and mena:
            source_id = f"workday:{token}:{b.get('site', 'careers')}"
        else:
            source_id = f"{kind}:{token}"

        if source_id not in existing:
            group = group_map.get(kind, "Company ATS")
            if mena:
                group = f"{group} (MENA)"
            cfg = {k: v for k, v in b.items() if k not in ("name", "ats")}
            to_insert.append((
                source_id,
                "company",
                group,
                name,
                kind,
                json.dumps(cfg),
                1,
                0,
                stamp,
                stamp,
            ))

    if to_insert:
        with db.write() as conn:
            conn.executemany(
                """INSERT OR IGNORE INTO sources_registry
                   (source_id, kind, group_name, label, ats, config, enabled, is_custom, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                to_insert,
            )

    return len(to_insert)


def load_sources(include_disabled: bool = False) -> list[Source]:
    """Load sources from SQLite registry (seeded if empty)."""
    seed_sources_registry()
    query = "SELECT * FROM sources_registry"
    if not include_disabled:
        query += " WHERE enabled = 1"
    query += " ORDER BY group_name, label"

    rows = db.rows(query)
    sources: list[Source] = []
    for r in rows:
        src = _source_from_dict(r)
        if src:
            sources.append(src)
    return sources


def list_sources_status() -> list[dict[str, Any]]:
    """List all sources in registry combined with their health and run statistics."""
    seed_sources_registry()
    sources = db.rows("SELECT * FROM sources_registry ORDER BY group_name, label")
    health_rows = {h["source"]: h for h in db.rows("SELECT * FROM source_health")}

    results = []
    for s in sources:
        sid = s["source_id"]
        h = health_rows.get(sid, {})
        # Determine status
        if not s.get("enabled"):
            status = "disabled"
        elif h.get("last_error"):
            status = "error"
        elif h.get("last_ok") and h.get("fetched", 0) == 0:
            status = "empty"
        elif h.get("last_ok"):
            status = "ok"
        else:
            status = "pending"

        results.append({
            "source_id": sid,
            "label": s["label"],
            "group_name": s["group_name"],
            "ats": s.get("ats") or "builtin",
            "kind": s["kind"],
            "enabled": bool(s["enabled"]),
            "is_custom": bool(s.get("is_custom")),
            "notes": s.get("notes") or "",
            "status": status,
            "last_run": h.get("last_run"),
            "last_ok": h.get("last_ok"),
            "last_error": h.get("last_error"),
            "error_category": h.get("error_category"),
            "http_code": h.get("http_code"),
            "fetched": h.get("fetched", 0),
            "kept": h.get("kept", 0),
            "seconds": h.get("seconds", 0),
        })
    return results


def set_source_enabled(source_id: str, enabled: bool) -> bool:
    seed_sources_registry()
    with db.write() as conn:
        rc = conn.execute(
            "UPDATE sources_registry SET enabled = ?, updated_at = ? WHERE source_id = ?",
            (1 if enabled else 0, db.now(), source_id),
        ).rowcount
    return rc > 0


def add_custom_source(
    ats_type: str,
    token: str,
    name: str,
    group_name: str = "Custom employers",
    config_extra: Optional[dict] = None,
) -> dict[str, Any]:
    """Add a new custom ATS board source."""
    seed_sources_registry()
    ats_type = ats_type.lower().strip()
    token = token.strip()
    name = name.strip() or token
    group_name = group_name.strip() or "Custom employers"

    valid_ats = {"greenhouse", "lever", "lever_eu", "ashby", "smartrecruiters", "workday", *GENERIC_TYPES}
    if ats_type not in valid_ats:
        raise ValueError(f"Invalid source type '{ats_type}'. Allowed: {', '.join(sorted(valid_ats))}")
    if ats_type in ("wordpress", "rss", "page") and not re.match(r"^https?://[^\s/]+", token):
        raise ValueError("For a lab website, feed or listing page, give the full address starting with https://")

    cfg = {"token": token, **(config_extra or {})}
    if ats_type in GENERIC_TYPES:
        built = academic.from_config(ats_type, token, name, cfg)
        source_id = built.name
        if group_name == "Custom employers":
            group_name = "Schools & labs"
    elif ats_type == "workday" and cfg.get("mena"):
        source_id = f"workday:{token}:{cfg.get('site', 'careers')}"
    else:
        source_id = f"{ats_type}:{token}"

    stamp = db.now()
    with db.write() as conn:
        conn.execute(
            """INSERT INTO sources_registry
               (source_id, kind, group_name, label, ats, config, enabled, is_custom, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, 1, 1, ?, ?)
               ON CONFLICT(source_id) DO UPDATE SET
                 label = excluded.label, group_name = excluded.group_name,
                 config = excluded.config, enabled = 1, updated_at = excluded.updated_at""",
            (source_id, "research" if ats_type in GENERIC_TYPES else "company", group_name, name, ats_type, json.dumps(cfg), stamp, stamp),
        )

    return db.one("SELECT * FROM sources_registry WHERE source_id = ?", (source_id,))


def delete_custom_source(source_id: str) -> bool:
    with db.write() as conn:
        rc = conn.execute(
            "DELETE FROM sources_registry WHERE source_id = ? AND is_custom = 1", (source_id,)
        ).rowcount
    return rc > 0
