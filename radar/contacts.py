"""Contact discovery from public, verifiable sources.

Three kinds of people are collected, each with the evidence that justifies it:

* ``offer``  — supervisors and contact addresses printed in a live offer;
* ``alumni`` — researchers who have published with an ENP Algiers affiliation
  and are now based in a target country (OpenAlex affiliation history);
* ``expert`` — authors of recent papers on the candidate's exact topics at
  institutions in target countries (OpenAlex works search).

Nothing is inferred from a person's name, and no e-mail address is ever
guessed: an address is stored only when it is published on a page whose URL is
kept next to it.
"""

from __future__ import annotations

import json
import re
from datetime import date, timedelta
from typing import Any, Callable, Iterable, Optional
from urllib.parse import urlparse

from . import db, net
from .text import clean, extract_emails, fold

OPENALEX = "https://api.openalex.org"
AI_TERMS = re.compile(
    r"language|linguist|nlp|text|speech|retrieval|semantic|learning|neural|artificial intelligence|data mining|"
    r"machine|recommend|knowledge|vision|pattern|classification|big data|explain|topic model|sentiment|"
    r"information|ontolog|reasoning|generative|transformer",
    re.I,
)
Log = Callable[[str], None]


def _recent_year(author: dict[str, Any]) -> int:
    years = [int(item.get("year", 0)) for item in author.get("counts_by_year") or [] if item.get("works_count")]
    return max(years) if years else 0


def _topic_relevance(topics: Iterable[dict[str, Any]]) -> tuple[int, list[str]]:
    score, names = 0, []
    for index, topic in enumerate(list(topics)[:8]):
        name = clean(topic.get("display_name"))
        field = clean((topic.get("field") or {}).get("display_name"))
        subfield = clean((topic.get("subfield") or {}).get("display_name"))
        weight = 3 if index < 3 else 1
        if subfield in ("Artificial Intelligence", "Information Systems", "Computer Vision and Pattern Recognition",
                        "Human-Computer Interaction", "Computational Theory and Mathematics"):
            score += 9 * weight
        elif field in ("Computer Science", "Decision Sciences", "Mathematics"):
            score += 5 * weight
        if AI_TERMS.search(name):
            score += 4 * weight
        names.append(name)
    return min(100, score), names[:5]


def harvest_alumni(log: Log) -> tuple[int, bool]:
    """Researchers with an ENP-Algiers publication affiliation now working in a target country."""
    priorities = db.priorities()
    institutions: dict[str, str] = priorities.get("alumni_institutions") or {}
    allowed = [code for tier in priorities["tiers"].values() for code in tier["countries"]]
    this_year = date.today().year
    created = 0
    for institution_id, institution_name in institutions.items():
        for chunk_start in range(0, len(allowed), 12):
            codes = "|".join(allowed[chunk_start:chunk_start + 12])
            cursor = "*"
            while cursor:
                try:
                    page = net.get_json(f"{OPENALEX}/authors", params={
                        "filter": f"affiliations.institution.id:{institution_id},last_known_institutions.country_code:{codes}",
                        "per-page": 200, "cursor": cursor,
                        "select": "id,display_name,orcid,works_count,cited_by_count,last_known_institutions,topics,counts_by_year",
                    })
                except net.FetchError as exc:
                    log(f"OpenAlex alumni query stopped: {exc}")
                    return created, False
                with db.write() as connection:
                    for author in page.get("results", []):
                        relevance, topics = _topic_relevance(author.get("topics") or [])
                        if relevance < 20 or _recent_year(author) < this_year - 3:
                            continue
                        home = next((inst for inst in author.get("last_known_institutions") or []
                                     if inst.get("country_code") in allowed), None)
                        if not home:
                            continue
                        _, was_new = db.upsert_contact(connection, {
                            "name": clean(author["display_name"]), "role": "Researcher",
                            "org": clean(home.get("display_name")), "country": home.get("country_code"),
                            "url": author["id"], "orcid": (author.get("orcid") or "").replace("https://orcid.org/", "") or None,
                            "openalex_id": author["id"].rsplit("/", 1)[-1], "kind": "alumni", "topics": topics,
                            "evidence": [{"type": "affiliation", "text": f"Has published with an affiliation at {institution_name} (OpenAlex affiliation history)", "url": author["id"]}],
                            "relevance": min(100, relevance + 15),
                        })
                        created += was_new
                cursor = (page.get("meta") or {}).get("next_cursor") if len(page.get("results", [])) == 200 else None
    log(f"Alumni network: {created} new researcher(s) with an ENP Algiers publication affiliation.")
    return created, True


def harvest_experts(log: Log) -> tuple[int, bool]:
    """Authors of recent papers on the candidate's topics at institutions in target countries."""
    priorities = db.priorities()
    tiers = priorities["tiers"]
    groups = {
        "tier1": tiers["tier1"]["countries"],
        "tier2": tiers["tier2"]["countries"],
        "tier3": tiers["tier3"]["countries"][:12],
        "mena": tiers["mena"]["countries"],
    }
    since = (date.today() - timedelta(days=540)).isoformat()
    people: dict[str, dict[str, Any]] = {}
    for topic in priorities.get("expert_topics") or []:
        for tier, codes in groups.items():
            try:
                page = net.get_json(f"{OPENALEX}/works", params={
                    "search": topic,
                    "filter": f"authorships.institutions.country_code:{'|'.join(codes)},from_publication_date:{since}",
                    "per-page": 40 if tier == "tier1" else 25,
                    "select": "id,title,publication_date,authorships,doi",
                })
            except net.FetchError as exc:
                log(f"OpenAlex topic search stopped at “{topic}” ({exc}) — the rest is retried on the next run.")
                return _store_experts(people, log), False
            for work in page.get("results", []):
                title = clean(work.get("title"))
                if not title or not AI_TERMS.search(title):
                    continue
                authorships = work.get("authorships") or []
                for authorship in authorships:
                    position = authorship.get("author_position")
                    # Last and corresponding authors are the people who run the group and take interns.
                    if position != "last" and not authorship.get("is_corresponding"):
                        continue
                    home = next((inst for inst in authorship.get("institutions") or []
                                 if inst.get("country_code") in codes), None)
                    author = authorship.get("author") or {}
                    if not home or not author.get("id"):
                        continue
                    record = people.setdefault(author["id"], {
                        "name": clean(author.get("display_name")), "org": clean(home.get("display_name")),
                        "country": home.get("country_code"), "orcid": (author.get("orcid") or "").replace("https://orcid.org/", "") or None,
                        "openalex_id": author["id"].rsplit("/", 1)[-1], "url": author["id"],
                        "topics": [], "evidence": [], "senior": False, "tier": tier,
                        "rank": (priorities.get("expert_topics") or []).index(topic),
                    })
                    if topic not in record["topics"]:
                        record["topics"].append(topic)
                    if len(record["evidence"]) < 4 and title not in [item["text"] for item in record["evidence"]]:
                        record["evidence"].append({
                            "type": "paper", "text": title, "year": (work.get("publication_date") or "")[:4],
                            "url": work.get("doi") or work.get("id"), "topic": topic,
                        })
                    record["senior"] = record["senior"] or position == "last"
    return _store_experts(people, log), True


def _store_experts(people: dict[str, dict[str, Any]], log: Log) -> int:
    created = 0
    with db.write() as connection:
        for record in people.values():
            # Topics are listed by priority in the settings, regions by tier: both shape the order.
            relevance = min(100, 30 + 9 * len(record["evidence"]) + 6 * len(record["topics"])
                            + (12 if record["senior"] else 0) + max(0, 14 - 3 * record["rank"])
                            + {"tier1": 14, "tier2": 9, "mena": 7, "tier3": 3}.get(record["tier"], 0))
            _, was_new = db.upsert_contact(connection, {
                "name": record["name"], "role": "Senior author / group lead" if record["senior"] else "Researcher",
                "org": record["org"], "country": record["country"], "url": record["url"], "orcid": record["orcid"],
                "openalex_id": record["openalex_id"], "kind": "expert", "topics": record["topics"],
                "evidence": record["evidence"], "relevance": relevance,
            })
            created += was_new
    log(f"Topic experts: {len(people)} active researcher(s) matched, {created} new.")
    return created


# ------------------------------------------------------------ e-mail lookup
_ARXIV_ID = re.compile(r"arxiv[.:/]+(?:abs/|pdf/|html/)?(\d{4}\.\d{4,5})", re.I)
_PUBLIC_MAIL = ("gmail.com", "outlook.com", "hotmail.com", "yahoo.com", "yahoo.fr", "protonmail.com", "proton.me")
_SECOND_LEVEL = {"ac", "co", "edu", "gov", "org", "com", "net"}


def _name_matches(email: str, name: str) -> bool:
    """True when the mailbox name clearly belongs to ``name`` (and not to a co-author with the same surname)."""
    local = re.sub(r"[^a-z]", "", fold(email.split("@")[0]))
    parts = [part for part in re.split(r"[\s.-]+", fold(name)) if part]
    if len(parts) < 2 or not local:
        return False
    first, last = parts[0], parts[-1]
    if len(first) >= 3 and first in local and (last in local or (len(last) >= 4 and last[:4] in local)):
        return True
    if local in (first[0] + last, last + first[0], first + last[0]):
        return True
    return len(last) >= 6 and last in local


def _site_label(host: str) -> str:
    """Organisation label of a host name: ``www.eurecom.fr`` → ``eurecom``, ``cs.ox.ac.uk`` → ``ox``."""
    parts = [part for part in host.lower().split(".") if part and part != "www"]
    if len(parts) >= 3 and parts[-2] in _SECOND_LEVEL:
        return parts[-3]
    return parts[-2] if len(parts) >= 2 else (parts[0] if parts else "")


def _plausible_address(email: str, source_url: str, org: str) -> bool:
    """Accept an address only if its domain belongs to the page it was found on or to the employer.

    Personal pages often print decoys (``name@myinstitutionname.fr``); an address whose domain
    matches neither the hosting site nor the organisation is treated as one of those.
    """
    domain = email.split("@", 1)[1].lower()
    if domain in _PUBLIC_MAIL:
        return True
    label = _site_label(domain)
    if len(label) < 2:
        return False
    if label == _site_label(urlparse(source_url).netloc):
        return True
    return len(label) >= 4 and label in re.sub(r"[^a-z0-9]", "", fold(org))


def _arxiv_ids(contact: dict[str, Any]) -> list[str]:
    """arXiv identifiers of the person's recent papers (from stored evidence, then from OpenAlex)."""
    found: list[str] = []

    def collect(text: object) -> None:
        for match in _ARXIV_ID.finditer(str(text or "")):
            if match.group(1) not in found:
                found.append(match.group(1))

    evidence = contact.get("evidence") or []
    if isinstance(evidence, str):
        try:
            evidence = json.loads(evidence)
        except json.JSONDecodeError:
            evidence = []
    for item in evidence:
        collect(item.get("url"))
    if len(found) < 2 and contact.get("openalex_id"):
        since = (date.today() - timedelta(days=1100)).isoformat()
        try:
            page = net.get_json(f"{OPENALEX}/works", params={
                "filter": f"author.id:{contact['openalex_id']},from_publication_date:{since}",
                "per-page": 25, "sort": "publication_date:desc", "select": "id,doi,locations"})
        except net.FetchError:
            page = {}
        for work in page.get("results", []):
            collect(work.get("doi"))
            for location in work.get("locations") or []:
                collect(location.get("landing_page_url"))
                collect(location.get("pdf_url"))
    return found[:4]


def _email_from_papers(contact: dict[str, Any]) -> Optional[tuple[str, str]]:
    """Authors print their contact address on their papers; read it from the arXiv HTML edition."""
    for arxiv_id in _arxiv_ids(contact):
        url = f"https://arxiv.org/html/{arxiv_id}"
        try:
            response = net.get(url, timeout=20, retries=0)
        except net.FetchError:
            continue
        for email in extract_emails(response.text[:300000]):
            # A leading digit is usually a footnote marker glued to the address: not trustworthy.
            if not email[0].isdigit() and _name_matches(email, contact["name"]):
                return email, url
    return None


def find_public_email(contact: dict[str, Any]) -> Optional[tuple[str, str]]:
    """Look for an address the person has published themselves.  Returns (email, source_url)."""
    name, org = contact["name"], contact.get("org") or ""
    if contact.get("orcid"):
        try:
            person = net.get_json(f"https://pub.orcid.org/v3.0/{contact['orcid']}/person", headers={"Accept": "application/json"})
            for item in ((person.get("emails") or {}).get("email") or []):
                if item.get("email"):
                    return item["email"].lower(), f"https://orcid.org/{contact['orcid']}"
            pages = [clean((item.get("url") or {}).get("value")) for item in ((person.get("researcher-urls") or {}).get("researcher-url") or [])]
        except net.FetchError:
            pages = []
        for page in pages[:3]:
            found = _email_on_page(page, name, org)
            if found:
                return found, page
    return _email_from_papers(contact)


def _email_on_page(url: str, name: str, org: str = "") -> Optional[str]:
    if not url.startswith("http") or url.lower().endswith(".pdf"):
        return None
    try:
        response = net.get(url, timeout=15, retries=0)
    except net.FetchError:
        return None
    if "html" not in response.headers.get("content-type", "") or fold(name.split()[-1]) not in fold(response.text[:400000]):
        return None
    for email in extract_emails(response.text[:400000].replace("mailto:", " ")):
        if _name_matches(email, name) and _plausible_address(email, url, org):
            return email
    return None


def enrich_contact(contact_id: int) -> dict[str, Any]:
    contact = db.one("SELECT * FROM contacts WHERE id = ?", (contact_id,))
    if contact is None:
        raise KeyError(contact_id)
    if contact.get("email"):
        return {"email": contact["email"], "source": contact.get("email_source"), "found": True}
    result = find_public_email(contact)
    if result is None:
        if net.is_cooling_down("api.openalex.org"):
            # The publication list could not be read (daily quota): inconclusive, so try again later.
            return {"found": False, "retry": True}
        db.execute("UPDATE contacts SET notes = coalesce(notes, '') || ? WHERE id = ? AND coalesce(notes, '') NOT LIKE '%No public e-mail found%'",
                   (f"\nNo public e-mail found automatically on {date.today().isoformat()} — check the profile page.", contact_id))
        return {"found": False}
    email, source = result
    db.execute("UPDATE contacts SET email = ?, email_source = ? WHERE id = ?", (email, source, contact_id))
    return {"email": email, "source": source, "found": True}


def auto_enrich(limit: int, log: Log) -> int:
    """Look up published addresses for the most relevant contacts still missing one."""
    pending = db.rows(
        "SELECT id, name FROM contacts WHERE email IS NULL AND status != 'ignored' AND kind != 'offer' "
        "AND coalesce(notes, '') NOT LIKE '%No public e-mail found%' "
        "ORDER BY (evidence LIKE '%arxiv%') DESC, relevance DESC LIMIT ?", (limit,))
    found = 0
    for contact in pending:
        try:
            found += bool(enrich_contact(contact["id"]).get("found"))
        except Exception as exc:  # A lookup must never break a run.
            log(f"E-mail lookup failed for {contact['name']}: {exc}")
    if pending:
        log(f"E-mail lookup: {found}/{len(pending)} published address(es) found.")
    return found
