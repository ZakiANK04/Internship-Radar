"""Connectors for public applicant-tracking APIs.

Greenhouse, Lever, Ashby, SmartRecruiters and Workday all publish the job
boards of their customers through documented or openly served JSON endpoints.
One generic connector per platform covers every employer listed in
``radar/data/boards.json``.
"""

from __future__ import annotations

from typing import Any

from .. import net
from ..scoring import is_internship_title, is_relevant_title
from ..text import clean, html_to_text, parse_date
from .base import Context, RawOffer, Source


def _wanted(title: str) -> bool:
    return is_internship_title(title) and is_relevant_title(title)


# ---------------------------------------------------------------- Greenhouse
def greenhouse(token: str, name: str) -> Source:
    def fetch(ctx: Context) -> list[RawOffer]:
        data = net.get_json(f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs")
        offers: list[RawOffer] = []
        for job in data.get("jobs", []):
            title = clean(job.get("title"))
            location = clean((job.get("location") or {}).get("name"))
            if not _wanted(title) or not ctx.location_ok(location):
                continue
            if len(offers) >= ctx.detail_cap:
                break
            description = ""
            try:
                detail = net.get_json(f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs/{job['id']}")
                description = html_to_text(detail.get("content"))
                offices = ", ".join(clean(office.get("name")) for office in detail.get("offices") or [])
                if offices and offices.lower() not in location.lower():
                    location = f"{location} ({offices})" if location else offices
            except net.FetchError:
                pass
            offers.append(RawOffer(
                uid=f"greenhouse:{token}:{job['id']}", title=title, org=name, url=job.get("absolute_url") or "",
                location=location, description=description,
                posted_at=parse_date(job.get("first_published") or job.get("updated_at")),
            ))
        return offers

    return Source(f"greenhouse:{token}", "company", name, fetch, "Greenhouse")


# --------------------------------------------------------------------- Lever
def lever(token: str, name: str, host: str = "api.lever.co") -> Source:
    def fetch(ctx: Context) -> list[RawOffer]:
        data = net.get_json(f"https://{host}/v0/postings/{token}?mode=json")
        offers: list[RawOffer] = []
        for job in data if isinstance(data, list) else []:
            title = clean(job.get("text"))
            categories = job.get("categories") or {}
            commitment = clean(categories.get("commitment"))
            location = clean(categories.get("location")) or ", ".join(categories.get("allLocations") or [])
            internship = is_internship_title(title) or is_internship_title(commitment)
            if not internship or not is_relevant_title(title) or not ctx.location_ok(location, job.get("country")):
                continue
            sections = [clean(job.get("descriptionPlain"))]
            for block in job.get("lists") or []:
                sections.append(f"{clean(block.get('text'))}\n{html_to_text(block.get('content'))}")
            sections.append(clean(job.get("additionalPlain")))
            offers.append(RawOffer(
                uid=f"lever:{token}:{job.get('id')}", title=title, org=name, url=job.get("hostedUrl") or "",
                location=location, country_hint=job.get("country"),
                description="\n".join(part for part in sections if part),
                posted_at=parse_date(job.get("createdAt")), remote=job.get("workplaceType") == "remote",
                trusted_internship=is_internship_title(commitment),
            ))
        return offers

    return Source(f"lever:{token}", "company", name, fetch, "Lever")


# --------------------------------------------------------------------- Ashby
def ashby(token: str, name: str) -> Source:
    def fetch(ctx: Context) -> list[RawOffer]:
        data = net.get_json(f"https://api.ashbyhq.com/posting-api/job-board/{token}")
        offers: list[RawOffer] = []
        for job in data.get("jobs", []):
            title = clean(job.get("title"))
            intern_type = clean(job.get("employmentType")).lower() == "intern"
            if not (is_internship_title(title) or intern_type) or not is_relevant_title(title):
                continue
            address = ((job.get("address") or {}).get("postalAddress") or {})
            places = [clean(job.get("location"))] + [clean(item.get("location")) for item in job.get("secondaryLocations") or []]
            if address.get("addressCountry"):
                places.append(clean(address.get("addressCountry")))
            location = ", ".join(dict.fromkeys(place for place in places if place))
            if not ctx.location_ok(location):
                continue
            offers.append(RawOffer(
                uid=f"ashby:{token}:{job.get('id')}", title=title, org=name, url=job.get("jobUrl") or "",
                location=location, description=(job.get("descriptionPlain") or "").strip(),
                posted_at=parse_date(job.get("publishedAt")), remote=bool(job.get("isRemote")),
                trusted_internship=intern_type,
            ))
        return offers

    return Source(f"ashby:{token}", "company", name, fetch, "Ashby")


# ----------------------------------------------------------- SmartRecruiters
SR_QUERIES = ("internship", "stage", "stagiaire", "thesis", "praktikum", "tirocinio")


def smartrecruiters(token: str, name: str) -> Source:
    def fetch(ctx: Context) -> list[RawOffer]:
        seen: dict[str, dict[str, Any]] = {}
        base = f"https://api.smartrecruiters.com/v1/companies/{token}/postings"
        for query in SR_QUERIES:
            offset = 0
            while offset < 300:
                page = net.get_json(base, params={"q": query, "limit": 100, "offset": offset})
                content = page.get("content") or []
                for job in content:
                    seen.setdefault(job["id"], job)
                offset += 100
                if offset >= int(page.get("totalFound") or 0):
                    break
        offers: list[RawOffer] = []
        for job in seen.values():
            title = clean(job.get("name"))
            place = job.get("location") or {}
            location = ", ".join(part for part in (clean(place.get("city")), clean(place.get("region")), clean(place.get("country")).upper()) if part)
            if not _wanted(title) or not ctx.location_ok(location, place.get("country")):
                continue
            if len(offers) >= ctx.detail_cap:
                break
            description, url = "", f"https://jobs.smartrecruiters.com/{token}/{job['id']}"
            try:
                detail = net.get_json(f"{base}/{job['id']}")
                url = detail.get("postingUrl") or url
                sections = ((detail.get("jobAd") or {}).get("sections") or {})
                description = "\n".join(
                    html_to_text((sections.get(key) or {}).get("text"))
                    for key in ("jobDescription", "qualifications", "additionalInformation")
                )
            except net.FetchError:
                pass
            offers.append(RawOffer(
                uid=f"smartrecruiters:{token}:{job['id']}", title=title,
                org=clean((job.get("company") or {}).get("name")) or name, url=url, location=location,
                country_hint=place.get("country"), description=description,
                posted_at=parse_date(job.get("releasedDate")), remote=bool(place.get("remote")),
            ))
        return offers

    return Source(f"smartrecruiters:{token}", "company", name, fetch, "SmartRecruiters")


# ------------------------------------------------------------------- Workday
WD_QUERIES = ("internship", "stage", "intern", "thesis", "stagiaire")
# Multinationals list their Cairo / Gulf internships among thousands of postings; these reach them.
WD_MENA_QUERIES = ("intern Egypt", "intern Cairo", "intern Dubai", "intern Abu Dhabi", "intern Saudi", "intern Riyadh", "intern Qatar")


def workday(tenant: str, pod: str, site: str, name: str, mena: bool = False) -> Source:
    host = f"https://{tenant}.{pod}.myworkdayjobs.com"
    api = f"{host}/wday/cxs/{tenant}/{site}"

    def fetch(ctx: Context) -> list[RawOffer]:
        seen: dict[str, dict[str, Any]] = {}
        for query in (WD_MENA_QUERIES if mena else WD_QUERIES + WD_MENA_QUERIES[:1]):
            offset = 0
            while offset < 100:
                page = net.post_json(f"{api}/jobs", {"appliedFacets": {}, "limit": 20, "offset": offset, "searchText": query})
                postings = page.get("jobPostings") or []
                for job in postings:
                    if job.get("externalPath"):
                        seen.setdefault(job["externalPath"], job)
                offset += 20
                if len(postings) < 20 or offset >= int(page.get("total") or 0):
                    break
        offers: list[RawOffer] = []
        for path, job in seen.items():
            title = clean(job.get("title"))
            listed = clean(job.get("locationsText"))
            if not _wanted(title) or not ctx.location_ok(listed):
                continue
            if len(offers) >= ctx.detail_cap:
                break
            try:
                info = net.get_json(f"{api}{path}").get("jobPostingInfo") or {}
            except net.FetchError:
                continue
            country = clean((info.get("country") or {}).get("descriptor"))
            places = [clean(info.get("location"))] + [clean(item) for item in info.get("additionalLocations") or []]
            location = ", ".join(dict.fromkeys(part for part in (*places, country) if part))
            if not ctx.location_ok(location):
                continue
            offers.append(RawOffer(
                uid=f"workday:{tenant}:{info.get('jobReqId') or path}", title=clean(info.get("title")) or title,
                org=name, url=info.get("externalUrl") or f"{host}/{site}{path}", location=location,
                description=html_to_text(info.get("jobDescription")), posted_at=parse_date(info.get("startDate")),
                remote="remote" in clean(info.get("remoteType")).lower(),
            ))
        return offers

    return Source(f"workday:{tenant}:{site}" if mena else f"workday:{tenant}", "company", name, fetch, "Workday")
