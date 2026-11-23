"""“Connect with Google”: client setup, consent link, token exchange, Gmail API sending and reading.

Google is never contacted: every HTTP call is replaced by a stand-in.
"""
from __future__ import annotations

import base64
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from urllib.parse import parse_qs, urlparse

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from radar import config, db, google_mail, inbox, mailer  # noqa: E402

CLIENT_JSON = json.dumps({"installed": {"client_id": "123-abc.apps.googleusercontent.com", "client_secret": "GOCSPX-test",
                                        "project_id": "internship-radar"}})


def id_token(address: str) -> str:
    payload = base64.urlsafe_b64encode(json.dumps({"email": address}).encode()).rstrip(b"=").decode()
    return f"header.{payload}.signature"


class Reply:
    def __init__(self, status: int, body: dict) -> None:
        self.status_code, self._body, self.content = status, body, json.dumps(body).encode()

    def json(self) -> dict:
        return self._body


class GoogleMail(unittest.TestCase):
    def setUp(self) -> None:
        self.folder = tempfile.TemporaryDirectory()
        self.saved = (config.DB_PATH, config.DOCUMENTS_DIR)
        config.DB_PATH = Path(self.folder.name) / "test.db"
        config.DOCUMENTS_DIR = Path(self.folder.name) / "documents"
        db.initialize()
        mailer.update_settings({"accounts": [{"address": "me@school.test"}, {"address": "me@gmail.test"}]})
        google_mail._pending.clear()
        google_mail._tokens.clear()

    def tearDown(self) -> None:
        config.DB_PATH, config.DOCUMENTS_DIR = self.saved
        self.folder.cleanup()

    def connect(self, address: str = "me@school.test", index: int = 0, read: bool = True) -> dict:
        google_mail.set_client(CLIENT_JSON)
        url = google_mail.start(index, read, 8765)
        state = parse_qs(urlparse(url).query)["state"][0]
        scopes = ["openid", "email", google_mail.SEND_SCOPE] + ([google_mail.READ_SCOPE] if read else [])
        token = Reply(200, {"access_token": "at-1", "expires_in": 3599, "refresh_token": "rt-1", "scope": " ".join(scopes),
                            "id_token": id_token(address)})
        with mock.patch.object(google_mail.requests, "post", return_value=token) as post:
            result = google_mail.finish({"state": [state], "code": ["auth-code"]})
        self.exchange = post.call_args.kwargs["data"]
        return result

    def test_client_file_is_validated(self):
        status = google_mail.set_client(CLIENT_JSON)
        self.assertEqual((status["configured"], status["project"]), (True, "internship-radar"))
        with self.assertRaises(google_mail.GoogleError):
            google_mail.set_client(json.dumps({"web": {"client_id": "x.apps.googleusercontent.com", "client_secret": "s"}}))
        with self.assertRaises(google_mail.GoogleError):
            google_mail.set_client("not json")
        self.assertNotIn("GOCSPX-test", json.dumps(db.get_setting("google_client")))  # the secret is stored encrypted

    def test_consent_link_uses_loopback_pkce_and_minimal_scopes(self):
        google_mail.set_client(CLIENT_JSON)
        query = parse_qs(urlparse(google_mail.start(0, False, 8765)).query)
        self.assertEqual(query["redirect_uri"], ["http://127.0.0.1:8765/"])
        self.assertEqual(query["code_challenge_method"], ["S256"])
        self.assertEqual(query["login_hint"], ["me@school.test"])
        self.assertIn(google_mail.SEND_SCOPE, query["scope"][0])
        self.assertNotIn(google_mail.READ_SCOPE, query["scope"][0])
        self.assertEqual(query["access_type"], ["offline"])

    def test_sign_in_stores_an_encrypted_refresh_token(self):
        result = self.connect()
        self.assertEqual(result, {"address": "me@school.test", "read": True, "index": 0})
        self.assertEqual(self.exchange["grant_type"], "authorization_code")
        self.assertTrue(self.exchange["code_verifier"])
        account = mailer.settings()["accounts"][0]
        self.assertTrue(google_mail.can_read(account))
        self.assertNotIn("rt-1", json.dumps(account))
        self.assertTrue(mailer.public_settings()["ready"])
        self.assertTrue(mailer.public_settings()["accounts"][0]["google"]["connected"])

    def test_a_link_works_once(self):
        self.connect()
        with self.assertRaises(google_mail.GoogleError):
            google_mail.finish({"state": ["unknown"], "code": ["x"]})

    def test_connected_address_replaces_the_typed_one(self):
        self.connect(address="real@school.test")
        self.assertEqual(mailer.settings()["accounts"][0]["address"], "real@school.test")

    def test_refusal_is_explained(self):
        google_mail.set_client(CLIENT_JSON)
        state = parse_qs(urlparse(google_mail.start(0, True, 8765)).query)["state"][0]
        with self.assertRaisesRegex(google_mail.GoogleError, "administrator"):
            google_mail.finish({"state": [state], "error": ["admin_policy_enforced"]})

    def test_sending_goes_through_the_gmail_api_upload(self):
        self.connect()
        application = {"id": None, "recipient": "prof@lab.example.fr", "subject": "Candidature", "body": "Bonjour", "language": "FR",
                       "letter": "", "selected_documents": []}
        with mock.patch.object(mailer, "validate"), \
             mock.patch.object(google_mail.requests, "request", return_value=Reply(200, {"id": "msg-1"})) as call:
            sender = mailer.send(application)
        self.assertEqual(sender, "me@school.test")
        method, url = call.call_args.args
        self.assertEqual((method, url), ("POST", google_mail.UPLOAD))
        self.assertEqual(call.call_args.kwargs["headers"]["Authorization"], "Bearer at-1")
        self.assertIn(b"To: prof@lab.example.fr", call.call_args.kwargs["data"])

    def test_expired_access_token_is_renewed_once(self):
        self.connect()
        account = mailer.settings()["accounts"][0]
        replies = [Reply(401, {}), Reply(200, {"id": "msg-2"})]
        with mock.patch.object(google_mail.requests, "request", side_effect=replies) as call, \
             mock.patch.object(google_mail.requests, "post", return_value=Reply(200, {"access_token": "at-2", "expires_in": 3599})):
            google_mail.send(account, mailer.EmailMessage())
        self.assertEqual(call.call_args.kwargs["headers"]["Authorization"], "Bearer at-2")

    def test_withdrawn_permission_is_reported_and_marked(self):
        self.connect()
        google_mail._tokens.clear()
        with mock.patch.object(google_mail.requests, "post", return_value=Reply(400, {"error": "invalid_grant"})):
            with self.assertRaisesRegex(google_mail.GoogleError, "Connect with Google"):
                google_mail.access_token(mailer.settings()["accounts"][0])
        self.assertTrue(mailer.public_settings()["accounts"][0]["google"]["expired"])

    def test_timeout_is_never_retried_from_the_other_account(self):
        self.connect()
        mailer.update_settings({"accounts": [{"address": "me@school.test"}, {"address": "me@gmail.test", "password": "app-pass"}]})
        with mock.patch.object(mailer, "validate"), \
             mock.patch.object(google_mail.requests, "request", side_effect=requests.Timeout()), \
             mock.patch.object(mailer, "_connect") as smtp:
            with self.assertRaises(mailer.Uncertain):
                mailer.send({"recipient": "x@y.fr", "subject": "s", "body": "b", "letter": "", "selected_documents": []})
        smtp.assert_not_called()

    def test_reading_uses_gmail_search(self):
        self.connect()
        raw = base64.urlsafe_b64encode(b"From: Prof <prof@lab.example.fr>\r\nSubject: Re\r\n\r\nBonjour").decode()
        replies = [Reply(200, {"messages": [{"id": "a1"}]}), Reply(200, {"raw": raw})]
        with mock.patch.object(google_mail.requests, "request", side_effect=replies) as call:
            box = google_mail.ApiMailbox(mailer.settings()["accounts"][0])
            ids = box.search("from:(prof@lab.example.fr) after:2026/11/01", [])
            message = box.fetch(ids[0])
        self.assertEqual(call.call_args_list[0].kwargs["params"]["q"], "from:(prof@lab.example.fr) after:2026/11/01")
        self.assertEqual(inbox.reply_text(message), "Bonjour")

    def test_send_only_connection_is_not_used_for_reading(self):
        self.connect(read=False)
        with self.assertRaises(google_mail.GoogleError):
            google_mail.ApiMailbox(mailer.settings()["accounts"][0])

    def test_changing_the_address_drops_the_connection(self):
        self.connect()
        mailer.update_settings({"accounts": [{"address": "other@school.test"}, {"address": "me@gmail.test"}]})
        self.assertFalse(google_mail.connected(mailer.settings()["accounts"][0]))

    def test_interrupted_send_is_handed_back(self):
        with db.write() as connection:
            connection.execute("INSERT INTO applications (kind, stage, recipient, subject, body, created_at, updated_at) "
                               "VALUES ('spontaneous', 'sending', 'x@y.fr', 's', 'b', '2026-11-16T01:00:00Z', '2026-11-16T01:59:58Z')")
        self.assertEqual(mailer.recover_interrupted(), 1)
        row = db.one("SELECT stage, send_error FROM applications")
        self.assertEqual(row["stage"], "ready")
        self.assertIn("Sent folder", row["send_error"])


if __name__ == "__main__":
    unittest.main()
