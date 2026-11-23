"""Apps Script e-mail template, language by workplace, attachments wording."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from radar import outreach  # noqa: E402

PROFILE = {"full_name": "A. Z. Aouanouk", "phone": "+213", "email": "me@example.org", "linkedin": "li", "github": "gh"}


def contact(**row):
    import json
    return {"name": row.pop("name", "Jane Doe"), "role": "", "org": "", "topics": [], "notes": "",
            "source_row": json.dumps({"title": "", "lab": "", "university": "", "areas": "", "notes": "", **row})}


class Outreach(unittest.TestCase):
    def test_language_follows_the_workplace(self):
        self.assertEqual(outreach.language_for("FR"), "FR")
        self.assertEqual(outreach.language_for("BE", "Université de Namur"), "FR")
        self.assertEqual(outreach.language_for("BE", "KU Leuven"), "EN")
        self.assertEqual(outreach.language_for("CH", "EPFL"), "FR")
        self.assertEqual(outreach.language_for("CH", "ETH Zürich"), "EN")
        self.assertEqual(outreach.language_for("IT", "Politecnico di Milano"), "EN")

    def test_greeting_like_the_script(self):
        details = outreach._details(contact(name="Hanane Azzag", title="Madame", notes="Professeure des universités"))
        self.assertEqual(outreach.greeting(details, "FR"), "Madame la Professeure Azzag,")
        self.assertEqual(outreach.greeting(details, "EN"), "Dear Professor Azzag,")
        plain = outreach._details(contact(name="John Smith", title="Monsieur"))
        self.assertEqual(outreach.greeting(plain, "FR"), "Monsieur Smith,")

    def test_experience_matches_whole_words(self):
        note = outreach._details(contact(areas="Data Mining", notes="noter l'orthographe"))
        self.assertIn("NeoGAM", outreach.experience(note, "FR"))  # data mining, not the "graph" in orthographe
        nlp = outreach._details(contact(areas="NLP, LLMs"))
        self.assertIn("ECCBC", outreach.experience(nlp, "EN"))

    def test_attachments_sentence_names_what_is_attached(self):
        docs = [{"doc_type": "cv"}, {"doc_type": "transcript"}]
        self.assertEqual(outreach.attachments_sentence("FR", docs, True),
                         "Vous trouverez en pièces jointes mon CV, ma lettre de motivation et mes relevés de notes.")
        self.assertEqual(outreach.attachments_sentence("EN", docs[:1], False), "Please find attached my CV.")

    def test_full_email_and_follow_up(self):
        person = contact(name="Jane Doe", title="Madame", lab="LIX", university="École polytechnique", areas="NLP")
        email = outreach.initial_email(person, "FR", PROFILE, [{"doc_type": "cv"}], letter=True)
        self.assertEqual(email["subject"], outreach.SUBJECT["FR"])
        self.assertIn("4 à 6 mois, à partir de janvier 2027", email["body"])
        self.assertIn("«LIX», rattachée à l’École polytechnique", email["body"])
        self.assertTrue(email["body"].endswith("GitHub : gh"))
        relance = outreach.follow_up_email(person, "EN", PROFILE, [{"doc_type": "cv"}])
        self.assertEqual(relance["subject"], outreach.FOLLOW_UP_SUBJECT["EN"])
        self.assertIn("I am attaching my CV again", relance["body"])

    def test_verification_flag_and_override(self):
        flagged = {"notes": "From your list. Your note: Adresse déduite — à vérifier avant envoi.", "source_row": "{}"}
        self.assertEqual(outreach.needs_checking(flagged), "à vérifier")
        flagged["notes"] += "\n[Address verified by you on 2026-11-15]"
        self.assertIsNone(outreach.needs_checking(flagged))
        history_only = {"notes": "Your Apps Script skipped them (Ignoré: à vérifier). Your note: Full professor.", "source_row": "{}"}
        self.assertIsNone(outreach.needs_checking(history_only))


if __name__ == "__main__":
    unittest.main()
