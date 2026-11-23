"""Your own researcher list, and researchers who work like the ones on it.

``import_spreadsheet`` loads a list you keep in Excel (name, e-mail, lab, research
areas, outreach history) into the People page.  The outreach history is kept, so
nobody you already wrote to is e-mailed again by mistake.

``find_similar`` resolves each listed researcher in OpenAlex, builds the research
profile of the whole list (the topics they publish on), and looks for other active
researchers in your target countries whose publications cover the same topics.
Every match carries the evidence: which of your researchers they resemble and the
shared topics.  Nothing is inferred from a person's name.
"""

from __future__ import annotations

import json
import math
import re
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Optional

from . import db, net
from .contacts import OPENALEX
from .text import clean, detect_countries, fold

Log = Callable[..., None]
SELECT = "id,display_name,orcid,works_count,cited_by_count,last_known_institutions,topics,counts_by_year"
# Columns written by your Google Apps Script mailer: these are YOUR sends.  The older
# Status / Contacted / Follow-up / Replied columns belong to someone else's earlier
# outreach, so they are kept as notes and never block or mark anything.
OWN_SEND_COLUMNS = ("result", "sent_on")


# ------------------------------------------------------------------ import
def _iso_date(value: Any) -> str:
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return clean(value)[:10]


def read_spreadsheet(path: Path) -> list[dict[str, Any]]:
    """Rows of the first sheet whose header names a researcher and an e-mail."""
    import openpyxl

    workbook = openpyxl.load_workbook(path, data_only=True, read_only=True)
    try:
        rows = iter(list(workbook.worksheets[0].iter_rows(values_only=True)))
    finally:
        workbook.close()  # read-only workbooks keep the file locked until closed
    header = [clean(cell).lower() for cell in next(rows)]

    def column(*names: str) -> Optional[int]:
        for name in names:
            for index, title in enumerate(header):
                if title == name or (len(name) > 3 and name in title):
                    return index
        return None

    name_col = column("name", "nom", "researcher", "professor", "w") or 0
    columns = {key: column(*aliases) for key, aliases in {
        "email": ("email", "e-mail", "mail"), "title": ("title", "titre"), "university": ("university", "université", "institution"),
        "lab": ("laboratory", "laboratoire", "lab"), "country": ("country", "pays"), "city": ("city", "ville"),
        "areas": ("research areas", "domaines", "topics"), "profile": ("profile url", "profil"), "website": ("website", "site"),
        "match": ("match %", "match"), "status": ("status", "statut"), "contacted": ("contacted",), "follow_up": ("follow-up",),
        "replied": ("replied", "réponse"), "notes": ("notes",), "sent_on": ("date d’envoi", "date d'envoi", "sent on"),
        "result": ("résultat envoi",), "send_error": ("erreur envoi",), "language": ("langue email", "language"),
    }.items()}
    people = []
    for row in rows:
        if not row or not clean(row[name_col] if name_col < len(row) else ""):
            continue
        get = lambda key: row[columns[key]] if columns.get(key) is not None and columns[key] < len(row) else None  # noqa: E731
        people.append({"name": clean(row[name_col]), **{key: get(key) for key in columns}})
    return people


def import_spreadsheet(path: Path, log: Log = print) -> dict[str, int]:
    people = read_spreadsheet(path)
    label = Path(path).name
    created = updated = 0
    with db.write() as connection:
        for person in people:
            email = clean(person.get("email")).lower() or None
            if email and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[a-z]{2,}", email):
                email = None
            org = ", ".join(part for part in (clean(person.get("lab")), clean(person.get("university"))) if part and part != "None")
            country_found = detect_countries(clean(person.get("country")))
            title = clean(person.get("title"))
            role = title if title and title.lower() not in ("monsieur", "madame", "mr", "mrs", "ms", "dr") else "Researcher"
            areas = [clean(area) for area in re.split(r"[,;]", clean(person.get("areas"))) if clean(area)]
            result = clean(person.get("result"))
            sent_on = _iso_date(person.get("sent_on"))
            you_sent = result.lower().startswith("envoy") and bool(re.match(r"\d{4}-\d{2}-\d{2}", sent_on))
            history = [f"From your list “{label}”."]
            if you_sent:
                history.append(f"You e-mailed them with your Apps Script on {sent_on} ({result.split('–')[-1].strip() or 'initial'}).")
            elif result and result != "None":
                reason = clean(person.get("send_error"))
                history.append(f"Your Apps Script skipped them ({result}{': ' + reason if reason and reason != 'None' else ''}).")
            earlier = clean(person.get("status"))
            if earlier and earlier != "None":
                dates = [f"{text} {_iso_date(person.get(key))}" for key, text in (("contacted", "contacted"), ("follow_up", "follow-up"), ("replied", "replied"))
                         if re.match(r"\d{4}-\d{2}-\d{2}", _iso_date(person.get(key)))]
                history.append(f"Earlier outreach by someone else (Status column): {earlier}" + (f" — {', '.join(dates)}" if dates else "") + ".")
            note = clean(person.get("notes"))
            notes = " ".join(history) + (f"\nYour note: {note}" if note and note != "None" else "")
            values = {
                "name": person["name"], "role": role, "org": org or None, "country": country_found[0] if country_found else None,
                "email": email, "email_source": f"your list ({label})" if email else None, "email_verified": 1 if email else 0,
                "url": clean(person.get("profile")) or clean(person.get("website")) or None, "kind": "imported",
                "topics": areas, "evidence": [{"type": "list", "text": f"On your list: {org or 'no lab given'}" + (f" — {', '.join(areas[:4])}" if areas else ""), "url": clean(person.get("profile")) or None}],
                "relevance": int(float(person.get("match") or 0)) if str(person.get("match") or "").replace(".", "", 1).isdigit() else 60,
            }
            existing = connection.execute("SELECT id FROM contacts WHERE lower(email) = ? OR key = ?",
                                          (email or "~", db.contact_key(person["name"], org))).fetchone()
            if existing:
                fields = ", ".join(f"{field} = ?" for field in ("role", "org", "country", "email", "email_source", "url", "topics", "evidence"))
                connection.execute(f"UPDATE contacts SET {fields}, kind = 'imported', last_seen = ? WHERE id = ?",
                                   (values["role"], values["org"], values["country"], values["email"], values["email_source"], values["url"],
                                    json.dumps(areas, ensure_ascii=False), json.dumps(values["evidence"], ensure_ascii=False), db.now(), existing["id"]))
                contact_id = existing["id"]
                updated += 1
            else:
                contact_id, _ = db.upsert_contact(connection, values)
                created += 1
            source_row = {"title": title, "lab": clean(person.get("lab")), "university": clean(person.get("university")),
                          "areas": clean(person.get("areas")), "notes": note if note != "None" else "", "sheet_language": clean(person.get("language"))}
            # Status reflects only your own sends; "declined"/"replied" set later in the app are kept.
            connection.execute(
                "UPDATE contacts SET notes = ?, source_row = ?, status = CASE WHEN status IN ('replied', 'declined', 'ignored') THEN status "
                "WHEN ? THEN 'contacted' ELSE 'new' END WHERE id = ?",
                (notes, json.dumps(source_row, ensure_ascii=False), 1 if you_sent else 0, contact_id))
            if you_sent:
                _record_script_send(connection, contact_id, email, sent_on, result)
    log(f"Imported {created} new and refreshed {updated} existing researcher(s) from {label}.")
    return {"created": created, "updated": updated, "total": len(people)}


def _record_script_send(connection, contact_id: int, email: Optional[str], sent_on: str, result: str) -> None:
    """Put an e-mail you sent with the Apps Script on the pipeline, so its follow-up is tracked."""
    if connection.execute("SELECT 1 FROM applications WHERE contact_id = ? AND sent_at = ?", (contact_id, sent_on)).fetchone():
        return
    days = int(db.priorities().get("follow_up_days") or 10)
    follow_up = (datetime.fromisoformat(sent_on) + timedelta(days=days)).date().isoformat()
    stamp = db.now()
    connection.execute(
        """INSERT INTO applications(offer_id, contact_id, kind, stage, language, recipient, subject, body, letter, generator,
                                    sent_at, follow_up_at, sent_from, notes, created_at, updated_at)
           VALUES(NULL, ?, 'spontaneous', 'sent', 'EN', ?, ?, '', '', 'apps-script', ?, ?, ?, ?, ?, ?)""",
        (contact_id, email or "", "Application – Final-year internship (PFE) in Data Science & AI – ENP Algiers (2026–2027)",
         sent_on, follow_up, "your Google Apps Script", f"Sent outside the Radar ({result}); the text is not stored here.", stamp, stamp))


# --------------------------------------------------------- OpenAlex lookup
def _same_name(a: str, b: str) -> bool:
    parts_a, parts_b = set(re.findall(r"[a-z]+", fold(a))), set(re.findall(r"[a-z]+", fold(b)))
    return len(parts_a & parts_b) >= 2 or (len(parts_a) == 1 and parts_a <= parts_b)


def _topics(author: dict[str, Any]) -> dict[str, float]:
    """Topic id → weight (share of the author's works on that topic)."""
    topics = author.get("topics") or []
    total = sum(int(topic.get("count") or 1) for topic in topics[:15]) or 1
    return {topic["id"].rsplit("/", 1)[-1]: int(topic.get("count") or 1) / total for topic in topics[:15] if topic.get("id")}


def _topic_names(author: dict[str, Any]) -> dict[str, str]:
    return {topic["id"].rsplit("/", 1)[-1]: clean(topic.get("display_name")) for topic in (author.get("topics") or []) if topic.get("id")}


def _active(author: dict[str, Any], years: int = 3) -> bool:
    recent = date.today().year - years
    return any(int(item.get("year") or 0) >= recent and item.get("works_count") for item in author.get("counts_by_year") or [])


def resolve(log: Log = print, chunk: int = 20) -> int:
    """Find each listed researcher's OpenAlex profile (chunk names per query)."""
    pending = db.rows("SELECT id, name, org, country FROM contacts WHERE kind = 'imported' AND openalex_id IS NULL")
    resolved = 0
    for start in range(0, len(pending), chunk):
        batch = pending[start:start + chunk]
        names = "|".join(re.sub(r"[|,:']", " ", person["name"]) for person in batch)
        try:
            page = net.get_json(f"{OPENALEX}/authors", params={"filter": f"display_name.search:{names}", "per-page": 200, "select": SELECT})
        except net.FetchError as exc:
            log(f"OpenAlex look-up stopped: {exc}")
            break
        candidates = page.get("results", [])
        with db.write() as connection:
            for person in batch:
                org_words = set(re.findall(r"[a-z]{4,}", fold(person["org"] or "")))

                def fit(author: dict[str, Any]) -> float:
                    if not _same_name(person["name"], author.get("display_name", "")):
                        return -1
                    places = author.get("last_known_institutions") or []
                    score = math.log1p(author.get("works_count") or 0)
                    if person["country"] and any(place.get("country_code") == person["country"] for place in places):
                        score += 5
                    if org_words and any(org_words & set(re.findall(r"[a-z]{4,}", fold(place.get("display_name")))) for place in places):
                        score += 6
                    return score

                best = max(candidates, key=fit, default=None)
                if best is None or fit(best) < 5:  # name alone is not enough: the country or lab must match too
                    continue
                connection.execute("UPDATE contacts SET openalex_id = ?, orcid = coalesce(orcid, ?), profile_topics = ? WHERE id = ?",
                                   (best["id"].rsplit("/", 1)[-1], (best.get("orcid") or "").replace("https://orcid.org/", "") or None,
                                    json.dumps({"weights": _topics(best), "names": _topic_names(best)}, ensure_ascii=False), person["id"]))
                resolved += 1
    log(f"Publication profiles found for {resolved} of {len(pending)} researcher(s) on your list.")
    return resolved


# ------------------------------------------------------------ similarity
def _cosine(a: dict[str, float], b: dict[str, float]) -> float:
    shared = set(a) & set(b)
    if not shared:
        return 0.0
    dot = sum(a[key] * b[key] for key in shared)
    return dot / (math.sqrt(sum(v * v for v in a.values())) * math.sqrt(sum(v * v for v in b.values())))


TIER_QUOTA = {"tier1": 70, "tier2": 25, "mena": 25, "tier3": 40}  # how many look-alikes to keep per region
TIER_BONUS = {"tier1": 0.12, "tier2": 0.08, "mena": 0.06, "tier3": 0.0}


def _confirmed_home(author: dict[str, Any], codes: list[str]) -> Optional[dict[str, Any]]:
    """Current institution in a target country, confirmed by at least two recent publication years.

    OpenAlex sometimes merges two people with the same name; requiring the affiliation to recur
    filters out most of those mix-ups.  Company labs are skipped: your list is academic hosts.
    """
    recent = date.today().year - 3
    years_by_institution = {aff["institution"]["id"]: [y for y in aff.get("years") or [] if y >= recent]
                            for aff in author.get("affiliations") or [] if aff.get("institution")}
    for place in author.get("last_known_institutions") or []:
        if place.get("country_code") not in codes or place.get("type") == "company":
            continue
        if len(years_by_institution.get(place.get("id"), [])) >= 2:
            return place
    return None


def find_similar(tiers: dict[str, dict[str, Any]], log: Log = print) -> int:
    """Researchers in your target regions whose publication topics match people on your list."""
    seeds = []
    for row in db.rows("SELECT id, name, profile_topics FROM contacts WHERE kind = 'imported' AND profile_topics IS NOT NULL"):
        profile = json.loads(row["profile_topics"])
        if profile.get("weights"):
            seeds.append({"name": row["name"], **profile})
    if not seeds:
        log("No publication profile available for your list yet — run the look-up first.")
        return 0
    # The list's research profile: topics weighted by how many of your researchers work on them.
    popularity: Counter = Counter()
    names: dict[str, str] = {}
    for seed in seeds:
        for topic, weight in seed["weights"].items():
            popularity[topic] += weight
        names.update(seed["names"])
    top_topics = [topic for topic, _ in popularity.most_common(24)]
    known = {row["openalex_id"] for row in db.rows("SELECT openalex_id FROM contacts WHERE openalex_id IS NOT NULL")}
    by_tier: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
    for tier, setting in tiers.items():
        countries = setting["countries"]
        pages = 2 if tier == "tier1" else 1
        for codes in [countries[i:i + 9] for i in range(0, len(countries), 9)]:
            for topic_chunk in (top_topics[:12], top_topics[12:24]):
                for number in range(1, pages + 1):
                    try:
                        page = net.get_json(f"{OPENALEX}/authors", params={
                            "filter": f"topics.id:{'|'.join(topic_chunk)},last_known_institutions.country_code:{'|'.join(codes)},works_count:>15",
                            "sort": "cited_by_count:desc", "per-page": 200, "page": number, "select": SELECT + ",affiliations"})
                    except net.FetchError as exc:
                        log(f"OpenAlex search stopped: {exc}")
                        return _store_similar(_pick(by_tier), log)
                    for author in page.get("results", []):
                        author_id = author["id"].rsplit("/", 1)[-1]
                        if author_id in known or any(author_id in found for found in by_tier.values()) or not _active(author):
                            continue
                        home = _confirmed_home(author, codes)
                        if not home:
                            continue
                        weights = _topics(author)
                        best_seed, best_score = max(((seed, _cosine(weights, seed["weights"])) for seed in seeds), key=lambda item: item[1])
                        if best_score < 0.3:
                            continue
                        shared = sorted(set(weights) & set(best_seed["weights"]), key=lambda t: -best_seed["weights"][t])
                        by_tier[tier][author_id] = {"author": author, "home": home, "seed": best_seed["name"],
                                                    "score": min(1.0, best_score + TIER_BONUS.get(tier, 0)),
                                                    "shared": [names.get(t) or _topic_names(author).get(t, t) for t in shared[:4]]}
    return _store_similar(_pick(by_tier), log)


def _pick(by_tier: dict[str, dict[str, dict[str, Any]]]) -> dict[str, dict[str, Any]]:
    picked: dict[str, dict[str, Any]] = {}
    for tier, found in by_tier.items():
        best = sorted(found.items(), key=lambda item: -item[1]["score"])[: TIER_QUOTA.get(tier, 30)]
        picked.update(best)
    return picked


def _name_key(name: str) -> str:
    """Order-free name key; any punctuation (including Unicode hyphens) separates words."""
    return " ".join(sorted(re.findall(r"[a-z]+", fold(re.sub(r"[^\w]+", " ", name or "")))))


def _store_similar(found: dict[str, dict[str, Any]], log: Log) -> int:
    created = 0
    # OpenAlex can split one person into two profiles: never add someone already on your list.
    listed = {_name_key(row["name"]) for row in db.rows("SELECT name FROM contacts")}
    with db.write() as connection:
        for author_id, match in found.items():
            author, home = match["author"], match["home"]
            if _name_key(author["display_name"]) in listed:
                continue
            senior = (author.get("works_count") or 0) >= 60
            _, was_new = db.upsert_contact(connection, {
                "name": clean(author["display_name"]), "role": "Senior researcher / group lead" if senior else "Researcher",
                "org": clean(home.get("display_name")), "country": home.get("country_code"),
                "url": author["id"], "orcid": (author.get("orcid") or "").replace("https://orcid.org/", "") or None,
                "openalex_id": author_id, "kind": "similar",
                "topics": [name for name in _topic_names(author).values()][:5],
                "evidence": [{"type": "similar", "text": f"Publishes like {match['seed']} (on your list) — shared topics: {', '.join(match['shared'])}",
                              "url": author["id"]}],
                "relevance": min(100, int(40 + 60 * match["score"]) + (8 if senior else 0)),
            })
            created += was_new
            connection.execute("UPDATE contacts SET profile_topics = ? WHERE openalex_id = ?",
                               (json.dumps({"weights": _topics(author), "names": _topic_names(author)}, ensure_ascii=False), author_id))
    log(f"Similar researchers: {len(found)} matched, {created} new.")
    return created


# ------------------------------------------------------------ added by hand
_EMAIL = re.compile(r"[A-Za-z0-9._%+'-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_HONORIFIC = re.compile(r"^\s*(?:(prof(?:essor|esseur|esseure)?\.?|pr\.?)|(dr\.?|docteur)|(mme\.?|madame|mrs\.?|ms\.?)|(m\.|monsieur|mr\.?))\s+", re.I)
# What each choice in the form means for the greeting ("Monsieur le Professeur X," / "Dear Professor X,").
TITLES = {"prof_m": ("Monsieur, Professeur", "Professor"), "prof_f": ("Madame, Professeure", "Professor"),
          "prof": ("Professeur", "Professor"), "res_m": ("Monsieur", "Researcher"), "res_f": ("Madame", "Researcher"), "": ("", "")}


def _country_code(value: str, *hints: str) -> Optional[str]:
    value = clean(value)
    if re.fullmatch(r"[A-Za-z]{2}", value):
        return value.upper()
    for text in (value, *hints):
        found = detect_countries(text) if text else []
        if found:
            return found[0]
    return None


def add_person(values: dict[str, Any], log: Callable[..., None] = print) -> dict[str, Any]:
    """Add one researcher you found yourself.  Their address counts as checked by you."""
    name = re.sub(r"\s+", " ", clean(values.get("name")))
    honorific = _HONORIFIC.match(name)
    title_key = str(values.get("title") or "")
    if honorific:  # "Prof. Marie Curie" typed in the name box
        name = name[honorific.end():].strip()
        if not title_key:
            title_key = "prof" if honorific.group(1) else "res_f" if honorific.group(3) else "res_m" if honorific.group(4) else ""
    if len(name) < 3 or " " not in name:
        raise ValueError("Give the person's first and last name.")
    email = clean(values.get("email")).lower()
    if email and not re.fullmatch(_EMAIL.pattern, email):
        raise ValueError(f"“{email}” is not a valid e-mail address.")
    lab, university, city = clean(values.get("lab")), clean(values.get("university")), clean(values.get("city"))
    areas_text = clean(values.get("areas"))
    areas = [area for area in (clean(part) for part in re.split(r"[,;\n]", areas_text)) if area]
    url = clean(values.get("url"))
    if url and not re.match(r"^https?://", url):
        url = "https://" + url
    note = clean(values.get("note"))
    title, role = TITLES.get(title_key, TITLES[""])
    org = ", ".join(part for part in (lab, university) if part) or None
    country = _country_code(str(values.get("country") or ""), f"{university} {city}", lab)

    existing = db.one("SELECT * FROM contacts WHERE (? != '' AND lower(email) = ?) OR key = ?", (email, email, db.contact_key(name, org)))
    if existing:
        fills = {"email": email or None, "org": org, "country": country, "url": url or None}
        updates = {field: value for field, value in fills.items() if value and not existing.get(field)}
        if updates:
            db.execute(f"UPDATE contacts SET {', '.join(f'{field} = ?' for field in updates)} WHERE id = ?", (*updates.values(), existing["id"]))
        return {"id": existing["id"], "created": False, "name": existing["name"],
                "message": f"{existing['name']} is already in your researchers ({existing['status']})" + (" — missing details were added." if updates else ".")}

    today = date.today().isoformat()
    record = {
        "name": name, "role": role or None, "org": org, "country": country, "email": email or None,
        "email_source": "added by you" if email else None, "email_verified": 1 if email else 0, "url": url or None,
        "kind": "manual", "topics": areas, "relevance": 90, "confidence": 90, "role_verified": 1,
        "evidence": [{"type": "manual", "text": "Added by you" + (f" — {', '.join(areas[:4])}" if areas else ""), "url": url or None,
                      "retrieved_at": today}],
    }
    source_row = {"title": title, "lab": lab, "university": university or (city if not lab else ""), "areas": ", ".join(areas), "notes": note}
    with db.write() as connection:
        contact_id, _ = db.upsert_contact(connection, record)
        connection.execute("UPDATE contacts SET notes = ?, source_row = ?, status = 'new' WHERE id = ?",
                           (f"Added by you on {today}." + (f"\nYour note: {note}" if note else ""), json.dumps(source_row, ensure_ascii=False), contact_id))
    log(f"Added {name}" + (f" ({org})" if org else "") + " to your researchers.")
    return {"id": contact_id, "created": True, "name": name, "message": f"{name} added."}


def _is_country_name(field: str) -> bool:
    """“France”, “FR”, “Italia”: a field that is a country and nothing else (not “Inria Grenoble”)."""
    from .config import COUNTRY_NAMES

    if re.fullmatch(r"[A-Z]{2}", field.strip()):
        return field.strip() in COUNTRY_NAMES
    found = detect_countries(field)
    if len(found) != 1:
        return False
    rest = fold(field)
    for alias in (COUNTRY_NAMES.get(found[0], ""), field):
        rest = rest.replace(fold(alias), "")
    return len(rest.strip()) <= 3 and len(field) <= 25 and not re.search(r"univ|inst|lab|inria|cnrs|school|ecole|college", fold(field))


def parse_list(text: str) -> list[dict[str, str]]:
    """One person per line: a name, an e-mail address, and optionally the institution and country, in any order."""
    people = []
    for line in (text or "").splitlines():
        line = line.strip().strip("•-*").strip()
        if not line:
            continue
        found = _EMAIL.search(line)
        if not found:
            people.append({"line": line, "error": "no e-mail address on this line"})
            continue
        email = found.group(0)
        before, after = line[:found.start()], line[found.end():]
        fields = [clean(part).strip("<>()\"' ") for part in re.split(r"[,;\t|]", before + "\t" + after)]
        fields = [field for field in fields if field and field not in "<>"]
        name = clean(before).strip("<>()\"',;:\t ") or (fields[0] if fields else "")
        rest = [field for field in fields if field != name]
        country = next((field for field in reversed(rest) if _is_country_name(field)), "")
        institution = ", ".join(field for field in rest if field != country)
        people.append({"line": line, "name": name, "email": email, "university": institution, "country": country})
    return people


def add_list(text: str, log: Callable[..., None] = print) -> dict[str, Any]:
    added, existing, problems, ids = [], [], [], []
    for person in parse_list(text):
        if person.get("error"):
            problems.append({"line": person["line"], "reason": person["error"]})
            continue
        try:
            result = add_person(person, log=log)
        except ValueError as exc:
            problems.append({"line": person["line"], "reason": str(exc)})
            continue
        ids.append(result["id"])
        (added if result["created"] else existing).append(result["name"])
    return {"added": added, "existing": existing, "problems": problems, "ids": ids}
