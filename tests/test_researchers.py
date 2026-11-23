"""Importing a researcher spreadsheet and matching look-alikes (offline).

Run with:  .\\venv\\Scripts\\python.exe -m unittest discover -s tests
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from radar import config, db, researchers  # noqa: E402

HEADER = ["w", "Email", "Title", "University", "Laboratory", "Country", "City", "Research areas", "Profile URL",
          "Website", "Verified", "Match %", "Status", "Contacted", "Follow-up", "Replied", "Notes", "Résultat envoi", "Date d’envoi"]
ROWS = [
    ["Ada Lovelace", "ada.lovelace@univ.example.fr", "Madame", "Université Exemple", "LIX", "France", "Paris",
     "Machine Learning, NLP", "https://lab.example.fr/ada", None, None, 72, "Sent", datetime(2026, 11, 9), None, None, "Hosts PFE interns", "Envoyé – initial", datetime(2026, 11, 12)],
    ["Alan Turing", "alan.turing@poli.example.it", "Professeur", "Politecnico", "AILab", "Italie", "Milan",
     "Computer Vision", None, None, None, 55, "Replied", datetime(2026, 9, 2), None, datetime(2026, 9, 14), None, None, None],
    ["Grace Hopper", "grace.hopper@kuleuven.example.be", "Madame", "KU Leuven", "LIIR", "Belgique", "Leuven",
     "Information Retrieval", None, None, None, 60, "Rejected", None, None, None, "à vérifier", "Ignoré", None],
    [None] * len(HEADER),
]


class Import(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        import openpyxl

        cls.folder = tempfile.TemporaryDirectory()
        cls.saved = config.DB_PATH
        config.DB_PATH = Path(cls.folder.name) / "test.db"
        db.initialize()
        cls.path = Path(cls.folder.name) / "list.xlsx"
        workbook = openpyxl.Workbook()
        sheet = workbook.active
        sheet.append(HEADER)
        for row in ROWS:
            sheet.append(row)
        workbook.save(cls.path)

    @classmethod
    def tearDownClass(cls) -> None:
        config.DB_PATH = cls.saved
        cls.folder.cleanup()

    def test_import_keeps_contact_details_and_history(self):
        result = researchers.import_spreadsheet(self.path, log=lambda *_: None)
        self.assertEqual(result["total"], 3)  # the empty row is ignored
        people = {row["name"]: row for row in db.rows("SELECT * FROM contacts WHERE kind = 'imported'")}
        ada = people["Ada Lovelace"]
        self.assertEqual((ada["email"], ada["country"], ada["status"]), ("ada.lovelace@univ.example.fr", "FR", "contacted"))
        self.assertIn("2026-11-12", ada["notes"])  # your own send date
        self.assertEqual(db.one("SELECT stage, sent_at FROM applications WHERE contact_id = ?", (ada["id"],)), {"stage": "sent", "sent_at": "2026-11-12"})
        self.assertIn("Hosts PFE interns", ada["notes"])
        self.assertEqual(ada["role"], "Researcher")  # a salutation is not a job title
        # Someone else's earlier status is a note, not your history.
        self.assertEqual(people["Alan Turing"]["status"], "new")
        self.assertIn("Earlier outreach by someone else", people["Alan Turing"]["notes"])
        self.assertEqual(people["Alan Turing"]["country"], "IT")
        from radar import outreach
        self.assertEqual(people["Grace Hopper"]["status"], "new")
        self.assertEqual(outreach.needs_checking(people["Grace Hopper"]), "à vérifier")
        # Importing again refreshes instead of duplicating.
        again = researchers.import_spreadsheet(self.path, log=lambda *_: None)
        self.assertEqual((again["created"], again["updated"]), (0, 3))
        self.assertEqual(db.one("SELECT count(*) AS n FROM contacts")["n"], 3)


class Similarity(unittest.TestCase):
    def test_cosine(self):
        self.assertAlmostEqual(researchers._cosine({"T1": 1.0}, {"T1": 1.0}), 1.0)
        self.assertEqual(researchers._cosine({"T1": 1.0}, {"T2": 1.0}), 0.0)
        self.assertGreater(researchers._cosine({"T1": 0.6, "T2": 0.4}, {"T1": 0.5, "T3": 0.5}), 0.3)

    def test_names_match_across_spellings(self):
        self.assertEqual(researchers._name_key("Nicolò Cesa‐Bianchi"), researchers._name_key("Nicolo Cesa-Bianchi"))
        self.assertEqual(researchers._name_key("Claudia d’Amato"), researchers._name_key("Claudia d'Amato"))
        self.assertTrue(researchers._same_name("Elena Baralis", "Elena Maria Baralis"))
        self.assertFalse(researchers._same_name("Elena Baralis", "Elena Rossi"))

    def test_affiliation_must_recur(self):
        this_year = datetime.now().year
        author = {"last_known_institutions": [{"id": "I1", "country_code": "FR", "type": "education"}],
                  "affiliations": [{"institution": {"id": "I1"}, "years": [this_year]}]}
        self.assertIsNone(researchers._confirmed_home(author, ["FR"]))
        author["affiliations"][0]["years"] = [this_year, this_year - 1]
        self.assertEqual(researchers._confirmed_home(author, ["FR"])["id"], "I1")
        author["last_known_institutions"][0]["type"] = "company"
        self.assertIsNone(researchers._confirmed_home(author, ["FR"]))


if __name__ == "__main__":
    unittest.main()
