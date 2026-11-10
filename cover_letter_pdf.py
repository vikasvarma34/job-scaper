import io
import re
from datetime import date
from xml.sax.saxutils import escape

from reportlab.lib.enums import TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer

from pdf_generator import _register_arial_fonts


def _format_current_date() -> str:
    today = date.today()
    return f"{today.strftime('%B')} {today.day}, {today.year}"


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
        fontSize=18,
        leading=21,
        alignment=TA_LEFT,
        spaceAfter=4,
    )
    style_contact = ParagraphStyle(
        name="CoverLetterContact",
        parent=styles["Normal"],
        fontName=regular_font,
        fontSize=12,
        leading=14,
        alignment=TA_LEFT,
        spaceAfter=2,
    )
    style_date = ParagraphStyle(
        name="CoverLetterDate",
        parent=styles["Normal"],
        fontName=regular_font,
        fontSize=12,
        leading=14,
        alignment=TA_RIGHT,
        spaceAfter=8,
    )
    style_body = ParagraphStyle(
        name="CoverLetterBody",
        parent=styles["Normal"],
        fontName=regular_font,
        fontSize=12,
        leading=14,
        alignment=TA_LEFT,
        spaceAfter=10,
    )

    story: list = []

    story.append(Paragraph(escape(_format_current_date()), style_date))

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


class CoverLetterPageLimitError(ValueError):
    """The document is intact but cannot be returned under the one-page limit."""
    def __init__(self, page_count):
        self.page_count = page_count
        super().__init__(f"This cover letter is {page_count} pages. Please shorten the content or adjust the available "
                         "layout settings to fit one page. No content has been removed.")


def _register_builder_fonts():
    """Use only bundled, OFL-licensed Arimo; never depend on system Arial."""
    from pathlib import Path
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    folder = Path(__file__).resolve().parent / 'assets' / 'fonts' / 'arimo'
    regular, bold = 'CoverLetterArimo', 'CoverLetterArimo-Bold'
    for name, filename in ((regular, 'Arimo-Regular.ttf'), (bold, 'Arimo-Bold.ttf')):
        if name not in pdfmetrics.getRegisteredFontNames():
            pdfmetrics.registerFont(TTFont(name, str(folder / filename)))
    pdfmetrics.registerFontFamily(regular, normal=regular, bold=bold)
    return regular, bold


def create_builder_cover_letter_pdf(data: dict, *, font_size=11.5, top_margin=0.75,
                                    bottom_margin=0.75, side_margin=0.75) -> bytes:
    """Render a structured standalone letter without changing legacy PDF behavior."""
    from datetime import date
    from reportlab.lib.pagesizes import A4
    import pdfplumber
    from cover_letter_model import validate_cover_letter
    data = validate_cover_letter(data)
    size = float(font_size)
    margins = [float(top_margin), float(bottom_margin), float(side_margin)]
    if not 11 <= size <= 16 or any(not 0.25 <= m <= 1.5 for m in margins):
        raise ValueError("Font size must be 11–16 pt; margins must be 0.25–1.5 inches")
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4, topMargin=margins[0]*inch,
                           bottomMargin=margins[1]*inch, leftMargin=margins[2]*inch,
                           rightMargin=margins[2]*inch)
    regular, bold = _register_builder_fonts()
    body = ParagraphStyle('LetterBody', fontName=regular, fontSize=size,
                          leading=size*1.22, spaceAfter=12, alignment=TA_LEFT)
    header = ParagraphStyle('LetterHeader', parent=body, fontName=bold,
                            fontSize=16, leading=19.52, spaceAfter=8, keepWithNext=True)
    contact = ParagraphStyle('LetterContact', parent=body, spaceAfter=2)
    contact_last = ParagraphStyle('LetterContactLast', parent=contact, spaceAfter=16)
    date_style = ParagraphStyle('LetterDate', parent=body, spaceBefore=16, spaceAfter=16)
    recipient_style = ParagraphStyle('LetterRecipient', parent=body, spaceAfter=18)
    subject = ParagraphStyle('LetterSubject', parent=body, fontName=bold,
                             spaceBefore=12, spaceAfter=12, keepWithNext=True)
    greeting = ParagraphStyle('LetterGreeting', parent=body, spaceAfter=12, keepWithNext=True)
    story = []

    def add(text, style=body):
        if text.strip():
            story.append(Paragraph(escape(text).replace('\n', '<br/>'), style))

    applicant, recipient = data['applicant'], data['recipient']
    add(applicant['name'], header)
    # Keep contact lines together; give only the final line a section-sized gap.
    contact_lines = [' | '.join(applicant.get(k, '') for k in ('email', 'phone', 'location') if applicant.get(k))]
    contact_lines.extend(applicant.get(key, '') for key in ('linkedin', 'portfolio'))
    contact_lines = [line for line in contact_lines if line.strip()]
    for index, line in enumerate(contact_lines):
        add(line, contact_last if index == len(contact_lines) - 1 else contact)
    letter_date = date.fromisoformat(data['date'])
    add(f"{letter_date.strftime('%B')} {letter_date.day}, {letter_date.year}", date_style)
    add('\n'.join(recipient.get(k, '') for k in ('name', 'job_title', 'company', 'address') if recipient.get(k)), recipient_style)
    add(data['subject'], subject)
    add(data['greeting'], greeting)
    for paragraph in data['paragraphs']:
        add(paragraph)
    closing_style = ParagraphStyle('LetterClosing', parent=body, spaceBefore=16, spaceAfter=8,
                                   keepWithNext=bool(data['signature'].strip()))
    signature_style = ParagraphStyle('LetterSignature', parent=body, spaceAfter=0)
    add(data['closing'], closing_style)
    add(data['signature'], signature_style)
    doc.build(story)
    pdf_bytes = buffer.getvalue()
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        page_count = len(pdf.pages)
    if page_count > 1:
        raise CoverLetterPageLimitError(page_count)
    return pdf_bytes
