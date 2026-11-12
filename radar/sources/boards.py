"""Connectors for job boards and very large single employers with open search endpoints."""

from __future__ import annotations

import re
from urllib.parse import urljoin

from lxml import etree, html as lxml_html

from .. import net
from ..scoring import is_internship_title, is_relevant_title
from ..text import clean, detect_countries, html_to_text, parse_date
from .base import Context, RawOffer, Source

_ISO3 = {"FR": "FRA", "IT": "ITA", "BE": "BEL", "DE": "DEU", "LU": "LUX", "ES": "ESP", "NL": "NLD", "IE": "IRL",
         "GB": "GBR", "CH": "CHE", "PL": "POL", "CZ": "CZE", "SE": "SWE", "AE": "ARE", "SA": "SAU", "EG": "EGY",
         "QA": "QAT", "PT": "PRT", "AT": "AUT", "RO": "ROU", "FI": "FIN", "DK": "DNK"}


# -------------------------------------------------------------------- Amazon
def amazon() -> Source:
    def fetch(ctx: Context) -> list[RawOffer]:
        offers: dict[str, RawOffer] = {}
        for code in [code for code in _ISO3 if code in ctx.allowed_countries]:
            try:
                data = net.get_json("https://www.amazon.jobs/en/search.json",
                                    params={"base_query": "intern", "normalized_country_code[]": _ISO3[code],
                                            "result_limit": 100, "sort": "recent"})
            except net.FetchError:
                continue
            for job in data.get("jobs", []):
                title = clean(job.get("title"))
                if not (is_internship_title(title) or job.get("is_intern")) or not is_relevant_title(title):
                    continue
                description = "\n".join(html_to_text(job.get(key)) for key in ("description", "basic_qualifications", "preferred_qualifications"))
                offers[str(job.get("id_icims") or job.get("id"))] = RawOffer(
                    uid=f"amazon:{job.get('id_icims') or job.get('id')}", title=title,
                    org=clean(job.get("company_name")) or "Amazon", url=urljoin("https://www.amazon.jobs", job.get("job_path") or ""),
                    location=clean(job.get("normalized_location") or job.get("location")), country_hint=code,
                    description=description, posted_at=parse_date(job.get("posted_date")), trusted_internship=True,
                )
        return list(offers.values())

    return Source("amazon", "company", "Amazon", fetch, "Large employers")


# ----------------------------------------------------------------- Arbeitnow
def arbeitnow() -> Source:
    def fetch(ctx: Context) -> list[RawOffer]:
        offers: list[RawOffer] = []
        for page in range(1, 7):
            try:
                data = net.get_json("https://www.arbeitnow.com/api/job-board-api", params={"page": page})
            except net.FetchError:
                break
            jobs = data.get("data") or []
            for job in jobs:
                title = clean(job.get("title"))
                types = " ".join(job.get("job_types") or [])
                if not (is_internship_title(title) or is_internship_title(types)) or not is_relevant_title(title):
                    continue
                location = clean(job.get("location"))
                if not ctx.location_ok(location):
                    continue
                offers.append(RawOffer(
                    uid=f"arbeitnow:{job.get('slug')}", title=title, org=clean(job.get("company_name")),
                    url=job.get("url") or "", location=location or "Germany",
                    country_hint=None if location else "DE", description=html_to_text(job.get("description")),
                    posted_at=parse_date(job.get("created_at")), remote=bool(job.get("remote")),
                ))
            if not jobs or not (data.get("links") or {}).get("next"):
                break
        return offers

    return Source("arbeitnow", "board", "Arbeitnow (Germany)", fetch, "Job boards")


# ----------------------------------------------------------------- HelloWork
HELLOWORK_QUERIES = ("stage fin d'études NLP", "stage LLM", "stage intelligence artificielle générative",
                     "stage machine learning", "stage data scientist", "stage PFE data", "stage deep learning",
                     "stage RAG", "stage IA agentique", "stage traitement automatique des langues")


def hellowork() -> Source:
    site = "https://www.hellowork.com"

    def fetch(ctx: Context) -> list[RawOffer]:
        cards: dict[str, dict] = {}
        for query in HELLOWORK_QUERIES:
            try:
                page = net.get(f"{site}/fr-fr/emploi/recherche.html", params={"k": query, "c": "Stage"})
            except net.FetchError:
                break
            tree = lxml_html.fromstring(page.text)
            for item in tree.xpath("//li[@data-id-storage-item-id]"):
                identifier = item.get("data-id-storage-item-id")
                title = clean("".join(item.xpath(".//input[@name='title']/@value")))
                company = clean("".join(item.xpath(".//input[@name='company']/@value")))
                if identifier and title and identifier not in cards:
                    cards[identifier] = {"title": title, "company": company, "text": clean(item.text_content())}
        offers: list[RawOffer] = []
        for identifier, card in cards.items():
            # HelloWork title-cases every word; restore the usual spelling of technical acronyms.
            title = re.sub(r"\s*H/F\s*$", "", card["title"])
            title = re.sub(r"\b(Nlp|Llms?|Rag|Ia|Ai|Ml|Cad|Api|Sql|Erp|Rh|Bi|Cnd|Tal|Gpu|Hpc|Iot|Ux|Ui|Devops|Mlops|Genai)\b",
                           lambda match: {"Llms": "LLMs", "Devops": "DevOps", "Mlops": "MLOps", "Genai": "GenAI"}.get(match.group(1), match.group(1).upper()), title)
            if not is_internship_title(title) or not is_relevant_title(title, 0.5):
                continue
            if len(offers) >= ctx.detail_cap * 3:
                break
            url = f"{site}/fr-fr/emplois/{identifier}.html"
            description, location, posted, deadline = "", "France", None, None
            try:
                detail = lxml_html.fromstring(net.get(url).text)
                sections = [html_to_text(etree.tostring(section, encoding="unicode")) for section in detail.xpath("//section")]
                description = "\n".join(text for text in sections if len(text) > 150 and not text.startswith("À noter"))[:9000]
                town = clean("".join(detail.xpath("(//*[@data-cy='localisationCard'])[1]//text()")))
                town = re.sub(r"\s*-\s*\d{2,3}$", "", town)
                location = f"{town}, France" if town else "France"
            except (net.FetchError, etree.Error):
                pass
            offers.append(RawOffer(
                uid=f"hellowork:{identifier}", title=title, org=card["company"] or "Employer on HelloWork", url=url,
                location=location, country_hint="FR", description=description, posted_at=posted,
                deadline=deadline, trusted_internship=True,
            ))
        return offers

    return Source("hellowork", "board", "HelloWork (France)", fetch, "Job boards")


# ------------------------------------------------------------------- Tanqeeb
TANQEEB_SITES = {"EG": "egypt", "AE": "uae", "SA": "saudi", "QA": "qatar", "KW": "kuwait", "BH": "bahrain", "OM": "oman"}
TANQEEB_QUERIES = ("machine learning intern", "data science intern", "AI intern", "NLP intern", "data analyst intern")


def tanqeeb() -> Source:
    """Regional aggregator covering Egypt and the Gulf (it indexes postings from many local boards)."""

    def fetch(ctx: Context) -> list[RawOffer]:
        cards: dict[str, dict] = {}
        for code, site in TANQEEB_SITES.items():
            if code not in ctx.allowed_countries:
                continue
            base = f"https://{site}.tanqeeb.com"
            for query in TANQEEB_QUERIES:
                try:
                    tree = lxml_html.fromstring(net.get(f"{base}/jobs/search", params={"keywords": query}).text)
                except net.FetchError:
                    break
                for card in tree.xpath("//article[@data-job-id]"):
                    identifier = card.get("data-job-id")
                    title = clean(card.get("data-job-name"))
                    if not identifier or identifier in cards or card.get("data-job-closed") == "1":
                        continue
                    if not is_internship_title(title) or not is_relevant_title(title, 0.5):
                        continue
                    # Each country site also lists a few postings located elsewhere; keep local ones only.
                    if code not in detect_countries(f"{card.get('data-job-country')} {card.get('data-job-location')}"):
                        continue
                    cards[identifier] = {
                        "title": title, "company": clean(card.get("data-job-company")), "code": code,
                        "location": clean(card.get("data-job-location")), "url": urljoin(base, card.get("data-job-url") or ""),
                        "remote": "remote" in clean(card.get("data-job-workplace")).lower(),
                        "nationality": clean(card.get("data-job-nationality")), "origin": clean(card.get("data-job-source")),
                    }
        offers: list[RawOffer] = []
        for identifier, card in list(cards.items())[: ctx.detail_cap * 3]:
            description = ""
            try:
                detail = lxml_html.fromstring(net.get(card["url"]).text)
                blocks = detail.xpath("//*[@id='jobDescriptionBody']")
                if blocks:
                    description = html_to_text(etree.tostring(blocks[0], encoding="unicode"))[:7000]
            except (net.FetchError, etree.Error):
                pass
            if card["nationality"]:
                description = f"Nationality requirement stated by the employer: {card['nationality']}.\n{description}"
            offers.append(RawOffer(
                uid=f"tanqeeb:{identifier}", title=card["title"], org=card["company"] or "Employer on Tanqeeb",
                url=card["url"], location=card["location"], country_hint=card["code"], description=description,
                remote=card["remote"], trusted_internship=True,
            ))
        return offers

    return Source("tanqeeb", "board", "Tanqeeb (Egypt & Gulf)", fetch, "Job boards")


def all_sources() -> list[Source]:
    return [amazon(), arbeitnow(), hellowork(), tanqeeb()]
