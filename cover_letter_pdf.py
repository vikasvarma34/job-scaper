import io
import re
from xml.sax.saxutils import escape

from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer

from pdf_generator import _register_arial_fonts


def _clean_text(value: str | None) -> str:
    text = str(value or "").strip()
    if not text or text == "NA":
        return ""
    return " ".join(text.split())


def _split_paragraphs(text: str | None) -> list[str]:
    raw_text = str(text or "").replace("\r\n", "\n")
    if "\n\n" not in raw_text and "\n" in raw_text:
        return [line.strip() for line in raw_text.splitlines() if line.strip()]

    paragraphs = []
    for part in raw_text.split("\n\n"):
        cleaned = "\n".join(line.strip() for line in part.splitlines() if line.strip()).strip()
        if cleaned:
            paragraphs.append(cleaned)
    return paragraphs


def _extract_signoff_block(text: str | None) -> tuple[list[str], str | None, str | None]:
    raw_text = str(text or "").replace("\r\n", "\n").strip()
    if not raw_text:
        return [], None, None

    signoff_pattern = re.compile(
        r"(?is)(.*?)(?:\n\s*\n)?"
        r"(Sincerely|Best regards|Kind regards|Regards|Thank you),?"
        r"(?:\s*\n+\s*|\s+)"
        r"([A-Za-z][A-Za-z .'-]{1,80})\s*$"
    )
    match = signoff_pattern.match(raw_text)
    if not match:
        return _split_paragraphs(raw_text), None, None

    body_text = match.group(1).strip()
    closing = f"{match.group(2).strip()},"
    name = _clean_text(match.group(3))
    return _split_paragraphs(body_text), closing, name


def create_cover_letter_pdf(
    *,
    applicant_name: str,
    email: str,
    phone: str,
    location: str,
    linkedin: str = "",
    cover_letter_text: str,
) -> bytes:
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=letter,
        leftMargin=0.7 * inch,
        rightMargin=0.7 * inch,
        topMargin=0.7 * inch,
        bottomMargin=0.7 * inch,
    )

    styles = getSampleStyleSheet()
    regular_font, bold_font = _register_arial_fonts()

    style_name = ParagraphStyle(
        name="CoverLetterName",
        parent=styles["Heading1"],
        fontName=bold_font,
        fontSize=15,
        leading=18,
        alignment=TA_LEFT,
        spaceAfter=3,
    )
    style_contact = ParagraphStyle(
        name="CoverLetterContact",
        parent=styles["Normal"],
        fontName=regular_font,
        fontSize=10.5,
        leading=12.5,
        alignment=TA_LEFT,
        spaceAfter=2,
    )
    style_body = ParagraphStyle(
        name="CoverLetterBody",
        parent=styles["Normal"],
        fontName=regular_font,
        fontSize=11,
        leading=14,
        alignment=TA_LEFT,
        spaceAfter=10,
    )

    story: list = []

    if _clean_text(applicant_name):
        story.append(Paragraph(escape(_clean_text(applicant_name).upper()), style_name))

    contact_parts = [_clean_text(email), _clean_text(phone), _clean_text(location)]
    contact_line = " | ".join(part for part in contact_parts if part)
    if contact_line:
        story.append(Paragraph(escape(contact_line), style_contact))

    linkedin_url = _clean_text(linkedin)
    if linkedin_url:
        story.append(Paragraph(escape(f"LinkedIn: {linkedin_url}"), style_contact))

    story.append(Spacer(1, 0.28 * inch))

    body_paragraphs, closing_line, signoff_name = _extract_signoff_block(cover_letter_text)

    for paragraph in body_paragraphs:
        story.append(Paragraph(escape(paragraph), style_body))

    if closing_line:
        story.append(Spacer(1, 0.08 * inch))
        story.append(Paragraph(escape(closing_line), style_body))
    if signoff_name:
        story.append(Spacer(1, 0.04 * inch))
        story.append(Paragraph(escape(signoff_name), style_body))

    doc.build(story)
    return buffer.getvalue()
