"""Local web app: a small JSON API over the tracker plus the single-page interface.

Standard library only (``http.server``); bound to 127.0.0.1 so nothing is
exposed outside this machine.
"""

from __future__ import annotations

import json
import mimetypes
import re
import threading
import webbrowser
from datetime import date, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable, Optional
from urllib.parse import parse_qs, quote, urlparse

from . import __version__, config, contacts, db, mailer, pipeline, scoring, writer


class ApiError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


# ------------------------------------------------------------------- queries
OFFER_LIST_COLUMNS = (
    "id, source, source_kind, title, org, country, city, tier, url, lang, contract, posted_at, deadline, remote, "
    "score, flags, tags, status, active, first_seen, last_seen, (llm IS NOT NULL) AS reviewed, "
    "(SELECT count(*) FROM offer_contacts oc WHERE oc.offer_id = offers.id) AS contact_count, "
    "(SELECT a.stage FROM applications a WHERE a.offer_id = offers.id ORDER BY a.id DESC LIMIT 1) AS application_stage"
)


def api_state(_: dict) -> dict:
    priorities = db.priorities()
    floor = int(priorities.get("min_score") or 0)
    offers = db.one(
        "SELECT count(*) AS total, sum(score >= 85) AS grade_a, sum(score >= 72 AND score < 85) AS grade_b, "
        "sum(first_seen >= ?) AS fresh FROM offers WHERE active = 1 AND status != 'dismissed' AND score >= ?",
        ((date.today() - timedelta(days=2)).isoformat(), floor)) or {}
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
        "llm": writer.llm_status(), "closing": closing, "agents": list(pipeline.AGENTS), "mail": mailer.public_settings(),
    }


def api_offers(_: dict) -> dict:
    offers = db.rows(f"SELECT {OFFER_LIST_COLUMNS} FROM offers WHERE active = 1 OR status NOT IN ('new', 'dismissed') ORDER BY score DESC")
    for offer in offers:
        db.decode(offer, "flags", "tags")
    return {"offers": offers}


def api_offer(params: dict) -> dict:
    offer = db.decode(db.one("SELECT * FROM offers WHERE id = ?", (params["id"],)), "flags", "tags", "score_parts", "llm")
    if offer is None:
        raise ApiError(404, "Unknown offer.")
    offer["breakdown"] = scoring.describe_parts(offer["score_parts"] or {})
    offer["contacts"] = [db.decode(row, "topics", "evidence") for row in db.rows(
        "SELECT c.* FROM contacts c JOIN offer_contacts oc ON oc.contact_id = c.id WHERE oc.offer_id = ?", (offer["id"],))]
    employer = (offer.get("org") or "").split(" — ")[0].strip()
    offer["related_contacts"] = [db.decode(row, "topics", "evidence") for row in db.rows(
        "SELECT * FROM contacts WHERE status != 'ignored' AND kind IN ('alumni', 'expert') AND org LIKE ? "
        "ORDER BY relevance DESC LIMIT 4", (f"%{employer[:30]}%",))] if len(employer) >= 4 else []
    offer["applications"] = db.rows("SELECT id, stage, language, subject, updated_at FROM applications WHERE offer_id = ? ORDER BY id DESC", (offer["id"],))
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
        "FROM contacts c ORDER BY c.relevance DESC, c.name")
    for person in people:
        db.decode(person, "topics", "evidence")
    return {"contacts": people}


def api_contact_update(params: dict) -> dict:
    body = params["body"]
    allowed = {key: body[key] for key in ("status", "notes", "email", "role", "url") if key in body}
    if not allowed:
        raise ApiError(400, "Nothing to update.")
    if "email" in allowed:
        allowed["email"] = (allowed["email"] or "").strip() or None
        allowed["email_source"] = "entered manually" if allowed["email"] else None
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
    return {"applications": db.rows(
        """SELECT a.*, o.title AS offer_title, o.org AS offer_org, o.url AS offer_url, o.score AS offer_score,
                  o.country AS offer_country, o.deadline AS offer_deadline,
                  c.name AS contact_name, c.org AS contact_org, c.email AS contact_email
           FROM applications a LEFT JOIN offers o ON o.id = a.offer_id LEFT JOIN contacts c ON c.id = a.contact_id
           ORDER BY a.updated_at DESC""")}


def api_draft(params: dict) -> dict:
    body = params["body"]
    try:
        draft = writer.compose(offer_id=body.get("offer_id"), contact_id=body.get("contact_id"),
                               language=body.get("language"), use_llm=bool(body.get("use_llm", True)))
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
            draft["application_id"] = existing["id"]
            return draft
        draft["application_id"] = pipeline.save_application(offer_id=body.get("offer_id"), contact_id=body.get("contact_id"), draft=draft)
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


def api_application_delete(params: dict) -> dict:
    db.execute("DELETE FROM applications WHERE id = ?", (params["id"],))
    return {"ok": True}


def api_follow_up(params: dict) -> dict:
    application = db.one(
        "SELECT a.*, c.name AS contact_name FROM applications a LEFT JOIN contacts c ON c.id = a.contact_id WHERE a.id = ?", (params["id"],))
    if application is None:
        raise ApiError(404, "Unknown application.")
    return writer.follow_up_draft(application, application["language"], db.profile())


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
    ("GET", re.compile(r"^/api/offers/(?P<id>\d+)$"), api_offer),
    ("POST", re.compile(r"^/api/offers/(?P<id>\d+)$"), api_offer_update),
    ("GET", re.compile(r"^/api/contacts$"), api_contacts),
    ("POST", re.compile(r"^/api/contacts/(?P<id>\d+)$"), api_contact_update),
    ("POST", re.compile(r"^/api/contacts/(?P<id>\d+)/enrich$"), api_contact_enrich),
    ("GET", re.compile(r"^/api/directory$"), api_directory),
    ("GET", re.compile(r"^/api/applications$"), api_applications),
    ("POST", re.compile(r"^/api/applications/(?P<id>\d+)$"), api_application_update),
    ("DELETE", re.compile(r"^/api/applications/(?P<id>\d+)$"), api_application_delete),
    ("POST", re.compile(r"^/api/applications/(?P<id>\d+)/approve$"), api_application_approve),
    ("POST", re.compile(r"^/api/applications/(?P<id>\d+)/send-now$"), api_application_send_now),
    ("GET", re.compile(r"^/api/mail$"), api_mail_get),
    ("POST", re.compile(r"^/api/mail$"), api_mail_set),
    ("POST", re.compile(r"^/api/mail/test$"), api_mail_test),
    ("GET", re.compile(r"^/api/applications/(?P<id>\d+)/follow-up$"), api_follow_up),
    ("POST", re.compile(r"^/api/draft$"), api_draft),
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
            if method == "GET" and (match := re.match(r"^/api/applications/(?P<id>\d+)/(?P<kind>eml|letter)$", path)):
                payload, content_type, filename = eml_download(match.groupdict())
                disposition = "inline" if match["kind"] == "letter" else "attachment"
                self._send(200, payload, content_type, {"Content-Disposition": f'{disposition}; filename="{filename}"'})
                return
            if path.startswith("/api/"):
                body: dict = {}
                if method in ("POST", "DELETE"):
                    length = int(self.headers.get("Content-Length") or 0)
                    if length > 2_000_000:
                        raise ApiError(413, "Request too large.")
                    raw = self.rfile.read(length) if length else b""
                    body = json.loads(raw.decode("utf-8")) if raw.strip() else {}
                    if not isinstance(body, dict):
                        raise ApiError(400, "A JSON object is expected.")
                for route_method, pattern, handler in ROUTES:
                    match = pattern.match(path)
                    if match and route_method == method:
                        params = {**match.groupdict(), "query": parse_qs(parsed.query), "body": body}
                        self._json(200, handler(params))
                        return
                raise ApiError(404, "Unknown endpoint.")
            if method != "GET":
                raise ApiError(405, "Method not allowed.")
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
