"""Local web app: a small JSON API over the tracker plus the single-page interface.

Standard library only (``http.server``); bound to 127.0.0.1 so nothing is
exposed outside this machine.
"""

from __future__ import annotations

import html
import json
import mimetypes
import re
import threading
import webbrowser
from datetime import date, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable, Optional
from urllib.parse import parse_qs, quote, urlparse

from . import __version__, config, contacts, db, documents, inbox, mailer, notify, pay, pipeline, scoring, sources, workflow, writer


class ApiError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


# ------------------------------------------------------------------- queries
OFFER_LIST_COLUMNS = (
    "id, source, source_kind, title, org, country, city, tier, url, lang, contract, posted_at, deadline, remote, "
    "score, flags, tags, pay, status, active, first_seen, last_seen, (llm IS NOT NULL) AS reviewed, "
    "(SELECT count(*) FROM offer_contacts oc WHERE oc.offer_id = offers.id) AS contact_count, "
    "(SELECT a.stage FROM applications a WHERE a.offer_id = offers.id ORDER BY a.id DESC LIMIT 1) AS application_stage"
)


def api_state(_: dict) -> dict:
    priorities = db.priorities()
    floor = int(priorities.get("min_score") or 0)
    visible, visible_params = workflow.visible_offers_sql()
    offers = db.one(
        "SELECT count(*) AS total, sum(score >= 85) AS grade_a, sum(score >= 72 AND score < 85) AS grade_b, "
        f"sum(first_seen >= ?) AS fresh FROM offers WHERE {visible}",
        ((date.today() - timedelta(days=2)).isoformat(), *visible_params)) or {}
    offers["hidden"] = (db.one("SELECT count(*) AS n FROM offers WHERE active = 1 AND status != 'dismissed'") or {}).get("n", 0) - int(offers.get("total") or 0)
    by_tier = db.rows("SELECT tier, count(*) AS n FROM offers WHERE active = 1 AND status != 'dismissed' AND score >= ? GROUP BY tier", (floor,))
    by_country = db.rows("SELECT country, count(*) AS n, max(score) AS best FROM offers WHERE active = 1 AND status != 'dismissed' "
                         "AND score >= ? GROUP BY country ORDER BY n DESC", (floor,))
    stages = {row["stage"]: row["n"] for row in db.rows("SELECT stage, count(*) AS n FROM applications GROUP BY stage")}
    contact_counts = {row["kind"]: row["n"] for row in db.rows("SELECT kind, count(*) AS n FROM contacts WHERE status != 'ignored' GROUP BY kind")}
    health = db.one("SELECT count(*) AS total, sum(last_error IS NULL) AS ok, sum(kept) AS kept FROM source_health") or {}
    last_run = db.decode(db.one("SELECT * FROM runs ORDER BY id DESC LIMIT 1"), "summary")
    closing = db.rows(
        "SELECT id, title, org, deadline, score FROM offers WHERE active = 1 AND status IN ('new', 'shortlisted') AND deadline >= ? "
        "AND deadline <= ? AND score >= ? ORDER BY deadline LIMIT 8",
        (date.today().isoformat(), (date.today() + timedelta(days=21)).isoformat(), max(floor, 50)))
    return {
        "version": __version__, "today": date.today().isoformat(), "profile": db.profile(), "priorities": priorities,
        "countries": config.COUNTRY_NAMES, "offers": {key: int(value or 0) for key, value in offers.items()},
        "by_tier": {row["tier"]: row["n"] for row in by_tier}, "by_country": by_country, "stages": stages,
        "contacts": contact_counts, "email_count": (db.one("SELECT count(*) AS n FROM contacts WHERE email IS NOT NULL") or {}).get("n", 0),
        "health": {key: int(value or 0) for key, value in health.items()}, "last_run": last_run, "run": pipeline.status(),
        "llm": writer.llm_status(), "closing": closing, "agents": list(pipeline.AGENTS), "mail": mailer.public_settings(), "pay": pay_overview(floor),
        "view_defaults": workflow.view_defaults(), "session_stats": workflow.stats(),
        "todo": (db.one("SELECT count(*) AS n FROM applications WHERE stage IN ('replied', 'bounced') "
                        "OR (sent_at IS NULL AND stage IN ('to_review', 'ready') AND coalesce(recipient, '') != '')") or {}).get("n", 0),
        "automation": {"inbox_enabled": bool(db.get_setting("inbox_enabled", True)), "notify_enabled": bool(db.get_setting("notify_enabled", True)),
                       "notify_to": db.get_setting("notify_to") or db.profile().get("email") or "",
                       "inbox_checked_at": db.get_setting("inbox_checked_at"), "inbox_error": db.get_setting("inbox_error") or ""},
    }


def pay_overview(floor: int) -> dict:
    """Stated internship pay (normalised to euros per month) per region, for the Today page."""
    import json as _json
    from statistics import median

    groups: dict[str, list[int]] = {}
    totals = {"stated": 0, "legal_min": 0, "unknown": 0, "unpaid": 0}
    for row in db.rows("SELECT tier, pay FROM offers WHERE active = 1 AND status != 'dismissed' AND score >= ? AND pay IS NOT NULL", (floor,)):
        info = _json.loads(row["pay"])
        totals[info.get("source", "unknown")] = totals.get(info.get("source", "unknown"), 0) + 1
        if info.get("source") == "stated" and info.get("monthly_eur"):
            groups.setdefault(row["tier"] or "remote", []).append(int(info["monthly_eur"]))
    regions = {}
    for tier, values in groups.items():
        values.sort()
        regions[tier] = {"n": len(values), "median": int(median(values)), "low": values[len(values) // 4], "high": values[(3 * len(values)) // 4],
                         "min": values[0], "max": values[-1]}
    return {"totals": totals, "regions": regions, "fr_min": pay.FR_MIN_MONTHLY, "fr_min_hourly": pay.FR_MIN_HOURLY}


def api_researchers_import(params: dict) -> dict:
    """Load an Excel researcher list, then find look-alikes in the background."""
    import base64
    import binascii

    from . import researchers

    body = params["body"]
    filename = re.sub(r"[^A-Za-z0-9._ ()-]+", "_", str(body.get("filename") or "researchers.xlsx"))[:80]
    if not filename.lower().endswith(".xlsx"):
        raise ApiError(400, "Please choose an Excel file (.xlsx).")
    try:
        content = base64.b64decode(body.get("data") or "", validate=True)
    except (binascii.Error, ValueError):
        raise ApiError(400, "The file could not be read.")
    if not content or len(content) > 5_000_000:
        raise ApiError(400, "The file is empty or larger than 5 MB.")
    folder = config.DATA_DIR / "imports"
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / filename
    target.write_bytes(content)
    try:
        result = researchers.import_spreadsheet(target)
    except Exception as exc:  # openpyxl raises many kinds of errors on malformed files
        raise ApiError(400, f"Could not read this spreadsheet: {exc}")
    db.set_setting("similar_requested", True)
    pipeline.start_background(["contacts"], trigger="import")
    return result


def api_researchers_similar(_: dict) -> dict:
    db.set_setting("similar_requested", True)
    return {"started": pipeline.start_background(["contacts"], trigger="similar")}


def api_offers(_: dict) -> dict:
    offers = db.rows(f"SELECT {OFFER_LIST_COLUMNS} FROM offers WHERE active = 1 OR status NOT IN ('new', 'dismissed') ORDER BY score DESC")
    for offer in offers:
        db.decode(offer, "flags", "tags", "pay")
        offer["pay_label"] = pay.label(offer["pay"])
        offer["ineligible"] = workflow.ineligible(offer["flags"])
    return {"offers": offers}


def api_offer(params: dict) -> dict:
    offer = db.decode(db.one("SELECT * FROM offers WHERE id = ?", (params["id"],)), "flags", "tags", "score_parts", "llm", "pay")
    if offer is None:
        raise ApiError(404, "Unknown offer.")
    offer["pay_label"] = pay.label(offer["pay"])
    offer["breakdown"] = scoring.describe_parts(offer["score_parts"] or {})
    offer["contacts"] = [db.decode(row, "topics", "evidence") for row in db.rows(
        """SELECT c.*, oc.relevance AS offer_relevance, oc.role AS offer_role
           FROM contacts c JOIN offer_contacts oc ON oc.contact_id = c.id
           WHERE oc.offer_id = ? ORDER BY c.confidence DESC, c.relevance DESC""", (offer["id"],))]
    employer = (offer.get("org") or "").split(" — ")[0].strip()
    offer["related_contacts"] = [db.decode(row, "topics", "evidence") for row in db.rows(
        "SELECT * FROM contacts WHERE status != 'ignored' AND kind IN ('alumni', 'expert') AND org LIKE ? "
        "ORDER BY confidence DESC, relevance DESC LIMIT 4", (f"%{employer[:30]}%",))] if len(employer) >= 4 else []
    offer["applications"] = db.rows("SELECT id, stage, language, subject, updated_at FROM applications WHERE offer_id = ? ORDER BY id DESC", (offer["id"],))
    offer["available_documents"] = documents.list_documents()
    offer["default_document_ids"] = documents.get_defaults_for_language(offer.get("lang") or "EN")
    return {"offer": offer}


def api_offer_update(params: dict) -> dict:
    body = params["body"]
    fields, values = [], []
    if "status" in body:
        if body["status"] not in db.OFFER_STATUSES:
            raise ApiError(400, "Unknown status.")
        fields.append("status = ?")
        values.append(body["status"])
    if "notes" in body:
        fields.append("notes = ?")
        values.append(str(body["notes"])[:4000])
    if not fields:
        raise ApiError(400, "Nothing to update.")
    db.execute(f"UPDATE offers SET {', '.join(fields)} WHERE id = ?", (*values, params["id"]))
    return {"ok": True}


def api_contacts(_: dict) -> dict:
    people = db.rows(
        "SELECT c.*, (SELECT count(*) FROM offer_contacts oc WHERE oc.contact_id = c.id) AS offer_count, "
        "(SELECT a.stage FROM applications a WHERE a.contact_id = c.id ORDER BY a.id DESC LIMIT 1) AS application_stage "
        "FROM contacts c ORDER BY c.is_starred DESC, c.confidence DESC, c.relevance DESC, c.name")
    from . import outreach

    from . import places

    for person in people:
        db.decode(person, "topics", "evidence")
        person["needs_checking"] = outreach.needs_checking(person)
        person.update(places.locate(person))
        person.pop("source_row", None)  # only needed on the server
    return {"contacts": people, "areas": {key: spec["label"] for key, spec in places.AREAS.items()}}


def api_places_find(params: dict) -> dict:
    """Find researchers publishing AI work in an area (Nice · Côte d'Azur), then their published addresses."""
    from . import places

    area = str(params["body"].get("area") or "cote_azur")
    if area not in places.AREAS:
        raise ApiError(400, "Unknown area.")
    messages: list[str] = []
    result = places.find_in_area(area, log=lambda message, *_: messages.append(str(message)))
    if result.get("openalex_ids"):  # address lookups take a while: they continue in the background
        threading.Thread(target=places.enrich_area, args=(result["openalex_ids"],),
                         kwargs={"log": lambda message, *_: print(f"[places   ] {message}", flush=True)}, daemon=True).start()
    result.pop("openalex_ids", None)
    return {**result, "log": messages}


def api_contact_add(params: dict) -> dict:
    """Add researchers you found yourself — one person, or a pasted list — and optionally write their e-mails now."""
    from . import researchers

    body = params["body"]
    try:
        if body.get("list") is not None:
            result = researchers.add_list(str(body["list"]))
        else:
            single = researchers.add_person(body.get("person") or {})
            result = {"added": [single["name"]] if single["created"] else [], "existing": [] if single["created"] else [single["name"]],
                      "problems": [], "ids": [single["id"]], "message": single["message"]}
    except ValueError as exc:
        raise ApiError(400, str(exc))
    result["drafts"] = 0
    if body.get("prepare") and result["ids"]:
        prepared = workflow.prepare_outreach(contact_ids=result["ids"])
        result["drafts"], result["skipped"] = prepared["created"], prepared["skipped"]
    return result


def api_contact_update(params: dict) -> dict:
    body = params["body"]
    allowed = {key: body[key] for key in ("status", "notes", "email", "role", "url", "is_starred") if key in body}
    if not allowed:
        raise ApiError(400, "Nothing to update.")
    if "email" in allowed:
        allowed["email"] = (allowed["email"] or "").strip() or None
        allowed["email_source"] = "entered manually" if allowed["email"] else None
    if "is_starred" in allowed:
        allowed["is_starred"] = 1 if bool(allowed["is_starred"]) else 0
    assignments = ", ".join(f"{key} = ?" for key in allowed)
    db.execute(f"UPDATE contacts SET {assignments} WHERE id = ?", (*allowed.values(), params["id"]))
    return {"ok": True}


def api_contact_enrich(params: dict) -> dict:
    try:
        return contacts.enrich_contact(int(params["id"]))
    except KeyError:
        raise ApiError(404, "Unknown contact.")


def api_directory(_: dict) -> dict:
    priorities = db.priorities()
    tier_of = {code: name for name, tier in priorities["tiers"].items() for code in tier["countries"]}
    entries = pipeline.load_directory()
    for entry in entries:
        entry["tier"] = tier_of.get(entry["country"], "tier3")
    return {"directory": entries}


def api_applications(_: dict) -> dict:
    rows = db.rows(
        """SELECT a.*, o.title AS offer_title, o.org AS offer_org, o.url AS offer_url, o.score AS offer_score,
                  o.country AS offer_country, o.deadline AS offer_deadline,
                  c.name AS contact_name, c.org AS contact_org, c.email AS contact_email
           FROM applications a LEFT JOIN offers o ON o.id = a.offer_id LEFT JOIN contacts c ON c.id = a.contact_id
           ORDER BY a.updated_at DESC""")
    # Attachments are loaded when a draft is opened (one query per row made this list take 14 s).
    return {"applications": rows}


def api_draft(params: dict) -> dict:
    body = params["body"]
    doc_ids = body.get("document_ids")
    try:
        draft = writer.compose(offer_id=body.get("offer_id"), contact_id=body.get("contact_id"),
                               language=body.get("language"), use_llm=bool(body.get("use_llm", True)),
                               attached_docs=doc_ids)
    except ValueError as exc:
        raise ApiError(400, str(exc))
    if body.get("save", True):
        # One open draft per target: asking again refreshes it instead of piling up copies.
        existing = db.one(
            "SELECT id FROM applications WHERE stage IN ('to_review', 'ready') AND sent_at IS NULL "
            "AND coalesce(offer_id, 0) = ? AND coalesce(contact_id, 0) = ? ORDER BY id DESC LIMIT 1",
            (body.get("offer_id") or 0, body.get("contact_id") or 0))
        if existing:
            db.execute("UPDATE applications SET language = ?, recipient = ?, subject = ?, body = ?, letter = ?, generator = ?, updated_at = ? WHERE id = ?",
                       (draft["language"], draft["recipient"], draft["subject"], draft["body"], draft["letter"], draft["generator"], db.now(), existing["id"]))
            # The language may have changed: re-pick the CV, transcript, etc. unless documents were chosen by hand.
            documents.set_application_documents(existing["id"], doc_ids if doc_ids is not None else documents.get_defaults_for_language(draft["language"]))
            draft["application_id"] = existing["id"]
            draft["documents"] = documents.get_application_documents(existing["id"])
            return draft
        draft["application_id"] = pipeline.save_application(offer_id=body.get("offer_id"), contact_id=body.get("contact_id"), draft=draft, selected_document_ids=doc_ids)
        draft["documents"] = documents.get_application_documents(draft["application_id"])
        if body.get("offer_id"):
            db.execute("UPDATE offers SET status = 'shortlisted' WHERE id = ? AND status = 'new'", (body["offer_id"],))
    return draft


def api_application_update(params: dict) -> dict:
    body = params["body"]
    application = db.one("SELECT * FROM applications WHERE id = ?", (params["id"],))
    if application is None:
        raise ApiError(404, "Unknown application.")
    if application["stage"] == "sending":
        raise ApiError(409, "This e-mail is being sent right now.")
    allowed = {key: body[key] for key in ("stage", "subject", "body", "letter", "recipient", "notes", "language", "follow_up_at") if key in body}
    if "recipient" in allowed:
        allowed["recipient"] = str(allowed["recipient"]).strip()
    if "stage" in allowed:
        if allowed["stage"] not in db.APPLICATION_STAGES:
            raise ApiError(400, "Unknown stage.")
        if allowed["stage"] == "scheduled" and application["stage"] != "scheduled":
            # Dropping a card on "Scheduled" is the same act as approving it.
            edits = {key: value for key, value in allowed.items() if key != "stage"}
            if edits:
                db.execute(f"UPDATE applications SET {', '.join(f'{key} = ?' for key in edits)} WHERE id = ?", (*edits.values(), params["id"]))
            try:
                return {"ok": True, **mailer.approve(int(params["id"]))}
            except mailer.MailError as exc:
                raise ApiError(400, str(exc))
        if application["stage"] == "scheduled" and allowed["stage"] != "scheduled":
            allowed["send_at"] = None
            allowed["approved_at"] = None
        if allowed["stage"] == "sent" and not application["sent_at"]:
            allowed["sent_at"] = date.today().isoformat()
            days = int(db.priorities().get("follow_up_days") or 10)
            allowed["follow_up_at"] = (date.today() + timedelta(days=days)).isoformat()
        offer_status = {"sent": "applied", "follow_up": "applied", "interview": "interview", "offer": "offer", "rejected": "rejected"}.get(allowed["stage"])
        if offer_status and application["offer_id"]:
            db.execute("UPDATE offers SET status = ? WHERE id = ?", (offer_status, application["offer_id"]))
        if allowed["stage"] in ("sent", "follow_up", "interview") and application["contact_id"]:
            db.execute("UPDATE contacts SET status = 'contacted' WHERE id = ?", (application["contact_id"],))
    assignments = ", ".join(f"{key} = ?" for key in allowed)
    db.execute(f"UPDATE applications SET {assignments}, updated_at = ? WHERE id = ?", (*allowed.values(), db.now(), params["id"]))
    return {"ok": True}


def api_application_approve(params: dict) -> dict:
    """The candidate validates a draft: it is queued and leaves after the configured delay."""
    try:
        return {"ok": True, **mailer.approve(int(params["id"]), params["body"].get("delay_hours"))}
    except mailer.MailError as exc:
        raise ApiError(400, str(exc))


def api_application_send_now(params: dict) -> dict:
    try:
        mailer.approve(int(params["id"]), 0)
    except mailer.MailError as exc:
        raise ApiError(400, str(exc))
    outcome = mailer.deliver(int(params["id"]))
    if not outcome["sent"]:
        raise ApiError(502, outcome["error"])
    return outcome


def api_mail_get(_: dict) -> dict:
    return mailer.public_settings()


def api_mail_set(params: dict) -> dict:
    try:
        return mailer.update_settings(params["body"])
    except (mailer.MailError, ValueError, TypeError) as exc:
        raise ApiError(400, str(exc))


def api_mail_test(_: dict) -> dict:
    return {"results": mailer.test_accounts()}


def api_google_client(params: dict) -> dict:
    from . import google_mail

    body = params["body"]
    try:
        if body.get("remove"):
            google_mail.remove_client()
        else:
            google_mail.set_client(str(body.get("json") or ""), str(body.get("client_id") or ""), str(body.get("client_secret") or ""))
    except mailer.MailError as exc:
        raise ApiError(400, str(exc))
    return mailer.public_settings()


def api_google_connect(params: dict) -> dict:
    from . import google_mail

    body = params["body"]
    try:
        return {"url": google_mail.start(int(body.get("index") or 0), bool(body.get("read", True)), params["port"])}
    except mailer.MailError as exc:
        raise ApiError(400, str(exc))


def api_google_disconnect(params: dict) -> dict:
    from . import google_mail

    return google_mail.disconnect(int(params["body"].get("index") or 0))


def google_return_page(query: dict) -> bytes:
    """The page Google sends the browser back to after the consent screen."""
    from . import google_mail

    try:
        result = google_mail.finish(query)
        title, ok = "Connected", True
        text = (f"<b>{html.escape(result['address'])}</b> is connected. The Radar can now send "
                + ("and read the replies to " if result["read"] else "") + "your applications from it.")
    except mailer.MailError as exc:
        title, ok, text = "Not connected", False, html.escape(str(exc))
    colour = "#0e9f6e" if ok else "#cf3f36"
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title} — Internship Radar</title>
<style>body{{font:15px/1.5 system-ui,-apple-system,Segoe UI,sans-serif;background:#f4f6f8;color:#1b2430;display:grid;place-items:center;min-height:100vh;margin:0}}
.card{{background:#fff;border:1px solid #dde3ea;border-radius:14px;padding:28px 32px;max-width:520px;box-shadow:0 8px 30px rgba(0,0,0,.06)}}
h1{{margin:0 0 8px;font-size:20px;color:{colour}}}a{{display:inline-block;margin-top:16px;background:#0e9f6e;color:#fff;padding:8px 14px;border-radius:8px;text-decoration:none}}
@media (prefers-color-scheme:dark){{body{{background:#0a0e13;color:#e6edf3}}.card{{background:#111820;border-color:#212d3a}}}}</style></head>
<body><div class="card"><h1>{"✓ " if ok else ""}{title}</h1><p>{text}</p><a href="/#settings">Back to the Radar</a></div>
<script>try {{ if (window.opener) {{ window.opener.postMessage({{ radarGoogle: {str(ok).lower()} }}, location.origin); setTimeout(() => window.close(), 1800); }} }} catch (e) {{}}</script>
</body></html>""".encode("utf-8")


def api_application_delete(params: dict) -> dict:
    db.execute("DELETE FROM applications WHERE id = ?", (params["id"],))
    return {"ok": True}


def api_follow_up(params: dict) -> dict:
    application = db.one(
        "SELECT a.*, c.name AS contact_name FROM applications a LEFT JOIN contacts c ON c.id = a.contact_id WHERE a.id = ?", (params["id"],))
    if application is None:
        raise ApiError(404, "Unknown application.")
    return writer.follow_up_draft(application, application["language"], db.profile())


def api_follow_up_create(params: dict) -> dict:
    new_id = pipeline.prepare_follow_up(int(params["id"]))
    if new_id is None:
        raise ApiError(400, "Only an application that was sent can be followed up.")
    return {"application_id": new_id}


def api_session(_: dict) -> dict:
    return workflow.session()


def api_bulk(params: dict) -> dict:
    body = params["body"]
    ids = [int(item) for item in body.get("ids") or [] if str(item).isdigit()]
    action = str(body.get("action") or "")
    if not ids or action not in ("approve", "delete", "unapprove", "declined", "closed", "interview", "sent"):
        raise ApiError(400, "Choose at least one e-mail and a valid action.")
    return workflow.bulk(ids, action)


def api_outreach_prepare(params: dict) -> dict:
    body = params["body"]
    ids = [int(item) for item in body.get("contact_ids") or [] if str(item).isdigit()] or None
    return workflow.prepare_outreach(limit=max(1, min(60, int(body.get("limit") or 25))), contact_ids=ids)


def api_outreach_sooner(_: dict) -> dict:
    return workflow.send_sooner()


def api_outreach_send(params: dict) -> dict:
    ids = [int(item) for item in params["body"].get("contact_ids") or [] if str(item).isdigit()]
    try:
        return workflow.send_to(ids)
    except mailer.MailError as exc:
        raise ApiError(400, str(exc))


def api_offer_kit(params: dict) -> dict:
    try:
        return workflow.kit(int(params["id"]), params["body"].get("language") if params.get("body") else None)
    except KeyError:
        raise ApiError(404, "Unknown offer.")


def api_offer_applied(params: dict) -> dict:
    try:
        return workflow.mark_applied(int(params["id"]))
    except KeyError:
        raise ApiError(404, "Unknown offer.")


def api_inbox_check(_: dict) -> dict:
    try:
        result = inbox.check(lambda *args: None)
    except Exception as exc:
        raise ApiError(502, f"The mailbox could not be read: {exc}")
    return {**result, "error": db.get_setting("inbox_error") or ""}


def api_automation(params: dict) -> dict:
    body = params["body"]
    for key in ("inbox_enabled", "notify_enabled"):
        if key in body:
            db.set_setting(key, bool(body[key]))
    if "notify_to" in body:
        address = str(body["notify_to"] or "").strip()
        if address and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[A-Za-z]{2,}", address):
            raise ApiError(400, "That notification address is not valid.")
        db.set_setting("notify_to", address)
    if body.get("send_test"):
        if not notify.summary(lambda *args: None, force=True):
            raise ApiError(502, "The test summary could not be sent — check the e-mail settings.")
    return {"ok": True}


def api_run(params: dict) -> dict:
    body = params["body"]
    started = pipeline.start_background(body.get("agents") or None, trigger="app", force=bool(body.get("force")))
    return {"started": started, "run": pipeline.status()}


def api_run_status(_: dict) -> dict:
    state = pipeline.status()
    run_id = state.get("run_id") or (db.one("SELECT max(id) AS id FROM runs") or {}).get("id")
    events = db.rows("SELECT ts, agent, level, message FROM run_events WHERE run_id = ? ORDER BY id DESC LIMIT 400", (run_id,)) if run_id else []
    return {"run": state, "events": events[::-1]}


def api_agents(_: dict) -> dict:
    runs = [db.decode(row, "summary") for row in db.rows("SELECT * FROM runs ORDER BY id DESC LIMIT 15")]
    sources = db.rows("SELECT * FROM source_health ORDER BY (last_error IS NOT NULL) DESC, kept DESC, source")
    return {"runs": runs, "sources": sources, "llm": writer.llm_status()}


def api_settings(params: dict) -> dict:
    body = params["body"]
    # Only the edited fields are stored, so later improvements to the defaults still apply.
    if isinstance(body.get("profile"), dict):
        current = db.profile()
        edits = {key: value for key, value in body["profile"].items() if key in current and value != current[key]}
        db.set_setting("profile", {**(db.get_setting("profile") or {}), **edits})
    if isinstance(body.get("priorities"), dict):
        current = db.priorities()
        edits = {key: value for key, value in body["priorities"].items() if key in current and value != current[key]}
        db.set_setting("priorities", {**(db.get_setting("priorities") or {}), **edits})
    return {"ok": True, "profile": db.profile(), "priorities": db.priorities()}


def api_launchpad(_: dict) -> dict:
    """One-click, pre-filled searches on boards that do not allow automated reading."""
    queries_fr = ["stage fin d'études NLP", "stage LLM", "stage PFE data science", "stage IA générative"]
    queries_en = ["NLP internship 2027", "LLM research intern", "machine learning intern", "data science intern"]
    boards = [
        {"group": "France", "name": "Welcome to the Jungle", "links": [
            {"label": query, "url": f"https://www.welcometothejungle.com/fr/jobs?query={quote(query)}&refinementList%5Bcontract_type%5D%5B%5D=internship"} for query in queries_fr]},
        {"group": "France", "name": "LinkedIn — France", "links": [
            {"label": query, "url": f"https://www.linkedin.com/jobs/search/?keywords={quote(query)}&location=France&f_JT=I&f_TPR=r604800"} for query in queries_fr]},
        {"group": "France", "name": "Indeed France", "links": [
            {"label": query, "url": f"https://fr.indeed.com/jobs?q={quote(query)}&l=France&sc=0kf%3Ajt%28internship%29%3B"} for query in queries_fr[:3]]},
        {"group": "Paris schools & labs", "name": "Paris-Saclay M2 internship catalogue", "links": [
            {"label": "All internships (university network or VPN)", "url": "https://gsweb1.dsi.universite-paris-saclay.fr/internship/list"}]},
        {"group": "Paris schools & labs", "name": "LinkedIn — Île-de-France research internships", "links": [
            {"label": query, "url": f"https://www.linkedin.com/jobs/search/?keywords={quote(query)}&location=%C3%8Ele-de-France&f_JT=I&f_TPR=r604800"}
            for query in ("stage recherche NLP", "stage laboratoire IA", "Efrei stage IA", "Télécom SudParis stage")]},
        {"group": "Paris schools & labs", "name": "Lab boards the Radar cannot scan", "links": [
            {"label": "Efrei Research Lab", "url": "https://www.efrei.fr/recherche/"},
            {"label": "LIP6 AI2D internships", "url": "https://ai2d.lip6.fr/taxonomy/term/3"},
            {"label": "LIRIS internship offers", "url": "https://liris.cnrs.fr/en/emplois/offres-de-stage"},
            {"label": "ENS cognitive-science internships", "url": "https://master-cognitive-science.ens.psl.eu/en/jobs/"}]},
        {"group": "France", "name": "JobTeaser", "links": [
            {"label": query, "url": f"https://www.jobteaser.com/fr/job-offers?contract=internship&q={quote(query)}"} for query in queries_fr[:3]]},
        {"group": "Italy · Belgium · Germany · Luxembourg", "name": "LinkedIn", "links": [
            {"label": f"{query} — {place}", "url": f"https://www.linkedin.com/jobs/search/?keywords={quote(query)}&location={quote(place)}&f_JT=I&f_TPR=r604800"}
            for place in ("Italy", "Belgium", "Germany", "Luxembourg") for query in queries_en[:2]]},
        {"group": "Italy · Belgium · Germany · Luxembourg", "name": "EURAXESS", "links": [
            {"label": "Internship / master thesis in AI", "url": "https://euraxess.ec.europa.eu/jobs/search?keywords=internship%20artificial%20intelligence"}]},
        {"group": "Europe-wide", "name": "LinkedIn — European Union", "links": [
            {"label": query, "url": f"https://www.linkedin.com/jobs/search/?keywords={quote(query)}&location=European%20Union&f_JT=I&f_TPR=r604800"} for query in queries_en]},
        {"group": "Europe-wide", "name": "Google Jobs", "links": [
            {"label": query, "url": f"https://www.google.com/search?q={quote(query + ' Europe')}&udm=8"} for query in queries_en[:3]]},
        {"group": "Egypt", "name": "Wuzzuf", "links": [
            {"label": query, "url": f"https://wuzzuf.net/search/jobs/?q={quote(query)}&a=hpb"} for query in ("machine learning intern", "data science internship", "NLP intern")]},
        {"group": "Egypt", "name": "LinkedIn — Egypt", "links": [
            {"label": query, "url": f"https://www.linkedin.com/jobs/search/?keywords={quote(query)}&location=Egypt&f_JT=I"} for query in queries_en[:3]]},
        {"group": "Gulf", "name": "LinkedIn — UAE · Saudi Arabia · Qatar", "links": [
            {"label": f"{query} — {place}", "url": f"https://www.linkedin.com/jobs/search/?keywords={quote(query)}&location={quote(place)}&f_JT=I"}
            for place in ("United Arab Emirates", "Saudi Arabia", "Qatar") for query in ("AI intern", "data science intern")]},
        {"group": "Gulf", "name": "Bayt", "links": [
            {"label": "Machine-learning internships", "url": "https://www.bayt.com/en/international/jobs/machine-learning-intern-jobs/"},
            {"label": "Data-science internships", "url": "https://www.bayt.com/en/international/jobs/data-science-intern-jobs/"}]},
        {"group": "Gulf", "name": "GulfTalent", "links": [
            {"label": "Internships", "url": "https://www.gulftalent.com/jobs/title/intern"}]},
    ]
    return {"boards": boards}


def api_documents(params: dict) -> dict:
    query = params.get("query") or {}
    doc_type = query.get("type", [None])[0]
    lang = query.get("lang", [None])[0]
    return {"documents": documents.list_documents(doc_type=doc_type, language=lang)}


def api_document_get(params: dict) -> dict:
    doc = documents.get_document(int(params["id"]))
    if not doc:
        raise ApiError(404, "Document not found.")
    return {"document": doc}


def api_document_add(params: dict) -> dict:
    body = params["body"]
    title = str(body.get("title") or "").strip()
    doc_type = str(body.get("doc_type") or "cv").strip().lower()
    language = str(body.get("language") or "ALL").strip().upper()
    version = str(body.get("version") or "").strip()
    notes = str(body.get("notes") or "").strip()
    is_default = bool(body.get("is_default", False))

    filename = str(body.get("filename") or "").strip()
    b64_content = body.get("content_base64") or body.get("content_b64")
    filepath = body.get("filepath")

    if b64_content:
        import base64
        try:
            file_bytes = base64.b64decode(b64_content)
        except Exception as exc:
            raise ApiError(400, f"Invalid base64 document content: {exc}")
        if not filename:
            filename = "document.pdf"
    elif filepath:
        clean_path = Path(str(filepath).replace("\\", "/"))
        target = (config.ROOT_DIR / clean_path).resolve()
        if config.ROOT_DIR.resolve() not in target.parents and target != config.ROOT_DIR:
            raise ApiError(400, "File path escapes project directory.")
        if not target.exists() or not target.is_file():
            raise ApiError(404, f"File '{filepath}' not found.")
        file_bytes = target.read_bytes()
        if not filename:
            filename = target.name
    else:
        raise ApiError(400, "Provide either content_base64 or filepath.")

    try:
        doc = documents.add_document(
            file_bytes,
            filename,
            title=title or filename,
            doc_type=doc_type,
            language=language,
            version=version,
            notes=notes,
            is_default=is_default,
        )
        return {"ok": True, "document": doc}
    except documents.DocumentError as exc:
        raise ApiError(400, str(exc))


def api_document_update(params: dict) -> dict:
    body = params["body"]
    try:
        doc = documents.update_document(int(params["id"]), **body)
        return {"ok": True, "document": doc}
    except documents.DocumentError as exc:
        raise ApiError(400, str(exc))


def api_documents_compress(params: dict) -> dict:
    """Compress one document, or every large one when no id is given."""
    doc_id = params["body"].get("id")
    try:
        results = [documents.compress_document(int(doc_id))] if doc_id else documents.compress_large()
    except documents.DocumentError as exc:
        raise ApiError(400, str(exc))
    return {"results": results, "saved": sum(item["before"] - item["after"] for item in results),
            "documents": documents.list_documents()}


def api_document_restore(params: dict) -> dict:
    try:
        documents.restore_original(int(params["id"]))
    except documents.DocumentError as exc:
        raise ApiError(400, str(exc))
    return {"documents": documents.list_documents()}


def api_document_delete(params: dict) -> dict:
    ok = documents.delete_document(int(params["id"]))
    if not ok:
        raise ApiError(404, "Document not found.")
    return {"ok": True}


def api_application_documents(params: dict) -> dict:
    return {"documents": documents.get_application_documents(int(params["id"]))}


def api_application_documents_set(params: dict) -> dict:
    body = params["body"]
    doc_ids = body.get("document_ids")
    if not isinstance(doc_ids, list):
        raise ApiError(400, "'document_ids' must be a list of document IDs.")
    docs = documents.set_application_documents(int(params["id"]), doc_ids)
    return {"ok": True, "documents": docs}


def api_sources(_: dict) -> dict:
    return {"sources": sources.list_sources_status()}


def api_source_toggle(params: dict) -> dict:
    source_id = params["id"]
    enabled = bool(params["body"].get("enabled", True))
    sources.set_source_enabled(source_id, enabled)
    return {"ok": True, "sources": sources.list_sources_status()}


def api_source_add(params: dict) -> dict:
    body = params["body"]
    ats = str(body.get("ats") or "").strip().lower()
    token = str(body.get("token") or "").strip()
    name = str(body.get("name") or "").strip()
    group = str(body.get("group_name") or "Custom employers").strip()
    extra = body.get("config") or {}
    if not ats or not token:
        raise ApiError(400, "Both 'ats' and 'token' are required.")
    try:
        src = sources.add_custom_source(ats, token, name, group_name=group, config_extra=extra)
        return {"ok": True, "source": src}
    except ValueError as exc:
        raise ApiError(400, str(exc))


def api_source_delete(params: dict) -> dict:
    source_id = params["id"]
    ok = sources.delete_custom_source(source_id)
    if not ok:
        raise ApiError(404, "Source not found or cannot be deleted.")
    return {"ok": True}


def eml_download(params: dict) -> tuple[bytes, str, str]:
    application = mailer.load_application(int(params["id"]))
    if application is None:
        raise ApiError(404, "Unknown application.")
    if params.get("kind") == "letter":
        return mailer.letter_pdf(application), "application/pdf", mailer.letter_filename(application)
    name = re.sub(r"[^A-Za-z0-9]+", "_", application.get("subject") or "application")[:60].strip("_") or "application"
    return mailer.build_message(application, draft=True).as_bytes(), "message/rfc822", f"{name}.eml"


ROUTES: list[tuple[str, re.Pattern[str], Callable[[dict], Any]]] = [
    ("GET", re.compile(r"^/api/state$"), api_state),
    ("GET", re.compile(r"^/api/offers$"), api_offers),
    ("POST", re.compile(r"^/api/researchers/import$"), api_researchers_import),
    ("POST", re.compile(r"^/api/researchers/similar$"), api_researchers_similar),
    ("GET", re.compile(r"^/api/offers/(?P<id>\d+)$"), api_offer),
    ("POST", re.compile(r"^/api/offers/(?P<id>\d+)$"), api_offer_update),
    ("GET", re.compile(r"^/api/contacts$"), api_contacts),
    ("POST", re.compile(r"^/api/places/find$"), api_places_find),
    ("POST", re.compile(r"^/api/contacts/add$"), api_contact_add),
    ("POST", re.compile(r"^/api/contacts/(?P<id>\d+)$"), api_contact_update),
    ("POST", re.compile(r"^/api/contacts/(?P<id>\d+)/enrich$"), api_contact_enrich),
    ("GET", re.compile(r"^/api/directory$"), api_directory),
    ("GET", re.compile(r"^/api/applications$"), api_applications),
    ("POST", re.compile(r"^/api/applications/(?P<id>\d+)$"), api_application_update),
    ("DELETE", re.compile(r"^/api/applications/(?P<id>\d+)$"), api_application_delete),
    ("POST", re.compile(r"^/api/applications/(?P<id>\d+)/approve$"), api_application_approve),
    ("POST", re.compile(r"^/api/applications/(?P<id>\d+)/send-now$"), api_application_send_now),
    ("GET", re.compile(r"^/api/documents$"), api_documents),
    ("POST", re.compile(r"^/api/documents$"), api_document_add),
    ("GET", re.compile(r"^/api/documents/(?P<id>\d+)$"), api_document_get),
    ("POST", re.compile(r"^/api/documents/(?P<id>\d+)$"), api_document_update),
    ("DELETE", re.compile(r"^/api/documents/(?P<id>\d+)$"), api_document_delete),
    ("POST", re.compile(r"^/api/documents/compress$"), api_documents_compress),
    ("POST", re.compile(r"^/api/documents/(?P<id>\d+)/restore$"), api_document_restore),
    ("GET", re.compile(r"^/api/applications/(?P<id>\d+)/documents$"), api_application_documents),
    ("POST", re.compile(r"^/api/applications/(?P<id>\d+)/documents$"), api_application_documents_set),
    ("GET", re.compile(r"^/api/sources$"), api_sources),
    ("POST", re.compile(r"^/api/sources/(?P<id>[^/]+)/toggle$"), api_source_toggle),
    ("POST", re.compile(r"^/api/sources/add$"), api_source_add),
    ("DELETE", re.compile(r"^/api/sources/(?P<id>[^/]+)$"), api_source_delete),
    ("GET", re.compile(r"^/api/mail$"), api_mail_get),
    ("POST", re.compile(r"^/api/mail$"), api_mail_set),
    ("POST", re.compile(r"^/api/mail/test$"), api_mail_test),
    ("POST", re.compile(r"^/api/google/client$"), api_google_client),
    ("POST", re.compile(r"^/api/google/connect$"), api_google_connect),
    ("POST", re.compile(r"^/api/google/disconnect$"), api_google_disconnect),
    ("GET", re.compile(r"^/api/applications/(?P<id>\d+)/follow-up$"), api_follow_up),
    ("POST", re.compile(r"^/api/applications/(?P<id>\d+)/follow-up$"), api_follow_up_create),
    ("POST", re.compile(r"^/api/draft$"), api_draft),
    ("GET", re.compile(r"^/api/session$"), api_session),
    ("POST", re.compile(r"^/api/applications/bulk$"), api_bulk),
    ("POST", re.compile(r"^/api/outreach/prepare$"), api_outreach_prepare),
    ("POST", re.compile(r"^/api/outreach/send$"), api_outreach_send),
    ("POST", re.compile(r"^/api/outreach/sooner$"), api_outreach_sooner),
    ("POST", re.compile(r"^/api/offers/(?P<id>\d+)/kit$"), api_offer_kit),
    ("POST", re.compile(r"^/api/offers/(?P<id>\d+)/applied$"), api_offer_applied),
    ("POST", re.compile(r"^/api/inbox/check$"), api_inbox_check),
    ("POST", re.compile(r"^/api/automation$"), api_automation),
    ("POST", re.compile(r"^/api/run$"), api_run),
    ("GET", re.compile(r"^/api/run/status$"), api_run_status),
    ("GET", re.compile(r"^/api/agents$"), api_agents),
    ("POST", re.compile(r"^/api/settings$"), api_settings),
    ("GET", re.compile(r"^/api/launchpad$"), api_launchpad),
]


class Handler(BaseHTTPRequestHandler):
    server_version = "InternshipRadar"

    def log_message(self, *_: Any) -> None:  # Keep the console for agent output.
        return

    def _send(self, status: int, payload: bytes, content_type: str, extra: Optional[dict] = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(payload)

    def _json(self, status: int, data: Any) -> None:
        self._send(status, json.dumps(data, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

    def _local_only(self) -> bool:
        """Refuse cross-site requests: state-changing calls must come from this app's own page."""
        host = (self.headers.get("Host") or "").split(":")[0]
        if host not in ("127.0.0.1", "localhost"):
            return False
        origin = self.headers.get("Origin")
        return not origin or urlparse(origin).hostname in ("127.0.0.1", "localhost")

    def _dispatch(self, method: str) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        if not self._local_only():
            self._json(403, {"error": "Requests are only accepted from this machine."})
            return
        try:
            if method == "GET" and (match := re.match(r"^/api/documents/(?P<id>\d+)/file$", path)):
                doc = documents.get_document(int(match["id"]))
                if not doc:
                    raise ApiError(404, "Document not found.")
                file_path = documents.get_document_path(doc)
                if not file_path.exists() or not file_path.is_file():
                    raise ApiError(404, "Document file not found on disk.")
                content_type = doc.get("mime_type") or "application/octet-stream"
                fname = doc.get("original_filename") or file_path.name
                self._send(200, file_path.read_bytes(), content_type, {"Content-Disposition": f'inline; filename="{fname}"'})
                return
            if method == "GET" and (match := re.match(r"^/api/applications/(?P<id>\d+)/(?P<kind>eml|letter)$", path)):
                payload, content_type, filename = eml_download(match.groupdict())
                disposition = "inline" if match["kind"] == "letter" else "attachment"
                self._send(200, payload, content_type, {"Content-Disposition": f'{disposition}; filename="{filename}"'})
                return
            if path.startswith("/api/"):
                body: dict = {}
                if method in ("POST", "DELETE"):
                    length = int(self.headers.get("Content-Length") or 0)
                    if length > 25_000_000:
                        raise ApiError(413, "Request too large.")
                    raw = self.rfile.read(length) if length else b""
                    body = json.loads(raw.decode("utf-8")) if raw.strip() else {}
                    if not isinstance(body, dict):
                        raise ApiError(400, "A JSON object is expected.")
                for route_method, pattern, handler in ROUTES:
                    match = pattern.match(path)
                    if match and route_method == method:
                        params = {**match.groupdict(), "query": parse_qs(parsed.query), "body": body, "port": self.server.server_address[1]}
                        self._json(200, handler(params))
                        return
                raise ApiError(404, "Unknown endpoint.")
            if method != "GET":
                raise ApiError(405, "Method not allowed.")
            query = parse_qs(parsed.query)
            if path in ("/", "") and query.get("state"):  # Google's sign-in comes back to the loopback address
                from . import google_mail

                if google_mail.is_callback(query):
                    self._send(200, google_return_page(query), "text/html; charset=utf-8")
                    return
            self._static(path)
        except ApiError as exc:
            self._json(exc.status, {"error": str(exc)})
        except json.JSONDecodeError:
            self._json(400, {"error": "Invalid JSON."})
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as exc:  # Surface the problem to the interface instead of dropping the connection.
            self._json(500, {"error": f"{type(exc).__name__}: {exc}"})

    def _static(self, path: str) -> None:
        name = "index.html" if path in ("/", "") else path.lstrip("/")
        target = (config.WEB_DIR / name).resolve()
        if config.WEB_DIR.resolve() not in target.parents or not target.is_file():
            raise ApiError(404, "Not found.")
        content_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        if content_type.startswith("text/") or content_type in ("application/javascript",):
            content_type += "; charset=utf-8"
        self._send(200, target.read_bytes(), content_type)

    def do_GET(self) -> None:  # noqa: N802
        self._dispatch("GET")

    def do_POST(self) -> None:  # noqa: N802
        self._dispatch("POST")

    def do_DELETE(self) -> None:  # noqa: N802
        self._dispatch("DELETE")


def _sender_loop(stop: threading.Event) -> None:
    """While the app is open, send approved e-mails as soon as their delay has elapsed."""
    while not stop.wait(60):
        try:
            for outcome in mailer.send_due():
                print(f"[mail     ] {'sent to ' + outcome['to'] if outcome['sent'] else 'failed: ' + outcome['error']}", flush=True)
            if inbox.due(30):
                inbox.check(lambda *args: None)
                notify.events(lambda *args: None)
        except Exception as exc:  # The loop must survive any single failure.
            print(f"[mail     ] {type(exc).__name__}: {exc}", flush=True)


def serve(port: int = 8765, open_browser: bool = True, autorun: bool = True) -> int:
    db.initialize()
    try:
        httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    except OSError as exc:
        print(f"Could not start on port {port}: {exc}. Is the app already running? Try --port {port + 1}.")
        return 1
    httpd.daemon_threads = True
    url = f"http://127.0.0.1:{port}/"
    stop = threading.Event()
    threading.Thread(target=writer.check_llm, daemon=True).start()
    threading.Thread(target=_sender_loop, args=(stop,), daemon=True).start()
    if autorun:
        threading.Thread(target=pipeline.schedule_loop, args=(stop,), daemon=True).start()
        if not db.get_setting("last_run_at"):
            pipeline.start_background(trigger="first-launch")
    print(f"Internship Radar {__version__} — {url}  (Ctrl+C to stop)")
    if open_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        stop.set()
        httpd.server_close()
    return 0
