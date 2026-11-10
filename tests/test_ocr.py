"""Finding Tesseract and its language files for OCR; no Tesseract is needed to run these."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from concierge import ocr


def language_folder(folder, languages=ocr.LANGUAGES):
    for language in languages:
        (Path(folder) / f"{language}.traineddata").write_bytes(b"data")
    return Path(folder)


class FindTesseractTest(unittest.TestCase):
    def setUp(self):
        ocr.configure.cache_clear()
        self.addCleanup(ocr.configure.cache_clear)

    def test_program_on_the_path_is_used(self):
        with patch.dict(os.environ, {"CCIFJ_TESSERACT_CMD": ""}), patch("shutil.which", return_value="/usr/bin/tesseract"):
            self.assertEqual(ocr.tesseract_command(), "/usr/bin/tesseract")

    def test_usual_install_folder_is_used_when_the_path_was_not_updated(self):
        with tempfile.TemporaryDirectory() as folder:
            program = Path(folder) / "Tesseract-OCR" / "tesseract.exe"
            program.parent.mkdir()
            program.write_bytes(b"")
            with patch.dict(os.environ, {"CCIFJ_TESSERACT_CMD": "", "ProgramFiles": folder}), patch("shutil.which", return_value=None):
                self.assertEqual(ocr.tesseract_command(), str(program))

    def test_missing_program_is_reported_not_raised(self):
        with patch.dict(os.environ, {"CCIFJ_TESSERACT_CMD": ""}), patch("shutil.which", return_value=None), \
                patch.object(ocr, "_usual_places", return_value=[]):
            self.assertIsNone(ocr.tesseract_command())
            self.assertIsNone(ocr.configure())
            report = ocr.status()
        self.assertFalse(report["available"])
        self.assertEqual(report["missing"], list(ocr.LANGUAGES))

    def test_a_wrong_override_falls_back_to_the_normal_search(self):
        with patch.dict(os.environ, {"CCIFJ_TESSERACT_CMD": "/nowhere/tesseract"}), patch("shutil.which", return_value="/usr/bin/tesseract"):
            self.assertEqual(ocr.tesseract_command(), "/usr/bin/tesseract")

    def test_project_language_files_are_used_only_when_all_three_are_there(self):
        with tempfile.TemporaryDirectory() as complete, tempfile.TemporaryDirectory() as partial:
            language_folder(complete)
            language_folder(partial, ("eng",))
            self.assertTrue(ocr.has_languages(Path(complete)))
            self.assertFalse(ocr.has_languages(Path(partial)))
            for folder, expected in ((partial, None), (complete, complete)):
                ocr.configure.cache_clear()
                environment = {"CCIFJ_TESSDATA_DIR": folder, "CCIFJ_TESSERACT_CMD": ""}
                with patch.dict(os.environ, environment), patch("shutil.which", return_value="/usr/bin/tesseract"):
                    os.environ.pop("TESSDATA_PREFIX", None)
                    self.assertEqual(ocr.configure(), "/usr/bin/tesseract")
                    self.assertEqual(os.environ.get("TESSDATA_PREFIX"), expected)

    def test_missing_ocr_gives_a_clear_optional_message(self):
        from fastapi import HTTPException
        from PIL import Image
        import pytesseract
        from concierge.api import attachments
        with patch.object(ocr, "configure", return_value=None), \
                patch.object(pytesseract, "image_to_string", side_effect=pytesseract.TesseractNotFoundError()):
            with self.assertRaises(HTTPException) as raised:
                attachments._ocr(Image.new("RGB", (10, 10)))
        self.assertEqual(raised.exception.status_code, 503)
        self.assertIn("install_ocr", raised.exception.detail)
        self.assertIn("work without it", raised.exception.detail)


if __name__ == "__main__":
    unittest.main()
