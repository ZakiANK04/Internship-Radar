"""E-mail sending, exercised against a local stand-in SMTP server (nothing leaves this machine).

Run with:  .\\venv\\Scripts\\python.exe -m unittest discover -s tests
"""

from __future__ import annotations

import base64
import email
import socketserver
import sys
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from radar import config, db, mailer, pipeline  # noqa: E402

PASSWORDS = {"primary@school.test": "right-primary", "fallback@mail.test": "right-fallback"}
RECEIVED: list[tuple[str, list[str], bytes]] = []


class _Handler(socketserver.StreamRequestHandler):
    """Just enough SMTP for smtplib: EHLO, AUTH PLAIN, MAIL, RCPT, DATA, QUIT."""

    def handle(self) -> None:
        self.wfile.write(b"220 stub ready\r\n")
        sender, recipients, user = "", [], None
        while True:
            line = self.rfile.readline()
            if not line:
                return
            command = line.decode("utf-8", "replace").strip()
            upper = command.upper()
            if upper.startswith("EHLO"):
                self.wfile.write(b"250-stub\r\n250 AUTH PLAIN\r\n")
            elif upper.startswith("AUTH PLAIN"):
                _, name, password = base64.b64decode(command.split()[2]).decode().split("\0")
                if PASSWORDS.get(name) == password:
                    user = name
                    self.wfile.write(b"235 ok\r\n")
                else:
                    self.wfile.write(b"535 5.7.8 Username and Password not accepted\r\n")
            elif upper.startswith("MAIL FROM"):
                sender = command.split(":", 1)[1].split()[0].strip("<>")
                self.wfile.write(b"250 ok\r\n" if user else b"530 authentication required\r\n")
            elif upper.startswith("RCPT TO"):
                recipients.append(command.split(":", 1)[1].strip().strip("<>"))
                self.wfile.write(b"250 ok\r\n")
            elif upper == "DATA":
                self.wfile.write(b"354 go\r\n")
                data = b""
                while not data.endswith(b"\r\n.\r\n"):
                    data += self.rfile.readline()
                RECEIVED.append((sender, recipients, data[:-5]))
                sender, recipients = "", []
                self.wfile.write(b"250 queued\r\n")
            elif upper == "QUIT":
                self.wfile.write(b"221 bye\r\n")
                return
            else:
                self.wfile.write(b"250 ok\r\n")


class MailerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.folder = tempfile.TemporaryDirectory()
        cls.original_db = config.DB_PATH
        config.DB_PATH = Path(cls.folder.name) / "test.db"
        db.initialize()
        cls.server = socketserver.ThreadingTCPServer(("127.0.0.1", 0), _Handler)
        cls.port = cls.server.server_address[1]
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()
        config.DB_PATH = cls.original_db
        cls.folder.cleanup()

    def setUp(self) -> None:
        RECEIVED.clear()
        db.execute("DELETE FROM applications")
        db.execute("DELETE FROM settings WHERE key = 'mail'")
        self.configure("right-primary", "right-fallback")

    def configure(self, primary_password: str, fallback_password: str, **extra) -> None:
        stub = {"host": "127.0.0.1", "port": self.port, "security": "none"}
        mailer.update_settings({"accounts": [
            {"address": "primary@school.test", "password": primary_password, **stub},
            {"address": "fallback@mail.test", "password": fallback_password, **stub}], **extra})

    def draft(self, language: str = "FR", recipient: str = "lab@example.org") -> int:
        return pipeline.save_application(offer_id=None, contact_id=None, draft={
            "kind": "spontaneous", "language": language, "recipient": recipient,
            "subject": "Candidature — stage de fin d'études" if language == "FR" else "Application — final-year internship",
            "body": "Bonjour,\n\nJe vous écris au sujet de votre offre de stage.\n\nBien cordialement,\nCandidate",
            "letter": "Madame, Monsieur,\n\nÉlève-ingénieur en dernière année, je vous adresse ma candidature.\n\nCordialement,"})

    def stage(self, application_id: int) -> str:
        return db.one("SELECT stage FROM applications WHERE id = ?", (application_id,))["stage"]

    def make_due(self, application_id: int) -> None:
        past = (datetime.now(timezone.utc) - timedelta(minutes=1)).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        db.execute("UPDATE applications SET send_at = ? WHERE id = ?", (past, application_id))

    # ----------------------------------------------------------------- tests
    def test_password_is_stored_encrypted_and_never_exposed(self):
        stored = db.get_setting("mail")["accounts"][0]["secret"]
        self.assertNotIn("right-primary", stored)
        self.assertEqual(mailer.reveal(stored), "right-primary")
        self.assertNotIn("secret", str(mailer.public_settings()))
        self.assertTrue(mailer.public_settings()["accounts"][0]["has_password"])

    def test_message_carries_letter_and_cv_in_the_posting_language(self):
        for language, cv, letter in (("FR", "candidate_cv_fr.pdf", "Lettre_de_motivation"), ("EN", "candidate_cv_en.pdf", "Cover_Letter")):
            message = mailer.build_message(mailer.load_application(self.draft(language)), sender="primary@school.test")
            names = [part.get_filename() for part in message.iter_attachments()]
            self.assertEqual(len(names), 2)
            self.assertTrue(names[0].startswith(letter) and names[0].endswith(".pdf"))
            self.assertEqual(names[1], cv)
            self.assertTrue(next(message.iter_attachments()).get_content().startswith(b"%PDF"))
            self.assertIn("primary@school.test", message["From"])

    def test_approval_waits_six_hours_and_nothing_leaves_early(self):
        application_id = self.draft()
        result = mailer.approve(application_id)
        self.assertEqual(result["delay_hours"], 6)
        wait = datetime.fromisoformat(result["send_at"].replace("Z", "+00:00")) - datetime.now(timezone.utc)
        self.assertTrue(timedelta(hours=5, minutes=58) < wait <= timedelta(hours=6))
        self.assertEqual(self.stage(application_id), "scheduled")
        self.assertEqual(mailer.send_due(), [])
        self.assertEqual(RECEIVED, [])

    def test_due_email_is_sent_once_from_the_primary_account(self):
        application_id = self.draft()
        mailer.approve(application_id)
        self.make_due(application_id)
        first = mailer.send_due()
        self.assertTrue(first[0]["sent"])
        self.assertEqual(mailer.send_due(), [])
        self.assertEqual(len(RECEIVED), 1)
        sender, recipients, raw = RECEIVED[0]
        self.assertEqual((sender, recipients), ("primary@school.test", ["lab@example.org"]))
        self.assertEqual(len(list(email.message_from_bytes(raw, policy=email.policy.default).iter_attachments())), 2)
        row = db.one("SELECT * FROM applications WHERE id = ?", (application_id,))
        self.assertEqual((row["stage"], row["sent_from"]), ("sent", "primary@school.test"))
        self.assertTrue(row["sent_at"] and row["follow_up_at"])

    def test_fallback_account_is_used_when_the_primary_is_refused(self):
        self.configure("wrong-password", "right-fallback")
        application_id = self.draft()
        mailer.approve(application_id, 0)
        outcome = mailer.deliver(application_id)
        self.assertTrue(outcome["sent"])
        self.assertEqual(outcome["from"], "fallback@mail.test")
        self.assertEqual(RECEIVED[0][0], "fallback@mail.test")

    def test_failure_is_reported_and_not_retried_blindly(self):
        self.configure("wrong-password", "also-wrong")
        application_id = self.draft()
        mailer.approve(application_id, 0)
        outcome = mailer.deliver(application_id)
        self.assertFalse(outcome["sent"])
        row = db.one("SELECT * FROM applications WHERE id = ?", (application_id,))
        self.assertEqual(row["stage"], "ready")
        self.assertIn("refused", row["send_error"])
        self.assertEqual(mailer.send_due(), [])

    def test_cannot_approve_without_a_recipient_or_send_twice(self):
        with self.assertRaises(mailer.MailError):
            mailer.approve(self.draft(recipient=""))
        application_id = self.draft()
        mailer.approve(application_id, 0)
        mailer.deliver(application_id)
        with self.assertRaises(mailer.MailError):
            mailer.approve(application_id)
        self.assertEqual(len(RECEIVED), 1)

    def test_cancelling_before_the_delay_keeps_the_email_back(self):
        application_id = self.draft()
        mailer.approve(application_id)
        db.execute("UPDATE applications SET stage = 'to_review', send_at = NULL WHERE id = ?", (application_id,))
        self.assertEqual(mailer.send_due(), [])
        self.assertEqual(RECEIVED, [])

    def test_daily_cap_holds_the_rest_for_tomorrow(self):
        self.configure("right-primary", "right-fallback", daily_cap=2)
        ids = [self.draft(recipient=f"lab{index}@example.org") for index in range(3)]
        for application_id in ids:
            mailer.approve(application_id)
            self.make_due(application_id)
        self.assertEqual(len(mailer.send_due()), 2)
        self.assertEqual(len(RECEIVED), 2)
        self.assertEqual(self.stage(ids[2]), "scheduled")


if __name__ == "__main__":
    unittest.main()
