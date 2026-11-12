"""SQLite persistence for Internship Radar (standard library only)."""

from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Iterable, Iterator, Optional

from . import config

_WRITE_LOCK = threading.RLock()

OFFER_STATUSES = ("new", "shortlisted", "applied", "interview", "offer", "rejected", "dismissed")
APPLICATION_STAGES = ("to_review", "ready", "scheduled", "sent", "follow_up", "interview", "offer", "rejected", "closed")

SCHEMA = """
CREATE TABLE IF NOT EXISTS offers (
    id INTEGER PRIMARY KEY,
    uid TEXT NOT NULL UNIQUE,
    source TEXT NOT NULL,
    source_kind TEXT NOT NULL,
    title TEXT NOT NULL,
    org TEXT,
    country TEXT,
    city TEXT,
    tier TEXT,
    location_raw TEXT,
    url TEXT,
    description TEXT,
    lang TEXT,
    contract TEXT,
    posted_at TEXT,
    deadline TEXT,
    remote INTEGER DEFAULT 0,
    score INTEGER DEFAULT 0,
    score_parts TEXT,
    flags TEXT,
    tags TEXT,
    llm TEXT,
    status TEXT NOT NULL DEFAULT 'new',
    notes TEXT,
    active INTEGER NOT NULL DEFAULT 1,
    missed_runs INTEGER NOT NULL DEFAULT 0,
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_offers_score ON offers(score DESC);
CREATE INDEX IF NOT EXISTS idx_offers_source ON offers(source);
CREATE INDEX IF NOT EXISTS idx_offers_status ON offers(status);

CREATE TABLE IF NOT EXISTS contacts (
    id INTEGER PRIMARY KEY,
    key TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    role TEXT,
    org TEXT,
    country TEXT,
    email TEXT,
    email_source TEXT,
    url TEXT,
    orcid TEXT,
    openalex_id TEXT,
    kind TEXT NOT NULL,
    topics TEXT,
    evidence TEXT,
    relevance INTEGER DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'new',
    notes TEXT,
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_contacts_kind ON contacts(kind);

CREATE TABLE IF NOT EXISTS offer_contacts (
    offer_id INTEGER NOT NULL REFERENCES offers(id) ON DELETE CASCADE,
    contact_id INTEGER NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
    PRIMARY KEY (offer_id, contact_id)
);

CREATE TABLE IF NOT EXISTS applications (
    id INTEGER PRIMARY KEY,
    offer_id INTEGER REFERENCES offers(id) ON DELETE SET NULL,
    contact_id INTEGER REFERENCES contacts(id) ON DELETE SET NULL,
    kind TEXT NOT NULL DEFAULT 'offer',
    stage TEXT NOT NULL DEFAULT 'to_review',
    language TEXT NOT NULL DEFAULT 'EN',
    recipient TEXT,
    subject TEXT,
    body TEXT,
    generator TEXT,
    sent_at TEXT,
    follow_up_at TEXT,
    notes TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_applications_stage ON applications(stage);

CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT NOT NULL DEFAULT 'running',
    trigger TEXT,
    summary TEXT
);

CREATE TABLE IF NOT EXISTS run_events (
    id INTEGER PRIMARY KEY,
    run_id INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    ts TEXT NOT NULL,
    agent TEXT NOT NULL,
    level TEXT NOT NULL DEFAULT 'info',
    message TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS source_health (
    source TEXT PRIMARY KEY,
    kind TEXT,
    last_run TEXT,
    last_ok TEXT,
    last_error TEXT,
    fetched INTEGER DEFAULT 0,
    kept INTEGER DEFAULT 0,
    seconds REAL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS link_checks (
    url TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    http_code INTEGER,
    checked_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


def now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def connect() -> sqlite3.Connection:
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(str(config.DB_PATH), timeout=30)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA journal_mode = WAL")
    return connection


@contextmanager
def write() -> Iterator[sqlite3.Connection]:
    """Serialise writers; SQLite handles readers concurrently through WAL."""
    with _WRITE_LOCK:
        connection = connect()
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()


def rows(query: str, params: Iterable[Any] = ()) -> list[dict[str, Any]]:
    connection = connect()
    try:
        return [dict(row) for row in connection.execute(query, tuple(params)).fetchall()]
    finally:
        connection.close()


def one(query: str, params: Iterable[Any] = ()) -> Optional[dict[str, Any]]:
    result = rows(query, params)
    return result[0] if result else None


def execute(query: str, params: Iterable[Any] = ()) -> int:
    with write() as connection:
        cursor = connection.execute(query, tuple(params))
        return int(cursor.lastrowid or cursor.rowcount or 0)


def initialize() -> None:
    with write() as connection:
        connection.executescript(SCHEMA)
        # Columns added after the first release (e-mail sending).
        existing = {row["name"] for row in connection.execute("PRAGMA table_info(applications)").fetchall()}
        for column in ("letter", "approved_at", "send_at", "sent_from", "send_error"):
            if column not in existing:
                connection.execute(f"ALTER TABLE applications ADD COLUMN {column} TEXT")


# ----------------------------------------------------------------- settings
def get_setting(key: str, default: Any = None) -> Any:
    row = one("SELECT value FROM settings WHERE key = ?", (key,))
    if row is None:
        return default
    try:
        return json.loads(row["value"])
    except json.JSONDecodeError:
        return default


def set_setting(key: str, value: Any) -> None:
    execute(
        "INSERT INTO settings(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, json.dumps(value, ensure_ascii=False)),
    )


def profile() -> dict:
    """Defaults from the CV, overlaid with whatever was edited in the app (only edits are stored)."""
    stored = get_setting("profile") or {}
    return {**config.DEFAULT_PROFILE, **stored}


def priorities() -> dict:
    stored = get_setting("priorities") or {}
    return {**config.DEFAULT_PRIORITIES, **stored}


# ------------------------------------------------------------------- offers
OFFER_FIELDS = (
    "source", "source_kind", "title", "org", "country", "city", "tier", "location_raw", "url",
    "description", "lang", "contract", "posted_at", "deadline", "remote", "score", "score_parts",
    "flags", "tags",
)


def upsert_offer(connection: sqlite3.Connection, uid: str, values: dict[str, Any]) -> tuple[int, bool]:
    """Insert or refresh an offer.  User state (status, notes) is never overwritten."""
    stamp = now()
    payload = {key: values.get(key) for key in OFFER_FIELDS}
    for key in ("score_parts", "flags", "tags"):
        if payload[key] is not None and not isinstance(payload[key], str):
            payload[key] = json.dumps(payload[key], ensure_ascii=False)
    existing = connection.execute("SELECT id FROM offers WHERE uid = ?", (uid,)).fetchone()
    if existing:
        assignments = ", ".join(f"{key} = ?" for key in OFFER_FIELDS)
        connection.execute(
            f"UPDATE offers SET {assignments}, last_seen = ?, active = 1, missed_runs = 0 WHERE id = ?",
            (*payload.values(), stamp, existing["id"]),
        )
        return int(existing["id"]), False
    columns = ", ".join(OFFER_FIELDS)
    marks = ", ".join("?" for _ in OFFER_FIELDS)
    cursor = connection.execute(
        f"INSERT INTO offers (uid, {columns}, first_seen, last_seen) VALUES (?, {marks}, ?, ?)",
        (uid, *payload.values(), stamp, stamp),
    )
    return int(cursor.lastrowid), True


# ----------------------------------------------------------------- contacts
CONTACT_FIELDS = (
    "name", "role", "org", "country", "email", "email_source", "url", "orcid", "openalex_id", "kind",
    "topics", "evidence", "relevance",
)


def contact_key(name: str, org: Optional[str]) -> str:
    import re
    import unicodedata

    def norm(value: Optional[str]) -> str:
        text = unicodedata.normalize("NFKD", value or "").encode("ascii", "ignore").decode()
        return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()

    return f"{norm(name)}|{norm(org)[:40]}"


def upsert_contact(connection: sqlite3.Connection, values: dict[str, Any]) -> tuple[int, bool]:
    """Insert a contact or enrich an existing one without discarding known data."""
    stamp = now()
    key = values.get("key") or contact_key(values["name"], values.get("org"))
    payload = {field: values.get(field) for field in CONTACT_FIELDS}
    for field in ("topics", "evidence"):
        if payload[field] is not None and not isinstance(payload[field], str):
            payload[field] = json.dumps(payload[field], ensure_ascii=False)
    existing = connection.execute("SELECT * FROM contacts WHERE key = ?", (key,)).fetchone()
    if existing is None and payload.get("openalex_id"):
        existing = connection.execute(
            "SELECT * FROM contacts WHERE openalex_id = ?", (payload["openalex_id"],)
        ).fetchone()
    if existing:
        merged = {}
        for field in CONTACT_FIELDS:
            new_value, old_value = payload[field], existing[field]
            if field == "relevance":
                merged[field] = max(int(new_value or 0), int(old_value or 0))
            elif field == "kind":
                merged[field] = old_value if old_value == "alumni" else (new_value or old_value)
            elif field in ("email", "email_source"):
                merged[field] = old_value or new_value
            else:
                merged[field] = new_value if new_value not in (None, "", "[]") else old_value
        assignments = ", ".join(f"{field} = ?" for field in CONTACT_FIELDS)
        connection.execute(
            f"UPDATE contacts SET {assignments}, last_seen = ? WHERE id = ?",
            (*merged.values(), stamp, existing["id"]),
        )
        return int(existing["id"]), False
    columns = ", ".join(CONTACT_FIELDS)
    marks = ", ".join("?" for _ in CONTACT_FIELDS)
    cursor = connection.execute(
        f"INSERT INTO contacts (key, {columns}, first_seen, last_seen) VALUES (?, {marks}, ?, ?)",
        (key, *payload.values(), stamp, stamp),
    )
    return int(cursor.lastrowid), True


def link_offer_contact(connection: sqlite3.Connection, offer_id: int, contact_id: int) -> None:
    connection.execute(
        "INSERT OR IGNORE INTO offer_contacts(offer_id, contact_id) VALUES(?, ?)", (offer_id, contact_id)
    )


def decode(row: Optional[dict[str, Any]], *fields: str) -> Optional[dict[str, Any]]:
    """Decode JSON text columns in place for API responses."""
    if row is None:
        return None
    for field in fields:
        value = row.get(field)
        if isinstance(value, str) and value:
            try:
                row[field] = json.loads(value)
            except json.JSONDecodeError:
                pass
        elif value is None:
            row[field] = [] if field in ("flags", "tags", "topics", "evidence") else {}
    return row
