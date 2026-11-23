"""Review session: reply detection helpers, batch approval, application kits, outreach preparation."""
from __future__ import annotations

import email
import email.policy
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from radar import config, db, inbox, mailer, pipeline, workflow  # noqa: E402


def message(body: str, **headers: str) -> email.message.Message:
    fields = {"From": "Prof <prof@lab.example.fr>", "Subject": "Re: Candidature", **headers}
    raw = "".join(f"{key}: {value}\n" for key, value in fields.items())
    data = (raw + "Content-Type: text/plain; charset=utf-8\nContent-Transfer-Encoding: 8bit\n\n" + body).encode("utf-8")
    return email.message_from_bytes(data, policy=email.policy.default)


class Replies(unittest.TestCase):
    def test_tone_hints(self):
        self.assertEqual(inbox.tone("Malheureusement, je ne dispose pas de stage à proposer."), "declined")
        self.assertEqual(inbox.tone("I would be glad to host you but do not have any funding to cover your expenses."), "unfunded")
        self.assertEqual(inbox.tone("Please see the application instructions on my website."), "redirect")
        self.assertEqual(inbox.tone("Could we schedule a call next week to discuss?"), "positive")
        self.assertEqual(inbox.tone("Bien reçu."), "neutral")

    def test_quoted_original_is_removed(self):
        text = inbox.reply_text(message("Bonjour,\nMerci pour votre message.\n\nLe 12 nov. 2026 à 10:00, Zak a écrit :\n> Madame,\n> ..."))
        self.assertEqual(text, "Bonjour,\nMerci pour votre message.")
        wrapped = inbox.reply_text(message(
            "Dear Ahcene,\nPlease apply on my website.\n\nOn Thu, Nov 12, 2026 at 2:44 PM Ahcene Zakaria AOUANOUK <me@x.org>\nwrote:\n> Dear Professor"))
        self.assertEqual(wrapped, "Dear Ahcene,\nPlease apply on my website.")

    def test_automatic_replies_are_recognised(self):
        self.assertTrue(inbox.is_auto_reply(message("Absent", **{"Auto-Submitted": "auto-replied"})))
        self.assertTrue(inbox.is_auto_reply(message("Away", Subject="Automatic reply: Candidature")))
        self.assertFalse(inbox.is_auto_reply(message("Bonjour")))

    def test_bounce_names_the_failed_address(self):
        notice = message("Delivery failed.\nFinal-Recipient: rfc822; gone@lab.example.fr\n",
                         From="Mail Delivery Subsystem <mailer-daemon@googlemail.com>")
        self.assertEqual(inbox.failed_recipients(notice, {"gone@lab.example.fr", "other@x.fr"}), {"gone@lab.example.fr"})


class Workflow(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.folder = tempfile.TemporaryDirectory()
        cls.saved = (config.DB_PATH, config.DOCUMENTS_DIR)
        config.DB_PATH = Path(cls.folder.name) / "test.db"
        config.DOCUMENTS_DIR = Path(cls.folder.name) / "documents"
        db.initialize()
        mailer.update_settings({"accounts": [{"address": "me@school.test", "password": "secret", "host": "127.0.0.1", "port": 1, "security": "none"}]})

    @classmethod
    def tearDownClass(cls) -> None:
        config.DB_PATH, config.DOCUMENTS_DIR = cls.saved
        cls.folder.cleanup()

    def contact(self, name: str, address: str, notes: str = "", country: str = "FR") -> int:
        with db.write() as connection:
            contact_id, _ = db.upsert_contact(connection, {"name": name, "org": "LIX", "country": country, "email": address,
                                                           "kind": "similar", "topics": ["NLP"], "evidence": [], "relevance": 80})
            connection.execute("UPDATE contacts SET notes = ? WHERE id = ?", (notes, contact_id))
        return contact_id

    def test_outreach_is_prepared_only_for_verified_new_people(self):
        ok = self.contact("Ada Lovelace", "ada@lab.example.fr")
        flagged = self.contact("Bob Guess", "bob@lab.example.fr", notes="Adresse déduite — à vérifier")
        result = workflow.prepare_outreach(contact_ids=[ok, flagged])
        self.assertEqual(result["created"], 1)
        self.assertIn("address to verify", result["skipped"][0]["reason"])
        draft = db.one("SELECT language, stage, recipient FROM applications WHERE contact_id = ?", (ok,))
        self.assertEqual((draft["language"], draft["stage"], draft["recipient"]), ("FR", "to_review", "ada@lab.example.fr"))
        self.assertEqual(workflow.prepare_outreach(contact_ids=[ok])["created"], 0)  # never a second e-mail to the same person

    def test_batch_approval_spaces_the_sends(self):
        ids = []
        for index in range(3):
            contact_id = self.contact(f"Person {index} Test", f"p{index}@lab.example.fr")
            workflow.prepare_outreach(contact_ids=[contact_id])
            ids.append(db.one("SELECT id FROM applications WHERE contact_id = ?", (contact_id,))["id"])
        result = workflow.bulk(ids, "approve")
        self.assertEqual((result["done"], result["failed"]), (3, []))
        times = [row["send_at"] for row in db.rows("SELECT send_at FROM applications WHERE id IN (?, ?, ?) ORDER BY id", ids)]
        self.assertEqual(times, sorted(times))
        self.assertEqual(len(set(times)), 3)
        self.assertEqual(workflow.bulk(ids[:1], "unapprove")["done"], 1)
        self.assertEqual(db.one("SELECT stage FROM applications WHERE id = ?", (ids[0],))["stage"], "to_review")

    def test_reply_stops_the_follow_up(self):
        contact_id = self.contact("Carla Reply", "carla@lab.example.fr")
        workflow.prepare_outreach(contact_ids=[contact_id])
        application = db.one("SELECT * FROM applications WHERE contact_id = ?", (contact_id,))
        db.execute("UPDATE applications SET stage = 'follow_up', sent_at = '2026-11-01' WHERE id = ?", (application["id"],))
        follow_up = pipeline.prepare_follow_up(application["id"])
        self.assertIsNotNone(follow_up)
        inbox.record_reply({**application, "recipient": "carla@lab.example.fr"}, sender="carla@lab.example.fr", subject="Re",
                           text="Malheureusement non.", when="2026-11-12", auto=False)
        row = db.one("SELECT stage, reply_tone FROM applications WHERE id = ?", (application["id"],))
        self.assertEqual((row["stage"], row["reply_tone"]), ("replied", "declined"))
        self.assertIsNone(db.one("SELECT id FROM applications WHERE id = ?", (follow_up,)))
        self.assertEqual(db.one("SELECT status FROM contacts WHERE id = ?", (contact_id,))["status"], "replied")

    def test_application_kit_and_default_filters(self):
        with db.write() as connection:
            offer_id, _ = db.upsert_offer(connection, "test:1", {
                "source": "test", "source_kind": "company", "title": "Stage NLP", "org": "via SFdS", "country": "FR", "tier": "tier1",
                "url": "https://example.org/job", "description": "Stage de 6 mois en NLP et LLM.", "lang": "fr", "score": 90,
                "score_parts": {}, "flags": [], "tags": [], "pay": {}})
        kit = workflow.kit(offer_id)
        self.assertEqual((kit["stage"], kit["language"]), ("kit", "FR"))
        self.assertNotIn("via SFdS", kit["letter"])  # a board is never named as the employer
        self.assertEqual(workflow.kit(offer_id)["application_id"], kit["application_id"])  # reused, not duplicated
        workflow.mark_applied(offer_id)
        self.assertEqual(db.one("SELECT status FROM offers WHERE id = ?", (offer_id,))["status"], "applied")
        self.assertEqual(db.one("SELECT stage, sent_from FROM applications WHERE id = ?", (kit["application_id"],)),
                         {"stage": "sent", "sent_from": "employer website"})
        self.assertTrue(workflow.ineligible(["Nationality / security-clearance restriction mentioned"]))
        self.assertTrue(workflow.ineligible(["German required"]))
        self.assertFalse(workflow.ineligible(["Start date looks like 2026 / immediate — check that a 2027 start is possible"]))


if __name__ == "__main__":
    unittest.main()
