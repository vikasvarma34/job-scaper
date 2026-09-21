import io
import json
import unittest
from pathlib import Path
from unittest import mock

import pdfplumber

import app as app_module


ROOT_DIR = Path(__file__).resolve().parents[1]
SAMPLE_RESUME_PATH = ROOT_DIR / "resume_builder.json"
if not SAMPLE_RESUME_PATH.exists():
    SAMPLE_RESUME_PATH = ROOT_DIR / "resume_new_google.json"
if not SAMPLE_RESUME_PATH.exists():
    SAMPLE_RESUME_PATH = ROOT_DIR / "resume_new.json"


class ResumeBuilderTests(unittest.TestCase):
    def setUp(self):
        self.app = app_module.app.test_client()
        with open(SAMPLE_RESUME_PATH, "r", encoding="utf-8") as f:
            self.sample_resume = json.load(f)

    def test_resume_builder_page_renders_ok(self):
        res = self.app.get("/builder")
        self.assertEqual(res.status_code, 200)
        self.assertIn(b"Resume Builder", res.data)
        self.assertIn(b"Structured Form", res.data)
        self.assertIn(b"Raw JSON", res.data)

    def test_api_builder_get_base(self):
        res = self.app.get("/api/builder/base")
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertTrue(data.get("ok"))
        self.assertIn("name", data.get("resume", {}))

    def test_api_builder_generate_pdf_from_payload(self):
        payload = {
            "company_name": "Google",
            "file_name": "Vikas_Varma_Pokala_Resume_Google.pdf",
            "resume_data": self.sample_resume,
        }
        res = self.app.post("/api/builder/pdf", json=payload)
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.mimetype, "application/pdf")
        self.assertIn("Vikas_Varma_Pokala_Resume_Google.pdf", res.headers.get("Content-Disposition", ""))

        # Verify PDF contents with pdfplumber
        with pdfplumber.open(io.BytesIO(res.data)) as pdf:
            extracted_text = "\n".join(page.extract_text() or "" for page in pdf.pages)

        self.assertIn("VIKAS VARMA POKALA", extracted_text)
        self.assertIn("Sri Sai Educational Society", extracted_text)
        self.assertIn("Markitech AI", extracted_text)

        # Hierarchy & clean presentation assertions
        # 1. Company Name + Employment Dates
        self.assertIn("Jul 2026 – Present", extracted_text)
        self.assertIn("Feb 2024 – Feb 2026", extracted_text)

        # 2. Job Title + Location directly underneath with middle dot
        self.assertIn("Full Stack Developer – AI & Web Applications · Kodad, India", extracted_text)
        self.assertIn("Full Stack Developer · Toronto, Canada", extracted_text)

        # 3. Concise project headings
        self.assertIn("CliniScripts — Clinical Documentation Platform", extracted_text)
        self.assertIn("TELUS Marketplace — IoT Marketplace", extracted_text)
        self.assertIn("CliniAssess — Pre-Visit Assessment Platform", extracted_text)
        self.assertIn("Institution Digital Systems — Website, Email, and College Portal", extracted_text)

        # 4. No scale/metrics in project headings
        self.assertNotIn("used by 250+ doctors -", extracted_text)
        self.assertNotIn("used at the University of Toronto by 30+ doctors -", extracted_text)

        # 5. 250+ doctors evidence retained in bullets
        self.assertIn("250+ doctors", extracted_text)

        # 6. Avoid duplicate identical dates for Sri Sai Educational Society
        self.assertNotIn("Institution Digital Systems — Website, Email, and College Portal -", extracted_text)
        self.assertNotIn("Institution Digital Systems — Website, Email, and College Portal — Jul", extracted_text)

    def test_api_builder_generate_pdf_missing_payload(self):
        res = self.app.post("/api/builder/pdf", json={})
        self.assertEqual(res.status_code, 400)
        data = res.get_json()
        self.assertFalse(data.get("ok"))

    @mock.patch("supabase_utils.save_custom_resume")
    def test_api_builder_save_resume(self, mock_save):
        mock_save.return_value = {
            "id": "123e4567-e89b-12d3-a456-426614174000",
            "company_name": "Google",
            "file_name": "Vikas_Resume_Google.pdf",
            "resume_data": self.sample_resume,
        }

        payload = {
            "company_name": "Google",
            "file_name": "Vikas_Resume_Google.pdf",
            "resume_data": self.sample_resume,
        }
        res = self.app.post("/api/builder/save", json=payload)
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertTrue(data.get("ok"))
        self.assertEqual(data["saved"]["company_name"], "Google")

    @mock.patch("supabase_utils.list_saved_resumes")
    def test_api_builder_list_resumes(self, mock_list):
        mock_list.return_value = [
            {
                "id": "123",
                "company_name": "Google",
                "file_name": "Vikas_Resume_Google.pdf",
                "created_at": "2026-09-21T12:00:00Z",
            }
        ]
        res = self.app.get("/api/builder/resumes")
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertTrue(data.get("ok"))
        self.assertEqual(len(data["resumes"]), 1)
        self.assertEqual(data["resumes"][0]["company_name"], "Google")

    @mock.patch("supabase_utils.get_saved_resume")
    def test_api_builder_get_saved_resume(self, mock_get):
        mock_get.return_value = {
            "id": "123",
            "company_name": "Google",
            "file_name": "Vikas_Resume_Google.pdf",
            "resume_data": self.sample_resume,
        }
        res = self.app.get("/api/builder/resumes/123")
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertTrue(data.get("ok"))
        self.assertEqual(data["resume"]["company_name"], "Google")

    @mock.patch("supabase_utils.delete_saved_resume")
    def test_api_builder_delete_saved_resume(self, mock_delete):
        mock_delete.return_value = True
        res = self.app.delete("/api/builder/resumes/123")
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertTrue(data.get("ok"))


if __name__ == "__main__":
    unittest.main()
