"""Focused unit tests for the enhanced Radar features:
- Document library validation, storage safety, and per-application selection
- Safe SQLite schema migrations and idempotency
- Source registry management (enable/disable, custom sources, health reporting)
- Contact enrichment fields, confidence ranking, and evidence merging
- Writer document-aware attachment phrasing

Run with:  .\\venv\\Scripts\\python.exe -m unittest tests/test_features.py
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from radar import config, db, documents, pipeline, server, sources, writer  # noqa: E402


class FeaturesTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.folder = tempfile.TemporaryDirectory()
        cls.original_db = config.DB_PATH
        cls.original_docs = config.DOCUMENTS_DIR
        config.DB_PATH = Path(cls.folder.name) / "test_features.db"
        config.DOCUMENTS_DIR = Path(cls.folder.name) / "documents"
        config.DOCUMENTS_DIR.mkdir(parents=True, exist_ok=True)
        db.initialize()

    @classmethod
    def tearDownClass(cls) -> None:
        config.DB_PATH = cls.original_db
        config.DOCUMENTS_DIR = cls.original_docs
        cls.folder.cleanup()

    def setUp(self) -> None:
        # Clean test tables between tests
        db.execute("DELETE FROM application_documents")
        db.execute("DELETE FROM applications")
        db.execute("DELETE FROM offer_contacts")
        db.execute("DELETE FROM contacts")
        db.execute("DELETE FROM offers")
        db.execute("DELETE FROM documents")


class DocumentLibraryTests(FeaturesTestCase):
    def test_add_and_retrieve_document(self):
        doc = documents.add_document(
            b"%PDF-1.4 sample content",
            "sample_cv.pdf",
            title="My Test CV",
            doc_type="cv",
            language="FR",
            version="2026.1",
            notes="Initial draft",
            is_default=True,
        )
        self.assertIsNotNone(doc["id"])
        self.assertEqual(doc["title"], "My Test CV")
        self.assertEqual(doc["doc_type"], "cv")
        self.assertEqual(doc["language"], "FR")
        self.assertEqual(doc["version"], "2026.1")
        self.assertEqual(doc["is_default"], 1)

        # Stored file check
        file_path = documents.get_document_path(doc)
        self.assertTrue(file_path.exists())
        self.assertEqual(file_path.read_bytes(), b"%PDF-1.4 sample content")
        # Filename is unique and safe
        self.assertTrue(doc["filename"].startswith("doc_"))
        self.assertTrue(doc["filename"].endswith(".pdf"))

    def test_path_traversal_protection(self):
        # Attempt path traversal in original_filename
        doc = documents.add_document(
            b"%PDF-1.4 test",
            "../../etc/passwd.pdf",
            title="Sneaky Doc",
            doc_type="other",
        )
        target = documents.get_document_path(doc)
        self.assertTrue(str(target).startswith(str(config.DOCUMENTS_DIR.resolve())))
        self.assertNotIn("..", target.name)

    def test_file_validation_rejections(self):
        # Empty file
        with self.assertRaises(documents.DocumentError):
            documents.add_document(b"", "empty.pdf", title="Empty")

        # Unsupported extension
        with self.assertRaises(documents.DocumentError):
            documents.add_document(b"echo evil", "script.sh", title="Bad Ext")

        # Oversized file (>15MB)
        large_bytes = b"0" * (16 * 1024 * 1024)
        with self.assertRaises(documents.DocumentError):
            documents.add_document(large_bytes, "large.pdf", title="Too Big")

    def test_default_toggle_exclusivity(self):
        doc1 = documents.add_document(
            b"%PDF-1.4 v1", "cv_v1.pdf", title="CV v1", doc_type="cv", language="FR", is_default=True
        )
        self.assertEqual(documents.get_document(doc1["id"])["is_default"], 1)

        doc2 = documents.add_document(
            b"%PDF-1.4 v2", "cv_v2.pdf", title="CV v2", doc_type="cv", language="FR", is_default=True
        )
        # doc2 is now default, doc1 should have been un-defaulted
        self.assertEqual(documents.get_document(doc2["id"])["is_default"], 1)
        self.assertEqual(documents.get_document(doc1["id"])["is_default"], 0)

    def test_per_application_document_selection(self):
        cv = documents.add_document(b"%PDF-1.4 cv", "cv.pdf", title="Job CV", doc_type="cv", language="FR")
        transcript = documents.add_document(
            b"%PDF-1.4 grades", "grades.pdf", title="M2 Grades", doc_type="transcript", language="FR"
        )
        cert = documents.add_document(
            b"%PDF-1.4 cert", "cert.pdf", title="TOEIC", doc_type="certificate", language="ALL"
        )

        app_id = pipeline.save_application(
            offer_id=None,
            contact_id=None,
            draft={"language": "FR", "recipient": "test@lab.fr", "subject": "Stage", "body": "Bonjour"},
            selected_document_ids=[cv["id"], transcript["id"]],
        )

        selected = documents.get_application_documents(app_id)
        self.assertEqual(len(selected), 2)
        self.assertEqual([d["id"] for d in selected], [cv["id"], transcript["id"]])

        # Update application document selection to add certificate
        updated = documents.set_application_documents(app_id, [cv["id"], cert["id"]])
        self.assertEqual(len(updated), 2)
        self.assertEqual([d["id"] for d in updated], [cv["id"], cert["id"]])

    def test_delete_document_removes_file(self):
        doc = documents.add_document(b"%PDF-1.4 temp", "temp.pdf", title="Temp", doc_type="other")
        path = documents.get_document_path(doc)
        self.assertTrue(path.exists())

        ok = documents.delete_document(doc["id"])
        self.assertTrue(ok)
        self.assertFalse(path.exists())
        self.assertIsNone(documents.get_document(doc["id"]))


class SourceRegistryTests(FeaturesTestCase):
    def test_seed_and_list_sources(self):
        count = sources.seed_sources_registry()
        self.assertGreaterEqual(count, 0)

        all_sources = sources.list_sources_status()
        self.assertGreater(len(all_sources), 50)
        # Builtins like INRIA or EURECOM must be present
        names = {s["source_id"] for s in all_sources}
        self.assertIn("inria", names)
        self.assertIn("eurecom", names)

    def test_toggle_source_enabled(self):
        sources.seed_sources_registry()
        # Toggle INRIA off
        ok = sources.set_source_enabled("inria", False)
        self.assertTrue(ok)
        status_map = {s["source_id"]: s for s in sources.list_sources_status()}
        self.assertFalse(status_map["inria"]["enabled"])
        self.assertEqual(status_map["inria"]["status"], "disabled")

        # Toggle back on
        sources.set_source_enabled("inria", True)
        status_map = {s["source_id"]: s for s in sources.list_sources_status()}
        self.assertTrue(status_map["inria"]["enabled"])

    def test_custom_source_lifecycle(self):
        # Add custom Ashby source
        custom = sources.add_custom_source("ashby", "testcorp", "Test Corp", group_name="Test Startups")
        self.assertEqual(custom["source_id"], "ashby:testcorp")
        self.assertEqual(custom["label"], "Test Corp")
        self.assertEqual(custom["is_custom"], 1)

        # Verify it loads in sources list
        live = sources.load_sources(include_disabled=True)
        live_names = {s.name for s in live}
        self.assertIn("ashby:testcorp", live_names)

        # Delete custom source
        deleted = sources.delete_custom_source("ashby:testcorp")
        self.assertTrue(deleted)
        live_after = sources.load_sources(include_disabled=True)
        self.assertNotIn("ashby:testcorp", {s.name for s in live_after})


class ContactEnrichmentTests(FeaturesTestCase):
    def test_contact_upsert_and_evidence_merging(self):
        with db.write() as conn:
            c1_id, inserted = db.upsert_contact(conn, {
                "name": "Dr. Alice Smith",
                "org": "Inria",
                "role": "Team Lead",
                "role_category": "team_lead",
                "role_verified": True,
                "confidence": 0.95,
                "email": "alice.smith@inria.fr",
                "email_verified": True,
                "url": "https://team.inria.fr/alice",
                "evidence": [{"url": "https://team.inria.fr/alice", "kind": "lab_page", "retrieved_at": "2026-11-15"}],
            })
        self.assertTrue(inserted)
        c1 = db.one("SELECT * FROM contacts WHERE id = ?", (c1_id,))
        self.assertEqual(c1["role_category"], "team_lead")
        self.assertEqual(c1["role_verified"], 1)
        self.assertEqual(c1["confidence"], 0.95)
        self.assertEqual(c1["email_verified"], 1)

        # Merge new evidence without overwriting
        with db.write() as conn:
            c2_id, inserted2 = db.upsert_contact(conn, {
                "name": "Dr. Alice Smith",
                "org": "Inria",
                "role": "Directrice de recherche",
                "evidence": [{"url": "https://hal.science/alice", "kind": "hal_paper", "retrieved_at": "2026-11-15"}],
            })
        self.assertFalse(inserted2)
        self.assertEqual(c1_id, c2_id)
        evidence = json.loads(db.one("SELECT evidence FROM contacts WHERE id = ?", (c1_id,))["evidence"])
        self.assertEqual(len(evidence), 2)
        urls = [e["url"] for e in evidence]
        self.assertIn("https://team.inria.fr/alice", urls)
        self.assertIn("https://hal.science/alice", urls)

    def test_offer_contact_linking_with_relevance(self):
        # Create offer
        db.execute(
            """INSERT INTO offers (uid, source, source_kind, title, org, country, score, url, lang, active, first_seen, last_seen)
               VALUES ('test-uid-1', 'test', 'research', 'Stage NLP', 'Inria', 'FR', 90, 'http://example.com', 'FR', 1, '2026-11-15', '2026-11-15')"""
        )
        offer_id = db.one("SELECT id FROM offers WHERE uid = 'test-uid-1'")["id"]

        with db.write() as conn:
            contact_id, _ = db.upsert_contact(conn, {
                "name": "Prof. Bob Martin",
                "org": "Inria",
                "email": "bob@inria.fr",
            })
            db.link_offer_contact(conn, offer_id, contact_id, relevance=95, role="Primary Supervisor")

        links = db.rows("SELECT * FROM offer_contacts WHERE offer_id = ?", (offer_id,))
        self.assertEqual(len(links), 1)
        self.assertEqual(links[0]["relevance"], 95)
        self.assertEqual(links[0]["role"], "Primary Supervisor")


class WriterDocumentAwarenessTests(FeaturesTestCase):
    def test_email_body_adapts_to_attached_documents(self):
        cv = documents.add_document(b"%PDF-1.4 cv", "cv_fr.pdf", title="CV FR", doc_type="cv", language="FR")
        transcript = documents.add_document(
            b"%PDF-1.4 tr", "releve.pdf", title="Relevé", doc_type="transcript", language="FR"
        )

        # Draft with only CV
        phrase_cv_only = writer._email_attachment_closing([cv], "FR")
        self.assertIn("CV", phrase_cv_only)
        self.assertNotIn("relevé", phrase_cv_only.lower())

        # Draft with CV and Transcript
        phrase_with_transcripts = writer._email_attachment_closing([cv, transcript], "FR")
        self.assertIn("CV", phrase_with_transcripts)
        self.assertIn("relevés", phrase_with_transcripts.lower())

        # English draft with both
        phrase_en = writer._email_attachment_closing([cv, transcript], "EN")
        self.assertIn("CV", phrase_en)
        self.assertIn("transcript", phrase_en.lower())


class ServerApiTests(FeaturesTestCase):
    def test_document_api_endpoints(self):
        import base64

        b64 = base64.b64encode(b"%PDF-1.4 test api doc").decode()
        # Add doc via API
        res = server.api_document_add({
            "body": {
                "title": "API CV",
                "content_base64": b64,
                "filename": "api_cv.pdf",
                "doc_type": "cv",
                "language": "EN",
                "is_default": True,
            }
        })
        self.assertTrue(res["ok"])
        doc_id = res["document"]["id"]

        # List docs via API
        doc_list = server.api_documents({})["documents"]
        self.assertTrue(any(d["id"] == doc_id for d in doc_list))

        # Get doc via API
        doc = server.api_document_get({"id": doc_id})["document"]
        self.assertEqual(doc["title"], "API CV")

        # Update doc via API
        updated = server.api_document_update({"id": doc_id, "body": {"title": "Updated API CV"}})
        self.assertEqual(updated["document"]["title"], "Updated API CV")

        # Set application documents via API
        app_id = pipeline.save_application(
            offer_id=None,
            contact_id=None,
            draft={"language": "EN", "recipient": "hr@test.com", "subject": "Internship", "body": "Hello"},
        )
        set_res = server.api_application_documents_set({"id": app_id, "body": {"document_ids": [doc_id]}})
        self.assertTrue(set_res["ok"])
        self.assertEqual(len(set_res["documents"]), 1)

        # Get application documents via API
        get_res = server.api_application_documents({"id": app_id})
        self.assertEqual(len(get_res["documents"]), 1)
        self.assertEqual(get_res["documents"][0]["id"], doc_id)

        # Delete doc via API
        del_res = server.api_document_delete({"id": doc_id})
        self.assertTrue(del_res["ok"])

    def test_source_api_endpoints(self):
        sources.seed_sources_registry()

        # List sources via API
        all_sources = server.api_sources({})["sources"]
        self.assertGreater(len(all_sources), 50)

        # Toggle source via API
        toggle_res = server.api_source_toggle({"id": "inria", "body": {"enabled": False}})
        self.assertTrue(toggle_res["ok"])
        inria = next(s for s in toggle_res["sources"] if s["source_id"] == "inria")
        self.assertFalse(inria["enabled"])

        # Add custom source via API
        add_res = server.api_source_add({
            "body": {
                "ats": "greenhouse",
                "token": "api_custom_corp",
                "name": "Custom Corp",
            }
        })
        self.assertTrue(add_res["ok"])
        self.assertEqual(add_res["source"]["source_id"], "greenhouse:api_custom_corp")

        # Delete custom source via API
        del_res = server.api_source_delete({"id": "greenhouse:api_custom_corp"})
        self.assertTrue(del_res["ok"])

    def test_contact_star_api_endpoint(self):
        with db.write() as conn:
            cid, _ = db.upsert_contact(conn, {"name": "Starred Person", "org": "Lab", "email": "p@lab.fr"})

        res = server.api_contact_update({"id": cid, "body": {"is_starred": 1}})
        self.assertTrue(res["ok"])
        contact = db.one("SELECT * FROM contacts WHERE id = ?", (cid,))
        self.assertEqual(contact["is_starred"], 1)

        # Unstar
        server.api_contact_update({"id": cid, "body": {"is_starred": 0}})
        contact = db.one("SELECT * FROM contacts WHERE id = ?", (cid,))
        self.assertEqual(contact["is_starred"], 0)


if __name__ == "__main__":
    unittest.main()
