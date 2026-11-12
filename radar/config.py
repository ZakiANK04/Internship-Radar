"""Paths, default profile and default search priorities.

Everything here is a *default*: the live values are stored in the ``settings``
table and edited from the web app, so nothing needs to be changed in code.
"""

from __future__ import annotations

import os
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
PACKAGE_DIR = Path(__file__).resolve().parent
DATA_DIR = ROOT_DIR / "data"
DB_PATH = DATA_DIR / "radar.db"
OUTPUT_DIR = DATA_DIR / "outputs"
DIGEST_DIR = DATA_DIR / "digests"
WEB_DIR = PACKAGE_DIR / "web"
SEED_DIR = PACKAGE_DIR / "data"
LEGACY_DB_PATH = ROOT_DIR / "legacy" / "database" / "tracker.db"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36 InternshipRadar/3.0 (personal job search)"
)

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY") or None
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-flash-latest")

# --------------------------------------------------------------------------
# Candidate profile defaults can be supplied in the ignored local_profile.py.
# The writer never states anything that is not in this structure.
# --------------------------------------------------------------------------
try:
    from .local_profile import DEFAULT_PROFILE
except ImportError:
    # A public clone starts with an empty profile; enter personal information in the app.
    DEFAULT_PROFILE: dict = {
        "full_name": "", "email": "", "phone": "", "city": "",
        "linkedin": "", "github": "", "portfolio": "",
        "school": "", "school_fr": "", "degree_en": "", "degree_fr": "",
        "graduation": "", "availability_start": "", "availability_end": "",
        "duration_months": 6, "languages": "", "nationality_note": "",
        "cv_files": {"FR": "", "EN": ""}, "skills": [], "experiences": [],
        "awards_en": "", "awards_fr": "",
    }

# --------------------------------------------------------------------------
# Search priorities.  Tier 1 mirrors the original tracker (France, Italy,
# Belgium); tier 2 are the countries the candidate added to the profile
# (Germany, Luxembourg); tier 3 is the rest of Europe; "mena" covers Egypt and
# the Gulf states.
# --------------------------------------------------------------------------
DEFAULT_PRIORITIES: dict = {
    "tiers": {
        "tier1": {"label": "Priority — France · Italy · Belgium", "weight": 20, "countries": ["FR", "IT", "BE"]},
        "tier2": {"label": "Germany · Luxembourg", "weight": 16, "countries": ["DE", "LU"]},
        "tier3": {
            "label": "Rest of Europe",
            "weight": 12,
            "countries": ["CH", "NL", "ES", "PT", "AT", "IE", "SE", "DK", "FI", "NO", "PL", "CZ", "GB",
                          "GR", "HU", "RO", "EE", "LV", "LT", "SI", "SK", "HR", "BG", "MT", "CY", "IS", "MC"],
        },
        "mena": {"label": "Egypt · Gulf", "weight": 13, "countries": ["EG", "AE", "SA", "QA", "KW", "BH", "OM"]},
    },
    "domains": ["NLP", "LLM", "RAG", "Agentic AI", "Machine Learning", "Data Science", "Data Analytics"],
    "min_score": 38,
    "llm_triage_per_run": 20,
    "auto_draft_per_run": 5,
    "follow_up_days": 10,
    "refresh_hours": 12,
    "contact_refresh_days": 7,
    "alumni_institutions": {"I2802493600": "École Nationale Polytechnique (Algiers)"},
    "expert_topics": [
        "retrieval-augmented generation",
        "large language models evaluation",
        "multilingual natural language processing",
        "Arabic natural language processing",
        "LLM agents",
        "explainable machine learning",
        "information retrieval neural",
        "data quality machine learning",
    ],
}

COUNTRY_NAMES = {
    "FR": "France", "IT": "Italy", "BE": "Belgium", "DE": "Germany", "LU": "Luxembourg", "CH": "Switzerland",
    "NL": "Netherlands", "ES": "Spain", "PT": "Portugal", "AT": "Austria", "IE": "Ireland", "SE": "Sweden",
    "DK": "Denmark", "FI": "Finland", "NO": "Norway", "PL": "Poland", "CZ": "Czechia", "GB": "United Kingdom",
    "GR": "Greece", "HU": "Hungary", "RO": "Romania", "EE": "Estonia", "LV": "Latvia", "LT": "Lithuania",
    "SI": "Slovenia", "SK": "Slovakia", "HR": "Croatia", "BG": "Bulgaria", "MT": "Malta", "CY": "Cyprus",
    "IS": "Iceland", "MC": "Monaco", "EG": "Egypt", "AE": "United Arab Emirates", "SA": "Saudi Arabia",
    "QA": "Qatar", "KW": "Kuwait", "BH": "Bahrain", "OM": "Oman",
}
