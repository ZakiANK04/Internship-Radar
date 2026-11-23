"""Where researchers work (city, region, Nice · Côte d'Azur) and lighter attachments."""
from __future__ import annotations

import io
import sys
import tempfile
import unittest
from pathlib import Path

from PIL import Image
from pypdf import PdfReader

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from radar import compress, config, db, documents, places  # noqa: E402


def scan_pdf(width: int = 4800, height: int = 6800) -> bytes:
    """A page photographed at ~580 dpi, like the transcripts."""
    image = Image.new("RGB", (width, height), "white")
    pixels = image.load()
    for y in range(0, height, 7):
        for x in range(0, width, 3):
            pixels[x, y] = ((x * 7) % 255, (y * 3) % 255, (x + y) % 255)
    out = io.BytesIO()
    image.save(out, "PDF", resolution=580)
    return out.getvalue()


class Places(unittest.TestCase):
    def test_cote_azur_institutions(self):
        cases = [
            ({"org": "EURECOM", "country": "FR"}, "Sophia Antipolis"),
            ({"org": "l'équipe Maasai (Inria / I3S), Université Côte d'Azur / Inria", "country": "FR"}, "Sophia Antipolis"),
            ({"org": "Université Côte d'Azur", "country": "FR"}, "Nice"),
            ({"org": None, "country": "FR", "email": "someone@i3s.unice.fr"}, "Sophia Antipolis"),
            ({"org": "Centre Inria d'Université Côte d'Azur", "country": "FR"}, "Sophia Antipolis"),
        ]
        for person, city in cases:
            place = places.locate(person)
            self.assertEqual((place["city"], place["area"], place["region"]), (city, "cote_azur", "Provence-Alpes-Côte d'Azur"), person)

    def test_other_places(self):
        self.assertEqual(places.locate({"org": "Ca' Foscari University of Venice", "country": "IT"})["area"], None)  # not Nice
        self.assertEqual(places.locate({"org": "Politecnico di Milano", "country": "IT"})["city"], "Milan")
        self.assertEqual(places.locate({"org": "Università di Milano", "country": "IT"})["city"], "Milan")
        self.assertEqual(places.locate({"org": "LIX, École polytechnique", "country": "FR"})["region"], "Île-de-France")
        self.assertEqual(places.locate({"org": "KU Leuven", "country": "BE"})["region"], None)  # regions are French only
        self.assertEqual(places.locate({"org": "Some Lab", "country": "FR"})["city"], None)     # never guessed

    def test_spreadsheet_university_is_used(self):
        person = {"org": "votre équipe", "country": "FR", "source_row": '{"lab": "", "university": "Université de Nice"}'}
        self.assertEqual(places.locate(person)["city"], "Nice")


class Compression(unittest.TestCase):
    def test_scans_become_light_and_keep_their_pages(self):
        original = scan_pdf()
        smaller = compress.compress(original, ".pdf")
        self.assertIsNotNone(smaller)
        self.assertLess(len(smaller), len(original) * 0.5)
        reader = PdfReader(io.BytesIO(smaller))
        self.assertEqual(len(reader.pages), 1)
        image = reader.pages[0].images[0].image
        width_in = float(reader.pages[0].mediabox.width) / 72
        self.assertLessEqual(image.width, int(width_in * compress.TARGET_DPI) + 1)

    def test_no_gain_is_not_kept(self):
        once = compress.compress(scan_pdf(1200, 1700), ".pdf") or scan_pdf(1200, 1700)
        self.assertIsNone(compress.compress(once, ".pdf"))  # compressing twice gains nothing, so it is not redone
        self.assertIsNone(compress.compress(b"not a pdf at all", ".docx"))


class DocumentLibrary(unittest.TestCase):
    def setUp(self) -> None:
        self.folder = tempfile.TemporaryDirectory()
        self.saved = (config.DB_PATH, config.DOCUMENTS_DIR)
        config.DB_PATH = Path(self.folder.name) / "test.db"
        config.DOCUMENTS_DIR = Path(self.folder.name) / "documents"
        db.initialize()

    def tearDown(self) -> None:
        config.DB_PATH, config.DOCUMENTS_DIR = self.saved
        self.folder.cleanup()

    def test_large_upload_is_compressed_and_original_kept(self):
        data = scan_pdf()
        doc = documents.add_document(data, "Transcript.pdf", title="Transcript", doc_type="transcript", language="EN")
        self.assertLess(doc["file_size"], len(data) * 0.5)
        self.assertTrue(documents.has_original(doc))
        restored = documents.restore_original(doc["id"])
        self.assertEqual(restored["file_size"], len(data))
        self.assertTrue(documents.compress_document(doc["id"])["compressed"])
