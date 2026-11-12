"""Explainable fit scoring for internship offers.

The score (0-100) is a sum of six visible parts — domain, internship type,
location priority, timing, skills and language — minus eligibility penalties.
Nothing is hidden: ``score_parts`` and ``flags`` are stored with each offer and
shown in the interface so a ranking can always be questioned.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Optional

from .text import fold


def _rx(*terms: str) -> re.Pattern[str]:
    return re.compile(r"(?<![a-z0-9])(?:" + "|".join(terms) + r")(?![a-z0-9])", re.I)


# Patterns are applied to accent-folded, lower-cased text.
DOMAIN_GROUPS: tuple[tuple[str, float, re.Pattern[str]], ...] = (
    ("NLP / LLM", 1.0, _rx(
        r"nlp", r"natural language", r"traitement (?:automatique )?(?:du |des |de la )?langues?(?: naturelles?)?",
        r"tal", r"taln", r"llms?", r"large language models?", r"language models?", r"modeles? de langues?",
        r"modeles? de langage", r"rag", r"retrieval[- ]augmented", r"generation augmentee", r"gen ?ai",
        r"generative ai", r"ia generative", r"intelligence artificielle generative", r"agentic", r"agentique",
        r"ai agents?", r"agents? ia", r"multi[- ]agents?", r"chatbots?", r"conversational", r"conversationnel(?:le)?s?",
        r"text mining", r"fouille de textes?", r"transformers?", r"prompt(?:ing| engineering)?", r"fine[- ]tun\w+",
        r"machine translation", r"traduction automatique", r"multilingual", r"multilingue", r"semantic search",
        r"recherche semantique", r"embeddings?", r"information retrieval", r"recherche d'information",
        r"question answering", r"summari[sz]ation", r"speech recognition", r"reconnaissance vocale", r"asr",
        r"knowledge graphs?", r"graphes? de connaissances?", r"computational linguistics", r"linguistique",
        r"foundation models?", r"modeles? de fondation", r"vlm", r"gpt", r"bert", r"hugging ?face", r"langchain",
        r"sprachmodell\w*", r"sprachverarbeitung", r"linguaggio naturale",
    )),
    ("Machine learning", 0.82, _rx(
        r"machine learning", r"apprentissage (?:automatique|machine|profond|statistique|par renforcement)",
        r"deep learning", r"neural networks?", r"reseaux? de neurones", r"artificial intelligence",
        r"intelligence artificielle", r"ai", r"ia", r"ml", r"data scien\w+", r"scientifique des donnees",
        r"reinforcement learning", r"computer vision", r"vision par ordinateur", r"mlops", r"explainab\w+",
        r"explicab\w+", r"interpretab\w+", r"xai", r"trustworthy", r"ia de confiance", r"anomaly detection",
        r"detection d'anomalies", r"time series", r"series temporelles", r"forecasting", r"prevision",
        r"recommender", r"recommandation", r"graph neural", r"gnn", r"pytorch", r"tensorflow", r"scikit",
        r"kunstliche intelligenz", r"ki", r"maschinelles lernen", r"intelligenza artificiale",
        r"apprendimento automatico", r"predictive model\w+", r"modelisation predictive", r"generative",
        r"diffusion models?", r"federated learning", r"representation learning", r"self[- ]supervised",
        r"research scientist", r"applied scientist", r"research engineer", r"ingenieur de recherche",
    )),
    ("Data & analytics", 0.58, _rx(
        r"data analy\w+", r"analyse de donnees", r"data engineer\w*", r"ingenieur donnees", r"ingenieur data",
        r"big data", r"business intelligence", r"analytics", r"power ?bi", r"etl", r"data quality",
        r"qualite des donnees", r"data pipeline\w*", r"data platform", r"data", r"donnees", r"statisti\w+",
        r"optimi[sz]ation", r"recherche operationnelle", r"operations research", r"quantitative", r"quant",
        r"datenanalyse", r"daten", r"dati", r"sql", r"tableau", r"dashboard\w*", r"dataviz", r"data visuali[sz]ation",
    )),
    ("Software", 0.3, _rx(
        r"software engineer\w*", r"developpeu(?:r|se)", r"developer", r"python", r"backend", r"back-end",
        r"full[- ]?stack", r"cloud", r"devops", r"informatique", r"logiciel", r"software", r"entwickler\w*",
        r"sviluppatore", r"sre", r"platform engineer\w*", r"api",
    )),
)

SKILL_PATTERNS: dict[str, re.Pattern[str]] = {
    "Python": _rx("python"), "PyTorch": _rx("pytorch", "torch"), "Hugging Face": _rx(r"hugging ?face", "transformers"),
    "scikit-learn": _rx(r"scikit[- ]?learn", "sklearn"), "LangChain": _rx("langchain", "llamaindex"),
    "Vector DB": _rx("faiss", "chromadb", "chroma", r"vector (?:store|database|db)", "qdrant", "milvus", "pinecone", "weaviate"),
    "RAG": _rx("rag", r"retrieval[- ]augmented"), "spaCy": _rx("spacy"), "XGBoost": _rx("xgboost", "catboost", "lightgbm", "gradient boosting"),
    "SQL": _rx("sql", "postgres", "postgresql"), "FastAPI": _rx("fastapi", "flask", "django"),
    "React": _rx("react", r"next\.?js", "typescript", "angular"), "Docker": _rx("docker", "kubernetes", r"ci/cd"),
    "MLOps": _rx("mlops", "mlflow"), "pandas": _rx("pandas", "numpy"), "C++": _rx(r"c\+\+"), "Git": _rx("git", "github", "gitlab"),
    "Streamlit": _rx("streamlit", "gradio"), "ONNX": _rx("onnx"), "Power BI": _rx(r"power ?bi"),
}

INTERNSHIP = _rx(
    r"intern", r"interns", r"internship", r"internships", r"stage", r"stages", r"stagiaire", r"pfe",
    r"fin d'etudes", r"projet de fin d'etudes", r"end[- ]of[- ]stud(?:y|ies)", r"final[- ]year", r"master'?s? thesis",
    r"masterarbeit", r"abschlussarbeit", r"praktikum", r"praktikant(?:in)?", r"pflichtpraktikum", r"tirocinio",
    r"stagista", r"tesi", r"trainee", r"traineeship", r"afstudeer\w*", r"stagiair", r"practicas", r"becari[oa]",
    r"estagio", r"m2", r"visiting student", r"student researcher", r"research intern", r"thesis",
)
PFE = _rx(
    r"pfe", r"fin d'etudes", r"end[- ]of[- ]stud(?:y|ies)", r"final[- ]year", r"master'?s? thesis", r"master thesis",
    r"masterarbeit", r"abschlussarbeit", r"m2", r"bac ?\+ ?5", r"6 mois", r"six mois", r"6[- ]months?", r"six[- ]months?",
    r"5 a 6 mois", r"4 a 6 mois", r"tesi magistrale", r"tesi di laurea", r"de fin de cycle", r"ingenieur 3a",
    r"derniere annee", r"visiting student", r"graduation internship", r"afstudeerstage",
)
RESEARCH = _rx(r"research", r"recherche", r"r&d", r"forschung", r"ricerca", r"scientist", r"lab", r"laboratoire")
NOT_INTERNSHIP = _rx(
    r"phd", r"ph\.d", r"doctorant\w*", r"doctoral", r"doctorat", r"these cifre", r"cifre", r"post[- ]?doc\w*",
    r"postdoctoral", r"professor", r"professeur", r"maitre de conferences", r"ater", r"lecturer", r"cdi",
    r"senior", r"staff", r"principal", r"director", r"directeur", r"head of", r"manager", r"lead", r"chef de",
    r"internal", r"interne", r"international", r"internist",
)
# "apprentissage" alone means "learning" in French, so only contract wordings are matched.
APPRENTICESHIP = _rx(
    r"alternance", r"alternant(?:e|s|es)?", r"contrat d'apprentissage", r"en apprentissage", r"apprenti(?:e|s|es)?",
    r"apprenticeship", r"contrat pro\w*", r"work[- ]study", r"duales studium", r"ausbildung", r"apprendistato",
)
# Case-sensitive on purpose: "vie privée" must not be read as a V.I.E contract.
VIE_ACRONYM = re.compile(r"\bV\.?I\.?E\b")
ZRR = _rx(r"zone a regime restrictif", r"zrr", r"restricted area", r"enquete administrative")
WORKING_STUDENT = _rx(r"werkstudent\w*", r"working student", r"student job", r"studentische\w*", r"hiwi", r"job etudiant")
SUMMER = _rx(r"summer", r"ete", r"sommer", r"estate", r"verano")
RESTRICTED = _rx(
    r"security clearance", r"habilitation(?: defense| de securite| secret)?", r"secret defense",
    r"confidentiel defense", r"nationalite francaise", r"french nationality", r"eu citizen\w*",
    r"citizen of (?:an? )?(?:eu|european)", r"ressortissants? (?:de l')?(?:union europeenne|ue)", r"itar",
    r"export control", r"us citizen\w*", r"u\.s\. citizen\w*", r"eu/eea nationals?",
    r"nationals? of (?:eu|cern) member states?",
)
NO_SPONSOR = _rx(
    r"no visa sponsorship", r"not (?:able to )?(?:offer|provide) (?:visa )?sponsorship", r"unable to sponsor",
    r"must (?:already )?have (?:the )?right to work", r"valid work (?:permit|authori[sz]ation) (?:is )?required",
    r"without (?:visa )?sponsorship", r"eligible to work in the (?:eu|uk|netherlands|germany)",
)
ENROLLED_LOCAL = _rx(
    r"enrolled (?:at|in) an? (?:german|dutch|swiss|italian|spanish|austrian|uk|british|american|us|u\.s\.) universit\w+",
    r"immatrikuliert\w*", r"eingeschrieben\w*", r"iscritt[oi] (?:a|presso) un'universita italiana",
    r"etudiant\w* dans (?:une|un) (?:ecole|universite|etablissement) francais\w*",
)
LANGUAGE_REQUIRED = {
    "German": _rx(r"fluent (?:in )?german", r"german (?:is )?(?:required|mandatory|fluency)", r"deutschkenntnisse",
                  r"fliessend(?:e|es)? deutsch", r"verhandlungssicher\w* deutsch", r"sehr gute deutsch\w*", r"german c1", r"german b2"),
    "Italian": _rx(r"fluent (?:in )?italian", r"italian (?:is )?(?:required|mandatory)", r"ottima conoscenza (?:della lingua )?italian\w+", r"madrelingua italian\w+"),
    "Dutch": _rx(r"fluent (?:in )?dutch", r"dutch (?:is )?(?:required|mandatory)", r"vloeiend nederlands", r"beheersing van (?:de )?nederlandse"),
    "Spanish": _rx(r"fluent (?:in )?spanish", r"spanish (?:is )?(?:required|mandatory)", r"espanol nativo"),
    "Arabic": _rx(r"native arabic"),
}
TARGET_YEAR = _rx(r"2027")
EARLY_START = _rx(
    r"janvier", r"january", r"januar", r"gennaio", r"fevrier", r"february", r"februar", r"febbraio", r"mars",
    r"march", r"marz", r"marzo", r"debut 2027", r"early 2027", r"q1 2027", r"winter 2027", r"spring 2027",
    r"printemps 2027", r"s1 2027", r"h1 2027", r"1er semestre 2027",
)
STALE_START = _rx(
    r"summer 2026", r"ete 2026", r"fall 2026", r"autumn 2026", r"automne 2026", r"septembre 2026",
    r"september 2026", r"octobre 2026", r"october 2026", r"novembre 2026", r"november 2026", r"asap",
    r"des que possible", r"immediately", r"immediate start", r"ab sofort", r"start(?:ing)? (?:date )?:? ?(?:now|immediate\w*)",
)
SIX_MONTHS = _rx(r"6 mois", r"six mois", r"6[- ]months?", r"six[- ]months?", r"5 a 6 mois", r"4 a 6 mois",
                 r"4[- ]6 mois", r"5[- ]6 mois", r"4 to 6 months", r"5 to 6 months", r"5-6 months", r"4-6 months",
                 r"6 monate", r"6 mesi", r"24 weeks", r"26 weeks")


GRADE_A, GRADE_B, GRADE_C = 85, 72, 58


def grade(score: int) -> str:
    return "A" if score >= GRADE_A else "B" if score >= GRADE_B else "C" if score >= GRADE_C else "D"


@dataclass
class Assessment:
    score: int
    parts: dict[str, int]
    flags: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    contract: str = "internship"
    relevant: bool = True
    reason: str = ""

    @property
    def grade(self) -> str:
        return grade(self.score)


def is_internship_title(title: str) -> bool:
    """Cheap pre-filter used by connectors before fetching full descriptions."""
    text = fold(title)
    if not INTERNSHIP.search(text):
        return False
    stripped = re.sub(r"\binternational\w*|\binternal\w*|\binterne\b|\binternist\w*", " ", text)
    if not INTERNSHIP.search(stripped):
        return False
    return not re.search(r"(?<![a-z])(?:phd|ph\.d|doctorant\w*|doctoral|post[- ]?doc\w*|postdoctoral|these cifre|cifre)(?![a-z])", stripped) \
        or bool(re.search(r"master", stripped))


def domain_weight(text: str) -> tuple[float, list[str]]:
    """Return the strongest domain weight found in ``text`` and matching labels."""
    folded = fold(text)
    best, labels = 0.0, []
    for label, weight, pattern in DOMAIN_GROUPS:
        if pattern.search(folded):
            labels.append(label)
            best = max(best, weight)
    return best, labels


def is_relevant_title(title: str, minimum: float = 0.3) -> bool:
    return domain_weight(title)[0] >= minimum


def assess(
    *,
    title: str,
    description: str,
    country: Optional[str],
    tier_weight: int,
    lang: str = "en",
    remote: bool = False,
    source_kind: str = "",
    skills: Iterable[str] = (),
    trusted_internship: bool = False,
) -> Assessment:
    """Score one offer.  ``trusted_internship`` is set by sources that only list internships."""
    title_text = fold(title)
    body_text = fold(description)[:12000]
    everything = f"{title_text} {body_text}"
    flags: list[str] = []
    tags: list[str] = []
    parts: dict[str, int] = {}

    # ---- hard exclusions ---------------------------------------------------
    if not trusted_internship and not INTERNSHIP.search(everything[:1500]):
        return Assessment(0, {}, relevant=False, reason="not an internship")
    title_blockers = NOT_INTERNSHIP.findall(title_text)
    real_blockers = [word for word in title_blockers if word not in ("internal", "interne", "international", "internist", "lead", "manager", "staff")]
    if real_blockers and not re.search(r"master|intern|stage|pfe|stagiaire", title_text):
        return Assessment(0, {}, relevant=False, reason=f"not an internship ({real_blockers[0]})")
    if re.search(r"(?<![a-z])(?:phd|ph\.d|doctorant\w*|post[- ]?doc\w*|postdoctoral|cifre)(?![a-z])", title_text) and "master" not in title_text:
        return Assessment(0, {}, relevant=False, reason="doctoral or post-doctoral position")

    # ---- 1. domain (0-40) ---------------------------------------------------
    title_weight, title_labels = domain_weight(title_text)
    body_weight, body_labels = 0.0, []
    for label, weight, pattern in DOMAIN_GROUPS:
        hits = len(pattern.findall(body_text))
        if hits:
            body_labels.append(label)
            body_weight = max(body_weight, weight * (1.0 if hits >= 3 else 0.8 if hits == 2 else 0.6))
    if title_weight:
        domain = 45 * (0.68 * title_weight + 0.32 * max(body_weight, title_weight * 0.6))
    else:
        domain = 45 * 0.55 * body_weight
    parts["domain"] = round(domain)
    tags.extend(dict.fromkeys(title_labels + body_labels))
    if parts["domain"] < 12:
        return Assessment(0, parts, relevant=False, reason="outside the AI / data domain")

    # ---- 2. internship type (0-12) -----------------------------------------
    contract = "internship"
    if PFE.search(everything):
        kind, contract = 12, "pfe"
        tags.append("PFE / final-year")
    elif RESEARCH.search(title_text) or source_kind in ("research", "programme"):
        kind, contract = 10, "research internship"
        tags.append("Research")
    elif re.search(r"(?<![a-z])(?:trainee|traineeship|graduate program\w*)(?![a-z])", title_text):
        kind, contract = 4, "traineeship"
    else:
        kind = 8
    penalty = 0
    if APPRENTICESHIP.search(title_text) or (APPRENTICESHIP.search(body_text[:600]) and not re.search(r"stage|intern", title_text)):
        flags.append("Apprenticeship / alternance — requires enrolment in a local school")
        penalty += 45
        contract = "apprenticeship"
    if VIE_ACRONYM.search(title) or "volontariat international" in title_text:
        flags.append("V.I.E contract — reserved for EU/EEA nationals")
        penalty += 45
    if WORKING_STUDENT.search(title_text):
        flags.append("Working-student job — normally requires enrolment at a local university")
        penalty += 18
        contract = "working student"
    parts["type"] = kind

    # ---- 3. location (0-20) -------------------------------------------------
    parts["location"] = int(tier_weight) if country else (6 if remote else 0)

    # ---- 4. timing (0-8) ----------------------------------------------------
    timing = 4
    if TARGET_YEAR.search(everything):
        timing += 2
    if EARLY_START.search(everything):
        timing += 1
    if SIX_MONTHS.search(everything):
        timing += 2
        tags.append("≈6 months")
    if STALE_START.search(everything) and not TARGET_YEAR.search(everything):
        timing -= 4
        flags.append("Start date looks like 2026 / immediate — check that a 2027 start is possible")
    if SUMMER.search(title_text) and not SIX_MONTHS.search(everything):
        timing -= 3
        flags.append("Summer programme — availability ends 30 June 2027, ask about an earlier start")
    parts["timing"] = max(0, min(8, timing))

    # ---- 5. skills (0-10) ---------------------------------------------------
    wanted = {fold(skill) for skill in skills}
    matched = [name for name, pattern in SKILL_PATTERNS.items() if pattern.search(body_text) or pattern.search(title_text)]
    if wanted:
        matched = [name for name in matched if fold(name) in wanted or any(fold(name) in item or item in fold(name) for item in wanted)] or matched
    parts["skills"] = min(10, 2 * len(matched))
    tags.extend(matched[:6])

    # ---- 6. language (0-5) --------------------------------------------------
    language = 5
    for name, pattern in LANGUAGE_REQUIRED.items():
        if name != "Arabic" and pattern.search(everything):
            language = 0
            flags.append(f"{name} required")
            break
    else:
        if lang in ("de", "it", "nl", "es"):
            language = 2
            flags.append({"de": "Posting in German", "it": "Posting in Italian", "nl": "Posting in Dutch", "es": "Posting in Spanish"}[lang] + " — check the working language")
    parts["language"] = language

    # ---- eligibility --------------------------------------------------------
    if RESTRICTED.search(everything):
        flags.append("Nationality / security-clearance restriction mentioned")
        penalty += 22
    elif ZRR.search(everything):
        # Standard wording for French public labs: access is granted after a ministerial check
        # that non-EU interns routinely pass, but it adds weeks to the paperwork.
        flags.append("Restricted-access site (ZRR) — access clearance takes several weeks, apply early")
        penalty += 2
    if NO_SPONSOR.search(everything):
        flags.append("No visa sponsorship / existing right to work required")
        penalty += 15
    if ENROLLED_LOCAL.search(everything):
        flags.append("Requires enrolment at a local university")
        penalty += 15
    if re.search(r"convention de stage|internship agreement|tripartite", everything):
        tags.append("Convention de stage")
    if remote:
        tags.append("Remote possible")
    parts["penalty"] = -penalty

    score = max(0, min(100, sum(parts.values())))
    return Assessment(score, parts, flags, list(dict.fromkeys(tags)), contract, True)


def describe_parts(parts: dict[str, Any]) -> list[dict[str, Any]]:
    limits = {"domain": 45, "type": 12, "location": 20, "timing": 8, "skills": 10, "language": 5}
    labels = {"domain": "Domain fit", "type": "Internship type", "location": "Location priority",
              "timing": "Timing (Jan–Jun 2027)", "skills": "Skills overlap", "language": "Language"}
    result = [{"key": key, "label": labels[key], "value": int(parts.get(key, 0)), "max": limit} for key, limit in limits.items()]
    if parts.get("ai"):
        result.append({"key": "ai", "label": "AI second opinion", "value": int(parts["ai"]), "max": 8})
    if parts.get("penalty"):
        result.append({"key": "penalty", "label": "Eligibility penalties", "value": int(parts["penalty"]), "max": 0})
    return result
