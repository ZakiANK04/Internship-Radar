"""Where each researcher works — city, region, and areas you care about (Nice · Côte d'Azur first).

The place is read from what is already known about the person: their institution and lab,
the university in your spreadsheet, their e-mail domain, and the affiliations on their papers.
Nothing is looked up for this, and a city is only given when it is clearly named.
"""

from __future__ import annotations

import json
import re
from datetime import date, timedelta
from typing import Any, Callable, Optional

from . import db, net
from .text import _CITY_INDEX, _CITY_PATTERN, clean, fold

Log = Callable[..., None]

# Institutions and labs whose city is not written in their name.
_INSTITUTIONS: list[tuple[re.Pattern[str], str, str]] = [
    (re.compile(r"eurecom", re.I), "Sophia Antipolis", "FR"),
    (re.compile(r"\bi3s\b|sophia.?antipolis|inria.{0,40}(?:sophia|universit[ée] c[ôo]te d.?azur)|3ia c[ôo]te|\bskema\b", re.I), "Sophia Antipolis", "FR"),
    (re.compile(r"universit[ée]\s+(?:de\s+)?nice|universit[ée]\s+c[ôo]te\s+d.?azur|c[ôo]te\s+d.?azur\s+universit|\bljad\b|\binphyni\b|"
                r"observatoire de la c[ôo]te d.?azur|\bunice\b|univ-cotedazur", re.I), "Nice", "FR"),
    (re.compile(r"\bmonaco\b", re.I), "Monaco", "MC"),
    (re.compile(r"universit[ée] paris[- ]saclay|centralesup[ée]lec|\bensae\b|[ée]cole polytechnique(?! f[ée]d)|\blix\b|t[ée]l[ée]com paris|\bensta\b|\binria saclay", re.I), "Saclay", "FR"),
    (re.compile(r"sorbonne|\bpsl\b|\bens paris|[ée]cole normale sup[ée]rieure(?! de lyon)|\bcnam\b|universit[ée] paris cit[ée]|\binria paris|\blip6\b|\blipn\b", re.I), "Paris", "FR"),
    (re.compile(r"\birisa\b|\binria rennes|universit[ée] de rennes", re.I), "Rennes", "FR"),
    (re.compile(r"\bloria\b|universit[ée] de lorraine", re.I), "Nancy", "FR"),
    (re.compile(r"\blabri\b", re.I), "Bordeaux", "FR"),
    (re.compile(r"\birit\b|\blaas\b", re.I), "Toulouse", "FR"),
    (re.compile(r"\bliris\b|ens de lyon|[ée]cole normale sup[ée]rieure de lyon", re.I), "Lyon", "FR"),
    (re.compile(r"\blig\b|\bimag\b|universit[ée] grenoble", re.I), "Grenoble", "FR"),
    (re.compile(r"\bcristal\b|universit[ée] de lille", re.I), "Lille", "FR"),
    (re.compile(r"\blis-lab\b|\blis\b.{0,30}marseille|aix-marseille", re.I), "Marseille", "FR"),
]
_DOMAINS: dict[str, tuple[str, str]] = {
    "eurecom.fr": ("Sophia Antipolis", "FR"), "unice.fr": ("Nice", "FR"), "univ-cotedazur.fr": ("Nice", "FR"),
    "oca.eu": ("Nice", "FR"), "i3s.unice.fr": ("Sophia Antipolis", "FR"), "skema.edu": ("Sophia Antipolis", "FR"),
    "irisa.fr": ("Rennes", "FR"), "loria.fr": ("Nancy", "FR"), "labri.fr": ("Bordeaux", "FR"), "irit.fr": ("Toulouse", "FR"),
    "imag.fr": ("Grenoble", "FR"), "univ-grenoble-alpes.fr": ("Grenoble", "FR"), "univ-lille.fr": ("Lille", "FR"),
    "univ-amu.fr": ("Marseille", "FR"), "lis-lab.fr": ("Marseille", "FR"), "sorbonne-universite.fr": ("Paris", "FR"),
    "lip6.fr": ("Paris", "FR"), "u-paris.fr": ("Paris", "FR"), "cnam.fr": ("Paris", "FR"), "ens.fr": ("Paris", "FR"),
    "universite-paris-saclay.fr": ("Saclay", "FR"), "centralesupelec.fr": ("Saclay", "FR"), "polytechnique.edu": ("Saclay", "FR"),
    "telecom-paris.fr": ("Saclay", "FR"), "ensae.fr": ("Saclay", "FR"), "univ-lyon1.fr": ("Lyon", "FR"), "ens-lyon.fr": ("Lyon", "FR"),
    "univ-orleans.fr": ("Orléans", "FR"), "univ-artois.fr": ("Lens", "FR"), "polimi.it": ("Milan", "IT"), "unimi.it": ("Milan", "IT"),
    "unifi.it": ("Florence", "IT"), "unibo.it": ("Bologna", "IT"), "unitn.it": ("Trento", "IT"), "fbk.eu": ("Trento", "IT"),
    "uniroma1.it": ("Rome", "IT"), "polito.it": ("Turin", "IT"), "unipi.it": ("Pisa", "IT"), "kuleuven.be": ("Leuven", "BE"),
    "uantwerpen.be": ("Antwerp", "BE"), "ugent.be": ("Ghent", "BE"), "ulb.be": ("Brussels", "BE"), "uclouvain.be": ("Louvain-la-Neuve", "BE"),
    "umons.ac.be": ("Mons", "BE"), "uni.lu": ("Luxembourg", "LU"), "tum.de": ("Munich", "DE"), "kit.edu": ("Karlsruhe", "DE"),
    "kaust.edu.sa": ("Thuwal", "SA"), "mbzuai.ac.ae": ("Abu Dhabi", "AE"),
}

# Areas you can filter on directly.  Côte d'Azur: Nice and the technology park around Sophia Antipolis.
AREAS: dict[str, dict[str, Any]] = {
    "cote_azur": {"label": "Nice · Côte d'Azur", "country": "FR",
                  "cities": {"Nice", "Sophia Antipolis", "Valbonne", "Biot", "Antibes", "Cannes", "Mougins", "Grasse",
                             "Cagnes-sur-Mer", "Menton", "Villefranche-sur-Mer", "Monaco"}},
}
FRENCH_REGIONS = {
    "Provence-Alpes-Côte d'Azur": {"Nice", "Sophia Antipolis", "Valbonne", "Biot", "Antibes", "Cannes", "Mougins", "Grasse",
                                   "Cagnes-sur-Mer", "Menton", "Marseille", "Aix-en-Provence", "Toulon", "Avignon"},
    "Île-de-France": {"Paris", "Saclay", "Palaiseau", "Orsay", "Gif-sur-Yvette", "Versailles", "Évry", "Cergy", "Champs-sur-Marne",
                      "Créteil", "Saint-Denis", "Massy", "Jouy-en-Josas", "Guyancourt", "Évry-Courcouronnes"},
    "Auvergne-Rhône-Alpes": {"Lyon", "Grenoble", "Saint-Étienne", "Clermont-Ferrand", "Annecy", "Chambéry", "Villeurbanne"},
    "Occitanie": {"Toulouse", "Montpellier", "Nîmes", "Perpignan"},
    "Hauts-de-France": {"Lille", "Villeneuve-d'Ascq", "Amiens", "Lens", "Compiègne", "Valenciennes"},
    "Bretagne": {"Rennes", "Brest", "Lannion", "Lorient", "Vannes"},
    "Grand Est": {"Strasbourg", "Nancy", "Metz", "Reims", "Mulhouse", "Troyes"},
    "Nouvelle-Aquitaine": {"Bordeaux", "Talence", "Poitiers", "Limoges", "La Rochelle", "Pau"},
    "Pays de la Loire": {"Nantes", "Angers", "Le Mans"},
    "Centre-Val de Loire": {"Orléans", "Tours", "Blois"},
    "Normandie": {"Caen", "Rouen", "Le Havre"},
    "Bourgogne-Franche-Comté": {"Dijon", "Besançon", "Belfort"},
}
_REGION_OF = {city: region for region, cities in FRENCH_REGIONS.items() for city in cities}
_SAME_CITY = {"Milano": "Milan", "Roma": "Rome", "Torino": "Turin", "Firenze": "Florence", "Napoli": "Naples", "Genova": "Genoa",
              "Venezia": "Venice", "Padova": "Padua", "München": "Munich", "Munchen": "Munich", "Köln": "Cologne", "Koln": "Cologne",
              "Bruxelles": "Brussels", "Brussel": "Brussels", "Wien": "Vienna", "Lisboa": "Lisbon", "Praha": "Prague", "Warszawa": "Warsaw",
              "Genève": "Geneva", "Geneve": "Geneva", "Zürich": "Zurich", "Leuven": "Leuven", "Louvain": "Leuven", "Antwerpen": "Antwerp",
              "Gent": "Ghent", "Liège": "Liege", "Den Haag": "The Hague", "Göteborg": "Gothenburg", "København": "Copenhagen"}
_SPECIAL_CASE = {"sophia antipolis": "Sophia Antipolis", "sophia-antipolis": "Sophia Antipolis", "gif sur yvette": "Gif-sur-Yvette",
                 "villeneuve d'ascq": "Villeneuve-d'Ascq", "villeneuve-d'ascq": "Villeneuve-d'Ascq"}


def _named_city(text: str, country: Optional[str]) -> Optional[str]:
    for match in _CITY_PATTERN.finditer(fold(text)):
        name = match.group(1)
        if country is None or _CITY_INDEX[name] == country:
            city = _SPECIAL_CASE.get(name, name.title())
            return _SAME_CITY.get(city, city)
    return None


def _texts(person: dict[str, Any]) -> list[str]:
    row = person.get("source_row") or {}
    if isinstance(row, str):
        try:
            row = json.loads(row)
        except json.JSONDecodeError:
            row = {}
    evidence = person.get("evidence") or []
    if isinstance(evidence, str):
        try:
            evidence = json.loads(evidence)
        except json.JSONDecodeError:
            evidence = []
    affiliations = [clean(item.get("affiliation") or item.get("org") or "") for item in evidence if isinstance(item, dict)]
    return [clean(person.get("org")), clean(row.get("lab")), clean(row.get("university")), *affiliations]


def locate(person: dict[str, Any]) -> dict[str, Optional[str]]:
    """City, region and area of one researcher (None when it is not clearly known)."""
    country = person.get("country")
    city = None
    texts = [text for text in _texts(person) if text]
    for text in texts:
        for pattern, name, code in _INSTITUTIONS:
            if pattern.search(text) and (country in (None, code) or code == "MC"):
                city = name
                break
        if city:
            break
    if not city:
        domain = (person.get("email") or "").split("@")[-1].lower()
        for known, (name, code) in sorted(_DOMAINS.items(), key=lambda item: -len(item[0])):  # most specific first
            if domain == known or domain.endswith("." + known):
                if country in (None, code):
                    city = name
                break
    if not city:
        for text in texts:
            city = _named_city(text, country)
            if city:
                break
    region = _REGION_OF.get(city) if city and (country or "FR") == "FR" else None
    area = next((key for key, spec in AREAS.items() if city in spec["cities"]), None)
    return {"city": city, "region": region, "area": area}


# ------------------------------------------------------------ discovery
_AREA_INSTITUTIONS = {
    "cote_azur": ["Université Côte d'Azur", "EURECOM", "Centre Inria d'Université Côte d'Azur",
                  "Laboratoire d'Informatique, Signaux et Systèmes de Sophia Antipolis", "Observatoire de la Côte d'Azur"],
}
_BROAD_TOPICS = ["large language models", "natural language processing", "retrieval augmented generation", "machine learning",
                 "deep learning", "data science", "computer vision", "speech processing", "information retrieval",
                 "graph neural networks", "time series forecasting", "reinforcement learning"]


def _institution_ids(area: str, log: Log) -> list[str]:
    cache = db.get_setting(f"area_institutions_{area}") or []
    if cache:
        return cache
    ids: list[str] = []
    for name in _AREA_INSTITUTIONS[area]:
        try:
            page = net.get_json("https://api.openalex.org/institutions", params={"search": name, "filter": "country_code:FR", "per-page": 3})
        except net.FetchError as exc:
            log(f"OpenAlex did not answer for “{name}” ({exc}).")
            continue
        for result in page.get("results", [])[:1]:
            ids.append(result["id"].rsplit("/", 1)[-1])
    if ids:
        db.set_setting(f"area_institutions_{area}", ids)
    return ids


def find_in_area(area: str = "cote_azur", log: Log = print, max_topics: int = 14) -> dict[str, Any]:
    """Researchers who published AI work from this area's institutions in the last three years.

    Last and corresponding authors are kept — the people who lead groups and take interns.
    Addresses are added afterwards only when the person published them (ORCID, their own papers).
    """
    from .contacts import AI_TERMS, _store_experts

    institutions = _institution_ids(area, log)
    if not institutions:
        return {"found": 0, "created": 0, "error": "OpenAlex could not be reached — try again in a few minutes."}
    since = (date.today() - timedelta(days=3 * 365)).isoformat()
    topics = list(dict.fromkeys([*(db.priorities().get("expert_topics") or []), *_BROAD_TOPICS]))[:max_topics]
    people: dict[str, dict[str, Any]] = {}
    for rank, topic in enumerate(topics):
        try:
            page = net.get_json("https://api.openalex.org/works", params={
                "search": topic, "per-page": 50, "select": "id,title,publication_date,authorships,doi",
                "filter": f"authorships.institutions.lineage:{'|'.join(institutions)},from_publication_date:{since}"})
        except net.FetchError as exc:
            log(f"OpenAlex stopped at “{topic}” ({exc}); what was found so far is kept.")
            break
        for work in page.get("results", []):
            title = clean(work.get("title"))
            if not title or not AI_TERMS.search(f"{title} {topic}"):
                continue
            for authorship in work.get("authorships") or []:
                if authorship.get("author_position") != "last" and not authorship.get("is_corresponding"):
                    continue
                home = next((inst for inst in authorship.get("institutions") or []
                             if any(i in (inst.get("lineage") or [inst.get("id", "")]) or (inst.get("id") or "").endswith(i) for i in institutions)), None)
                author = authorship.get("author") or {}
                if not home or not author.get("id"):
                    continue
                record = people.setdefault(author["id"], {
                    "name": clean(author.get("display_name")), "org": clean(home.get("display_name")), "country": "FR",
                    "orcid": (author.get("orcid") or "").replace("https://orcid.org/", "") or None,
                    "openalex_id": author["id"].rsplit("/", 1)[-1], "url": author["id"], "topics": [], "evidence": [],
                    "senior": False, "tier": "tier1", "rank": rank})
                if topic not in record["topics"]:
                    record["topics"].append(topic)
                if len(record["evidence"]) < 4 and title not in [item["text"] for item in record["evidence"]]:
                    record["evidence"].append({"type": "paper", "text": title, "year": (work.get("publication_date") or "")[:4],
                                               "url": work.get("doi") or work.get("id"), "topic": topic,
                                               "affiliation": clean(home.get("display_name")), "retrieved_at": date.today().isoformat()})
                record["senior"] = record["senior"] or authorship.get("author_position") == "last"
                record["corresponding"] = record.get("corresponding") or bool(authorship.get("is_corresponding"))
    created = _store_experts(people, log) if people else 0
    log(f"{AREAS[area]['label']}: {len(people)} researcher(s) publishing AI work there, {created} new.")
    return {"found": len(people), "created": created, "openalex_ids": [record["openalex_id"] for record in people.values()]}


def enrich_area(openalex_ids: list[str], log: Log = print, limit: int = 40) -> int:
    """Look for the published address of the people just found (never guessed)."""
    from .contacts import enrich_contact

    if not openalex_ids:
        return 0
    marks = ",".join("?" for _ in openalex_ids)
    rows = db.rows(f"SELECT id FROM contacts WHERE openalex_id IN ({marks}) AND email IS NULL ORDER BY relevance DESC LIMIT ?",
                   (*openalex_ids, limit))
    found = 0
    for row in rows:
        try:
            found += bool(enrich_contact(row["id"]).get("found"))
        except Exception as exc:  # one lookup never stops the rest
            log(f"Address lookup failed: {exc}")
    log(f"Published addresses found for {found} of {len(rows)} new local researcher(s).")
    return found
