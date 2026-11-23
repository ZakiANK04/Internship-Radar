"""School, lab and network sources: reusable connector types plus the seeded list.

Four generic types cover most French lab and school postings, so a new lab can be
added from the app without code:

* ``recruitee`` — public Recruitee boards (e.g. Institut Mines-Télécom: Télécom
  SudParis, Télécom Paris, IMT schools);
* ``wordpress`` — lab sites built on WordPress, searched through their REST API;
* ``rss``       — any RSS/Atom feed (scientific societies, research networks);
* ``page``      — a listing page whose links point to offers (HTML pages or PDFs).

``linkedin()`` reads LinkedIn's public job search (the pages anyone can open
without signing in).  It never uses an account and stops at the first refusal.
"""

from __future__ import annotations

import io
import logging
import re
from typing import Any, Optional
from urllib.parse import quote, urljoin, urlparse

from lxml import etree, html as lxml_html

from .. import net
from ..scoring import is_internship_title, is_relevant_title
from ..text import clean, detect_city, detect_countries, html_to_text, parse_date
from .base import Context, RawOffer, Source
from .research import _contacts_from_text

logging.getLogger("pypdf").setLevel(logging.ERROR)  # malformed lab PDFs are common; their warnings are noise

BOARD_NAME = re.compile(r"\bboard\b|internships$|offers$|association|society", re.I)
INTERNSHIP_HINT = re.compile(r"stage|stagiaire|intern|internship|pfe|fin d.[ée]tudes|master|m2|thesis|tesi|praktikum", re.I)
PDF_PAGES = 4


# ----------------------------------------------------------------- helpers
def document_text(url: str) -> str:
    """Readable text of an offer page or PDF (first pages only)."""
    try:
        response = net.get(url, timeout=30, retries=0)
    except net.FetchError:
        return ""
    kind = response.headers.get("content-type", "").lower()
    if "pdf" in kind or url.lower().split("?")[0].endswith(".pdf"):
        try:
            from pypdf import PdfReader

            reader = PdfReader(io.BytesIO(response.content))
            return "\n".join((page.extract_text() or "") for page in reader.pages[:PDF_PAGES])[:12000]
        except Exception:  # Damaged or scanned PDFs: keep the title only.
            return ""
    if "html" not in kind:
        return ""
    try:
        tree = lxml_html.fromstring(response.text)
    except (etree.ParserError, ValueError):
        return ""
    for node in tree.xpath("//script|//style|//nav|//header|//footer|//aside"):
        node.drop_tree()
    main = tree.xpath("//main") or tree.xpath("//article") or tree.xpath("//*[@id='content']") or tree.xpath("//body")
    return html_to_text(etree.tostring(main[0], encoding="unicode"))[:12000] if main else ""


def _place(text: str, default_city: str, default_country: str) -> tuple[str, str]:
    """Location of a lab posting: a recognised city/country in the text, else the source's home."""
    found = detect_countries(text[:4000])
    country = default_country if (not found or default_country in found) else found[0]
    city = detect_city(text[:4000], country) if country != default_country or not default_city else default_city
    return f"{city or ''}, {country}".strip(", "), country


def _offer_from_text(uid: str, title: str, url: str, text: str, org: str, city: str, country: str,
                     posted: Optional[str] = None, trusted: bool = True) -> RawOffer:
    # A shared board (society, network, school list) is where the offer was found, not who hosts it:
    # such offers are labelled "via <board>" so no letter ever says "au sein de <board>".
    if BOARD_NAME.search(org):
        org = "via " + re.split(r"\s+[—(]", org)[0].strip()
    location, code = _place(f"{title}\n{text}", city, country)
    return RawOffer(uid=uid, title=title, org=org, url=url, location=location, country_hint=code, description=text,
                    posted_at=posted, trusted_internship=trusted, contacts=_contacts_from_text(text, org, url))


def _wanted(title: str, text: str = "") -> bool:
    """Lab boards mix internships with jobs and PhDs: keep internships in the AI/data field."""
    if not (is_internship_title(title) or (INTERNSHIP_HINT.search(title) and re.search(r"\bstage\b|internship|stagiaire", text[:2000], re.I))):
        return False
    if re.search(r"\b(phd|th[eè]se|doctora|post-?doc|ma[iî]tre de conf|professeur|cdd|cdi)\b", title, re.I) and not re.search(r"stage|intern|master|m2", title, re.I):
        return False
    return is_relevant_title(title, 0.5) or is_relevant_title(text[:1500], 0.8)


# --------------------------------------------------------------- recruitee
def recruitee(token: str, name: str, group: str = "Schools & labs") -> Source:
    def fetch(ctx: Context) -> list[RawOffer]:
        data = net.get_json(f"https://{token}.recruitee.com/api/offers/")
        offers: list[RawOffer] = []
        for job in data.get("offers") or []:
            title = clean(job.get("title"))
            internship = job.get("employment_type_code") == "internship"
            text = html_to_text(f"{job.get('description') or ''}\n{job.get('requirements') or ''}")
            if not (internship or is_internship_title(title)) or not (is_relevant_title(title, 0.5) or is_relevant_title(text[:1500], 0.8)):
                continue
            location = ", ".join(part for part in (clean(job.get("city")), clean(job.get("country"))) if part)
            if not ctx.location_ok(location, job.get("country_code")):
                continue
            salary = job.get("salary") or {}
            offers.append(RawOffer(
                uid=f"recruitee:{token}:{job.get('id')}", title=title, org=clean(job.get("company_name")) or name,
                url=job.get("careers_url") or "", location=location, country_hint=job.get("country_code"),
                description=text, posted_at=parse_date(job.get("published_at") or job.get("created_at")),
                deadline=parse_date(job.get("close_at")), remote=bool(job.get("remote")), trusted_internship=internship,
                salary={"min": salary.get("min"), "max": salary.get("max"), "currency": salary.get("currency"),
                        "period": salary.get("period")} if salary.get("min") else None,
                contacts=_contacts_from_text(text, name, job.get("careers_url") or ""),
            ))
        return offers

    return Source(f"recruitee:{token}", "research" if group == "Schools & labs" else "company", name, fetch, group)


# --------------------------------------------------------------- wordpress
def wordpress(site: str, name: str, city: str = "", country: str = "FR",
              terms: tuple[str, ...] = ("stage", "internship", "stagiaire")) -> Source:
    base = site.rstrip("/")

    def fetch(ctx: Context) -> list[RawOffer]:
        posts: dict[int, dict] = {}
        for term in terms:
            for post in net.get_json(f"{base}/wp-json/wp/v2/posts", params={
                    "search": term, "per_page": 20, "_fields": "id,date,link,title,content"}):
                posts.setdefault(post["id"], post)
        offers: list[RawOffer] = []
        for post in posts.values():
            title = clean(html_to_text((post.get("title") or {}).get("rendered")))
            text = html_to_text((post.get("content") or {}).get("rendered"))
            if not _wanted(title, text):
                continue
            # Many lab posts are a short announcement linking to the full PDF subject.
            if len(text) < 600:
                pdfs = re.findall(r'href="([^"]+\.pdf)"', (post.get("content") or {}).get("rendered") or "")
                if pdfs:
                    text = f"{text}\n{document_text(urljoin(post['link'], pdfs[0]))}"
            offers.append(_offer_from_text(f"wp:{urlparse(base).netloc}:{post['id']}", title, post["link"], text,
                                           name, city, country, parse_date(post.get("date"))))
        return offers

    return Source(f"wordpress:{urlparse(base).netloc}", "research", name, fetch, "Schools & labs")


# --------------------------------------------------------------------- rss
def rss(url: str, name: str, city: str = "", country: str = "FR", pages: int = 1) -> Source:
    def fetch(ctx: Context) -> list[RawOffer]:
        atom = "{http://www.w3.org/2005/Atom}"
        items = []
        for number in range(1, pages + 1):  # WordPress feeds page with ?paged=N
            try:
                root = etree.fromstring(net.get(url, params={"paged": number} if number > 1 else None).content)
            except net.FetchError:
                if number == 1:
                    raise
                break
            batch = root.findall(".//item") or root.findall(f".//{atom}entry")
            items.extend(batch)
            if not batch:
                break
        offers: list[RawOffer] = []
        for item in items:
            title = clean(item.findtext("title") or item.findtext(f"{atom}title"))
            link = clean(item.findtext("link")) or clean((item.find(f"{atom}link") is not None and item.find(f"{atom}link").get("href")) or "")
            body = item.findtext("{http://purl.org/rss/1.0/modules/content/}encoded") or item.findtext("description") or item.findtext(f"{atom}content") or ""
            text = html_to_text(body)
            if not title or not link or not _wanted(title, text):
                continue
            if len(text) < 600:
                text = f"{text}\n{document_text(link)}"
            posted = parse_date(item.findtext("pubDate") or item.findtext(f"{atom}updated"))
            offers.append(_offer_from_text(f"rss:{urlparse(url).netloc}:{link[-120:]}", title, link, text, name, city, country, posted))
        return offers

    return Source(f"rss:{urlparse(url).netloc}{urlparse(url).path}".rstrip("/"), "research", name, fetch, "Schools & labs")


# -------------------------------------------------------------------- page
def page(url: str, name: str, city: str = "", country: str = "FR", link_pattern: str = "") -> Source:
    """A page listing offers: every link that looks like an internship is opened (HTML or PDF)."""
    pattern = re.compile(link_pattern, re.I) if link_pattern else None

    def fetch(ctx: Context) -> list[RawOffer]:
        tree = lxml_html.fromstring(net.get(url).text)
        seen: dict[str, str] = {}
        for anchor in tree.xpath("//a[@href]"):
            href = urljoin(url, anchor.get("href"))
            label = clean(anchor.text_content()) or clean(anchor.get("title"))
            if href in seen or href.rstrip("/") == url.rstrip("/") or href.startswith("mailto:") or len(label) < 12:
                continue
            if pattern and not pattern.search(href + " " + label):
                continue
            if not pattern and not (INTERNSHIP_HINT.search(label) or INTERNSHIP_HINT.search(href.rsplit("/", 1)[-1])):
                continue
            seen[href] = re.sub(r"\.pdf$", "", label, flags=re.I).replace("_", " ")
        offers: list[RawOffer] = []
        for href, label in list(seen.items())[: ctx.detail_cap * 2]:
            if not (is_relevant_title(label, 0.3) or INTERNSHIP_HINT.search(label)):
                continue
            text = document_text(href)
            title = label
            if href.lower().endswith(".pdf") and text:
                # Prefer the subject's own heading (an early line naming the internship) to a file name.
                heading = next((clean(line) for line in text.splitlines()[:15]
                                if 15 <= len(clean(line)) <= 160 and INTERNSHIP_HINT.search(line)), "")
                title = heading or label
            if not _wanted(title, text) and not _wanted(label, text):
                continue
            offers.append(_offer_from_text(f"page:{urlparse(url).netloc}:{href[-140:]}", title, href, text, name, city, country))
        return offers

    return Source(f"page:{urlparse(url).netloc}{urlparse(url).path}".rstrip("/"), "research", name, fetch, "Schools & labs")


# ---------------------------------------------------------------- linkedin
LINKEDIN_SEARCHES: tuple[tuple[str, str, int], ...] = (
    # (keywords, location, pages of 10) — priorities first
    ("stage NLP LLM", "France", 2), ("stage machine learning", "France", 2), ("stage data scientist", "France", 2),
    ("stage intelligence artificielle", "Île-de-France", 2), ("stage IA générative", "France", 1),
    ("machine learning internship", "Italy", 1), ("AI internship", "Belgium", 1), ("stage data science", "Belgium", 1),
    ("machine learning internship", "Germany", 1), ("AI internship", "Luxembourg", 1),
    ("NLP internship", "European Union", 1), ("machine learning internship", "Switzerland", 1),
    ("AI internship", "Egypt", 1), ("AI internship", "United Arab Emirates", 1), ("AI internship", "Saudi Arabia", 1),
)


def linkedin() -> Source:
    host = "https://www.linkedin.com/jobs-guest/jobs/api"

    def fetch(ctx: Context) -> list[RawOffer]:
        cards: dict[str, dict] = {}
        for keywords, location, pages in LINKEDIN_SEARCHES:
            for number in range(pages):
                try:
                    response = net.get(f"{host}/seeMoreJobPostings/search", params={
                        "keywords": keywords, "location": location, "f_JT": "I", "f_TPR": "r2592000", "start": number * 10})
                except net.FetchError as exc:
                    if exc.status == 429:
                        raise  # LinkedIn asked to stop: end this source for the run
                    break
                if not response.text.strip():
                    break
                for card in lxml_html.fromstring(response.text).xpath("//div[@data-entity-urn]"):
                    job_id = card.get("data-entity-urn", "").rsplit(":", 1)[-1]
                    title = clean("".join(card.xpath(".//h3[contains(@class,'title')]//text()")))
                    if not job_id or job_id in cards or not title:
                        continue
                    cards[job_id] = {
                        "title": title,
                        "org": clean("".join(card.xpath(".//h4[contains(@class,'subtitle')]//text()"))),
                        "location": clean("".join(card.xpath(".//*[contains(@class,'job-search-card__location')]//text()"))),
                        "posted": card.xpath(".//time/@datetime")[:1],
                        "salary": clean("".join(card.xpath(".//*[contains(@class,'salary')]//text()"))),
                    }
        offers: list[RawOffer] = []
        for job_id, card in cards.items():
            if not (is_relevant_title(card["title"], 0.5)) or not ctx.location_ok(card["location"]):
                continue
            if len(offers) >= ctx.detail_cap * 3:
                break
            description, criteria = "", ""
            try:
                detail = lxml_html.fromstring(net.get(f"{host}/jobPosting/{job_id}").text)
                description = html_to_text(etree.tostring((detail.xpath("//div[contains(@class,'description__text')]") or [detail])[0], encoding="unicode"))
                criteria = " · ".join(clean(item.text_content()) for item in detail.xpath("//li[contains(@class,'description__job-criteria-item')]"))
                card["salary"] = card["salary"] or clean("".join(detail.xpath("//*[contains(@class,'compensation') or contains(@class,'salary')]//text()")))
            except net.FetchError as exc:
                if exc.status == 429:
                    break
            offers.append(RawOffer(
                uid=f"linkedin:{job_id}", title=card["title"], org=card["org"] or "Company on LinkedIn",
                url=f"https://www.linkedin.com/jobs/view/{job_id}/", location=card["location"],
                description=f"{description}\n{criteria}".strip(), posted_at=parse_date(card["posted"][0] if card["posted"] else None),
                trusted_internship=True, pay_hint=card["salary"],
            ))
        return offers

    return Source("linkedin", "board", "LinkedIn (public job search)", fetch, "Job boards")


# ------------------------------------------------------------------- seeds
def all_sources() -> list[Source]:
    """Built-in school and lab sources (others can be added from the app)."""
    return [
        recruitee("institutminestelecom", "Institut Mines-Télécom (Télécom SudParis, Télécom Paris, IMT)"),
        rss("https://www.ibisc.univ-evry.fr/feed/", "IBISC — Université d'Évry Paris-Saclay", "Évry", pages=3),
        rss("https://gdr-iasis.cnrs.fr/type-annonces/postes/postes_stagiaire/feed/",
            "GdR IASIS (CNRS) — national image, signal & AI internship board", pages=4),
        rss("https://www.atief.fr/rss.xml", "ATIEF — AI for education offers"),
        page("https://dataai.telecom-paris.fr/internships", "Télécom Paris — Data & AI internships", "Palaiseau"),
        page("https://www.lisn.upsaclay.fr/offres-emploi/", "LISN — Université Paris-Saclay", "Orsay", link_pattern=r"/offres-emploi/."),
        page("https://www.sfds.asso.fr/fr/n/506-consulter_les_offres_demploi/", "SFdS — French Statistical Society job board",
             link_pattern=r"stage|intern|m2"),
        linkedin(),
    ]


def from_config(kind: str, token: str, name: str, cfg: dict[str, Any]) -> Optional[Source]:
    """Build a user-added source of one of the generic types."""
    city, country = cfg.get("city", ""), (cfg.get("country") or "FR").upper()
    if kind == "recruitee":
        return recruitee(token, name, cfg.get("group") or "Schools & labs")
    if kind == "wordpress":
        return wordpress(token, name, city, country)
    if kind == "rss":
        return rss(token, name, city, country, int(cfg.get("pages") or 1))
    if kind == "page":
        return page(token, name, city, country, cfg.get("link_pattern", ""))
    return None


__all__ = ["all_sources", "from_config", "document_text", "linkedin", "page", "recruitee", "rss", "wordpress", "quote"]
