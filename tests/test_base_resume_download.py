import io
import json
import unittest
from pathlib import Path
from unittest import mock

import pdfplumber

import app as app_module
import custom_resume_generator
import pdf_generator
import resume_validator
from models import ResumePatch, is_resume_v2_model, parse_resume_data


ROOT_DIR = Path(__file__).resolve().parents[1]
BASE_RESUME_PATH = ROOT_DIR / "resume_new.json"


def load_base_resume():
    return parse_resume_data(json.loads(BASE_RESUME_PATH.read_text(encoding="utf-8")))


def extract_pdf_text(pdf_bytes: bytes) -> str:
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        return "\n".join(page.extract_text() or "" for page in pdf.pages)


class BaseResumeDownloadTests(unittest.TestCase):
    def test_attached_resume_loads_as_v2_and_preserves_new_current_role(self):
        resume = load_base_resume()

        self.assertTrue(is_resume_v2_model(resume))
        self.assertEqual(len(resume.experience), 2)
        current_role = resume.experience[0]
        self.assertEqual(current_role.company, "Sri Sai Educational Society")
        self.assertEqual(current_role.location, "Kodad, India")
        self.assertEqual(current_role.title, "Full Stack Developer – AI & Web Applications")
        self.assertEqual(current_role.start_date, "Mar 2026")
        self.assertEqual(current_role.end_date, "Present")
        self.assertEqual(
            current_role.project_blocks[0].project,
            "Institution Digital Systems — Website, Email, and College Portal",
        )
        self.assertIn("role-based college portal", current_role.project_blocks[0].bullets[1])

        id_map = custom_resume_generator.assign_resume_ids(resume)
        self.assertEqual(id_map["exp0"], 0)
        self.assertEqual(id_map["exp1"], 1)
        self.assertIn("Leading the redesign", id_map["exp0.proj0.bullet0"][3])

    def test_base_pdf_generation_does_not_mutate_source_and_includes_sri_sai(self):
        resume = load_base_resume()
        before = resume.model_dump(mode="json")

        pdf_bytes = pdf_generator.create_resume_pdf(resume)
        is_valid, issues = resume_validator.validate_generated_resume_pdf(pdf_bytes, resume)
        pdf_text = extract_pdf_text(pdf_bytes)

        self.assertTrue(is_valid, issues)
        self.assertEqual(before, resume.model_dump(mode="json"))
        self.assertIn("Sri Sai Educational Society", pdf_text)
        self.assertIn("Institution Digital Systems", pdf_text)

    def test_empty_tailoring_patch_preserves_new_role_and_existing_workflow_invariants(self):
        resume = load_base_resume()
        patch = ResumePatch()

        patched_resume, change_log, warnings = custom_resume_generator.apply_resume_patch(resume, patch)
        custom_resume_generator.validate_v2_patched_resume(resume, patched_resume, patch, change_log)

        self.assertEqual(warnings, [])
        self.assertEqual(patched_resume.experience[0].company, "Sri Sai Educational Society")
        self.assertEqual(patched_resume.experience[1].company, "Markitech AI")

    def test_base_resume_download_endpoint_returns_pdf_from_canonical_resume(self):
        with app_module.app.test_client() as client:
            response = client.get("/resume/base/download")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.mimetype, "application/pdf")
        self.assertIn(
            "Vikas_Varma_Pokala_Base_Resume.pdf",
            response.headers.get("Content-Disposition", ""),
        )
        self.assertIn("Sri Sai Educational Society", extract_pdf_text(response.data))

    def test_index_has_base_resume_button_and_download_path(self):
        dashboard_data = {
            "jobs": [],
            "jobs_error": "",
            "stats": {
                "total_jobs": 0,
                "scored_jobs": 0,
                "resumes_generated": 0,
                "pending_jobs": 0,
                "not_available_jobs": 0,
            },
        }
        with mock.patch.object(app_module, "_fetch_dashboard_data", return_value=dashboard_data):
            with app_module.app.test_client() as client:
                response = client.get("/")

        page = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn('id="base-resume-download-button"', page)
        self.assertIn("Download Base Resume", page)
        self.assertIn("/resume/base/download", page)


if __name__ == "__main__":
    unittest.main()
