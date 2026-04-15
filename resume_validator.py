import io
import re
from typing import Iterable

import pdfplumber

from models import Resume


def _normalize_text(value: str | None) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip()).lower()


def _normalize_phone(value: str | None) -> str:
    return re.sub(r"\D", "", str(value or ""))


def _normalize_url(value: str | None) -> str:
    text = _normalize_text(value)
    text = text.replace("https://", "").replace("http://", "")
    return text.rstrip("/")


def _has_content(value: object) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip()) and value.strip() != "NA"
    if isinstance(value, Iterable):
        return any(_has_content(item) for item in value)
    return True


def _contains(normalized_haystack: str, normalized_needle: str) -> bool:
    return bool(normalized_needle) and normalized_needle in normalized_haystack


def validate_generated_resume_pdf(
    pdf_bytes: bytes,
    resume_data: Resume,
    header_title: str | None = None,
) -> tuple[bool, list[str]]:
    """
    Extract text from the generated PDF and verify critical ATS-visible content exists.
    """
    issues: list[str] = []

    if not pdf_bytes:
        return False, ["Generated PDF is empty."]

    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        page_texts = [(page.extract_text() or "").strip() for page in pdf.pages]

    combined_text = "\n".join(text for text in page_texts if text).strip()
    if not combined_text:
        return False, ["Generated PDF does not contain extractable text."]

    normalized_text = _normalize_text(combined_text)
    normalized_text_no_urls = _normalize_url(combined_text)
    normalized_phone_text = _normalize_phone(combined_text)

    critical_checks: list[tuple[str, bool]] = [
        ("Missing candidate name in extracted PDF text.", _contains(normalized_text, _normalize_text(resume_data.name))),
        ("Missing email in extracted PDF text.", _contains(normalized_text, _normalize_text(resume_data.email))),
    ]

    if _normalize_phone(resume_data.phone):
        critical_checks.append(
            (
                "Missing phone number in extracted PDF text.",
                _normalize_phone(resume_data.phone) in normalized_phone_text,
            )
        )

    if _normalize_text(header_title):
        critical_checks.append(
            (
                "Missing target role title in extracted PDF text.",
                _contains(normalized_text, _normalize_text(header_title)),
            )
        )

    expected_sections = [
        ("PROFESSIONAL SUMMARY", _has_content(resume_data.summary)),
        ("TECHNICAL SKILLS", _has_content(resume_data.skills)),
        ("PROFESSIONAL EXPERIENCE", _has_content(resume_data.experience)),
        ("PROJECTS", _has_content(resume_data.projects)),
        ("EDUCATION", _has_content(resume_data.education)),
        ("CERTIFICATIONS", _has_content(resume_data.certifications)),
        ("LANGUAGES", _has_content(resume_data.languages)),
    ]
    for heading, should_exist in expected_sections:
        if should_exist and heading.lower() not in normalized_text:
            critical_checks.append(
                (f"Missing section heading '{heading}' in extracted PDF text.", False)
            )

    for message, passed in critical_checks:
        if not passed:
            issues.append(message)

    for exp in resume_data.experience:
        job_title = _normalize_text(exp.job_title)
        if job_title and job_title not in normalized_text:
            issues.append(
                f"Missing experience job title '{exp.job_title}' in extracted PDF text."
            )

    for project in resume_data.projects:
        project_name = _normalize_text(project.name)
        if project_name and project_name not in normalized_text:
            issues.append(
                f"Missing project name '{project.name}' in extracted PDF text."
            )

    if resume_data.links.linkedin:
        normalized_link = _normalize_url(resume_data.links.linkedin)
        if normalized_link and normalized_link not in normalized_text_no_urls:
            issues.append("Missing LinkedIn URL in extracted PDF text.")

    return len(issues) == 0, issues
