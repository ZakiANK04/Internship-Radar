"""Pay extraction, lab/school source helpers and default documents.

Run with:  .\\venv\\Scripts\\python.exe -m unittest discover -s tests
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from radar import config, db, documents, pay  # noqa: E402
from radar.sources import academic  # noqa: E402


class Pay(unittest.TestCase):
    def monthly(self, text: str, country: str = "FR"):
        result = pay.assess(text, country)
        return result["source"], result.get("monthly_eur")

    def test_common_phrasings(self):
        self.assertEqual(self.monthly("Gratification : 1 200 € brut par mois"), ("stated", 1200))
        self.assertEqual(self.monthly("Salary: €1,500 per month"), ("stated", 1500))
        self.assertEqual(self.monthly("Vergütung: 1.800 € brutto monatlich", "DE"), ("stated", 1800))
        self.assertEqual(self.monthly("Rémunération : 4,35 €/h"), ("stated", 660))
        self.assertEqual(self.monthly("Stage AI Engineer – Paris – 1 500 €/mois"), ("stated", 1500))

    def test_ranges_and_currencies(self):
        result = pay.assess("Compensation 1400-1600 EUR monthly", "FR")
        self.assertEqual((result["monthly_eur"], result["monthly_eur_max"]), (1400, 1600))
        self.assertEqual(pay.assess("Stipend of 3,000 AED per month", "AE")["currency"], "AED")

    def test_perks_and_company_figures_are_not_pay(self):
        self.assertEqual(self.monthly("Un budget de 200 € par mois pour utiliser les outils IA"), ("legal_min", pay.FR_MIN_MONTHLY))
        self.assertEqual(self.monthly("The startup raised 20 M€ and serves 12 000 users"), ("legal_min", pay.FR_MIN_MONTHLY))
        self.assertEqual(self.monthly("Remboursement transport 50 %, gratification 1 300 € par mois"), ("stated", 1300))

    def test_fallbacks_are_labelled(self):
        self.assertEqual(pay.assess("Nothing about money", "FR")["source"], "legal_min")
        self.assertIn("legal min", pay.label(pay.assess("Nothing", "FR")))
        self.assertEqual(pay.assess("Nothing about money", "DE")["source"], "unknown")
        self.assertEqual(pay.assess("This is an unpaid internship", "EG")["source"], "unpaid")

    def test_structured_salary(self):
        result = pay.assess("", "FR", {"min": 1200, "max": 1500, "currency": "eur", "period": "month"})
        self.assertEqual((result["monthly_eur"], result["monthly_eur_max"], result["source"]), (1200, 1500, "stated"))


class LabFilters(unittest.TestCase):
    def test_internships_in_the_field_are_kept(self):
        self.assertTrue(academic._wanted("[Stage M2] Grands modèles de langue pour la santé"))
        self.assertTrue(academic._wanted("Master's internship: multimodal LLMs for geolocation"))

    def test_phd_jobs_and_other_fields_are_dropped(self):
        self.assertFalse(academic._wanted("Offre de thèse CIFRE en apprentissage automatique"))
        self.assertFalse(academic._wanted("Poste de Maître de conférences en informatique"))
        self.assertFalse(academic._wanted("Stage M2 : panneaux biosourcés à base de pailles"))


class WithTempDatabase(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.folder = tempfile.TemporaryDirectory()
        cls.saved = (config.DB_PATH, config.DOCUMENTS_DIR)
        config.DB_PATH = Path(cls.folder.name) / "test.db"
        config.DOCUMENTS_DIR = Path(cls.folder.name) / "documents"
        db.initialize()

    @classmethod
    def tearDownClass(cls) -> None:
        config.DB_PATH, config.DOCUMENTS_DIR = cls.saved
        cls.folder.cleanup()


class DefaultDocuments(WithTempDatabase):
    def test_cv_plus_every_document_marked_send_by_default(self):
        pdf = b"%PDF-1.4 test"
        cv_fr = documents.add_document(pdf, "cv_fr.pdf", title="CV FR", doc_type="cv", language="FR", is_default=True)
        cv_en = documents.add_document(pdf, "cv_en.pdf", title="CV EN", doc_type="cv", language="EN", is_default=True)
        transcript = documents.add_document(pdf, "notes.pdf", title="Transcript", doc_type="transcript", language="ALL", is_default=True)
        letter = documents.add_document(pdf, "reco.pdf", title="Recommendation", doc_type="recommendation", language="ALL", is_default=True)
        documents.add_document(b"\x89PNG test", "badge.png", title="Badge", doc_type="certificate", language="ALL", is_default=False)
        ids = lambda doc: doc["id"] if isinstance(doc, dict) else doc  # noqa: E731
        self.assertEqual(documents.get_defaults_for_language("FR"), [ids(cv_fr), ids(transcript), ids(letter)])
        self.assertEqual(documents.get_defaults_for_language("EN")[0], ids(cv_en))
        # Two defaults of the same non-CV type can coexist; a second default CV replaces the first.
        second = documents.add_document(pdf, "reco2.pdf", title="Second letter", doc_type="recommendation", language="ALL", is_default=True)
        self.assertIn(ids(letter), documents.get_defaults_for_language("FR"))
        self.assertIn(ids(second), documents.get_defaults_for_language("FR"))
        new_cv = documents.add_document(pdf, "cv_fr2.pdf", title="CV FR v2", doc_type="cv", language="FR", is_default=True)
        self.assertEqual(documents.get_defaults_for_language("FR")[0], ids(new_cv))
        self.assertNotIn(ids(cv_fr), documents.get_defaults_for_language("FR"))


class CustomSources(WithTempDatabase):
    def test_lab_page_can_be_added_and_loaded(self):
        from radar import sources

        added = sources.add_custom_source("page", "https://lab.example.fr/stages", "Example lab")
        self.assertEqual(added["source_id"], "page:lab.example.fr/stages")
        self.assertIn("page:lab.example.fr/stages", [source.name for source in sources.load_sources()])
        with self.assertRaises(ValueError):
            sources.add_custom_source("rss", "not-a-url", "Broken")


if __name__ == "__main__":
    unittest.main()
