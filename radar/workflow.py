"""The review session: what needs you, batch approval, outreach preparation, application kits.

The Radar is built around short review sessions.  ``session()`` gathers everything
that needs a decision, in the order to handle it; ``bulk()`` applies one decision to
many e-mails; ``prepare_outreach()`` keeps a stock of ready-to-approve drafts for
researchers; ``kit()`` prepares an offer that must be applied to on the employer's site.
"""

from __future__ import annotations

import json
import re
from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional

from . import db, documents, mailer, outreach, writer

# Flags that mean "you probably cannot take this offer" (as opposed to "check the dates").
HARD_FLAG = re.compile(r"Apprenticeship|V\.I\.E|Working-student|\brequired\b|Nationality|visa sponsorship|enrolment", re.I)
HARD_FLAG_SQL = " OR ".join(f"flags LIKE '%{word}%'" for word in
                            ("Apprenticeship", "V.I.E", "Working-student", " required", "Nationality", "visa sponsorship", "enrolment"))
FOLLOW_UP_NOTE = "Follow-up to application #"
OPEN_DRAFT = "a.sent_at IS NULL AND a.stage IN ('to_review', 'ready')"


def ineligible(flags: Any) -> bool:
    if isinstance(flags, str):
        try:
            flags = json.loads(flags)
        except json.JSONDecodeError:
            flags = [flags]
    return any(HARD_FLAG.search(str(flag)) for flag in flags or [])


def view_defaults() -> dict[str, Any]:
    priorities = db.priorities()
    return {"min_score": 72 if priorities.get("hide_low_grades", True) else int(priorities.get("min_score") or 0),
            "hide_ineligible": bool(priorities.get("hide_ineligible", True))}


def visible_offers_sql() -> tuple[str, tuple]:
    """WHERE clause for offers shown by default (grade A/B, no eligibility blocker)."""
    defaults = view_defaults()
    clause = "active = 1 AND status != 'dismissed' AND score >= ?"
    if defaults["hide_ineligible"]:
        clause += f" AND NOT (coalesce(flags, '') != '' AND ({HARD_FLAG_SQL}))"
    return clause, (defaults["min_score"],)


# ------------------------------------------------------------------ session
def _application_rows(where: str, params: tuple = (), order: str = "a.updated_at DESC", limit: int = 400) -> list[dict[str, Any]]:
    rows = db.rows(
        f"""SELECT a.id, a.kind, a.stage, a.language, a.recipient, a.subject, a.sent_at, a.send_at, a.follow_up_at, a.sent_from,
                   a.send_error, a.notes, a.replied_at, a.reply_from, a.reply_snippet, a.reply_kind, a.reply_tone, a.offer_id, a.contact_id,
                   o.title AS offer_title, o.org AS offer_org, o.url AS offer_url, o.score AS offer_score,
                   c.name AS contact_name, c.org AS contact_org, c.country AS contact_country, c.kind AS contact_kind,
                   c.relevance AS contact_relevance, c.evidence AS contact_evidence, c.url AS contact_url
            FROM applications a LEFT JOIN offers o ON o.id = a.offer_id LEFT JOIN contacts c ON c.id = a.contact_id
            WHERE {where} ORDER BY {order} LIMIT {int(limit)}""", params)
    for row in rows:
        evidence = []
        try:
            evidence = json.loads(row.pop("contact_evidence") or "[]")
        except json.JSONDecodeError:
            row.pop("contact_evidence", None)
        row["why"] = (evidence[0].get("text") if evidence else "") or ""
    # Attachment names for drafts, in one query.
    draft_ids = [row["id"] for row in rows if row["stage"] in ("to_review", "ready")]
    titles: dict[int, list[str]] = {}
    for start in range(0, len(draft_ids), 400):
        chunk = draft_ids[start:start + 400]
        for item in db.rows(f"""SELECT ad.application_id, d.title FROM application_documents ad JOIN documents d ON d.id = ad.document_id
                                WHERE ad.application_id IN ({",".join("?" for _ in chunk)}) ORDER BY ad.position, d.id""", chunk):
            titles.setdefault(item["application_id"], []).append(item["title"])
    for row in rows:
        row["attachments"] = titles.get(row["id"], [])
    return rows


def session() -> dict[str, Any]:
    """Everything that needs a decision, in the order to handle it."""
    follow_ups = _application_rows(f"{OPEN_DRAFT} AND a.recipient != '' AND a.notes LIKE ?", (f"%{FOLLOW_UP_NOTE}%",), "a.created_at")
    drafts = _application_rows(f"{OPEN_DRAFT} AND a.recipient != '' AND coalesce(a.notes, '') NOT LIKE ?", (f"%{FOLLOW_UP_NOTE}%",),
                               "(c.country IN ('FR', 'IT', 'BE')) DESC, c.relevance DESC, a.id")
    replies = _application_rows("a.stage = 'replied'", (), "a.replied_at DESC")
    bounced = _application_rows("a.stage = 'bounced'", (), "a.replied_at DESC")
    failed = _application_rows("a.send_error IS NOT NULL AND a.sent_at IS NULL", (), "a.updated_at DESC")
    scheduled = _application_rows("a.stage IN ('scheduled', 'sending')", (), "a.send_at")
    to_verify = []
    for contact in db.rows("SELECT * FROM contacts WHERE kind IN ('imported', 'similar', 'expert', 'alumni') AND email IS NOT NULL "
                           "AND status = 'new' AND notes IS NOT NULL"):
        bounced_note = "bounced on" in contact["notes"] and "Address verified by you" not in contact["notes"]
        flag = outreach.needs_checking(contact) or ("bounced" if bounced_note else None)
        if flag:
            to_verify.append({"id": contact["id"], "name": contact["name"], "org": contact["org"], "email": contact["email"],
                              "url": contact["url"], "flag": flag, "note": (contact.get("notes") or "").split("Your note:")[-1].strip()[:200]})
    clause, params = visible_offers_sql()
    kits = db.rows(
        f"""SELECT id, title, org, country, city, score, pay, deadline, url, first_seen FROM offers
            WHERE {clause} AND status IN ('new', 'shortlisted')
              AND NOT EXISTS (SELECT 1 FROM applications a WHERE a.offer_id = offers.id AND a.sent_at IS NOT NULL)
            ORDER BY (status = 'shortlisted') DESC, score DESC, first_seen DESC LIMIT 12""", params)
    for offer in kits:
        offer["pay"] = json.loads(offer["pay"]) if offer.get("pay") else {}
    ready_targets = db.one(
        """SELECT count(*) AS n FROM contacts c WHERE c.email IS NOT NULL AND c.status = 'new' AND c.kind IN ('manual', 'imported', 'similar', 'expert', 'alumni')
           AND NOT EXISTS (SELECT 1 FROM applications a WHERE a.contact_id = c.id)""")["n"]
    return {"replies": replies, "bounced": bounced, "failed": failed, "follow_ups": follow_ups, "drafts": drafts, "scheduled": scheduled,
            "to_verify": to_verify, "kits": kits, "ready_targets": ready_targets, "stats": stats(), "capacity": capacity(),
            "inbox": {"checked_at": db.get_setting("inbox_checked_at"), "error": db.get_setting("inbox_error") or "",
                      "enabled": bool(db.get_setting("inbox_enabled", True))}}


def stats() -> dict[str, Any]:
    row = db.one(
        """SELECT count(*) AS sent, sum(stage = 'replied' OR reply_kind = 'reply') AS replied, sum(reply_tone = 'positive') AS positive,
                  sum(stage = 'bounced') AS bounced, sum(stage IN ('sent', 'follow_up')) AS waiting, sum(stage = 'interview') AS interviews,
                  sum(stage = 'offer') AS offers
           FROM applications WHERE sent_at IS NOT NULL AND kind = 'spontaneous'""") or {}
    result = {key: int(value or 0) for key, value in row.items()}
    delivered = max(1, result["sent"] - result["bounced"])
    result["reply_rate"] = round(100 * result["replied"] / delivered) if result["sent"] else 0
    result["web_applications"] = db.one("SELECT count(*) AS n FROM applications WHERE sent_at IS NOT NULL AND kind = 'offer'")["n"]
    return result


def capacity() -> dict[str, Any]:
    cap = int(mailer.settings()["daily_cap"])
    queued = db.one("SELECT count(*) AS n FROM applications WHERE stage IN ('scheduled', 'sending')")["n"]
    return {"daily_cap": cap, "sent_today": mailer.sent_today(), "queued": queued}


# --------------------------------------------------------------------- bulk
def bulk(ids: list[int], action: str) -> dict[str, Any]:
    """Apply one decision to many e-mails.  ``approve`` queues them, spaced out, within the daily cap."""
    done, failed = [], []
    if action == "approve":
        delay = float(mailer.settings()["delay_hours"])
        already = db.one("SELECT count(*) AS n FROM applications WHERE stage IN ('scheduled', 'sending')")["n"]
        for index, application_id in enumerate(ids):
            try:
                mailer.approve(int(application_id), delay + (already + index) * 3 / 60)  # 3 minutes apart
                done.append(application_id)
            except mailer.MailError as exc:
                failed.append({"id": application_id, "error": str(exc)})
        cap = int(mailer.settings()["daily_cap"])
        queued = already + len(done)
        room_today = max(0, cap - mailer.sent_today())
        days = 1 + max(0, queued - room_today + cap - 1) // cap if queued > room_today else 1
        return {"done": len(done), "failed": failed, "queued": queued, "daily_cap": cap, "days": days}
    for application_id in ids:
        row = db.one("SELECT id, stage, sent_at, contact_id FROM applications WHERE id = ?", (application_id,))
        if row is None:
            failed.append({"id": application_id, "error": "not found"})
            continue
        if action == "delete" and not row["sent_at"]:
            db.execute("DELETE FROM applications WHERE id = ?", (application_id,))
        elif action == "unapprove" and row["stage"] == "scheduled":
            db.execute("UPDATE applications SET stage = 'to_review', send_at = NULL, approved_at = NULL WHERE id = ?", (application_id,))
        elif action in ("declined", "closed", "interview", "sent"):
            stage = {"declined": "rejected"}.get(action, action)
            db.execute("UPDATE applications SET stage = ?, updated_at = ? WHERE id = ?", (stage, db.now(), application_id))
            if row["contact_id"] and action == "declined":
                db.execute("UPDATE contacts SET status = 'declined' WHERE id = ?", (row["contact_id"],))
        else:
            failed.append({"id": application_id, "error": f"cannot {action} this e-mail"})
            continue
        done.append(application_id)
    return {"done": len(done), "failed": failed}


# ------------------------------------------------------------------ outreach
def send_sooner(spacing_minutes: float = 1.0) -> dict[str, Any]:
    """Drop the waiting time of every scheduled e-mail: they go out from now on, in the same order,
    a minute apart, still within the daily limit."""
    rows = db.rows("SELECT id FROM applications WHERE stage = 'scheduled' ORDER BY send_at, id")
    start = datetime.now(timezone.utc).replace(microsecond=0)
    with db.write() as connection:
        for index, row in enumerate(rows):
            when = (start + timedelta(minutes=index * spacing_minutes)).isoformat().replace("+00:00", "Z")
            connection.execute("UPDATE applications SET send_at = ? WHERE id = ?", (when, row["id"]))
    return {"rescheduled": len(rows), **queue_status()}


def queue_status() -> dict[str, Any]:
    cap = int(mailer.settings()["daily_cap"])
    sent = mailer.sent_today()
    queued = db.one("SELECT count(*) AS n FROM applications WHERE stage IN ('scheduled', 'sending')")["n"]
    room = max(0, cap - sent)
    return {"daily_cap": cap, "sent_today": sent, "queued": queued, "room_today": room,
            "days": 0 if not queued else (1 if queued <= room else 1 + -(-(queued - room) // cap))}


def send_to(contact_ids: list[int]) -> dict[str, Any]:
    """“Write & send”: write the e-mail for every selected researcher and approve them all at once.

    Your click is the approval.  Everything else is unchanged: the waiting time before sending, the
    daily cap, the spacing between e-mails, and the refusal of addresses your notes flag.
    """
    if not mailer.public_settings()["ready"]:
        raise mailer.MailError("Connect a mailbox first (Settings › E-mail sending).")
    ids = sorted({int(item) for item in contact_ids})
    if not ids:
        return {"written": 0, "approved": 0, "failed": [], "skipped": []}
    prepared = prepare_outreach(contact_ids=ids)
    marks = ",".join("?" for _ in ids)
    drafts = [row["id"] for row in db.rows(
        f"SELECT a.id FROM applications a WHERE a.contact_id IN ({marks}) AND {OPEN_DRAFT} AND coalesce(a.recipient, '') != '' "
        "AND a.kind = 'spontaneous' ORDER BY a.id", ids)]
    result = bulk(drafts, "approve") if drafts else {"done": 0, "failed": [], "days": 0, "daily_cap": mailer.settings()["daily_cap"]}
    skipped = [item for item in prepared["skipped"] if item["reason"] != "already has an e-mail"]
    return {"written": prepared["created"], "approved": result["done"], "failed": result["failed"], "skipped": skipped,
            "days": result.get("days", 0), "daily_cap": result.get("daily_cap"), "delay_hours": mailer.settings()["delay_hours"]}


def prepare_outreach(limit: int = 25, contact_ids: Optional[list[int]] = None, max_pending: int = 80) -> dict[str, Any]:
    """Write review-ready e-mails for researchers you have not contacted, best matches first."""
    from . import pipeline

    pending = db.one(f"SELECT count(*) AS n FROM applications a WHERE {OPEN_DRAFT} AND a.kind = 'spontaneous'")["n"]
    if contact_ids:
        marks = ",".join("?" for _ in contact_ids)
        targets = db.rows(f"SELECT * FROM contacts WHERE id IN ({marks})", contact_ids)
    else:
        room = max(0, min(limit, max_pending - pending))
        targets = db.rows(
            """SELECT c.* FROM contacts c WHERE c.email IS NOT NULL AND c.status = 'new' AND c.kind IN ('manual', 'imported', 'similar', 'expert', 'alumni')
               AND NOT EXISTS (SELECT 1 FROM applications a WHERE a.contact_id = c.id)
               ORDER BY (c.kind = 'manual') DESC, (c.kind = 'imported') DESC, (c.country IN ('FR', 'IT', 'BE')) DESC, (c.country IN ('DE', 'LU')) DESC, c.relevance DESC
               LIMIT ?""", (room * 2,)) if room else []
        limit = room
    created, skipped = 0, []
    for contact in targets:
        if created >= limit and not contact_ids:
            break
        if not contact.get("email"):
            skipped.append({"name": contact["name"], "reason": "no e-mail address"})
            continue
        if contact.get("status") in ("contacted", "replied", "declined"):
            skipped.append({"name": contact["name"], "reason": f"already {contact['status']}"})
            continue
        flag = outreach.needs_checking(contact)
        if flag:
            skipped.append({"name": contact["name"], "reason": f"address to verify ({flag})"})
            continue
        if db.one("SELECT 1 AS x FROM applications WHERE contact_id = ? AND (sent_at IS NOT NULL OR stage IN ('to_review', 'ready', 'scheduled'))",
                  (contact["id"],)):
            skipped.append({"name": contact["name"], "reason": "already has an e-mail"})
            continue
        draft = writer.compose(contact_id=contact["id"], use_llm=False)
        pipeline.save_application(offer_id=None, contact_id=contact["id"], draft=draft,
                                  selected_document_ids=documents.get_defaults_for_language(draft["language"]))
        created += 1
    return {"created": created, "skipped": skipped, "pending": pending + created}


# ----------------------------------------------------------------------- kit
def kit(offer_id: int, language: Optional[str] = None) -> dict[str, Any]:
    """Everything needed to apply on the employer's site: tailored letter, the right files, the link."""
    from . import pipeline

    offer = db.one("SELECT * FROM offers WHERE id = ?", (offer_id,))
    if offer is None:
        raise KeyError(offer_id)
    existing = db.one("SELECT * FROM applications WHERE offer_id = ? AND kind = 'offer' ORDER BY (sent_at IS NULL) DESC, id DESC LIMIT 1", (offer_id,))
    if existing is None or (language and language.upper() != existing["language"] and not existing["sent_at"]):
        draft = writer.compose(offer_id=offer_id, language=language, use_llm=False)
        if existing is None:
            application_id = pipeline.save_application(offer_id=offer_id, contact_id=None, draft=draft, stage="kit",
                                                       selected_document_ids=documents.get_defaults_for_language(draft["language"]))
        else:
            application_id = existing["id"]
            db.execute("UPDATE applications SET language = ?, subject = ?, body = ?, letter = ?, updated_at = ? WHERE id = ?",
                       (draft["language"], draft["subject"], draft["body"], draft["letter"], db.now(), application_id))
            documents.set_application_documents(application_id, documents.get_defaults_for_language(draft["language"]))
        existing = db.one("SELECT * FROM applications WHERE id = ?", (application_id,))
        db.execute("UPDATE offers SET status = 'shortlisted' WHERE id = ? AND status = 'new'", (offer_id,))
    return {"application_id": existing["id"], "language": existing["language"], "letter": existing["letter"] or existing["body"],
            "applied_at": existing["sent_at"], "stage": existing["stage"],
            "documents": documents.get_application_documents(existing["id"]),
            "offer": {"id": offer["id"], "title": offer["title"], "org": offer["org"], "url": offer["url"], "deadline": offer["deadline"]}}


def mark_applied(offer_id: int) -> dict[str, Any]:
    data = kit(offer_id)
    today = date.today()
    db.execute("UPDATE applications SET stage = 'sent', sent_at = ?, sent_from = 'employer website', follow_up_at = ?, updated_at = ? WHERE id = ?",
               (today.isoformat(), (today + timedelta(days=14)).isoformat(), db.now(), data["application_id"]))
    db.execute("UPDATE offers SET status = 'applied' WHERE id = ?", (offer_id,))
    return {"ok": True, "applied_at": today.isoformat()}


def convert_formless_drafts() -> int:
    """Offer drafts without any e-mail address can never be sent: turn them into application kits."""
    return db.execute("UPDATE applications SET stage = 'kit' WHERE kind = 'offer' AND sent_at IS NULL AND coalesce(recipient, '') = '' "
                      "AND stage IN ('to_review', 'ready')")


def session_due(now: Optional[datetime] = None) -> bool:
    """True on the mornings of your review days (used for the summary e-mail)."""
    now = now or datetime.now()
    days = db.priorities().get("session_days") or ["Mon", "Thu"]
    return now.strftime("%a") in days and now.hour >= 7


__all__ = ["bulk", "capacity", "convert_formless_drafts", "ineligible", "kit", "mark_applied", "prepare_outreach", "session",
           "session_due", "stats", "view_defaults", "visible_offers_sql"]
