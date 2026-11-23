"""Spontaneous e-mails to researchers, reproducing your Google Apps Script mailer.

The French text is the script's text; the English one is a faithful translation
(the script used Google Translate, which is not available here).  The same rules
pick the greeting, the research focus, the role sentence and the experience to
highlight.  The language — and therefore the CV, transcript and cover letter
attached — follows where the person works, never their name.
"""

from __future__ import annotations

import json
import re
from typing import Any, Optional

from .text import clean, fold

DURATION = {"FR": "4 à 6 mois", "EN": "4- to 6-month"}
AVAILABILITY = {"FR": "à partir de janvier 2027", "EN": "starting in January 2027"}
SUBJECT = {
    "FR": "Candidature – Stage de fin d’études en Data Science & IA – ENP Alger (2026–2027)",
    "EN": "Application – Final-year internship (PFE) in Data Science & AI – ENP Algiers (2026–2027)",
}
FOLLOW_UP_SUBJECT = {
    "FR": "Relance – Candidature PFE en Data Science & IA – ENP Alger (2026–2027)",
    "EN": "Follow-up – PFE application in Data Science & AI – ENP Algiers (2026–2027)",
}

# Where French is the working language: France, Monaco, the Maghreb, French-speaking
# Belgium, Romandy and Québec.  Everyone else gets the English e-mail and documents.
FRENCH_COUNTRIES = {"FR", "MC", "DZ", "MA", "TN"}
FRENCH_BELGIUM = re.compile(r"uclouvain|louvain-la-neuve|catholique de louvain|ulb|libre de bruxelles|li[eè]ge|namur|mons|saint-louis|gembloux|charleroi|hainaut")
FRENCH_SWITZERLAND = re.compile(r"epfl|lausanne|gen[eè]v|geneva|idiap|martigny|neuch[aâ]tel|fribourg|hes-so|heig|valais|sion|hepia|unige|unil")
FRENCH_CANADA = re.compile(r"qu[ée]bec|montr[ée]al|laval|sherbrooke|[ée]cole de technologie sup|\bets\b|uqam|uqac|uqtr|uqo|inrs|polytechnique montr|hec montr|ivado|mila")


def language_for(country: Optional[str], org: str = "", city: str = "") -> str:
    """FR where the institution works in French, EN elsewhere."""
    place = fold(f"{org} {city}")
    code = (country or "").upper()
    if code in FRENCH_COUNTRIES:
        return "FR"
    if code == "BE" and FRENCH_BELGIUM.search(place):
        return "FR"
    if code == "CH" and FRENCH_SWITZERLAND.search(place):
        return "FR"
    if code == "CA" and FRENCH_CANADA.search(place):
        return "FR"
    return "EN"


# ------------------------------------------------------------- contact data
def _details(contact: dict[str, Any]) -> dict[str, str]:
    """Title, lab, university, research areas and notes, from your sheet when available."""
    row: dict[str, Any] = {}
    if contact.get("source_row"):
        try:
            row = json.loads(contact["source_row"]) if isinstance(contact["source_row"], str) else contact["source_row"]
        except json.JSONDecodeError:
            row = {}
    topics = contact.get("topics") or []
    if isinstance(topics, str):
        try:
            topics = json.loads(topics)
        except json.JSONDecodeError:
            topics = [topics]
    return {
        "name": clean(contact.get("name")),
        "title": clean(row.get("title")) or ("Professeur" if re.search(r"professor|professeur", contact.get("role") or "", re.I) else ""),
        "lab": clean(row.get("lab")),
        "university": clean(row.get("university")) or ("" if row else clean(contact.get("org"))),
        "areas": clean(row.get("areas")) or ", ".join(topics[:3]),
        "notes": clean(row.get("notes")) or clean(contact.get("role")),
    }


def _norm(value: str) -> str:
    return fold(value).replace("’", " ").replace("'", " ")


# ---------------------------------------------------------------- pieces
def greeting(details: dict[str, str], language: str) -> str:
    full = details["name"]
    surname = (full.split() or [full])[-1]
    title, notes = _norm(details["title"]), _norm(details["notes"])
    professor = bool(re.search(r"professeur|professor|maitre de conferences|associate professor|full professor", f"{title} {notes}"))
    if language == "FR":
        if re.search(r"madame|mrs|\bms\b", title):
            return f"Madame la Professeure {surname}," if professor else f"Madame {surname},"
        if re.search(r"monsieur|\bmr\b", title):
            return f"Monsieur le Professeur {surname}," if professor else f"Monsieur {surname},"
        if re.search(r"professeur|professor", title):
            return f"Bonjour Professeur {surname},"
        return f"Bonjour {full},"
    if professor or re.search(r"professeur|professor", title):
        return f"Dear Professor {surname},"
    if re.search(r"madame|mrs|\bms\b", title):
        return f"Dear Ms {surname},"
    if re.search(r"monsieur|\bmr\b", title):
        return f"Dear Mr {surname},"
    if re.search(r"research scientist|chercheur|researcher|senior", notes):
        return f"Dear Dr {surname},"
    return f"Dear {full},"


_EXTRA_FOCUS = [
    (r"reseaux complexes|complex networks", "les réseaux complexes", "complex networks"),
    (r"ia explicable|explainable ai|explainable artificial intelligence", "l’intelligence artificielle explicable", "explainable AI"),
    (r"diffusion de l information|information diffusion", "la diffusion de l’information dans les réseaux", "information diffusion in networks"),
    (r"apprentissage geometrique|geometric learning|graph learning", "l’apprentissage sur graphes", "learning on graphs"),
    (r"imagerie medicale|medical imaging", "l’imagerie médicale", "medical imaging"),
    (r"systemes urbains|urban systems|mobilite|transportation", "les systèmes urbains et la mobilité", "urban systems and mobility"),
    (r"series temporelles|time series", "les séries temporelles", "time series"),
    (r"optimisation combinatoire|combinatorial optimization", "l’optimisation combinatoire", "combinatorial optimisation"),
]


def research_focus(details: dict[str, str], language: str) -> str:
    parts = [details["areas"]] if details["areas"] else []
    notes = _norm(details["notes"])
    for pattern, french, english in _EXTRA_FOCUS:
        label = french if language == "FR" else english
        if re.search(pattern, notes) and label.lower() not in " ".join(parts).lower():
            parts.append(label)
    return " ; ".join(parts) if language == "FR" else "; ".join(parts)


def affiliation(details: dict[str, str], language: str) -> str:
    lab, university = details["lab"], details["university"]
    named_lab = bool(lab) and not re.match(r"^(votre equipe|your (group|team)|the )", _norm(lab))
    if language == "EN":
        lab_name = re.sub(r"^the\s+", "", lab, flags=re.I)
        if lab_name and university and not re.match(r"^(votre|your)", _norm(lab)):
            return f"within {lab_name} at {university}"
        if university:
            return f"at {university}"
        return "within your team" if lab else ""
    if named_lab and university:
        return f"au sein de la structure «{lab}», rattachée à {_format_university(university)}"
    if named_lab:
        return f"au sein de la structure «{lab}»"
    if university:
        return f"au sein de {_format_university(university)}"
    return "au sein de votre équipe" if lab else ""


def _format_university(university: str) -> str:
    n = _norm(university)
    if re.match(r"^(universite|universita|ecole|institut|epfl)\b", n):
        return f"l’{university}"
    if n.startswith("eurecom"):
        return "EURECOM"
    return f"l’établissement «{university}»"


def role(details: dict[str, str], language: str) -> str:
    text = _norm(f"{details['title']} {details['notes']}")
    feminine = bool(re.search(r"madame|mrs|\bms\b", _norm(details["title"])))
    if re.search(r"full professor|professeure? des universites|professeure? depuis|professor", text):
        return ("professeure" if feminine else "professeur") if language == "FR" else "a professor"
    if re.search(r"associate professor|maitre de conferences", text):
        return ("enseignante-chercheuse" if feminine else "enseignant-chercheur") if language == "FR" else "a faculty member"
    if re.search(r"research scientist", text):
        return "chercheur" if language == "FR" else "a researcher"
    if re.search(r"director|directeur|directrice", text):
        return "responsable de recherche" if language == "FR" else "a research lead"
    return ""


def interest_paragraph(details: dict[str, str], language: str) -> str:
    focus, where, who = research_focus(details, language), affiliation(details, language), role(details, language)
    if language == "FR":
        sentence = "Vos thématiques de recherche" + (f" autour de {focus}" if focus else "") + " ont particulièrement retenu mon attention"
        if who:
            sentence += ", tout comme " + " ".join(part for part in (f"votre activité de {who}", where) if part)
        elif where:
            sentence += f", tout comme vos travaux {where}"
        return sentence + (". Je suis particulièrement intéressé par les approches qui mobilisent la science des données et "
                           "l’intelligence artificielle pour analyser des problèmes complexes et développer des méthodes utiles à des applications concrètes.")
    sentence = "Your research" + (f" on {focus}" if focus else "") + " particularly caught my attention"
    if who and where:
        sentence += f", as did your work as {who} {where}"
    elif where:
        sentence += f", as did your team’s work {where}"
    elif who:
        sentence += f", as did your work as {who}"
    return sentence + (". I am especially interested in approaches that use data science and artificial intelligence to analyse "
                       "complex problems and to develop methods that serve concrete applications.")


_EXPERIENCES = [
    (r"reseau|network|complex|graph|urbain|urban|spatial|geospatial|mobilite|transport",
     "Dans le projet NeoGAM, développé dans le cadre de l’Algerian Engineering Competition, j’ai conçu une plateforme d’aide à la décision géospatiale couvrant les 1 541 communes algériennes, avec un moteur de scoring hybride associant XGBoost au modèle gravitaire de Huff.",
     "In the NeoGAM project, developed for the Algerian Engineering Competition, I designed a geospatial decision-support platform covering Algeria’s 1,541 municipalities, with a hybrid scoring engine combining XGBoost and the Huff gravity model."),
    (r"nlp|llm|language|langage|text|texte|transformer|rag|information extraction",
     "J’ai notamment développé chez ECCBC un assistant de traitement des réclamations fondé sur le RAG, et travaillé en R&D sur des Transformers appliqués à la formulation de fluides industriels.",
     "At ECCBC, I built a RAG-based complaint-handling assistant, and in R&D I worked on Transformers applied to the formulation of industrial fluids."),
    (r"optimization|optimisation|operations research|recommender|recommendation|data mining|decision|scheduling",
     "J’ai notamment développé NeoGAM, une plateforme géospatiale d’aide à la décision combinant XGBoost et le modèle gravitaire de Huff, ainsi qu’un système de recommandation bancaire reposant sur XGBoost et l’explicabilité SHAP.",
     "I developed NeoGAM, a geospatial decision-support platform combining XGBoost with the Huff gravity model, as well as a banking recommendation system based on XGBoost and SHAP explainability."),
    (r"software engineering|software architecture|cloud|iot|distributed system",
     "Lors de mon stage à la Société Générale Algérie, j’ai conçu un pipeline de contrôle de données de bout en bout, depuis l’ingestion de données réelles et la formalisation des règles métier jusqu’à une application web intégrée validée avec les experts du contrôle comptable.",
     "During my internship at Société Générale Algérie, I designed an end-to-end data-control pipeline, from ingesting real data and formalising business rules to an integrated web application validated with the accounting-control experts."),
    (r"computer vision|vision par ordinateur|medical image|image processing|imagerie",
     "Ma formation m’a permis d’acquérir des bases solides en apprentissage automatique, réseaux de neurones et traitement des données, que je souhaite approfondir dans le cadre de problématiques de recherche en vision par ordinateur.",
     "My training has given me a solid grounding in machine learning, neural networks and data processing, which I would like to deepen on research questions in computer vision."),
]
_DEFAULT_EXPERIENCE = (
    "J’ai notamment travaillé sur un moteur de scoring interprétable de la qualité des données en assurance, combinant règles métier, heuristiques NLP et Random Forest, ainsi que sur un système de recommandation bancaire intégrant XGBoost et SHAP.",
    "I also worked on an interpretable data-quality scoring engine for insurance, combining business rules, NLP heuristics and Random Forest, and on a banking recommendation system using XGBoost and SHAP.",
)


def experience(details: dict[str, str], language: str) -> str:
    text = _norm(f"{details['areas']} {details['notes']}")
    for pattern, french, english in _EXPERIENCES:
        # Whole words only: the script's "graph" also matched inside "orthographe".
        if re.search(r"\b(?:" + pattern + ")", text):
            return french if language == "FR" else english
    return _DEFAULT_EXPERIENCE[0 if language == "FR" else 1]


def attachments_sentence(language: str, documents: list[dict[str, Any]], letter: bool, again: bool = False) -> str:
    """Name exactly what is attached, in the order the files appear."""
    kinds = [doc.get("doc_type") for doc in documents]
    names_fr = {"cv": "mon CV", "transcript": "mes relevés de notes", "recommendation": "mes lettres de recommandation",
                "language_test": "mes certificats de langue", "reference": "la liste de mes références", "certificate": "des attestations"}
    names_en = {"cv": "my CV", "transcript": "my transcripts", "recommendation": "my recommendation letters",
                "language_test": "my language certificates", "reference": "my references", "certificate": "some certificates"}
    names = names_fr if language == "FR" else names_en
    items = list(dict.fromkeys(names[kind] for kind in kinds if kind in names))
    if letter:
        items.insert(1 if items[:1] == [names["cv"]] else 0, "ma lettre de motivation" if language == "FR" else "my cover letter")
    if not items:
        return ""
    listed = items[0] if len(items) == 1 else ", ".join(items[:-1]) + (" et " if language == "FR" else " and ") + items[-1]
    if language == "FR":
        return f"Je joins à nouveau {listed} pour faciliter l’examen de ma candidature." if again else f"Vous trouverez en pièces jointes {listed}."
    return f"I am attaching {listed} again to make my application easier to review." if again else f"Please find attached {listed}."


def signature(profile: dict[str, Any], language: str) -> str:
    second = ("Élève ingénieur – Génie industriel, option Data Science et IA\nÉcole Nationale Polytechnique, Alger" if language == "FR"
              else "Engineering student – Industrial Engineering, Data Science & AI track\nÉcole Nationale Polytechnique, Algiers")
    return (f"{profile['full_name']}\n{second}\n{profile['phone']} | {profile['email']}\n"
            f"LinkedIn : {profile.get('linkedin', '')}\nGitHub : {profile.get('github', '')}" if language == "FR" else
            f"{profile['full_name']}\n{second}\n{profile['phone']} | {profile['email']}\n"
            f"LinkedIn: {profile.get('linkedin', '')}\nGitHub: {profile.get('github', '')}")


# ---------------------------------------------------------------- e-mails
def initial_email(contact: dict[str, Any], language: str, profile: dict[str, Any],
                  documents: list[dict[str, Any]], letter: bool = True) -> dict[str, str]:
    details = _details(contact)
    lang = "FR" if language == "FR" else "EN"
    attached = attachments_sentence(lang, documents, letter)
    if lang == "FR":
        body = [
            greeting(details, lang),
            "Actuellement élève ingénieur en dernière année de Génie industriel, option Data Science et Intelligence Artificielle, à "
            "l’École Nationale Polytechnique d’Alger, je prépare mon projet de fin d’études pour l’année universitaire 2026–2027. "
            f"Je souhaiterais réaliser ce PFE sous la forme d’un stage de recherche de {DURATION[lang]}, {AVAILABILITY[lang]}, au sein de votre équipe.",
            interest_paragraph(details, lang),
            "Au cours de mes cinq expériences de stage en banque, assurance, FMCG et R&D industrielle, j’ai développé des systèmes "
            "NLP/RAG et des modèles prédictifs sur des données réelles, en intervenant sur différentes étapes allant de la préparation "
            f"des données à l’évaluation et au déploiement. {experience(details, lang)} J’ai également obtenu deux deuxièmes places "
            "dans des compétitions nationales d’IA.",
            "Je serais particulièrement motivé à contribuer à un sujet en lien avec les thématiques de votre équipe, selon vos "
            "problématiques et besoins actuels. Je reste également ouvert à une proposition de sujet que vous jugeriez pertinente.",
            f"Mon établissement en Algérie peut établir et signer la convention de stage nécessaire. {attached}".strip(),
            "Je vous remercie sincèrement pour votre temps et l’attention portée à ma candidature. Je serais ravi de pouvoir échanger "
            "avec vous sur une éventuelle possibilité de collaboration.",
            "Cordialement,\n" + signature(profile, lang),
        ]
    else:
        body = [
            greeting(details, lang),
            "I am a final-year engineering student in Industrial Engineering, Data Science and Artificial Intelligence track, at "
            "École Nationale Polytechnique in Algiers, and I am preparing my final-year project (PFE) for the 2026–2027 academic year. "
            f"I would like to carry it out as a {DURATION[lang]} research internship in your team, {AVAILABILITY[lang]}.",
            interest_paragraph(details, lang),
            "During my five internships in banking, insurance, FMCG and industrial R&D, I developed NLP/RAG systems and predictive "
            "models on real-world data, working on every stage from data preparation to evaluation and deployment. "
            f"{experience(details, lang)} I also won second place in two national AI competitions.",
            "I would be particularly motivated to contribute to a topic related to your team’s research, depending on your current "
            "questions and needs, and I remain open to any topic you would consider relevant.",
            f"My institution in Algeria can issue and sign the required internship agreement. {attached}".strip(),
            "Thank you sincerely for your time and for considering my application. I would be delighted to discuss a possible "
            "collaboration with you.",
            "Kind regards,\n" + signature(profile, lang),
        ]
    return {"subject": SUBJECT[lang], "body": "\n\n".join(body)}


def follow_up_email(contact: dict[str, Any], language: str, profile: dict[str, Any],
                    documents: list[dict[str, Any]], letter: bool = False) -> dict[str, str]:
    details = _details(contact)
    lang = "FR" if language == "FR" else "EN"
    focus, where = research_focus(details, lang), affiliation(details, lang)
    attached = attachments_sentence(lang, documents, letter, again=True)
    if lang == "FR":
        body = [
            greeting(details, lang),
            "Je me permets de revenir vers vous à la suite de mon précédent message concernant la possibilité de réaliser mon projet "
            "de fin d’études au sein de votre équipe. Je reste vivement intéressé par vos travaux"
            + (f" autour de {focus}" if focus else "") + (f" {where}" if where else "") + ".",
            "Actuellement élève ingénieur en dernière année de Génie industriel, option Data Science et Intelligence Artificielle, à "
            f"l’École Nationale Polytechnique d’Alger, je recherche un stage de fin d’études de {DURATION[lang]}, {AVAILABILITY[lang]}. "
            "Mon parcours comprend cinq expériences en banque, assurance, FMCG et R&D industrielle, avec le développement de systèmes "
            "NLP/RAG et de modèles prédictifs sur des données réelles, de la préparation des données jusqu’à l’évaluation et au déploiement.",
            "Je serais reconnaissant de savoir si une possibilité d’accueil pourrait être envisagée au sein de votre équipe, "
            "éventuellement sur un sujet que vous jugeriez pertinent au regard de vos axes et besoins actuels. Mon école peut établir "
            "et signer la convention de stage nécessaire.",
            attached,
            "Je vous remercie pour votre temps et reste à votre disposition pour toute information complémentaire.",
            "Cordialement,\n" + signature(profile, lang),
        ]
    else:
        body = [
            greeting(details, lang),
            "I am following up on my previous message about the possibility of carrying out my final-year project in your team. "
            "I remain very interested in your work" + (f" on {focus}" if focus else "") + (f" {where}" if where else "") + ".",
            "I am a final-year engineering student in Industrial Engineering, Data Science and Artificial Intelligence track, at "
            f"École Nationale Polytechnique in Algiers, looking for a {DURATION[lang]} final-year internship {AVAILABILITY[lang]}. "
            "My background includes five internships in banking, insurance, FMCG and industrial R&D, building NLP/RAG systems and "
            "predictive models on real-world data, from data preparation to evaluation and deployment.",
            "I would be grateful to know whether hosting me in your team could be considered, possibly on a topic you would find "
            "relevant to your current research and needs. My school can issue and sign the required internship agreement.",
            attached,
            "Thank you for your time; I remain at your disposal for any further information.",
            "Kind regards,\n" + signature(profile, lang),
        ]
    return {"subject": FOLLOW_UP_SUBJECT[lang], "body": "\n\n".join(part for part in body if part)}


VERIFY_WARNING = re.compile(r"name derived from email|first name unknown|please verify|a verifier|à vérifier|a confirmer|à confirmer|"
                            r"email guessed|adresse a verifier|adresse à vérifier|bounce|rebond", re.I)


def needs_checking(contact: Optional[dict[str, Any]]) -> Optional[str]:
    """Your note flags the address or identity as unverified (same rule as the Apps Script).

    Only your own note counts: for a person imported from your sheet, the text after "Your note:".
    Marking the address verified in the app clears the flag.
    """
    if not contact:
        return None
    notes = contact.get("notes") or ""
    if "Address verified by you" in notes:
        return None
    own = notes.split("Your note:", 1)[1] if "Your note:" in notes else ("" if contact.get("source_row") else notes)
    match = VERIFY_WARNING.search(own)
    return match.group(0) if match else None
