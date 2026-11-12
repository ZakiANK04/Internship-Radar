"""Regression tests for the parts that decide what the candidate sees: geography, scoring, address hygiene.

Run with:  .\\venv\\Scripts\\python.exe -m unittest discover -s tests
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from radar import scoring  # noqa: E402
from radar.contacts import _name_matches, _plausible_address  # noqa: E402
from radar.pipeline import _title_key  # noqa: E402
from radar.text import detect_countries, extract_emails, parse_date  # noqa: E402


def assess(title: str, description: str = "", country: str = "FR", weight: int = 20, **extra):
    return scoring.assess(title=title, description=description, country=country, tier_weight=weight, **extra)


class Geography(unittest.TestCase):
    def test_countries_and_cities(self):
        self.assertEqual(detect_countries("Paris, France"), ["FR"])
        self.assertEqual(detect_countries("Gif sur Yvette"), ["FR"])
        self.assertEqual(detect_countries("Milano"), ["IT"])
        self.assertEqual(detect_countries("Leuven"), ["BE"])
        self.assertEqual(detect_countries("Thuwal"), ["SA"])
        self.assertEqual(detect_countries("Cairo, Egypt"), ["EG"])
        self.assertEqual(detect_countries("AE, Dubai"), ["AE"])

    def test_unknown_location_is_not_guessed(self):
        self.assertEqual(detect_countries("2 Locations"), [])

    def test_dates(self):
        self.assertEqual(parse_date("Deadline to apply : 2027-02-28"), "2027-02-28")
        self.assertEqual(parse_date("11 October 2026"), "2026-10-11")


class Scoring(unittest.TestCase):
    def test_french_nlp_pfe_ranks_at_the_top(self):
        result = assess("Stage de fin d'études - Ingénieur NLP / LLM",
                        "Stage de 6 mois à partir de février 2027. RAG, LLM, Python, PyTorch, Hugging Face.", lang="fr")
        self.assertTrue(result.relevant)
        self.assertGreaterEqual(result.score, scoring.GRADE_A)
        self.assertEqual(result.contract, "pfe")

    def test_location_tier_changes_the_score(self):
        text = "Internship on large language models, 6 months, 2027."
        self.assertGreater(assess("LLM research intern", text, "FR", 20).score, assess("LLM research intern", text, "SE", 12).score)

    def test_apprentissage_means_learning_not_apprenticeship(self):
        result = assess("Stage - Apprentissage adaptatif pour les Transformers", "Stage de recherche en apprentissage profond.", lang="fr")
        self.assertFalse(any("Apprenticeship" in flag for flag in result.flags))

    def test_real_apprenticeship_is_dropped_or_flagged(self):
        pure = assess("Alternance - Data Scientist IA", "Contrat d'apprentissage de 24 mois en machine learning.", lang="fr")
        self.assertFalse(pure.relevant)
        mixed = assess("Stage ou alternance - Data Scientist IA", "Machine learning, Python.", lang="fr")
        self.assertTrue(any("Apprenticeship" in flag for flag in mixed.flags))
        self.assertLess(mixed.score, scoring.GRADE_C)

    def test_vie_privee_is_not_a_vie_contract(self):
        result = assess("Stage - Système de suivi respectueux de la vie privée", "Stage en machine learning et vie privée.", lang="fr")
        self.assertFalse(any("V.I.E" in flag for flag in result.flags))
        self.assertTrue(any("V.I.E" in flag for flag in assess("V.I.E Data Scientist", "machine learning internship").flags))

    def test_clearance_and_visa_restrictions_are_flagged(self):
        clearance = assess("AI intern", "Machine learning internship. Security clearance required; EU citizens only.")
        self.assertTrue(any("Nationality" in flag for flag in clearance.flags))
        sponsor = assess("AI intern", "Machine learning internship. We are unable to sponsor visas.")
        self.assertTrue(any("visa" in flag.lower() for flag in sponsor.flags))

    def test_out_of_domain_and_non_internships_are_dropped(self):
        self.assertFalse(assess("Finance Intern", "Support the accounting team with month-end closing.").relevant)
        self.assertFalse(assess("PhD position in NLP", "Doctoral position on language models. Internship possible.").relevant)
        self.assertFalse(assess("Senior Machine Learning Engineer", "Mentor interns on LLM projects.").relevant)
        self.assertFalse(assess("Arabic Culinary & Culture Intern", "Write about food.", "QA", 13).relevant)

    def test_title_prefilter(self):
        self.assertTrue(scoring.is_internship_title("Stagiaire Data Scientist H/F"))
        self.assertTrue(scoring.is_internship_title("Master Thesis: LLM agents"))
        self.assertFalse(scoring.is_internship_title("International Sales Manager"))
        self.assertFalse(scoring.is_internship_title("Internal Audit Lead"))


class Hygiene(unittest.TestCase):
    def test_decoy_addresses_are_rejected(self):
        self.assertEqual(extract_emails("papotti@myinstitutionname.fr first.last@domain.com"), [])
        self.assertEqual(extract_emails("jean.dupont [at] inria [dot] fr"), ["jean.dupont@inria.fr"])

    def test_address_must_belong_to_the_page_or_employer(self):
        self.assertTrue(_plausible_address("papotti@eurecom.fr", "http://www.eurecom.fr/~papotti/", "EURECOM"))
        self.assertFalse(_plausible_address("someone@other.fr", "http://www.eurecom.fr/", "EURECOM"))
        self.assertTrue(_plausible_address("a.b@cs.ox.ac.uk", "https://www.cs.ox.ac.uk/people/a.b", "University of Oxford"))

    def test_mailbox_must_match_the_person_not_a_namesake(self):
        self.assertTrue(_name_matches("wei.wang@x.edu", "Wei Wang"))
        self.assertTrue(_name_matches("wwang@x.edu", "Wei Wang"))
        self.assertTrue(_name_matches("papotti@eurecom.fr", "Paolo Papotti"))
        self.assertFalse(_name_matches("yuwang@x.edu", "Wei Wang"))
        self.assertFalse(_name_matches("li@x.cn", "Hang Li"))

    def test_same_posting_on_two_sources_shares_a_key(self):
        self.assertEqual(_title_key("Stage 2027 - AI/ML Engineer - 50% Client 50% R&D H/F", "FR"),
                         _title_key("Stage 2027 - Ai - Ml Engineer - 50% Client 50% R&D", "FR"))
        self.assertNotEqual(_title_key("Stage Data Scientist", "FR"), _title_key("Stage Data Scientist", "DE"))


if __name__ == "__main__":
    unittest.main()
