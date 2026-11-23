"""Researchers you add yourself: one person, a pasted list, and the e-mail written for them."""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from radar import config, db, outreach, researchers, workflow  # noqa: E402


class AddPerson(unittest.TestCase):
    def setUp(self) -> None:
        self.folder = tempfile.TemporaryDirectory()
        self.saved = (config.DB_PATH, config.DOCUMENTS_DIR)
        config.DB_PATH = Path(self.folder.name) / "test.db"
        config.DOCUMENTS_DIR = Path(self.folder.name) / "documents"
        db.initialize()

    def tearDown(self) -> None:
        config.DB_PATH, config.DOCUMENTS_DIR = self.saved
        self.folder.cleanup()

    def add(self, **values):
        return researchers.add_person({"name": "Marie Curie", "email": "marie.curie@u-paris.fr", "title": "prof_f",
                                       "lab": "Laboratoire de Physique", "university": "Université Paris Cité",
                                       "areas": "language models, information retrieval", **values}, log=lambda *a: None)

    def test_person_is_stored_with_everything_the_email_needs(self):
        result = self.add()
        self.assertTrue(result["created"])
        person = db.one("SELECT * FROM contacts WHERE id = ?", (result["id"],))
        self.assertEqual((person["kind"], person["country"], person["status"]), ("manual", "FR", "new"))
        self.assertIsNone(outreach.needs_checking(person))
        details = outreach._details(person)
        self.assertEqual(outreach.greeting(details, "FR"), "Madame la Professeure Curie,")
        self.assertEqual(outreach.greeting(details, "EN"), "Dear Professor Curie,")
        self.assertIn("language models", outreach.interest_paragraph(details, "EN"))

    def test_draft_is_written_in_the_right_language(self):
        contact_id = self.add()["id"]
        italian = researchers.add_person({"name": "Luca Rossi", "email": "luca.rossi@polimi.it", "university": "Politecnico di Milano"},
                                         log=lambda *a: None)["id"]
        self.assertEqual(workflow.prepare_outreach(contact_ids=[contact_id, italian])["created"], 2)
        drafts = {row["contact_id"]: row for row in db.rows("SELECT contact_id, language, recipient, body FROM applications")}
        self.assertEqual(drafts[contact_id]["language"], "FR")
        self.assertTrue(drafts[contact_id]["body"].startswith("Madame la Professeure Curie,"))
        self.assertEqual(drafts[italian]["language"], "EN")
        self.assertTrue(drafts[italian]["body"].startswith("Dear Luca Rossi,"))

    def test_honorific_in_the_name_box_sets_the_greeting(self):
        person = db.one("SELECT * FROM contacts WHERE id = ?", (self.add(name="Prof. Jean Dupont", email="jean@inria.fr", title="")["id"],))
        self.assertEqual(person["name"], "Jean Dupont")
        self.assertEqual(outreach.greeting(outreach._details(person), "FR"), "Bonjour Professeur Dupont,")

    def test_same_address_is_not_added_twice(self):
        first = self.add()
        again = self.add(name="Marie  Curie", title="")
        self.assertFalse(again["created"])
        self.assertEqual(again["id"], first["id"])
        self.assertEqual(db.one("SELECT count(*) AS n FROM contacts")["n"], 1)

    def test_bad_input_is_refused(self):
        with self.assertRaises(ValueError):
            self.add(name="Curie")
        with self.assertRaises(ValueError):
            self.add(email="not-an-address")

    def test_your_note_can_hold_the_email_back(self):
        result = self.add(note="adresse à vérifier")
        self.assertEqual(workflow.prepare_outreach(contact_ids=[result["id"]])["created"], 0)

    def test_pasted_list(self):
        result = researchers.add_list(
            "Prof. Marie Curie <marie.curie@u-paris.fr>, Université Paris Cité, France\n"
            "Jean Dupont, jean.dupont@inria.fr, Inria Grenoble\n"
            "Anna Weber\tanna.weber@tum.de\tTU München\tDE\n"
            "nobody here\n", log=lambda *a: None)
        self.assertEqual(len(result["added"]), 3)
        self.assertEqual(result["problems"][0]["reason"], "no e-mail address on this line")
        countries = {row["email"]: row["country"] for row in db.rows("SELECT email, country FROM contacts")}
        self.assertEqual(countries, {"marie.curie@u-paris.fr": "FR", "jean.dupont@inria.fr": "FR", "anna.weber@tum.de": "DE"})

    def test_added_people_come_first_for_drafts(self):
        with db.write() as connection:
            db.upsert_contact(connection, {"name": "Some Expert", "org": "Lab", "country": "FR", "email": "expert@lab.fr", "kind": "expert",
                                           "topics": [], "evidence": [], "relevance": 99})
        mine = self.add()["id"]
        workflow.prepare_outreach(limit=1)
        self.assertEqual([row["contact_id"] for row in db.rows("SELECT contact_id FROM applications")], [mine])

    def test_write_and_send_to_everyone_selected(self):
        from radar import mailer

        people = [self.add()["id"],
                  researchers.add_person({"name": "Luca Rossi", "email": "luca.rossi@polimi.it", "university": "Politecnico di Milano"},
                                         log=lambda *a: None)["id"],
                  researchers.add_person({"name": "Anna Weber", "email": "anna.weber@tum.de", "note": "adresse à vérifier"},
                                         log=lambda *a: None)["id"]]
        with self.assertRaises(mailer.MailError):  # no mailbox connected: nothing is written or scheduled
            workflow.send_to(people)
        mailer.update_settings({"accounts": [{"address": "me@school.test", "password": "secret"}], "delay_hours": 6, "daily_cap": 15})
        workflow.prepare_outreach(contact_ids=people[:1])  # one draft already waiting: it is approved too, not duplicated
        result = workflow.send_to(people)
        self.assertEqual((result["written"], result["approved"]), (1, 2))
        self.assertIn("address to verify", result["skipped"][0]["reason"])
        rows = db.rows("SELECT contact_id, stage, send_at FROM applications ORDER BY id")
        self.assertEqual([row["stage"] for row in rows], ["scheduled", "scheduled"])
        self.assertEqual(len({row["send_at"] for row in rows}), 2)  # spaced out, never all at once
        self.assertEqual(workflow.send_to(people)["approved"], 0)  # a second click sends nothing twice


if __name__ == "__main__":
    unittest.main()
