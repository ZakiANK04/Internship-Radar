"""Connectors for research institutes and funded research-internship programmes."""

from __future__ import annotations

import re
from urllib.parse import urljoin

from lxml import etree, html as lxml_html

from .. import net
from ..scoring import is_internship_title, is_relevant_title
from ..text import clean, extract_emails, fold, html_to_text, parse_date
from .base import Context, RawOffer, Source

_NAME = r"[A-ZÀ-Ý][\w'’.-]+(?:[ -][A-ZÀ-Ý][\w'’.-]+){1,3}"
_SUPERVISOR = re.compile(
    r"(?:supervis(?:ed|ion) (?:by|of)(?: team leader)?|encadr(?:é|ée|ement)(?: par| de| :)?|advisors?\s*:|"
    r"supervisors?\s*:|encadrant(?:e|s|\(e\)s?)?\s*:|contact\s*:|responsable\s*:)\s*(?:(?:Dr|Prof|Pr|M|Mme)\.?\s+)?(" + _NAME + ")"
)


def _contacts_from_text(text: str, org: str, url: str) -> list[dict]:
    """Collect people explicitly named as contact/supervisor, with any published e-mail."""
    emails = extract_emails(text)
    people: list[dict] = []
    for match in _SUPERVISOR.finditer(text):
        name = clean(match.group(1)).strip(".,;")
        if len(name.split()) >= 2 and name not in [person["name"] for person in people]:
            people.append({"name": name, "role": "Supervisor named in the offer", "org": org, "url": url})
    for email in emails:
        local = fold(email.split("@")[0])
        owner = next((p for p in people if fold(p["name"].split()[-1]) in local.replace(".", " ").replace("-", " ")), None)
        if owner:
            owner.setdefault("email", email)
            owner["email_source"] = url
        elif re.fullmatch(r"[a-z]+[._-][a-z-]+", email.split("@")[0]):
            guess = " ".join(part.capitalize() for part in re.split(r"[._]", email.split("@")[0]))
            people.append({"name": guess, "role": "Contact e-mail published in the offer", "org": org,
                           "url": url, "email": email, "email_source": url})
    return people[:4]


# --------------------------------------------------------------------- Inria
def inria() -> Source:
    listing = "https://jobs.inria.fr/public/classic/en/offres"

    def parse(tree) -> list[dict]:
        items = []
        for node in tree.xpath("//li[contains(@class,'resultats')]"):
            link = node.xpath(".//h2/a")
            if not link:
                continue
            info = clean(" ".join(node.xpath(".//ul//text()")))
            items.append({
                "title": clean(link[0].text_content()),
                "href": link[0].get("href"),
                "city": clean((re.search(r"Town/city\s*:\s*(.*?)(?:Inria Team|Deadline|$)", info) or [None, ""])[1]),
                "team": clean((re.search(r"Inria Team\s*:\s*(.*?)(?:Deadline|$)", info) or [None, ""])[1]),
                "deadline": parse_date((re.search(r"Deadline to apply\s*:\s*([\d-]+)", info) or [None, ""])[1]),
            })
        return items

    def fetch(ctx: Context) -> list[RawOffer]:
        page = net.get(listing)
        tree = lxml_html.fromstring(page.text)
        found: dict[str, dict] = {}
        for item in parse(tree):
            if is_internship_title(item["title"]):
                found[item["href"]] = item
        token = tree.xpath("//input[@name='recherche_offre[_token]']/@value")
        for function in ("4", "7") if token else ():
            form = {"recherche_offre[motsClef]": "", "recherche_offre[fonctionOffre]": function,
                    "recherche_offre[domaineActivite]": "", "recherche_offre[themeOffre]": "",
                    "recherche_offre[typeContrat]": "", "recherche_offre[CRI]": "",
                    "recherche_offre[_token]": token[0]}
            try:
                filtered = lxml_html.fromstring(net.post(listing, data=form).text)
            except net.FetchError:
                continue
            for item in parse(filtered):
                item["trusted"] = True
                found[item["href"]] = item
        offers: list[RawOffer] = []
        for href, item in list(found.items())[: ctx.detail_cap * 3]:
            url = urljoin(listing, href)
            reference = href.rstrip("/").split("/")[-1]
            description, contacts = "", []
            try:
                detail = lxml_html.fromstring(net.get(url).text)
                main = detail.xpath("//main") or detail.xpath("//body")
                description = html_to_text(etree.tostring(main[0], encoding="unicode"))
                start = description.find("Context")
                description = description[start:] if start > 0 else description
                description = description.split("About Inria")[0]
                contacts = _contacts_from_text(description, f"Inria — {item['team']}" if item["team"] else "Inria", url)
            except (net.FetchError, etree.Error):
                pass
            team = f" — {item['team']} team" if item["team"] else ""
            offers.append(RawOffer(
                uid=f"inria:{reference}", title=item["title"], org=f"Inria{team}", url=url,
                location=f"{item['city']}, France", country_hint="FR", description=description,
                deadline=item["deadline"], trusted_internship=bool(item.get("trusted")), contacts=contacts,
            ))
        return offers

    return Source("inria", "research", "Inria", fetch, "Research institutes")


# ----------------------------------------------------------------------- CEA
# The CEA search treats a multi-word query as one phrase, so single keywords are used.
CEA_KEYWORDS = ("LLM", "IA", "learning", "apprentissage", "data", "données", "langage", "neurones")
CEA_FEEDS = ("Rss_Contract=579&Rss_Profile=1898", "Rss_Contract=579&Rss_Profile=1909", "Rss_Contract=579")


def cea() -> Source:
    site = "https://www.emploi.cea.fr"

    def fetch(ctx: Context) -> list[RawOffer]:
        found: dict[str, dict] = {}
        for query in CEA_FEEDS:
            try:
                feed = etree.fromstring(net.get(f"{site}/handlers/offerRss.ashx?LCID=1036&{query}").content)
            except (net.FetchError, etree.Error):
                continue
            for item in feed.findall(".//item"):
                summary = html_to_text(item.findtext("description"))
                raw_title = clean(item.findtext("title"))
                title = re.sub(r"^\d{4}-\d+\s*-\s*", "", raw_title)
                reference = (re.search(r"idOffre=(\d+)", item.findtext("link") or "") or [None, raw_title])[1]
                found.setdefault(reference, {"title": title, "url": clean(item.findtext("link")),
                                             "city": clean(item.findtext("category")), "summary": "Stage. " + summary,
                                             "posted": parse_date(item.findtext("pubDate"))})
        for keyword in CEA_KEYWORDS:
            # The result list is paged through a server-side session, hence the plain ?page=N follow-ups.
            for number in range(1, 5):
                try:
                    page = (net.get(f"{site}/offre-de-emploi/liste-offres.aspx", params={"mode": "list", "keywords": keyword})
                            if number == 1 else net.get(f"{site}/offre-de-emploi/liste-offres.aspx?page={number}&LCID=1036"))
                except net.FetchError:
                    break
                tree = lxml_html.fromstring(page.text)
                links = tree.xpath("//a[contains(@href,'/offre-de-emploi/emploi-')]")
                for link in links:
                    href = link.get("href")
                    reference = (re.search(r"_(\d+)\.aspx", href) or [None, href])[1]
                    title = clean(link.text_content()) or clean(link.get("title"))
                    if title and reference not in found:
                        found[reference] = {"title": title, "url": urljoin(site, href), "city": "", "summary": "", "posted": None}
                if len(links) < 10:
                    break
        offers: list[RawOffer] = []
        for reference, item in found.items():
            title = item["title"]
            if not (is_internship_title(title) or item["summary"].startswith("Stage")):
                continue
            if not (is_relevant_title(title, 0.5) or is_relevant_title(item["summary"][:500], 0.8)):
                continue
            if len(offers) >= ctx.detail_cap * 2:
                break
            description = item["summary"]
            try:
                detail = lxml_html.fromstring(net.get(item["url"]).text)
                body = html_to_text(etree.tostring((detail.xpath("//form") or detail.xpath("//body"))[0], encoding="unicode"))
                start = body.find("Description du poste")
                if start > 0:
                    description = body[start:start + 6000]
                    description = re.split(r"Postuler à cette offre|Envoyer à un ami|Partager cette offre", description)[0]
                if not item["city"]:
                    place = re.search(r"(?:Site|Lieu|Localisation du poste)\s*:?\s*\n?([^\n]{2,60})", body)
                    item["city"] = clean(place.group(1)) if place else ""
            except (net.FetchError, etree.Error, IndexError):
                pass
            offers.append(RawOffer(
                uid=f"cea:{reference}", title=title, org="CEA", url=item["url"],
                location=f"{item['city']}, France".strip(", "), country_hint="FR", description=description,
                posted_at=item["posted"], trusted_internship=True,
                contacts=_contacts_from_text(description, "CEA", item["url"]),
            ))
        return offers

    return Source("cea", "research", "CEA", fetch, "Research institutes")


# ---------------------------------------------------------------- KAUST VSRP
KAUST_PROGRAMS = re.compile(r"computer science|applied mathematics|statistics|electrical and computer", re.I)


def kaust_vsrp() -> Source:
    search = "https://admissions.kaust.edu.sa/internship/search"

    def fetch(ctx: Context) -> list[RawOffer]:
        tree = lxml_html.fromstring(net.get(search, timeout=45).text)
        offers: list[RawOffer] = []
        for link in tree.xpath("//a[contains(@href,'/internship/search/project/')]"):
            card = clean(link.text_content())
            title = clean((link.xpath(".//*[self::h2 or self::h3 or self::h4 or self::h5]") or [link])[0].text_content())
            if not (KAUST_PROGRAMS.search(card) or is_relevant_title(card, 0.8)):
                continue
            if len(offers) >= ctx.detail_cap * 2:
                break
            url = link.get("href")
            description = card
            try:
                detail = lxml_html.fromstring(net.get(url, timeout=45).text)
                main = detail.xpath("//main") or detail.xpath("//body")
                body = html_to_text(etree.tostring(main[0], encoding="unicode"))
                start = body.find("Project Details")
                description = body[start:start + 6000] if start >= 0 else body[:6000]
            except (net.FetchError, etree.Error):
                pass
            offers.append(RawOffer(
                uid=f"kaust:{url.rstrip('/').split('/')[-1][:120]}", title=f"VSRP research internship — {title}"[:240],
                org="KAUST — Visiting Student Research Program", url=url, location="Thuwal, Saudi Arabia",
                country_hint="SA",
                description=("KAUST Visiting Student Research Program: funded research internship of 2 to 6 months, "
                             "open year-round to senior bachelor's and master's students; the programme covers "
                             "housing, return flights and a monthly stipend.\n" + description),
                posted_at=parse_date(link.get("data-publish-date")), trusted_internship=True,
            ))
        return offers

    return Source("kaust-vsrp", "programme", "KAUST VSRP", fetch, "Research institutes")


# ----------------------------------------------------------------------- BSC
def bsc() -> Source:
    listing = "https://www.bsc.es/join-us/job-opportunities"

    def fetch(ctx: Context) -> list[RawOffer]:
        tree = lxml_html.fromstring(net.get(listing).text)
        offers: list[RawOffer] = []
        for link in tree.xpath("//a[contains(@href,'/join-us/job-opportunities/')]"):
            title = clean(link.text_content())
            student_level = bool(re.search(r"\(R0\)|undergraduate|master", title, re.I))
            if not title or not (is_internship_title(title) or student_level) or re.search(r"\bphd\b|predoctoral", title, re.I):
                continue
            url = urljoin(listing, link.get("href"))
            if any(offer.url == url for offer in offers) or len(offers) >= ctx.detail_cap:
                continue
            description = ""
            try:
                detail = lxml_html.fromstring(net.get(url).text)
                main = detail.xpath("//main") or detail.xpath("//div[@id='content']") or detail.xpath("//body")
                description = html_to_text(etree.tostring(main[0], encoding="unicode"))[:7000]
            except (net.FetchError, etree.Error):
                pass
            offers.append(RawOffer(
                uid=f"bsc:{url.rstrip('/').split('/')[-1]}", title=title, org="Barcelona Supercomputing Center",
                url=url, location="Barcelona, Spain", country_hint="ES", description=description,
                trusted_internship=True,
            ))
        return offers

    return Source("bsc", "research", "Barcelona Supercomputing Center", fetch, "Research institutes")


# ----------------------------------------------------------------------- FBK
def fbk() -> Source:
    listing = "https://jobs.fbk.eu/Internships/"

    def fetch(ctx: Context) -> list[RawOffer]:
        tree = lxml_html.fromstring(net.get(listing).text)
        offers: list[RawOffer] = []
        for link in tree.xpath("//a[contains(@href,'/Annunci/')]"):
            title = clean(link.text_content())
            if not title or not re.search(r"intern|thesis|tesi|stage|tirocinio", title, re.I):
                continue
            url = urljoin(listing, link.get("href"))
            if any(offer.url == url for offer in offers) or len(offers) >= ctx.detail_cap * 2:
                continue
            description = ""
            try:
                detail = lxml_html.fromstring(net.get(url).text)
                main = detail.xpath("//main") or detail.xpath("//article") or detail.xpath("//body")
                description = html_to_text(etree.tostring(main[0], encoding="unicode"))[:7000]
            except (net.FetchError, etree.Error):
                pass
            offers.append(RawOffer(
                uid=f"fbk:{url.rstrip('/').split('/')[-1][:120]}", title=title, org="Fondazione Bruno Kessler (FBK)",
                url=url, location="Trento, Italy", country_hint="IT", description=description,
                trusted_internship=True, contacts=_contacts_from_text(description, "Fondazione Bruno Kessler", url),
            ))
        return offers

    return Source("fbk", "research", "Fondazione Bruno Kessler", fetch, "Research institutes")


def all_sources() -> list[Source]:
    return [inria(), cea(), kaust_vsrp(), bsc(), fbk()]
