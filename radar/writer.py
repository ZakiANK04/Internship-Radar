"""Truthful application writing: e-mails, cover letters, follow-ups, .eml drafts.

The deterministic templates only use facts stored in the profile.  When a
Gemini key is configured the draft can be polished by the model, but the model
receives the same facts and its output is rejected if it drops the structure
or introduces placeholders; the template is then used unchanged.
"""

from __future__ import annotations

import json
import re
from datetime import date
from typing import Any, Optional

from . import config, db, net, outreach
from .text import clean, fold

GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
_llm_disabled_reason: Optional[str] = None


# ------------------------------------------------------------------- Gemini
def llm_available() -> bool:
    return bool(config.GEMINI_API_KEY) and _llm_disabled_reason is None


def llm_status() -> str:
    if not config.GEMINI_API_KEY:
        return "No GEMINI_API_KEY in the environment — deterministic templates are used."
    if _llm_disabled_reason:
        return f"{_llm_disabled_reason}. Built-in templates are used instead."
    return f"Gemini ready ({config.GEMINI_MODEL})"


def gemini(prompt: str, *, system: str = "", max_tokens: int = 1200, temperature: float = 0.3, as_json: bool = False) -> Optional[str]:
    """Call Gemini once.  Returns ``None`` on any failure so callers fall back locally."""
    global _llm_disabled_reason
    if not llm_available():
        return None
    payload: dict[str, Any] = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": temperature, "maxOutputTokens": max_tokens},
    }
    if system:
        payload["systemInstruction"] = {"parts": [{"text": system}]}
    if as_json:
        payload["generationConfig"]["responseMimeType"] = "application/json"
    try:
        data = net.post_json(GEMINI_URL.format(model=config.GEMINI_MODEL), payload,
                             headers={"x-goog-api-key": config.GEMINI_API_KEY}, timeout=60, retries=0)
    except net.FetchError as exc:
        if exc.status == 429:
            _llm_disabled_reason = "Gemini quota reached"
        elif exc.status in (400, 401, 403, 404):
            _llm_disabled_reason = f"Google refused the request — “{exc}”"
        return None
    try:
        parts = data["candidates"][0]["content"]["parts"]
        return "".join(part.get("text", "") for part in parts).strip() or None
    except (KeyError, IndexError, TypeError):
        return None


def check_llm() -> str:
    """Send one tiny request so the reported status reflects what Google actually answers."""
    if config.GEMINI_API_KEY and _llm_disabled_reason is None:
        gemini("Reply with the single word: ready", max_tokens=16, temperature=0)
    return llm_status()


# ------------------------------------------------------------ fact selection
def _availability(profile: dict, language: str) -> str:
    start = date.fromisoformat(profile["availability_start"])
    end = date.fromisoformat(profile["availability_end"])
    if language == "FR":
        months = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août", "septembre", "octobre", "novembre", "décembre"]
        return f"du {start.day} {months[start.month - 1]} au {end.day} {months[end.month - 1]} {end.year}"
    return f"from {start.strftime('%d %B').lstrip('0')} to {end.strftime('%d %B %Y').lstrip('0')}"


def pick_experiences(profile: dict, text: str, count: int = 2) -> list[dict]:
    """Rank the candidate's verified experiences by overlap with the offer or research topic."""
    folded = fold(text)
    ranked = []
    for index, experience in enumerate(profile.get("experiences") or []):
        hits = sum(1 for tag in experience["tags"] if tag in folded)
        ranked.append((hits, -index, experience))
    ranked.sort(key=lambda item: (item[0], item[1]), reverse=True)
    return [item[2] for item in ranked[:count]]


def _focus_terms(text: str, language: str) -> str:
    """Name the two or three themes of the offer that the candidate can genuinely speak to."""
    catalogue = [
        (r"retrieval[- ]augmented|\brag\b|generation augmentee", "retrieval-augmented generation", "la génération augmentée par récupération (RAG)"),
        (r"\bllms?\b|large language model|language model|modeles? de langue|modeles? de langage", "large language models", "les grands modèles de langue"),
        (r"agentic|agentique|ai agents?|agents? ia|multi[- ]agent", "LLM-based agents", "les agents fondés sur les LLM"),
        (r"\bnlp\b|natural language|traitement automatique|\btal\b", "natural language processing", "le traitement automatique des langues"),
        (r"multilingual|multilingue|arabic|arabe", "multilingual NLP", "le TAL multilingue"),
        (r"explainab|interpretab|explicab|trustworthy|confiance", "explainable and trustworthy AI", "l'IA explicable et de confiance"),
        (r"information retrieval|recherche d'information|semantic search|recherche semantique", "information retrieval", "la recherche d'information"),
        (r"evaluation|benchmark", "model evaluation", "l'évaluation des modèles"),
        (r"data quality|qualite des donnees", "data quality", "la qualité des données"),
        (r"forecast|prevision|time series|series temporelles", "forecasting", "la prévision"),
        (r"computer vision|vision par ordinateur", "computer vision", "la vision par ordinateur"),
        (r"mlops|deployment|deploiement|production", "deploying models in production", "la mise en production de modèles"),
        (r"machine learning|apprentissage automatique|deep learning|apprentissage profond", "machine learning", "l'apprentissage automatique"),
        (r"data scien|data analy|analyse de donnees|analytics", "data science", "la science des données"),
    ]
    folded = fold(text)
    found = [(english, french) for pattern, english, french in catalogue if re.search(pattern, folded)]
    chosen = [item[0 if language == "EN" else 1] for item in found[:3]]
    if not chosen:
        chosen = ["applied machine learning"] if language == "EN" else ["l'apprentissage automatique appliqué"]
    joiner = " and " if language == "EN" else " et "
    return chosen[0] if len(chosen) == 1 else ", ".join(chosen[:-1]) + joiner + chosen[-1]


def _signature(profile: dict) -> str:
    lines = [profile["full_name"], profile["school"], f"{profile['email']} · {profile['phone']}"]
    links = [profile.get("linkedin"), profile.get("github"), profile.get("portfolio")]
    lines.append(" · ".join(link for link in links if link))
    return "\n".join(lines)


# ----------------------------------------------------------------- templates
def _attachment_phrase(language: str, attached_docs: Optional[list[dict]]) -> str:
    """Accurately describe attached application documents in the main letter/draft body."""
    if attached_docs is None:
        has_cv = True
        has_transcripts = False
    else:
        has_cv = any(d.get("doc_type") == "cv" for d in attached_docs)
        has_transcripts = any(d.get("doc_type") == "transcript" for d in attached_docs)

    if language == "FR":
        if has_cv and has_transcripts:
            return "Vous trouverez mon CV ainsi que mes relevés de notes en pièces jointes ; mes lettres de recommandation sont disponibles sur demande."
        elif has_cv:
            return "Vous trouverez mon CV en pièce jointe ; mes relevés de notes et lettres de recommandation sont disponibles sur demande."
        elif has_transcripts:
            return "Vous trouverez mes relevés de notes en pièces jointes ; mon CV et mes lettres de recommandation sont disponibles sur demande."
        else:
            return "Mon CV, mes relevés de notes et lettres de recommandation sont disponibles sur demande."
    else:
        if has_cv and has_transcripts:
            return "My CV and academic transcripts are attached; recommendation letters are available on request."
        elif has_cv:
            return "My CV is attached; transcripts and recommendation letters are available on request."
        elif has_transcripts:
            return "My academic transcripts are attached; my CV and recommendation letters are available on request."
        else:
            return "My CV, transcripts, and recommendation letters are available on request."


def offer_draft(offer: dict, contact: Optional[dict], language: str, profile: dict,
                attached_docs: Optional[list[dict]] = None) -> dict[str, str]:
    """Application message for a published offer."""
    text = f"{offer['title']} {offer.get('description') or ''}"
    first, second = (pick_experiences(profile, text) + [None, None])[:2]
    focus = _focus_terms(text, language)
    org = offer.get("org") or ""
    if not org or org.startswith("via "):  # found on a shared board: the host is not known by name
        org = ""
    availability = _availability(profile, language)
    attach_note = _attachment_phrase(language, attached_docs)
    if language == "FR":
        greeting = f"Bonjour {contact['name']}," if contact else "Madame, Monsieur,"
        subject = f"Candidature — {offer['title']} (stage de fin d'études, {profile['duration_months']} mois, à partir de fin janvier 2027)"
        body = [
            greeting,
            f"Élève-ingénieur en dernière année à l'{profile.get('school_fr') or profile['school']} ({profile['degree_fr']}), "
            f"je vous adresse ma candidature pour l'offre « {offer['title']} »{' au sein de ' + org if org else ''}. Je recherche mon stage de fin "
            f"d'études (PFE) de {profile['duration_months']} mois et je suis disponible {availability}.",
            f"Ce sujet m'intéresse particulièrement parce qu'il porte sur {focus}, des thématiques sur lesquelles j'ai déjà "
            "travaillé à partir de données réelles. "
            + (first["fr"] if first else "") + (" " + second["fr"] if second else ""),
            f"Ces expériences, auxquelles s'ajoutent {profile['awards_fr']}, m'ont appris à mener un projet de la "
            "formalisation du besoin jusqu'à un système livré et évalué. Je parle français (C1, TCF 541/699), anglais (C1) "
            "et arabe, et mon école établit une convention de stage tripartite.",
            f"{attach_note} Je serais heureux d'échanger avec vous lors d'un entretien à votre convenance.",
            "Je vous remercie de votre attention et vous prie d'agréer l'expression de mes salutations distinguées.",
            _signature(profile),
        ]
    else:
        greeting = f"Dear {contact['name']}," if contact else "Dear Hiring Team,"
        subject = f"Application — {offer['title']} ({profile['duration_months']}-month final-year internship from late January 2027)"
        body = [
            greeting,
            f"I am a final-year engineering student at {profile['school']} ({profile['degree_en']}), and I would like to apply "
            f"for the “{offer['title']}” position{' at ' + org if org else ''}. I am looking for my {profile['duration_months']}-month final-year "
            f"internship (PFE) and I am available {availability}.",
            f"The role appeals to me because it centres on {focus}, areas I have already worked in with real company data. "
            + (first["en"] if first else "") + (" " + second["en"] if second else ""),
            f"Together with {profile['awards_en']}, these projects taught me to take a problem from requirements to a "
            "delivered, evaluated system. I work in English (C1), French (C1, TCF 541/699) and Arabic, and my school issues "
            "the tripartite internship agreement required for the placement.",
            f"{attach_note} I would be glad to discuss the role at your convenience.",
            "Thank you for your time and consideration.",
            "Kind regards,\n" + _signature(profile),
        ]
    return {"subject": subject, "body": "\n\n".join(part for part in body if part)}


def outreach_draft(contact: dict, language: str, profile: dict,
                   attached_docs: Optional[list[dict]] = None) -> dict[str, str]:
    """Spontaneous application to a researcher, anchored on their published work."""
    evidence = contact.get("evidence") or []
    papers = [item for item in evidence if item.get("type") == "paper"]
    alumni = any(item.get("type") == "affiliation" for item in evidence)
    topics = ", ".join((contact.get("topics") or [])[:3])
    context = f"{topics} {' '.join(item.get('text', '') for item in papers)}"
    first, second = (pick_experiences(profile, context) + [None, None])[:2]
    focus = _focus_terms(context, language)
    availability = _availability(profile, language)
    lead = re.split(r",| et | and ", focus)[0].strip()
    if attached_docs is None:
        has_cv = True
        has_transcripts = False
    else:
        has_cv = any(d.get("doc_type") == "cv" for d in attached_docs)
        has_transcripts = any(d.get("doc_type") == "transcript" for d in attached_docs)
    if language == "FR":
        subject = f"Candidature spontanée — stage de fin d'études (PFE), {re.sub(r'^(?:la |le |les |l.)', '', lead)}, fin janv.–juin 2027"
        hook = (f"Vos travaux récents, notamment « {papers[0]['text']} » ({papers[0].get('year', '')}), portent sur {focus}, "
                "un sujet sur lequel je souhaite réaliser mon stage de fin d'études."
                if papers else f"Vos travaux sur {focus} correspondent au sujet sur lequel je souhaite réaliser mon stage de fin d'études.")
        tie = (" Étant moi-même élève de l'École Nationale Polytechnique d'Alger, à laquelle vos publications vous associent, "
               "je me permets de vous écrire directement.") if alumni else ""
        if has_cv and has_transcripts:
            doc_action = "Je joins mon CV ainsi que mes relevés de notes et peux vous transmettre mes lettres de recommandation."
        elif has_cv:
            doc_action = "Je joins mon CV et peux vous transmettre relevés de notes et lettres de recommandation."
        elif has_transcripts:
            doc_action = "Je joins mes relevés de notes et peux vous transmettre mon CV et mes lettres de recommandation."
        else:
            doc_action = "Je peux vous transmettre mon CV, mes relevés de notes et mes lettres de recommandation."
        body = [
            f"Bonjour {contact['name']},",
            f"Je suis élève-ingénieur en dernière année à l'{profile.get('school_fr') or profile['school']} ({profile['degree_fr']}). {hook}{tie}",
            (first["fr"] if first else "") + (" " + second["fr"] if second else ""),
            f"Je recherche un stage de {profile['duration_months']} mois, {availability}, dans une équipe de recherche. Mon école "
            "établit une convention de stage tripartite ; je parle français (C1), anglais (C1) et arabe.",
            f"Auriez-vous, vous ou un membre de votre équipe, un sujet de stage ouvert sur cette période ? {doc_action} "
            "Je reste à votre disposition pour un échange.",
            "Je vous remercie de votre attention.",
            "Bien cordialement,\n" + _signature(profile),
        ]
    else:
        subject = f"Prospective final-year research intern ({lead}), late Jan–June 2027"
        hook = (f"Your recent work, in particular “{papers[0]['text']}” ({papers[0].get('year', '')}), addresses {focus}, "
                "the area in which I would like to carry out my final-year research internship."
                if papers else f"Your work on {focus} matches the area in which I would like to carry out my final-year research internship.")
        tie = (" As a current student of École Nationale Polytechnique in Algiers, an institution your publications are "
               "affiliated with, I am taking the liberty of writing to you directly.") if alumni else ""
        if has_cv and has_transcripts:
            doc_action = "My CV and academic transcripts are attached, and I can send recommendation letters."
        elif has_cv:
            doc_action = "My CV is attached, and I can send transcripts and recommendation letters."
        elif has_transcripts:
            doc_action = "My academic transcripts are attached, and I can send my CV and recommendation letters."
        else:
            doc_action = "I can provide my CV, transcripts, and recommendation letters upon request."
        body = [
            f"Dear {contact['name']},",
            f"I am a final-year engineering student at {profile['school']} ({profile['degree_en']}). {hook}{tie}",
            (first["en"] if first else "") + (" " + second["en"] if second else ""),
            f"I am looking for a {profile['duration_months']}-month internship, {availability}, in a research group. My school "
            "issues the tripartite internship agreement, and I work in English (C1), French (C1) and Arabic.",
            f"Would you, or someone in your group, have an internship topic open for that period? {doc_action} "
            "I would be grateful for a short conversation if this is of interest.",
            "Thank you for your time.",
            "Kind regards,\n" + _signature(profile),
        ]
    return {"subject": subject, "body": "\n\n".join(part for part in body if part)}


def follow_up_draft(application: dict, language: str, profile: dict) -> dict[str, str]:
    if application.get("kind") == "spontaneous" and application.get("contact_id"):
        from . import documents

        contact = db.one("SELECT * FROM contacts WHERE id = ?", (application["contact_id"],))
        if contact:
            docs = documents.get_application_documents(application["id"]) if application.get("id") else _documents(None, language)
            return outreach.follow_up_email(contact, language, profile, docs or _documents(None, language))
    subject = clean(application.get("subject")) or ("my application" if language == "EN" else "ma candidature")
    name = application.get("contact_name")
    if language == "FR":
        body = [
            f"Bonjour {name}," if name else "Madame, Monsieur,",
            f"Je me permets de revenir vers vous au sujet de ma candidature envoyée le {application.get('sent_at') or 'récemment'} "
            f"(« {subject} »). Je reste très intéressé et disponible pour un stage de {profile['duration_months']} mois "
            f"{_availability(profile, 'FR')}.",
            "Je peux vous transmettre tout document complémentaire (relevés de notes, lettres de recommandation) ou me rendre "
            "disponible pour un échange.",
            "Bien cordialement,\n" + _signature(profile),
        ]
        return {"subject": f"Relance — {subject}", "body": "\n\n".join(body)}
    body = [
        f"Dear {name}," if name else "Dear Hiring Team,",
        f"I am following up on the application I sent on {application.get('sent_at') or 'recently'} (“{subject}”). I remain "
        f"very interested and available for a {profile['duration_months']}-month internship {_availability(profile, 'EN')}.",
        "I would be happy to send any further document (transcripts, recommendation letters) or to arrange a short call.",
        "Kind regards,\n" + _signature(profile),
    ]
    return {"subject": f"Follow-up — {subject}", "body": "\n\n".join(body)}


# ---------------------------------------------------------------- AI polish
def _profile_facts(profile: dict) -> str:
    experiences = "\n".join(f"- {item['en']}" for item in profile.get("experiences") or [])
    return (
        f"Name: {profile['full_name']}\nSchool: {profile['school']}\nDegree: {profile['degree_en']} (graduation {profile['graduation']})\n"
        f"Availability: {profile['availability_start']} to {profile['availability_end']} ({profile['duration_months']} months, final-year internship / PFE)\n"
        f"Languages: {profile['languages']}\nAwards: {profile['awards_en']}\nSkills: {', '.join(profile.get('skills') or [])}\n"
        f"Verified experience:\n{experiences}"
    )


def polish(draft: dict[str, str], *, target: str, language: str, profile: dict) -> Optional[dict[str, str]]:
    """Ask Gemini to tighten a draft without adding facts.  ``None`` means keep the template."""
    system = (
        "You edit job-application e-mails for a final-year engineering student. Use ONLY facts from the CANDIDATE FACTS "
        "block. Never invent experience, numbers, publications, skills or anything about the recipient. Never claim the "
        "candidate has read a paper. Keep the greeting, the signature block and every factual detail of the draft. "
        "Write natural, specific, confident prose with no clichés and no flattery; 170-230 words before the signature."
    )
    prompt = (
        f"CANDIDATE FACTS\n{_profile_facts(profile)}\n\nTARGET\n{target[:3500]}\n\nDRAFT ({'French' if language == 'FR' else 'English'})\n"
        f"Subject: {draft['subject']}\n\n{draft['body']}\n\n"
        "Rewrite the draft so that the second and third paragraphs speak precisely to the TARGET, choosing the most relevant "
        "verified experience. Keep the same language. Return JSON with keys \"subject\" and \"body\" only."
    )
    raw = gemini(prompt, system=system, max_tokens=1400, temperature=0.35, as_json=True)
    if not raw:
        return None
    try:
        data = json.loads(re.sub(r"^```(?:json)?\s*|\s*```$", "", raw.strip()))
        subject, body = clean(data["subject"]), str(data["body"]).strip()
    except (json.JSONDecodeError, KeyError, TypeError):
        return None
    if not (500 < len(body) < 3200) or re.search(r"\[[^\]]{2,40}\]|\{\{|XXX|lorem", body) or profile["full_name"] not in body:
        return None
    if profile["email"] not in body:
        body = body.rstrip() + "\n" + profile["email"]
    return {"subject": subject[:200], "body": body}


def triage(offer: dict, profile: dict) -> Optional[dict[str, Any]]:
    """Structured second opinion on one offer (used by the Analyst agent)."""
    system = "You screen internship postings for one candidate and answer with strict JSON. Be factual; use null when the posting does not say."
    prompt = (
        f"CANDIDATE\n{_profile_facts(profile)}\nConstraints: Algerian citizen applying from Algeria; needs a visa and a tripartite "
        "internship agreement from the school; cannot do apprenticeships, V.I.E or working-student jobs; wants NLP/LLM/RAG first, "
        "then machine learning, then data analytics.\n\n"
        f"POSTING\nTitle: {offer['title']}\nEmployer: {offer.get('org')}\nLocation: {offer.get('location_raw')}\n"
        f"{(offer.get('description') or '')[:5000]}\n\n"
        "Return JSON: {\"summary\": one sentence on what the intern will do, \"fit\": integer 0-10 for this candidate, "
        "\"why\": [up to 3 short reasons], \"concerns\": [up to 3 short eligibility or fit concerns], "
        "\"start\": start date text or null, \"duration\": text or null, \"paid\": text or null, "
        "\"language\": working-language requirement or null, \"is_internship\": boolean}"
    )
    raw = gemini(prompt, system=system, max_tokens=500, temperature=0.1, as_json=True)
    if not raw:
        return None
    try:
        data = json.loads(re.sub(r"^```(?:json)?\s*|\s*```$", "", raw.strip()))
        fit = max(0, min(10, int(data.get("fit", 5))))
    except (json.JSONDecodeError, TypeError, ValueError, AttributeError):
        return None
    return {
        "summary": clean(data.get("summary"))[:320], "fit": fit,
        "why": [clean(item)[:160] for item in (data.get("why") or [])[:3]],
        "concerns": [clean(item)[:160] for item in (data.get("concerns") or [])[:3]],
        "start": clean(data.get("start"))[:80] or None, "duration": clean(data.get("duration"))[:80] or None,
        "paid": clean(data.get("paid"))[:80] or None, "language": clean(data.get("language"))[:80] or None,
        "is_internship": bool(data.get("is_internship", True)),
    }


# ------------------------------------------------------------------- public
def choose_language(offer: Optional[dict], contact: Optional[dict]) -> str:
    """Offers: the posting's language.  People: the language where they work (see outreach.language_for)."""
    if offer is not None:
        return "FR" if (offer.get("lang") == "fr") else "EN"
    if contact:
        return outreach.language_for(contact.get("country"), contact.get("org") or "")
    return "EN"


def _documents(ids: Optional[list], language: str) -> list[dict]:
    """Documents for a draft: the ones chosen, else the defaults for its language."""
    from . import documents

    chosen = ids if ids is not None else documents.get_defaults_for_language(language)
    found = []
    for item in chosen or []:
        doc = item if isinstance(item, dict) else documents.get_document(int(item))
        if doc:
            found.append(doc)
    return found


def compose(*, offer_id: Optional[int] = None, contact_id: Optional[int] = None, language: Optional[str] = None,
            use_llm: bool = True, attached_docs: Optional[list[dict]] = None,
            attach_letter: bool = True) -> dict[str, Any]:
    """Build a draft for an offer and/or a contact; returns subject, body and provenance."""
    profile = db.profile()
    offer = db.one("SELECT * FROM offers WHERE id = ?", (offer_id,)) if offer_id else None
    contact = db.decode(db.one("SELECT * FROM contacts WHERE id = ?", (contact_id,)), "topics", "evidence") if contact_id else None
    if offer is None and contact is None:
        raise ValueError("An offer or a contact is required.")
    language = (language or choose_language(offer, contact)).upper()
    if offer is not None:
        draft = offer_draft(offer, contact, language, profile, attached_docs=attached_docs)
        target = f"Offer: {offer['title']} at {offer.get('org')}\n{offer.get('description') or ''}"
    else:
        # Researchers: your Apps Script e-mail is the message, the formal letter goes as the attached cover letter.
        letter = outreach_draft(contact, language, profile, attached_docs=attached_docs)
        email = outreach.initial_email(contact, language, profile, _documents(attached_docs, language), letter=attach_letter)
        return {"subject": email["subject"], "letter": letter["body"], "body": email["body"], "language": language,
                "generator": "apps-script template", "recipient": contact.get("email") or "", "kind": "spontaneous",
                "warning": outreach.needs_checking(contact)}
    generator = "template"
    if use_llm:
        improved = polish(draft, target=target, language=language, profile=profile)
        if improved:
            draft, generator = improved, f"gemini:{config.GEMINI_MODEL}"
    # The full text becomes the attached cover letter; the e-mail itself stays short.
    return {"subject": draft["subject"], "letter": draft["body"],
            "body": cover_note(offer, contact, language, profile, target, attached_docs=attached_docs, attach_letter=attach_letter),
            "language": language, "generator": generator,
            "recipient": (contact or {}).get("email") or "", "kind": "offer" if offer else "spontaneous"}


def _email_attachment_closing(language: str = "EN", attached_docs: Optional[list[Any]] = None, attach_letter: bool = True, is_spontaneous: bool = False) -> str:
    """Accurately summarize what is attached directly to the email message."""
    # Support both (language, docs) and (docs, language) argument order
    if isinstance(language, (list, tuple)) or (language is None and isinstance(attached_docs, str)):
        actual_docs = language
        actual_lang = str(attached_docs or "EN").upper()
    else:
        actual_lang = str(language or "EN").upper()
        actual_docs = attached_docs

    if actual_docs is None:
        has_cv = True
        has_transcripts = False
        has_certs = False
    else:
        normalized = [dict(d) if hasattr(d, "keys") else (d if isinstance(d, dict) else {}) for d in actual_docs]
        has_cv = any(d.get("doc_type") == "cv" for d in normalized)
        has_transcripts = any(d.get("doc_type") == "transcript" for d in normalized)
        has_certs = any(d.get("doc_type") in ("certificate", "cover_letter", "other") for d in normalized)

    if actual_lang == "FR":
        items = []
        if attach_letter:
            items.append("une lettre de motivation" if is_spontaneous else "ma lettre de motivation")
        if has_cv:
            items.append("mon CV")
        if has_transcripts:
            items.append("mes relevés de notes")
        if has_certs:
            items.append("mes pièces complémentaires")

        call = "Merci d'avance pour votre temps." if is_spontaneous else "Je suis disponible pour un échange quand cela vous convient."
        if not items:
            return f"Mon CV et mes relevés de notes sont disponibles sur demande. {call}"
        elif len(items) == 1:
            return f"Vous trouverez {items[0]} en pièce jointe. {call}"
        elif len(items) == 2:
            first = items[0][0].upper() + items[0][1:]
            return f"{first} et {items[1]} sont en pièces jointes. {call}"
        else:
            first_part = ", ".join(items[:-1])
            first_part = first_part[0].upper() + first_part[1:]
            return f"{first_part} et {items[-1]} sont en pièces jointes. {call}"
    else:
        items = []
        if attach_letter:
            items.append("a one-page cover letter" if is_spontaneous else "my cover letter")
        if has_cv:
            items.append("my CV")
        if has_transcripts:
            items.append("academic transcripts")
        if has_certs:
            items.append("supporting documents")

        call = "Thank you for your time." if is_spontaneous else "I would be glad to talk whenever suits you."
        if not items:
            return f"My CV and transcripts are available upon request. {call}"
        elif len(items) == 1:
            first = items[0][0].upper() + items[0][1:]
            return f"{first} is attached. {call}"
        elif len(items) == 2:
            first = items[0][0].upper() + items[0][1:]
            return f"{first} and {items[1]} are attached. {call}"
        else:
            first_part = ", ".join(items[:-1])
            first_part = first_part[0].upper() + first_part[1:]
            return f"{first_part}, and {items[-1]} are attached. {call}"


def cover_note(offer: Optional[dict], contact: Optional[dict], language: str, profile: dict, context: str,
               attached_docs: Optional[list[dict]] = None, attach_letter: bool = True) -> str:
    """The short message typed into the e-mail; the detail lives in the attached letter and CV."""
    experience = (pick_experiences(profile, context, 1) or [None])[0]
    short = (experience or {}).get("short_fr" if language == "FR" else "short_en", "")
    months, availability = profile["duration_months"], _availability(profile, language)
    signature = f"{profile['full_name']}\n{profile['phone']}\n{profile.get('linkedin') or ''}".strip()
    is_spontaneous = offer is None
    closing = _email_attachment_closing(language, attached_docs, attach_letter=attach_letter, is_spontaneous=is_spontaneous)

    if language == "FR":
        greeting = f"Bonjour {contact['name']}," if contact else "Bonjour,"
        if offer is not None:
            opening = (f"Je vous écris au sujet de votre offre « {offer['title']} ». Je suis en dernière année à l'École Nationale "
                       f"Polytechnique d'Alger, option Data Science et IA, et je cherche mon stage de fin d'études de {months} mois, {availability}.")
        else:
            papers = [item for item in (contact.get("evidence") or []) if item.get("type") == "paper"]
            about = f"votre article « {papers[0]['text']} »" if papers else "vos travaux récents"
            opening = (f"Je suis en dernière année à l'École Nationale Polytechnique d'Alger, option Data Science et IA. Je vous écris "
                       f"parce que {about} recoupe ce sur quoi je souhaite travailler pendant mon stage de fin d'études ({months} mois, {availability}).")
        return "\n\n".join(part for part in (greeting, opening, short, closing, "Bien cordialement,\n" + signature) if part)
    greeting = f"Dear {contact['name']}," if contact else "Hello,"
    if offer is not None:
        opening = (f"I am writing about your “{offer['title']}” opening. I am in my final year at École Nationale Polytechnique in "
                   f"Algiers, Data Science and AI track, and I am looking for my {months}-month final-year internship, {availability}.")
    else:
        papers = [item for item in (contact.get("evidence") or []) if item.get("type") == "paper"]
        about = f"your paper “{papers[0]['text']}”" if papers else "your recent work"
        opening = (f"I am in my final year at École Nationale Polytechnique in Algiers, Data Science and AI track. I am writing because "
                   f"{about} is close to what I want to work on during my final-year internship ({months} months, {availability}).")
    return "\n\n".join(part for part in (greeting, opening, short, closing, "Best regards,\n" + signature) if part)
