import io
import logging
import re
from xml.sax.saxutils import escape

from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import letter
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer

from models import Resume

logging.basicConfig(level=logging.INFO)

ARIAL_REGULAR_NAME = "ArialResume"
ARIAL_BOLD_NAME = "ArialResume-Bold"


def _register_arial_fonts() -> tuple[str, str]:
    regular_candidates = [
        "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/Library/Fonts/Arial.ttf",
    ]
    bold_candidates = [
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
        "/Library/Fonts/Arial Bold.ttf",
    ]

    try:
        if ARIAL_REGULAR_NAME not in pdfmetrics.getRegisteredFontNames():
            for path in regular_candidates:
                try:
                    pdfmetrics.registerFont(TTFont(ARIAL_REGULAR_NAME, path))
                    break
                except Exception:
                    continue
        if ARIAL_BOLD_NAME not in pdfmetrics.getRegisteredFontNames():
            for path in bold_candidates:
                try:
                    pdfmetrics.registerFont(TTFont(ARIAL_BOLD_NAME, path))
                    break
                except Exception:
                    continue
        if (
            ARIAL_REGULAR_NAME in pdfmetrics.getRegisteredFontNames()
            and ARIAL_BOLD_NAME in pdfmetrics.getRegisteredFontNames()
        ):
            pdfmetrics.registerFontFamily(
                ARIAL_REGULAR_NAME,
                normal=ARIAL_REGULAR_NAME,
                bold=ARIAL_BOLD_NAME,
            )
    except Exception:
        logging.exception("Failed while registering Arial fonts.")

    regular_font = (
        ARIAL_REGULAR_NAME
        if ARIAL_REGULAR_NAME in pdfmetrics.getRegisteredFontNames()
        else "Helvetica"
    )
    bold_font = (
        ARIAL_BOLD_NAME
        if ARIAL_BOLD_NAME in pdfmetrics.getRegisteredFontNames()
        else "Helvetica-Bold"
    )
    return regular_font, bold_font


def _safe_text(value: str | None) -> str:
    text = str(value or "").strip()
    if not text or text == "NA":
        return ""
    return " ".join(text.split())


def _safe_multiline_text(value: str | None) -> list[str]:
    if not value or value == "NA":
        return []
    text = str(value).replace("\r\n", "\n").replace("\r", "\n")
    # Split inline bullet separators into individual lines so all bullets render consistently.
    text = re.sub(r"\s*[•·●▪◦]\s*", "\n", text)

    lines: list[str] = []
    for raw_line in text.splitlines():
        cleaned = re.sub(r"^[-*•·●▪◦]+\s*", "", raw_line.strip()).strip()
        if cleaned:
            lines.append(cleaned)
    return lines


def _append_bullet_lines(
    target_story: list,
    text: str,
    style_bullet: ParagraphStyle,
) -> None:
    """
    Render only explicit newline-separated bullets to keep output predictable.
    """
    bullet_lines = _safe_multiline_text(text) or [_safe_text(text)]

    for bullet in bullet_lines:
        clean_bullet = bullet.lstrip("-*•· ").strip()
        if clean_bullet:
            target_story.append(Paragraph(f"- {escape(clean_bullet)}", style_bullet))


def _append_section_heading(
    target_story: list,
    title: str,
    style_heading: ParagraphStyle,
) -> None:
    target_story.append(Paragraph(escape(title), style_heading))


def _append_skill_lines(
    target_story: list,
    skills: list[str],
    style_skill_line: ParagraphStyle,
) -> None:
    cleaned_skills = [_safe_text(skill) for skill in skills if _safe_text(skill)]
    for skill in cleaned_skills:
        if ":" in skill:
            label, values = skill.split(":", 1)
            target_story.append(
                Paragraph(
                    f"<b>{escape(label.strip())}:</b> {escape(values.strip())}",
                    style_skill_line,
                )
            )
        else:
            target_story.append(Paragraph(escape(skill), style_skill_line))


def create_resume_pdf(resume_data: Resume, header_title: str | None = None) -> bytes:
    """
    Generate a strict ATS-first, single-column, text-based PDF resume.
    """
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=letter,
        leftMargin=0.55 * inch,
        rightMargin=0.55 * inch,
        topMargin=0.55 * inch,
        bottomMargin=0.55 * inch,
    )

    styles = getSampleStyleSheet()
    regular_font, bold_font = _register_arial_fonts()

    style_name = ParagraphStyle(
        name="Name",
        parent=styles["Heading1"],
        fontName=bold_font,
        fontSize=18,
        leading=21,
        alignment=TA_LEFT,
        spaceAfter=4,
    )
    style_header_title = ParagraphStyle(
        name="HeaderTitle",
        parent=styles["Normal"],
        fontName=bold_font,
        fontSize=12,
        leading=14,
        alignment=TA_LEFT,
        spaceAfter=4,
    )
    style_contact = ParagraphStyle(
        name="Contact",
        parent=styles["Normal"],
        fontName=regular_font,
        fontSize=10,
        leading=12,
        alignment=TA_LEFT,
        spaceAfter=2,
    )
    style_section_heading = ParagraphStyle(
        name="SectionHeading",
        parent=styles["Heading2"],
        fontName=bold_font,
        fontSize=13,
        leading=15,
        alignment=TA_LEFT,
        spaceBefore=6,
        spaceAfter=2,
    )
    style_body = ParagraphStyle(
        name="Body",
        parent=styles["Normal"],
        fontName=regular_font,
        fontSize=11,
        leading=13,
        alignment=TA_LEFT,
        spaceAfter=2,
    )
    style_role = ParagraphStyle(
        name="Role",
        parent=style_body,
        fontName=bold_font,
        spaceAfter=2,
    )
    style_meta = ParagraphStyle(
        name="Meta",
        parent=style_body,
        spaceAfter=1,
    )
    style_bullet = ParagraphStyle(
        name="Bullet",
        parent=style_body,
        leftIndent=12,
        firstLineIndent=0,
        spaceAfter=2,
    )

    story: list = []

    if _safe_text(resume_data.name):
        story.append(Paragraph(escape(_safe_text(resume_data.name).upper()), style_name))
    if _safe_text(header_title):
        story.append(Paragraph(escape(_safe_text(header_title)), style_header_title))

    contact_parts = [
        _safe_text(resume_data.email),
        _safe_text(resume_data.phone),
        _safe_text(resume_data.location),
    ]
    contact_parts = [part for part in contact_parts if part]
    if contact_parts:
        story.append(Paragraph(escape(" | ".join(contact_parts)), style_contact))

    link_parts = []
    if resume_data.links:
        for label, raw_url in (
            ("LinkedIn", resume_data.links.linkedin),
            ("GitHub", resume_data.links.github),
            ("Portfolio", resume_data.links.portfolio),
        ):
            clean_url = _safe_text(raw_url)
            if clean_url:
                link_parts.append(f"{label}: {clean_url}")
    if link_parts:
        story.append(Paragraph(escape(" | ".join(link_parts)), style_contact))

    summary_text = _safe_text(resume_data.summary)
    if summary_text:
        _append_section_heading(story, "PROFESSIONAL SUMMARY", style_section_heading)
        story.append(Paragraph(escape(summary_text), style_body))

    cleaned_skills = [_safe_text(skill) for skill in resume_data.skills if _safe_text(skill)]
    if cleaned_skills:
        _append_section_heading(story, "TECHNICAL SKILLS", style_section_heading)
        _append_skill_lines(story, cleaned_skills, style_body)

    if resume_data.experience:
        _append_section_heading(story, "PROFESSIONAL EXPERIENCE", style_section_heading)
        for exp in resume_data.experience:
            role_parts = [
                _safe_text(exp.job_title),
                _safe_text(exp.company),
            ]
            role_parts = [part for part in role_parts if part]
            if role_parts:
                story.append(Paragraph(escape(" | ".join(role_parts)), style_role))

            if _safe_text(exp.location):
                story.append(Paragraph(escape(_safe_text(exp.location)), style_meta))

            dates = ""
            if _safe_text(exp.start_date) and _safe_text(exp.end_date):
                dates = f"{_safe_text(exp.start_date)} - {_safe_text(exp.end_date)}"
            elif _safe_text(exp.start_date):
                dates = f"{_safe_text(exp.start_date)} - Present"
            elif _safe_text(exp.end_date):
                dates = _safe_text(exp.end_date)
            if dates:
                story.append(Paragraph(escape(dates), style_meta))

            _append_bullet_lines(story, exp.description, style_bullet)
            story.append(Spacer(1, 0.04 * inch))

    if resume_data.projects:
        _append_section_heading(story, "PROJECTS", style_section_heading)
        for project in resume_data.projects:
            project_name = _safe_text(project.name)
            if project_name:
                story.append(Paragraph(escape(project_name), style_role))

            _append_bullet_lines(story, project.description, style_bullet)

            technologies = [
                _safe_text(technology)
                for technology in (project.technologies or [])
                if _safe_text(technology)
            ]
            if technologies:
                story.append(
                    Paragraph(
                        f"<b>Technologies:</b> {escape(', '.join(technologies))}",
                        style_body,
                    )
                )
            story.append(Spacer(1, 0.04 * inch))

    if resume_data.education:
        _append_section_heading(story, "EDUCATION", style_section_heading)
        for edu in resume_data.education:
            degree_parts = [_safe_text(edu.degree)]
            if _safe_text(edu.field_of_study):
                degree_parts.append(_safe_text(edu.field_of_study))
            degree_line = ", ".join([part for part in degree_parts if part])
            if degree_line:
                story.append(Paragraph(escape(degree_line), style_role))

            if _safe_text(edu.institution):
                story.append(Paragraph(escape(_safe_text(edu.institution)), style_meta))

            years = ""
            if _safe_text(edu.start_year) and _safe_text(edu.end_year):
                years = f"{_safe_text(edu.start_year)} - {_safe_text(edu.end_year)}"
            elif _safe_text(edu.end_year):
                years = _safe_text(edu.end_year)
            elif _safe_text(edu.start_year):
                years = _safe_text(edu.start_year)
            if years:
                story.append(Paragraph(escape(years), style_meta))
            story.append(Spacer(1, 0.04 * inch))

    if resume_data.certifications:
        valid_certs = [
            cert
            for cert in resume_data.certifications
            if _safe_text(cert.name) or _safe_text(cert.issuer)
        ]
        if valid_certs:
            _append_section_heading(story, "CERTIFICATIONS", style_section_heading)
            for cert in valid_certs:
                cert_name = _safe_text(cert.name)
                if cert_name:
                    story.append(Paragraph(escape(cert_name), style_role))
                if _safe_text(cert.issuer):
                    story.append(Paragraph(escape(_safe_text(cert.issuer)), style_meta))
                if _safe_text(cert.year):
                    story.append(Paragraph(escape(_safe_text(cert.year)), style_meta))
                story.append(Spacer(1, 0.04 * inch))

    languages = [_safe_text(language) for language in resume_data.languages if _safe_text(language)]
    if languages:
        _append_section_heading(story, "LANGUAGES", style_section_heading)
        story.append(Paragraph(escape(", ".join(languages)), style_body))

    try:
        doc.build(story)
        logging.info("PDF generated successfully.")
    except Exception as exc:
        logging.error(f"Error building PDF: {exc}")
        raise

    pdf_bytes = buffer.getvalue()
    buffer.close()
    return pdf_bytes
