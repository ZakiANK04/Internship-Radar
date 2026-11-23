"""The autonomous pipeline: six agents run in sequence and report what they did.

Scout     collects postings from every live source.
Analyst   scores them (rules) and, when Gemini is available, adds a structured second opinion.
Contacts  gathers supervisors, ENP-affiliated researchers and topic experts with evidence.
Directory re-checks every curated link so stale pages are flagged, not trusted.
Writer    prepares review-ready drafts for the best new offers.
Follow-up moves silent applications to "follow-up due".
Reporter  writes the daily digest.
"""

from __future__ import annotations

import json
import re
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta, timezone
from typing import Any, Iterable, Optional

from . import config, contacts, db, documents, inbox, mailer, net, notify, pay, researchers, scoring, workflow, writer
from .sources import Context, RawOffer, Source, load_sources
from .text import clean, detect_city, detect_countries, fold, guess_language, normalise_country, pick_country

AGENTS = ("scout", "analyst", "contacts", "directory", "writer", "followup", "reporter")
_run_lock = threading.Lock()
_state: dict[str, Any] = {"running": False, "run_id": None, "agent": None, "progress": "", "started_at": None}


def status() -> dict[str, Any]:
    return dict(_state)


class RunLog:
    def __init__(self, run_id: int) -> None:
        self.run_id = run_id

    def __call__(self, agent: str, message: str, level: str = "info") -> None:
        _state["agent"], _state["progress"] = agent, message
        db.execute("INSERT INTO run_events(run_id, ts, agent, level, message) VALUES(?, ?, ?, ?, ?)",
                   (self.run_id, db.now(), agent, level, message[:1000]))
        print(f"[{agent:9s}] {message}", flush=True)

    def for_agent(self, agent: str):
        return lambda message, level="info": self(agent, message, level)


# ---------------------------------------------------------------------- Scout
def _tier_lookup(priorities: dict) -> tuple[dict[str, tuple[str, int]], list[str]]:
    lookup, order = {}, []
    for name, tier in priorities["tiers"].items():
        for code in tier["countries"]:
            lookup[code] = (name, int(tier["weight"]))
            order.append(code)
    return lookup, order


def _normalise(raw: RawOffer, source: Source, lookup: dict, order: list[str], skills: list[str]) -> Optional[dict[str, Any]]:
    hint = normalise_country(raw.country_hint)
    candidates = [hint] if hint else detect_countries(raw.location)
    country = pick_country(candidates, lookup.keys(), order)
    if country is None and not raw.remote:
        return None
    title, description = clean(raw.title), (raw.description or "").strip()
    lang = guess_language(description or title)
    tier, weight = lookup.get(country, ("remote", 6)) if country else ("remote", 6)
    result = scoring.assess(title=title, description=description, country=country, tier_weight=weight, lang=lang,
                            remote=raw.remote, source_kind=source.kind, skills=skills,
                            trusted_internship=raw.trusted_internship)
    if not result.relevant:
        return None
    return {
        "source": source.name, "source_kind": source.kind, "title": title[:300], "org": clean(raw.org)[:160],
        "country": country, "city": detect_city(raw.location, country), "tier": tier,
        "location_raw": clean(raw.location)[:200], "url": raw.url, "description": description[:20000], "lang": lang,
        "contract": result.contract, "posted_at": raw.posted_at, "deadline": raw.deadline, "remote": int(raw.remote),
        "score": result.score, "score_parts": result.parts, "flags": result.flags, "tags": result.tags,
        "pay": pay.assess(title + chr(10) + description, country, raw.salary, raw.pay_hint),
    }


def run_scout(log, only: Optional[Iterable[str]] = None) -> dict[str, Any]:
    priorities, profile = db.priorities(), db.profile()
    lookup, order = _tier_lookup(priorities)
    ctx = Context(allowed_countries=frozenset(lookup))
    sources = load_sources()
    if only:
        wanted = [item.lower() for item in only]
        sources = [source for source in sources if any(source.name.lower().startswith(item) for item in wanted)]
    log(f"Scanning {len(sources)} live source(s) across {len(lookup)} countries…")
    existing_llm = {row["uid"]: row["llm"] for row in db.rows("SELECT uid, llm FROM offers WHERE llm IS NOT NULL")}
    totals = {"sources": len(sources), "ok": 0, "failed": 0, "fetched": 0, "kept": 0, "new": 0}
    # Postings already held from an employer or institute keep priority over job-board copies,
    # including when only part of the sources is scanned.
    seen_keys: dict[str, str] = {
        _title_key(row["title"], row["country"]): row["source"]
        for row in db.rows("SELECT title, country, source FROM offers WHERE active = 1 AND source_kind != 'board'")}
    new_titles: list[str] = []

    def work(source: Source) -> tuple[Source, list[RawOffer], Optional[str], float]:
        started = time.monotonic()
        try:
            return source, source.fetch(ctx), None, time.monotonic() - started
        except Exception as exc:  # One failing source must never stop the run.
            return source, [], f"{type(exc).__name__}: {exc}"[:300], time.monotonic() - started

    done = 0
    results: list[tuple[Source, list[RawOffer], Optional[str], float]] = []
    with ThreadPoolExecutor(max_workers=12) as pool:
        futures = [pool.submit(work, source) for source in sources]
        for future in as_completed(futures):
            results.append(future.result())
            done += 1
            _state["progress"] = f"{done}/{len(sources)} sources scanned"
    # Employers and institutes are stored before job boards, so when the same posting
    # is found twice the copy that links to the original source is the one kept.
    results.sort(key=lambda item: item[0].kind == "board")
    for source, raws, error, seconds in results:
        kept = 0
        stamp = db.now()
        with db.write() as connection:
            if error is None:
                seen_uids = []
                for raw in raws:
                    values = _normalise(raw, source, lookup, order, profile.get("skills") or [])
                    if values is None:
                        continue
                    key = _title_key(values["title"], values["country"])
                    if len(key) > 24 and seen_keys.get(key, source.name) != source.name:
                        continue
                    seen_keys.setdefault(key, source.name)
                    _apply_llm_bonus(values, existing_llm.get(raw.uid))
                    offer_id, created = db.upsert_offer(connection, raw.uid, values)
                    seen_uids.append(raw.uid)
                    kept += 1
                    if created:
                        totals["new"] += 1
                        new_titles.append(f"{values['score']:3d}  {values['title']} — {values['org']} ({values['country'] or 'remote'})")
                    for person in raw.contacts:
                        c_today = date.today().isoformat()
                        c_ev = person.get("evidence") or [
                            {"type": "offer", "text": values["title"], "url": raw.url, "retrieved_at": c_today}
                        ]
                        contact_id, _ = db.upsert_contact(connection, {
                            "name": person["name"],
                            "role": person.get("role") or "Supervisor named in posting",
                            "role_category": person.get("role_category", "supervisor"),
                            "role_verified": int(person.get("role_verified", 1)),
                            "org": None if (person.get("org") or values["org"] or "").startswith("via ")
                            else (person.get("org") or values["org"]),
                            "country": values["country"],
                            "email": person.get("email"),
                            "email_source": person.get("email_source"),
                            "email_verified": int(person.get("email_verified", 1 if person.get("email") else 0)),
                            "url": person.get("url") or raw.url,
                            "kind": "offer",
                            "topics": values["tags"][:4],
                            "evidence": c_ev,
                            "relevance": min(100, values["score"] + 10),
                            "confidence": person.get("confidence", 95 if person.get("email") else 85),
                            "lookup_status": "found" if person.get("email") else "pending",
                            "last_lookup_at": c_today if person.get("email") else None,
                        })
                        db.link_offer_contact(
                            connection, offer_id, contact_id,
                            relevance=min(100, values["score"] + 10),
                            role=person.get("role") or "Supervisor named in offer",
                        )
                # Postings that vanished from a healthy source are retired after two misses.
                marks = ",".join("?" for _ in seen_uids) or "''"
                connection.execute(
                    f"UPDATE offers SET missed_runs = missed_runs + 1 WHERE source = ? AND active = 1 AND uid NOT IN ({marks})",
                    (source.name, *seen_uids))
                connection.execute("UPDATE offers SET active = 0 WHERE source = ? AND missed_runs >= 2", (source.name,))

            if error is None:
                health_status = "ok" if len(raws) > 0 else "empty"
                http_code = 200
                error_category = None
            else:
                health_status = "error"
                http_code = None
                error_category = "error"
                if "404" in error:
                    http_code = 404
                    error_category = "http_404"
                elif "422" in error:
                    http_code = 422
                    error_category = "http_422"
                elif "429" in error:
                    http_code = 429
                    error_category = "rate_limit"
                elif "timed out" in error.lower() or "timeout" in error.lower():
                    error_category = "timeout"
                elif "connection" in error.lower():
                    error_category = "connection"

            connection.execute(
                """INSERT INTO source_health(source, kind, last_run, last_ok, last_error, fetched, kept, seconds, status, http_code, error_category)
                   VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(source) DO UPDATE SET kind = excluded.kind, last_run = excluded.last_run,
                     last_ok = coalesce(excluded.last_ok, source_health.last_ok), last_error = excluded.last_error,
                     fetched = excluded.fetched, kept = excluded.kept, seconds = excluded.seconds,
                     status = excluded.status, http_code = excluded.http_code, error_category = excluded.error_category""",
                (source.name, f"{source.group}|{source.label}", stamp, None if error else stamp, error,
                 len(raws), kept, round(seconds, 1), health_status, http_code, error_category))
        totals["fetched"] += len(raws)
        totals["kept"] += kept
        totals["failed" if error else "ok"] += 1
        if error:
            log(f"{source.label}: failed — {error}", "warn")
        elif kept:
            log(f"{source.label}: {kept} matching internship(s) ({len(raws)} candidates)")
    with db.write() as connection:
        primary = {_title_key(row["title"], row["country"]) for row in connection.execute(
            "SELECT title, country FROM offers WHERE active = 1 AND source_kind != 'board'").fetchall()}
        for row in connection.execute("SELECT id, title, country FROM offers WHERE active = 1 AND source_kind = 'board'").fetchall():
            key = _title_key(row["title"], row["country"])
            if len(key) > 24 and key in primary:
                connection.execute("UPDATE offers SET active = 0, missed_runs = 9 WHERE id = ?", (row["id"],))
    log(f"Scout finished: {totals['kept']} matching offers, {totals['new']} new, "
        f"{totals['ok']}/{totals['sources']} sources healthy.")
    totals["new_titles"] = sorted(new_titles, reverse=True)[:40]
    return totals


_TITLE_NOISE = re.compile(
    r"\b(?:stage|stagiaire|internship|intern|pfe|h/f|f/h|m/f|f/m|h/f/x|m/w/d|all genders?|20\d\d|de fin d'etudes|bac ?\+ ?\d)\b")


def _title_key(title: str, country: Optional[str]) -> str:
    """Normalised title used to recognise one posting published on several sources."""
    text = _TITLE_NOISE.sub(" ", fold(title))
    return re.sub(r"[^a-z0-9]+", "", text) + f"|{country}"


def _apply_llm_bonus(values: dict[str, Any], llm_json: Optional[str]) -> None:
    if not llm_json:
        return
    try:
        fit = int(json.loads(llm_json).get("fit", 5))
    except (json.JSONDecodeError, TypeError, ValueError):
        return
    bonus = round((fit - 5) * 1.6)
    values["score_parts"] = {**values["score_parts"], "ai": bonus}
    values["score"] = max(0, min(100, values["score"] + bonus))


# -------------------------------------------------------------------- Analyst
def run_analyst(log) -> dict[str, Any]:
    filled = pay.backfill()  # offers stored before pay was extracted
    if filled:
        log(f"Pay read for {filled} older offer(s).")
    priorities, profile = db.priorities(), db.profile()
    limit = int(priorities.get("llm_triage_per_run") or 0)
    counts = db.one("SELECT count(*) AS total, sum(score >= 85) AS a, sum(score >= 72 AND score < 85) AS b FROM offers WHERE active = 1") or {}
    log(f"{counts.get('total') or 0} active offers ranked — {counts.get('a') or 0} grade A, {counts.get('b') or 0} grade B.")
    reviewed = 0
    if limit:
        writer.check_llm()
    if limit and writer.llm_available():
        pending = db.rows(
            "SELECT * FROM offers WHERE active = 1 AND llm IS NULL AND status IN ('new', 'shortlisted') AND score >= 72 "
            "AND length(coalesce(description, '')) > 300 ORDER BY score DESC LIMIT ?", (limit,))
        for offer in pending:
            verdict = writer.triage(offer, profile)
            if verdict is None:
                if not writer.llm_available():
                    log(f"AI review paused: {writer.llm_status()}", "warn")
                    break
                continue
            parts = json.loads(offer["score_parts"] or "{}")
            parts.pop("ai", None)
            base = max(0, min(100, sum(int(value) for value in parts.values())))
            bonus = round((verdict["fit"] - 5) * 1.6)
            if not verdict["is_internship"]:
                bonus -= 25
            parts["ai"] = bonus
            db.execute("UPDATE offers SET llm = ?, score = ?, score_parts = ? WHERE id = ?",
                       (json.dumps(verdict, ensure_ascii=False), max(0, min(100, base + bonus)), json.dumps(parts), offer["id"]))
            reviewed += 1
        log(f"AI second opinion added to {reviewed} offer(s).")
    elif limit:
        log(f"AI review skipped — {writer.llm_status()}")
    return {"reviewed": reviewed, "active": counts.get("total") or 0, "grade_a": counts.get("a") or 0, "grade_b": counts.get("b") or 0}


# ------------------------------------------------------------------- Contacts
def run_contacts(log, force: bool = False) -> dict[str, Any]:
    priorities = db.priorities()
    last = db.get_setting("contacts_refreshed_at")
    due = force or not last or (datetime.now(timezone.utc) - datetime.fromisoformat(last.replace("Z", "+00:00"))
                                > timedelta(days=int(priorities.get("contact_refresh_days") or 7)))
    summary = {"alumni": 0, "experts": 0, "emails": 0}
    if due:
        summary["alumni"], alumni_done = contacts.harvest_alumni(log)
        summary["experts"], experts_done = contacts.harvest_experts(log)
        # An interrupted harvest (daily API quota) is not recorded as done, so the next run completes it.
        if alumni_done and experts_done:
            db.set_setting("contacts_refreshed_at", db.now())
    else:
        log("Researcher network is fresh — next OpenAlex refresh in a few days.")
    # Your own researcher list: find each person's publication profile, then look-alikes.
    if db.one("SELECT 1 AS x FROM contacts WHERE kind = 'imported' LIMIT 1"):
        researchers.resolve(log)
        if due or db.get_setting("similar_requested"):
            summary["similar"] = researchers.find_similar(priorities["tiers"], log)
            db.set_setting("similar_requested", False)
    summary["emails"] = contacts.auto_enrich(12, log)
    summary["total"] = (db.one("SELECT count(*) AS n FROM contacts") or {}).get("n", 0)
    return summary


# ------------------------------------------------------------------ Directory
def load_directory() -> list[dict[str, Any]]:
    entries = json.loads((config.SEED_DIR / "directory.json").read_text(encoding="utf-8"))
    checks = {row["url"]: row for row in db.rows("SELECT * FROM link_checks")}
    for entry in entries:
        check = checks.get(entry["url"])
        entry["link_status"] = check["status"] if check else "unchecked"
        entry["checked_at"] = check["checked_at"] if check else None
    return entries


def run_directory(log, force: bool = False) -> dict[str, Any]:
    entries = load_directory()
    cutoff = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
    pending = [entry for entry in entries if force or not entry["checked_at"] or entry["checked_at"] < cutoff]
    if not pending:
        log(f"All {len(entries)} directory links were verified in the last 7 days.")
        return {"checked": 0, "entries": len(entries)}
    results: dict[str, int] = {}
    with ThreadPoolExecutor(max_workers=10) as pool:
        for entry, (state, code) in zip(pending, pool.map(lambda item: net.probe(item["url"]), pending)):
            db.execute(
                "INSERT INTO link_checks(url, status, http_code, checked_at) VALUES(?, ?, ?, ?) "
                "ON CONFLICT(url) DO UPDATE SET status = excluded.status, http_code = excluded.http_code, checked_at = excluded.checked_at",
                (entry["url"], state, code, db.now()))
            results[state] = results.get(state, 0) + 1
    log("Directory links re-checked: " + ", ".join(f"{count} {state}" for state, count in sorted(results.items())))
    return {"checked": len(pending), "entries": len(entries), **results}


# --------------------------------------------------------------------- Writer
def run_writer(log) -> dict[str, Any]:
    """Keep a stock of review-ready e-mails: researchers first, plus offers that name a contact with an address."""
    priorities = db.priorities()
    result = workflow.prepare_outreach(limit=int(priorities.get("outreach_drafts_per_run", 20) or 0))
    drafted = 0
    targets = db.rows(
        """SELECT o.*, c.id AS linked_contact FROM offers o JOIN offer_contacts oc ON oc.offer_id = o.id
           JOIN contacts c ON c.id = oc.contact_id AND c.email IS NOT NULL AND c.status = 'new'
           WHERE o.active = 1 AND o.status IN ('new', 'shortlisted') AND o.score >= 85
             AND NOT EXISTS (SELECT 1 FROM applications a WHERE a.offer_id = o.id OR a.contact_id = c.id)
           GROUP BY o.id ORDER BY o.score DESC LIMIT ?""", (int(priorities.get("auto_draft_per_run") or 0),))
    for offer in targets:
        lang = writer.choose_language(offer, None)
        default_ids = documents.get_defaults_for_language(lang)
        try:
            draft = writer.compose(offer_id=offer["id"], contact_id=offer["linked_contact"], language=lang, use_llm=False,
                                   attached_docs=[documents.get_document(did) for did in default_ids if documents.get_document(did)])
        except Exception as exc:
            log(f"Could not draft for “{offer['title']}”: {exc}", "warn")
            continue
        save_application(offer_id=offer["id"], contact_id=offer["linked_contact"], draft=draft, selected_document_ids=default_ids)
        drafted += 1
    workflow.convert_formless_drafts()
    log(f"{result['created']} researcher e-mail(s) and {drafted} offer e-mail(s) prepared — {result['pending'] + drafted} waiting for your approval.")
    return {"drafted": result["created"] + drafted, "pending": result["pending"] + drafted}


def save_application(*, offer_id: Optional[int], contact_id: Optional[int], draft: dict[str, Any],
                     stage: str = "to_review", selected_document_ids: Optional[list[int]] = None) -> int:
    stamp = db.now()
    lang = draft.get("language") or "EN"
    if selected_document_ids is None:
        selected_document_ids = documents.get_defaults_for_language(lang)

    import json
    doc_ids_json = json.dumps(selected_document_ids)
    app_id = db.execute(
        """INSERT INTO applications(offer_id, contact_id, kind, stage, language, recipient, subject, body, letter, generator,
                                    selected_document_ids, created_at, updated_at) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (offer_id, contact_id, draft.get("kind") or ("offer" if offer_id else "spontaneous"), stage,
         lang, draft.get("recipient") or "", draft.get("subject") or "", draft.get("body") or "",
         draft.get("letter") or "", draft.get("generator") or "template", doc_ids_json, stamp, stamp))

    if selected_document_ids:
        documents.set_application_documents(app_id, selected_document_ids)

    return app_id


def prepare_follow_up(application_id: int) -> Optional[int]:
    """Create a "Relance" draft for an e-mail that got no reply (it still needs your approval to be sent)."""
    original = db.one("SELECT a.*, c.name AS contact_name FROM applications a LEFT JOIN contacts c ON c.id = a.contact_id WHERE a.id = ?",
                      (application_id,))
    if original is None or not original.get("sent_at"):
        return None
    marker = f"Follow-up to application #{application_id}"
    existing = db.one("SELECT id FROM applications WHERE notes LIKE ? AND sent_at IS NULL", (f"%{marker}%",))
    if existing:
        return existing["id"]
    language = original.get("language") or "EN"
    text = writer.follow_up_draft(original, language, db.profile())
    draft = {"kind": original.get("kind") or "spontaneous", "language": language, "recipient": original.get("recipient") or "",
             "subject": text["subject"], "body": text["body"], "letter": "", "generator": "relance template"}
    new_id = save_application(offer_id=original.get("offer_id"), contact_id=original.get("contact_id"), draft=draft,
                              selected_document_ids=documents.get_defaults_for_language(language))
    db.execute("UPDATE applications SET notes = ? WHERE id = ?", (f"{marker} (first e-mail sent {original['sent_at']}).", new_id))
    return new_id


# ------------------------------------------------------------------ Follow-up
def run_followup(log) -> dict[str, Any]:
    delivered = mailer.send_due(log)  # approved e-mails whose waiting time is over
    try:
        inbox.check(log)  # replies and bounces first, so nobody who answered gets a follow-up
    except Exception as exc:
        log(f"Mailbox check failed: {exc}", "warn")
    days = int(db.priorities().get("follow_up_days") or 10)
    cutoff = (date.today() - timedelta(days=days)).isoformat()
    due = db.rows("SELECT id FROM applications WHERE stage = 'sent' AND sent_at IS NOT NULL AND sent_at <= ?", (cutoff,))
    for application in due:
        db.execute("UPDATE applications SET stage = 'follow_up', follow_up_at = ?, updated_at = ? WHERE id = ?",
                   (date.today().isoformat(), db.now(), application["id"]))
    closing = db.rows(
        "SELECT title, org, deadline FROM offers WHERE active = 1 AND status IN ('new', 'shortlisted') AND deadline IS NOT NULL "
        "AND deadline >= ? AND deadline <= ? AND score >= 72 ORDER BY deadline",
        (date.today().isoformat(), (date.today() + timedelta(days=14)).isoformat()))
    log(f"{len(due)} application(s) moved to follow-up; {len(closing)} strong offer(s) close within 14 days.")
    # Researchers who did not answer: prepare the "Relance" e-mail for your review (never sent without approval).
    prepared = 0
    for row in db.rows("SELECT id FROM applications WHERE stage = 'follow_up' AND kind = 'spontaneous' AND contact_id IS NOT NULL "
                       "ORDER BY sent_at LIMIT 40"):
        if not db.one("SELECT 1 AS x FROM applications WHERE notes LIKE ?", (f"%Follow-up to application #{row['id']}%",)):
            prepare_follow_up(row["id"])
            prepared += 1
    if prepared:
        log(f"{prepared} follow-up e-mail(s) prepared for your review.")
    notify.events(log)
    notify.summary(log)
    return {"follow_ups": len(due), "closing_soon": len(closing), "emails_sent": sum(item["sent"] for item in delivered)}


# ------------------------------------------------------------------- Reporter
def run_reporter(log, summary: dict[str, Any]) -> dict[str, Any]:
    config.DIGEST_DIR.mkdir(parents=True, exist_ok=True)
    today = date.today().isoformat()
    since = (datetime.now(timezone.utc) - timedelta(hours=26)).isoformat().replace("+00:00", "Z")
    fresh = db.rows("SELECT title, org, country, score, url, flags FROM offers WHERE active = 1 AND first_seen >= ? "
                    "ORDER BY score DESC LIMIT 25", (since,))
    top = db.rows("SELECT title, org, country, score, url, deadline FROM offers WHERE active = 1 AND status IN ('new', 'shortlisted') "
                  "ORDER BY score DESC LIMIT 15")
    todo = db.rows("SELECT a.stage, a.subject, o.title, o.org FROM applications a LEFT JOIN offers o ON o.id = a.offer_id "
                   "WHERE a.stage IN ('to_review', 'ready', 'follow_up') ORDER BY a.updated_at DESC LIMIT 20")
    lines = [f"# Internship Radar — {today}", ""]
    scout = summary.get("scout") or {}
    if scout:
        lines += [f"Scanned **{scout.get('sources', 0)}** sources ({scout.get('failed', 0)} unavailable): "
                  f"**{scout.get('kept', 0)}** matching offers, **{scout.get('new', 0)}** new.", ""]
    lines += ["## New since yesterday", ""] + ([f"- **{item['score']}** — [{item['title']}]({item['url']}) · {item['org']} · {item['country'] or 'remote'}" for item in fresh] or ["- Nothing new."])
    lines += ["", "## Best open matches", ""] + [f"- **{item['score']}** — [{item['title']}]({item['url']}) · {item['org']} · {item['country'] or 'remote'}"
                                                + (f" · deadline {item['deadline']}" if item["deadline"] else "") for item in top]
    lines += ["", "## Waiting for you", ""] + ([f"- {item['stage'].replace('_', ' ')}: {item['title'] or item['subject']}"
                                               + (f" — {item['org']}" if item["org"] else "") for item in todo] or ["- No pending drafts."])
    path = config.DIGEST_DIR / f"{today}.md"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    log(f"Digest written to {path.relative_to(config.ROOT_DIR)}")
    return {"digest": str(path.relative_to(config.ROOT_DIR)), "fresh": len(fresh)}


# ------------------------------------------------------------------ Orchestra
def run_pipeline(agents: Optional[Iterable[str]] = None, *, trigger: str = "manual", force: bool = False,
                 only_sources: Optional[Iterable[str]] = None) -> dict[str, Any]:
    """Run the selected agents in order.  Only one run can be active at a time."""
    selected = [agent for agent in AGENTS if not agents or agent in set(agents)]
    if not _run_lock.acquire(blocking=False):
        return {"status": "busy", "message": "A run is already in progress."}
    db.initialize()
    run_id = db.execute("INSERT INTO runs(started_at, status, trigger) VALUES(?, 'running', ?)", (db.now(), trigger))
    _state.update(running=True, run_id=run_id, agent=None, progress="starting", started_at=db.now())
    log = RunLog(run_id)
    summary: dict[str, Any] = {"agents": selected}
    outcome = "ok"
    try:
        steps = {
            "scout": lambda: run_scout(log.for_agent("scout"), only_sources),
            "analyst": lambda: run_analyst(log.for_agent("analyst")),
            "contacts": lambda: run_contacts(log.for_agent("contacts"), force),
            "directory": lambda: run_directory(log.for_agent("directory"), force),
            "writer": lambda: run_writer(log.for_agent("writer")),
            "followup": lambda: run_followup(log.for_agent("followup")),
            "reporter": lambda: run_reporter(log.for_agent("reporter"), summary),
        }
        for agent in selected:
            try:
                summary[agent] = steps[agent]()
            except Exception as exc:
                outcome = "partial"
                log(agent, f"Agent stopped: {type(exc).__name__}: {exc}", "error")
                traceback.print_exc()
    finally:
        db.execute("UPDATE runs SET finished_at = ?, status = ?, summary = ? WHERE id = ?",
                   (db.now(), outcome, json.dumps(summary, ensure_ascii=False), run_id))
        db.set_setting("last_run_at", db.now())
        _state.update(running=False, agent=None, progress="idle")
        _run_lock.release()
    return {"status": outcome, "run_id": run_id, **summary}


def start_background(agents: Optional[Iterable[str]] = None, *, trigger: str = "manual", force: bool = False) -> bool:
    if _state["running"]:
        return False
    threading.Thread(target=run_pipeline, args=(list(agents) if agents else None,),
                     kwargs={"trigger": trigger, "force": force}, daemon=True).start()
    return True


def schedule_loop(stop: threading.Event) -> None:
    """While the app is open, refresh on the configured cadence."""
    while not stop.wait(120):
        hours = float(db.priorities().get("refresh_hours") or 0)
        last = db.get_setting("last_run_at")
        if not hours or _state["running"]:
            continue
        if not last or datetime.now(timezone.utc) - datetime.fromisoformat(last.replace("Z", "+00:00")) > timedelta(hours=hours):
            start_background(trigger="schedule")


def tick(log=print) -> dict[str, Any]:
    """Background heartbeat (every 15 minutes): send what is due, read replies, notify, and catch up a missed daily scan."""
    db.initialize()
    result: dict[str, Any] = {"sent": sum(item["sent"] for item in mailer.send_due())}
    if inbox.due(30):
        try:
            result["inbox"] = inbox.check(log)
        except Exception as exc:  # the mailbox must never stop the sender
            result["inbox_error"] = str(exc)
    result["notified"] = notify.events(log) + int(notify.summary(log))
    # A run left "running" by a closed window is not running any more.
    stale = (datetime.now(timezone.utc) - timedelta(hours=2)).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    db.execute("UPDATE runs SET status = 'interrupted', finished_at = started_at WHERE status = 'running' AND started_at < ?", (stale,))
    last = db.get_setting("last_run_at")
    overdue = not last or datetime.now(timezone.utc) - datetime.fromisoformat(last.replace("Z", "+00:00")) > timedelta(hours=24)
    if overdue and not db.one("SELECT 1 AS x FROM runs WHERE status = 'running'"):
        result["catch_up"] = run_pipeline(trigger="catch-up").get("status")
    return result
