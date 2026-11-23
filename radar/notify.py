"""E-mails to yourself, only when something needs you.

* straight away: a researcher replied, an address bounced, or a shortlisted offer
  closes within a week;
* on your review days (Monday and Thursday by default): a short summary of what is
  waiting, sent once in the morning.

Each event is announced once.  Turn everything off in Settings → Automation.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from typing import Callable

from . import db, mailer, workflow

APP_URL = "http://127.0.0.1:8765"
TONE = {"positive": "looks positive", "unfunded": "would host you, but without funding", "redirect": "asks you to apply another way",
        "declined": "looks like a no", "neutral": "to read"}


def _recipient() -> str:
    return (db.get_setting("notify_to") or db.profile().get("email") or "").strip()


def enabled() -> bool:
    return bool(db.get_setting("notify_enabled", True)) and bool(_recipient()) and mailer.public_settings()["ready"]


def events(log: Callable[..., None] = print) -> int:
    """Announce new replies, bounces and close deadlines.  Returns the number of e-mails sent."""
    if not enabled():
        return 0
    replies = db.rows(
        """SELECT a.id, a.reply_tone, a.reply_snippet, a.reply_from, a.replied_at, c.name, c.org FROM applications a
           LEFT JOIN contacts c ON c.id = a.contact_id WHERE a.stage = 'replied' AND a.notified_at IS NULL ORDER BY a.replied_at""")
    bounces = db.rows(
        """SELECT a.id, a.recipient, c.name FROM applications a LEFT JOIN contacts c ON c.id = a.contact_id
           WHERE a.stage = 'bounced' AND a.notified_at IS NULL""")
    announced = set(db.get_setting("notified_deadlines") or [])
    soon = (date.today() + timedelta(days=7)).isoformat()
    deadlines = [row for row in db.rows(
        "SELECT id, title, org, deadline, url FROM offers WHERE active = 1 AND status = 'shortlisted' AND deadline IS NOT NULL "
        "AND deadline >= ? AND deadline <= ? ORDER BY deadline", (date.today().isoformat(), soon)) if row["id"] not in announced]
    if not (replies or bounces or deadlines):
        return 0
    lines, subject_bits = [], []
    if replies:
        subject_bits.append(f"{len(replies)} repl{'y' if len(replies) == 1 else 'ies'}")
        lines.append("REPLIES")
        for row in replies:
            lines.append(f"• {row['name'] or row['reply_from']} — {row['org'] or ''} ({TONE.get(row['reply_tone'], 'to read')})")
            excerpt = " ".join((row["reply_snippet"] or "").split())[:260]
            if excerpt:
                lines.append(f"    “{excerpt}…”")
        lines.append("")
    if bounces:
        subject_bits.append(f"{len(bounces)} bounced address{'es' if len(bounces) > 1 else ''}")
        lines.append("ADDRESSES THAT BOUNCED (find the right one before writing again)")
        lines += [f"• {row['name'] or ''} — {row['recipient']}" for row in bounces] + [""]
    if deadlines:
        subject_bits.append(f"{len(deadlines)} deadline{'s' if len(deadlines) > 1 else ''} this week")
        lines.append("SHORTLISTED OFFERS CLOSING WITHIN 7 DAYS")
        lines += [f"• {row['deadline']} — {row['title']} ({row['org']})\n    {row['url']}" for row in deadlines] + [""]
    lines.append(f"Open the Radar: {APP_URL}  (answer replies from your mailbox; the Radar only tracks them)")
    try:
        mailer.send_plain(_recipient(), "Internship Radar — " + ", ".join(subject_bits), "\n".join(lines))
    except mailer.MailError as exc:
        log(f"Could not send the notification: {exc}", "warn")
        return 0
    stamp = db.now()
    for row in [*replies, *bounces]:
        db.execute("UPDATE applications SET notified_at = ? WHERE id = ?", (stamp, row["id"]))
    db.set_setting("notified_deadlines", sorted(announced | {row["id"] for row in deadlines}))
    log(f"Notification sent to {_recipient()}: {', '.join(subject_bits)}.")
    return 1


def summary(log: Callable[..., None] = print, force: bool = False) -> bool:
    """The review-day summary: sent once on the morning of each session day."""
    today = date.today().isoformat()
    if not force and (not enabled() or not workflow.session_due() or db.get_setting("summary_sent_on") == today):
        return False
    data = workflow.session()
    stats = data["stats"]
    top = data["kits"][:5]
    lines = [
        f"Review session — {datetime.now().strftime('%A %d %B')}", "",
        f"Replies to read:            {len(data['replies'])}",
        f"Follow-ups ready to approve: {len(data['follow_ups'])}",
        f"New e-mails ready to approve: {len(data['drafts'])}",
        f"Addresses to verify:         {len(data['to_verify'])}",
        f"Researchers ready for a draft: {data['ready_targets']}", "",
        f"So far: {stats['sent']} e-mails sent, {stats['replied']} replies ({stats['reply_rate']} %), "
        f"{stats['positive']} positive, {stats['waiting']} still waiting.", "",
    ]
    if top:
        lines.append("Best open offers (apply on the employer's site with the application kit):")
        for offer in top:
            pay = offer["pay"].get("monthly_eur") if offer["pay"].get("source") == "stated" else None
            lines.append(f"• {offer['score']} — {offer['title']} · {offer['org']}" + (f" · {pay} €/month" if pay else ""))
        lines.append("")
    lines.append(f"Open the Radar: {APP_URL}")
    try:
        mailer.send_plain(_recipient(), f"Internship Radar — session summary ({len(data['replies'])} replies, "
                                        f"{len(data['drafts']) + len(data['follow_ups'])} e-mails to approve)", "\n".join(lines))
    except mailer.MailError as exc:
        log(f"Could not send the summary: {exc}", "warn")
        return False
    db.set_setting("summary_sent_on", today)
    log(f"Session summary sent to {_recipient()}.")
    return True


def preview() -> str:
    """Text of the next summary, without sending (for Settings)."""
    data = workflow.session()
    return json.dumps({"replies": len(data["replies"]), "to_approve": len(data["drafts"]) + len(data["follow_ups"]),
                       "to_verify": len(data["to_verify"])})
