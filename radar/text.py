"""Text helpers: HTML to text, language guess, e-mail extraction, geography."""

from __future__ import annotations

import html as html_module
import re
import unicodedata
from datetime import date, datetime
from typing import Iterable, Optional

from lxml import html as lxml_html


def clean(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def strip_accents(value: str) -> str:
    return unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()


def fold(value: object) -> str:
    return strip_accents(clean(value)).casefold()


def html_to_text(markup: object) -> str:
    """Convert an HTML fragment to readable text, keeping paragraph breaks."""
    raw = str(markup or "")
    if not raw.strip():
        return ""
    if "&lt;" in raw and "<" not in raw:
        raw = html_module.unescape(raw)
    if "<" not in raw:
        return clean(html_module.unescape(raw))
    try:
        tree = lxml_html.fromstring(f"<div>{raw}</div>")
    except Exception:
        return clean(re.sub(r"<[^>]+>", " ", raw))
    for element in tree.xpath("//script|//style|//noscript"):
        element.drop_tree()
    for element in tree.xpath("//br"):
        element.tail = "\n" + (element.tail or "")
    for element in tree.xpath("//p|//div|//li|//h1|//h2|//h3|//h4|//tr"):
        element.tail = "\n" + (element.tail or "")
    for element in tree.xpath("//li"):
        element.text = "• " + (element.text or "")
    text = tree.text_content()
    lines = [clean(line) for line in text.splitlines()]
    return "\n".join(line for line in lines if line and line != "•")


_STOPWORDS = {
    "fr": {"le", "la", "les", "des", "et", "vous", "pour", "dans", "une", "du", "en", "au", "avec", "sur", "est", "nous", "votre", "de"},
    "en": {"the", "and", "you", "for", "with", "our", "will", "of", "to", "in", "are", "your", "we", "is", "on", "as"},
    "de": {"und", "der", "die", "das", "mit", "für", "wir", "sie", "ist", "ein", "eine", "im", "zu", "den", "oder", "bei"},
    "it": {"il", "di", "che", "per", "con", "una", "del", "della", "le", "gli", "nel", "sono", "alla", "dei", "e"},
    "es": {"el", "los", "las", "para", "con", "una", "del", "que", "en", "por", "nuestro", "y", "se", "como"},
    "nl": {"het", "een", "van", "en", "voor", "met", "je", "wij", "bij", "zijn", "naar", "ons", "op", "te"},
}


def guess_language(text: str) -> str:
    words = re.findall(r"[a-zà-ÿ]+", text.lower())[:600]
    if len(words) < 12:
        return "en"
    scores = {lang: sum(word in stop for word in words) for lang, stop in _STOPWORDS.items()}
    return max(scores, key=scores.get)


_EMAIL = re.compile(r"(?<![%A-Za-z0-9._+-])[A-Za-z0-9][A-Za-z0-9._+-]{0,63}@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}")
_OBFUSCATED = re.compile(
    r"([A-Za-z0-9][A-Za-z0-9._-]{1,40})\s*(?:\[at\]|\(at\)|\{at\}|\sat\s|\[arobase\]|\(arobase\))\s*"
    r"([A-Za-z0-9-]+(?:\s*(?:\[dot\]|\(dot\)|\sdot\s|\.)\s*[A-Za-z0-9-]+)+)",
    re.I,
)
_GENERIC_MAILBOX = re.compile(r"^(no-?reply|noreply|donotreply|privacy|dpo|gdpr|rgpd|webmaster|abuse|postmaster|example|test|accessibilit)", re.I)
# Anti-spam decoys such as "name@myinstitutionname.fr" or "first.last@domain.com" are not addresses.
_PLACEHOLDER_DOMAIN = re.compile(
    r"(myinstitution|institutionname|yourdomain|mydomain|example|domain\.(com|org|fr)$|^email\.|nospam|no-spam|removethis|"
    r"remove-this|invalid|localhost|sentry|wixpress|company\.com$|universit[ey]\.(edu|fr|com)$|lab\.(fr|com|org)$)", re.I)


def extract_emails(text: str) -> list[str]:
    found = [match.group(0).strip(".").lower() for match in _EMAIL.finditer(text or "")]
    for match in _OBFUSCATED.finditer(text or ""):
        domain = re.sub(r"\s*(?:\[dot\]|\(dot\)|\sdot\s|\.)\s*", ".", match.group(2), flags=re.I)
        found.append(f"{match.group(1)}@{domain}".lower())
    unique = []
    for email in found:
        local, domain = email.split("@", 1)
        if _GENERIC_MAILBOX.search(local) or email.endswith((".png", ".jpg", ".gif", ".svg", ".webp")):
            continue
        if _PLACEHOLDER_DOMAIN.search(domain) or re.search(r"(firstname|lastname|surname|prenom|^nom$|^name$)", local):
            continue
        if email not in unique:
            unique.append(email)
    return unique


_MONTHS = {
    "jan": 1, "janv": 1, "january": 1, "janvier": 1, "feb": 2, "fev": 2, "february": 2, "fevrier": 2,
    "mar": 3, "march": 3, "mars": 3, "apr": 4, "avr": 4, "april": 4, "avril": 4, "may": 5, "mai": 5,
    "jun": 6, "june": 6, "juin": 6, "jul": 7, "july": 7, "juil": 7, "juillet": 7, "aug": 8, "august": 8,
    "aout": 8, "sep": 9, "sept": 9, "september": 9, "septembre": 9, "oct": 10, "october": 10, "octobre": 10,
    "nov": 11, "november": 11, "novembre": 11, "dec": 12, "december": 12, "decembre": 12,
}


def parse_date(value: object) -> Optional[str]:
    """Best-effort conversion of a date-like value to ISO ``YYYY-MM-DD``."""
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        try:
            seconds = value / 1000 if value > 1e11 else value
            return datetime.utcfromtimestamp(seconds).date().isoformat()
        except (OverflowError, OSError, ValueError):
            return None
    text = clean(value)
    match = re.search(r"(\d{4})-(\d{2})-(\d{2})", text)
    if match:
        return match.group(0)
    match = re.search(r"(\d{1,2})[/.](\d{1,2})[/.](\d{4})", text)
    if match:
        day, month, year = int(match.group(1)), int(match.group(2)), int(match.group(3))
        try:
            return date(year, month, day).isoformat()
        except ValueError:
            return None
    match = re.search(r"(\d{1,2})\s+([A-Za-zéûôÉ]+)\.?\s+(\d{4})", text)
    if match and fold(match.group(2)) in _MONTHS:
        try:
            return date(int(match.group(3)), _MONTHS[fold(match.group(2))], int(match.group(1))).isoformat()
        except ValueError:
            return None
    match = re.search(r"([A-Za-zéû]+)\.?\s+(\d{1,2}),?\s+(\d{4})", text)
    if match and fold(match.group(1)) in _MONTHS:
        try:
            return date(int(match.group(3)), _MONTHS[fold(match.group(1))], int(match.group(2))).isoformat()
        except ValueError:
            return None
    return None


# ------------------------------------------------------------------ geography
_COUNTRY_ALIASES: dict[str, tuple[str, ...]] = {
    "FR": ("france", "frankreich", "francia"),
    "IT": ("italy", "italia", "italie", "italien"),
    "BE": ("belgium", "belgique", "belgie", "belgien"),
    "DE": ("germany", "deutschland", "allemagne", "alemania"),
    "LU": ("luxembourg", "luxemburg"),
    "CH": ("switzerland", "suisse", "schweiz", "svizzera"),
    "NL": ("netherlands", "the netherlands", "nederland", "pays-bas", "holland"),
    "ES": ("spain", "espana", "espagne", "spanien"),
    "PT": ("portugal",),
    "AT": ("austria", "osterreich", "autriche"),
    "IE": ("ireland", "irlande"),
    "SE": ("sweden", "sverige", "suede"),
    "DK": ("denmark", "danmark", "danemark"),
    "FI": ("finland", "suomi", "finlande"),
    "NO": ("norway", "norge", "norvege"),
    "PL": ("poland", "polska", "pologne"),
    "CZ": ("czech republic", "czechia", "tchequie"),
    "GB": ("united kingdom", "uk", "england", "scotland", "wales", "great britain", "royaume-uni"),
    "GR": ("greece", "grece"),
    "HU": ("hungary", "hongrie"),
    "RO": ("romania", "roumanie"),
    "EE": ("estonia",),
    "LV": ("latvia",),
    "LT": ("lithuania",),
    "SI": ("slovenia",),
    "SK": ("slovakia",),
    "HR": ("croatia",),
    "BG": ("bulgaria",),
    "MT": ("malta",),
    "CY": ("cyprus",),
    "IS": ("iceland",),
    "MC": ("monaco",),
    "EG": ("egypt", "egypte"),
    "AE": ("united arab emirates", "uae", "u.a.e", "emirats arabes unis", "emirates"),
    "SA": ("saudi arabia", "ksa", "kingdom of saudi arabia", "arabie saoudite"),
    "QA": ("qatar",),
    "KW": ("kuwait", "koweit"),
    "BH": ("bahrain", "bahrein"),
    "OM": ("oman",),
    # Recognised so that offers there can be discarded explicitly.
    "US": ("united states", "usa", "u.s.", "united states of america", "etats-unis"),
    "CA": ("canada",),
    "IN": ("india", "inde"),
    "CN": ("china", "chine"),
    "JP": ("japan", "japon"),
    "SG": ("singapore", "singapour"),
    "AU": ("australia", "australie"),
    "BR": ("brazil", "bresil"),
    "MX": ("mexico", "mexique"),
    "IL": ("israel",),
    "TR": ("turkey", "turkiye", "turquie"),
    "KR": ("south korea", "korea"),
    "MA": ("morocco", "maroc"),
    "TN": ("tunisia", "tunisie"),
    "DZ": ("algeria", "algerie"),
    "ZA": ("south africa",),
    "NG": ("nigeria",),
    "KE": ("kenya",),
    "PK": ("pakistan",),
    "PH": ("philippines",),
    "MY": ("malaysia",),
    "TW": ("taiwan",),
    "HK": ("hong kong",),
    "AR": ("argentina",),
    "CO": ("colombia",),
    "UA": ("ukraine",),
    "RS": ("serbia",),
    "JO": ("jordan", "jordanie"),
    "LB": ("lebanon", "liban"),
}

_CITIES: dict[str, tuple[str, ...]] = {
    "FR": ("paris", "lyon", "marseille", "toulouse", "grenoble", "nantes", "rennes", "lille", "bordeaux",
           "nice", "sophia antipolis", "sophia-antipolis", "strasbourg", "montpellier", "saclay", "palaiseau",
           "gif-sur-yvette", "gif sur yvette", "orsay", "nancy", "villeneuve d'ascq", "villeneuve-d'ascq",
           "la defense", "courbevoie", "puteaux", "nanterre", "boulogne-billancourt", "issy-les-moulineaux",
           "massy", "velizy", "velizy-villacoublay", "guyancourt", "versailles", "le chesnay", "rocquencourt",
           "clermont-ferrand", "brest", "lannion", "caen", "rouen", "dijon", "saint-etienne", "aix-en-provence",
           "valbonne", "biot", "meylan", "montbonnot", "villeurbanne", "levallois-perret", "neuilly-sur-seine",
           "saint-denis", "montrouge", "chatillon", "cergy", "evry", "compiegne", "belfort", "annecy",
           "ile-de-france", "idf", "blagnac", "elancourt", "gennevilliers", "rungis", "chusclan", "cadarache",
           "marcoule", "poitiers", "limoges", "tours", "orleans", "angers", "le mans", "metz", "reims",
           "amiens", "pau", "bidart", "toulon", "avignon", "saint-nazaire", "bezons", "rueil-malmaison",
           "saint-ouen", "pantin", "ivry-sur-seine", "bagneux", "arcueil", "antony", "creteil", "noisy-le-grand"),
    "IT": ("milan", "milano", "rome", "roma", "turin", "torino", "bologna", "trento", "pisa", "genoa",
           "genova", "naples", "napoli", "florence", "firenze", "padua", "padova", "venice", "venezia",
           "bari", "modena", "verona", "trieste", "catania", "palermo", "cagliari", "bolzano", "pavia"),
    "BE": ("brussels", "bruxelles", "brussel", "leuven", "louvain", "louvain-la-neuve", "ghent", "gent",
           "gand", "antwerp", "antwerpen", "anvers", "liege", "namur", "mons", "charleroi", "mechelen",
           "kortrijk", "hasselt", "bruges", "zaventem", "diegem"),
    "DE": ("berlin", "munich", "munchen", "muenchen", "hamburg", "frankfurt", "stuttgart", "cologne",
           "koln", "dusseldorf", "duesseldorf", "karlsruhe", "darmstadt", "saarbrucken", "saarbruecken",
           "tubingen", "tuebingen", "heidelberg", "aachen", "dresden", "leipzig", "bonn", "nuremberg",
           "nurnberg", "erlangen", "kaiserslautern", "potsdam", "renningen", "hildesheim", "walldorf",
           "ingolstadt", "wolfsburg", "hannover", "bremen", "mannheim", "freiburg", "ulm", "regensburg",
           "garching", "ludwigsburg", "leonberg", "abstatt", "reutlingen", "schwieberdingen", "gerlingen"),
    "LU": ("luxembourg city", "esch-sur-alzette", "belval", "kirchberg", "belvaux"),
    "CH": ("zurich", "zuerich", "geneva", "geneve", "lausanne", "basel", "bern", "lugano", "martigny",
           "zug", "neuchatel", "winterthur", "st. gallen", "ruschlikon"),
    "NL": ("amsterdam", "rotterdam", "eindhoven", "utrecht", "delft", "the hague", "den haag", "leiden",
           "groningen", "nijmegen", "enschede", "veldhoven"),
    "ES": ("madrid", "barcelona", "valencia", "seville", "sevilla", "bilbao", "malaga", "zaragoza",
           "san sebastian", "donostia"),
    "PT": ("lisbon", "lisboa", "porto", "coimbra", "braga", "aveiro"),
    "AT": ("vienna", "wien", "graz", "linz", "salzburg", "innsbruck", "klosterneuburg"),
    "IE": ("dublin", "cork", "galway", "limerick"),
    "SE": ("stockholm", "gothenburg", "goteborg", "malmo", "lund", "uppsala", "linkoping"),
    "DK": ("copenhagen", "kobenhavn", "aarhus", "odense", "lyngby"),
    "FI": ("helsinki", "espoo", "tampere", "oulu", "turku"),
    "NO": ("oslo", "trondheim", "bergen", "stavanger"),
    "PL": ("warsaw", "warszawa", "krakow", "wroclaw", "gdansk", "poznan", "lodz", "katowice"),
    "CZ": ("prague", "praha", "brno", "ostrava"),
    "GB": ("london", "cambridge", "oxford", "edinburgh", "manchester", "bristol", "glasgow", "belfast",
           "leeds", "birmingham"),
    "GR": ("athens", "thessaloniki"),
    "HU": ("budapest",),
    "RO": ("bucharest", "cluj", "cluj-napoca", "timisoara", "iasi"),
    "EE": ("tallinn", "tartu"),
    "LV": ("riga",),
    "LT": ("vilnius", "kaunas"),
    "BG": ("sofia",),
    "HR": ("zagreb",),
    "SI": ("ljubljana",),
    "SK": ("bratislava",),
    "EG": ("cairo", "le caire", "giza", "alexandria", "new cairo", "6th of october", "maadi", "smart village",
           "nasr city", "heliopolis", "sheikh zayed"),
    "AE": ("dubai", "abu dhabi", "sharjah", "masdar", "al ain", "ras al khaimah", "ajman"),
    "SA": ("riyadh", "jeddah", "dhahran", "thuwal", "khobar", "al khobar", "dammam", "neom", "jubail"),
    "QA": ("doha", "lusail", "education city"),
    "KW": ("kuwait city",),
    "BH": ("manama",),
    "OM": ("muscat",),
    "US": ("new york", "san francisco", "seattle", "boston", "austin", "chicago", "los angeles", "palo alto",
           "mountain view", "sunnyvale", "santa clara", "san jose", "washington", "atlanta", "denver",
           "redmond", "menlo park", "cupertino", "san diego", "nyc", "california", "texas", "bellevue"),
    "CA": ("toronto", "montreal", "vancouver", "ottawa", "waterloo"),
    "IN": ("bangalore", "bengaluru", "hyderabad", "pune", "mumbai", "chennai", "gurgaon", "gurugram", "noida", "delhi"),
    "CN": ("beijing", "shanghai", "shenzhen", "hangzhou"),
    "JP": ("tokyo", "osaka"),
    "IL": ("tel aviv", "haifa", "jerusalem"),
    "TR": ("istanbul", "ankara", "izmir"),
    "MA": ("casablanca", "rabat", "tanger"),
    "TN": ("tunis",),
    "JO": ("amman",),
}

_ISO3 = {"FRA": "FR", "ITA": "IT", "BEL": "BE", "DEU": "DE", "LUX": "LU", "CHE": "CH", "NLD": "NL", "ESP": "ES",
         "PRT": "PT", "AUT": "AT", "IRL": "IE", "SWE": "SE", "DNK": "DK", "FIN": "FI", "NOR": "NO", "POL": "PL",
         "CZE": "CZ", "GBR": "GB", "EGY": "EG", "ARE": "AE", "SAU": "SA", "QAT": "QA", "KWT": "KW", "BHR": "BH",
         "OMN": "OM", "USA": "US", "CAN": "CA", "IND": "IN"}

_ALIAS_INDEX: list[tuple[re.Pattern[str], str]] = []
_CITY_INDEX: dict[str, str] = {}


def _build_indexes() -> None:
    for code, names in _COUNTRY_ALIASES.items():
        for name in names:
            _ALIAS_INDEX.append((re.compile(rf"(?<![a-z]){re.escape(name)}(?![a-z])"), code))
    _ALIAS_INDEX.sort(key=lambda item: -len(item[0].pattern))
    for code, cities in _CITIES.items():
        for city in cities:
            _CITY_INDEX.setdefault(city, code)


_build_indexes()
_CITY_PATTERN = re.compile(
    r"(?<![a-z])(" + "|".join(sorted((re.escape(city) for city in _CITY_INDEX), key=len, reverse=True)) + r")(?![a-z])"
)


def normalise_country(code: object) -> Optional[str]:
    text = clean(code).upper()
    if len(text) == 2 and text.isalpha():
        return "GB" if text == "UK" else text
    if len(text) == 3 and text in _ISO3:
        return _ISO3[text]
    return None


def detect_countries(location: object) -> list[str]:
    """Return every ISO-2 country recognisable in a free-text location."""
    text = fold(location)
    if not text:
        return []
    found: list[str] = []
    for pattern, code in _ALIAS_INDEX:
        if pattern.search(text) and code not in found:
            found.append(code)
    for match in _CITY_PATTERN.finditer(text):
        code = _CITY_INDEX[match.group(1)]
        if code not in found:
            found.append(code)
    if not found:
        # Bare ISO codes ("Paris, FR", "AE, Dubai") are only trusted as a last resort.
        for token in re.findall(r"(?:^|[\s,;(/|-])([A-Z]{2})(?=$|[\s,;)/|-])", clean(location)):
            code = "GB" if token == "UK" else token
            if code in _COUNTRY_ALIASES and code not in found:
                found.append(code)
    return found


def detect_city(location: object, country: Optional[str]) -> Optional[str]:
    text = fold(location)
    for match in _CITY_PATTERN.finditer(text):
        if country is None or _CITY_INDEX[match.group(1)] == country:
            return match.group(1).title()
    first = clean(location).split(",")[0].strip()
    return first[:60] if first and len(first) < 60 else None


def pick_country(candidates: Iterable[str], allowed: Iterable[str], order: Iterable[str]) -> Optional[str]:
    """Choose the best target country among several listed locations."""
    allowed_set = set(allowed)
    ranked = [code for code in order if code in allowed_set]
    options = [code for code in candidates if code in allowed_set]
    if not options:
        return None
    return min(options, key=lambda code: ranked.index(code) if code in ranked else 999)
