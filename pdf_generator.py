import io
import logging
import re
from xml.sax.saxutils import escape

from reportlab.lib.enums import TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import letter
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from models import ResumeLike, is_resume_v2_model

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


MONTH_ABBR_MAP = {
    "january": "Jan", "jan": "Jan",
    "february": "Feb", "feb": "Feb",
    "march": "Mar", "mar": "Mar",
    "april": "Apr", "apr": "Apr",
    "may": "May",
    "june": "Jun", "jun": "Jun",
    "july": "Jul", "jul": "Jul",
    "august": "Aug", "aug": "Aug",
    "september": "Sep", "sep": "Sep", "sept": "Sep",
    "october": "Oct", "oct": "Oct",
    "november": "Nov", "nov": "Nov",
    "december": "Dec", "dec": "Dec",
}


def _normalize_date_token(token: str) -> str:
    cleaned = token.strip()
    if not cleaned:
        return ""
    if cleaned.lower() in ("present", "current", "now"):
        return "Present"

    m = re.match(r"^([A-Za-z]+)\.?,?\s*(\d{4})$", cleaned)
    if m:
        month_str, year = m.group(1).lower(), m.group(2)
        abbr = MONTH_ABBR_MAP.get(month_str, month_str.capitalize()[:3])
        return f"{abbr} {year}"

    m = re.match(r"^(\d{4})[-/](\d{1,2})$", cleaned)
    if m:
        year, month_num = m.group(1), int(m.group(2))
        months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
        if 1 <= month_num <= 12:
            return f"{months[month_num - 1]} {year}"
        return cleaned

    m = re.match(r"^(\d{1,2})[-/](\d{4})$", cleaned)
    if m:
        month_num, year = int(m.group(1)), m.group(2)
        months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
        if 1 <= month_num <= 12:
            return f"{months[month_num - 1]} {year}"
        return cleaned

    return cleaned


def _format_date_range(start: str | None, end: str | None = None) -> str:
    start_str = _safe_text(start)
    end_str = _safe_text(end)

    if not start_str and not end_str:
        return ""

    if start_str and not end_str:
        parts = re.split(r"\s*[–—\-]\s*", start_str, maxsplit=1)
        if len(parts) == 2:
            s_clean = _normalize_date_token(parts[0])
            e_clean = _normalize_date_token(parts[1])
            if s_clean and e_clean:
                return f"{s_clean} – {e_clean}"
            return s_clean or e_clean
        return _normalize_date_token(start_str)

    s_clean = _normalize_date_token(start_str)
    e_clean = _normalize_date_token(end_str)

    if s_clean and e_clean:
        return f"{s_clean} – {e_clean}"
    elif s_clean:
        return f"{s_clean} – Present"
    elif e_clean:
        return e_clean
    return ""


def _clean_project_title(title: str | None) -> str:
    cleaned = _safe_text(title)
    if not cleaned:
        return ""
    lower = cleaned.lower()
    if lower.startswith("cliniscripts"):
        return "CliniScripts — Clinical Documentation Platform"
    if lower.startswith("telus marketplace"):
        return "TELUS Marketplace — IoT Marketplace"
    if lower.startswith("cliniassess"):
        return "CliniAssess — Pre-Visit Assessment Platform"

    cleaned = re.sub(r",\s*used (?:by|at)\s+.*$", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s*[-–—]\s*", " — ", cleaned, count=1)
    return cleaned


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
        clean_bullet = bullet.lstrip("-*•·●▪◦ ").strip()
        if clean_bullet:
            target_story.append(Paragraph(f"&bull; {escape(clean_bullet)}", style_bullet))


def _append_section_heading(
    target_story: list,
    title: str,
    style_heading: ParagraphStyle,
) -> None:
    target_story.append(Paragraph(escape(title), style_heading))


def _append_left_right_line(
    target_story: list,
    left_text: str,
    right_text: str,
    left_style: ParagraphStyle,
    right_style: ParagraphStyle,
    width: float,
    col_ratio: float = 0.72,
) -> None:
    clean_left = _safe_text(left_text)
    clean_right = _safe_text(right_text)
    if not clean_left and not clean_right:
        return
    if not clean_right:
        target_story.append(Paragraph(escape(clean_left), left_style))
        return
    if not clean_left:
        target_story.append(Paragraph(escape(clean_right), right_style))
        return

    table = Table(
        [
            [
                Paragraph(escape(clean_left), left_style),
                Paragraph(escape(clean_right), right_style),
            ]
        ],
        colWidths=[width * col_ratio, width * (1.0 - col_ratio)],
        hAlign="LEFT",
    )
    table.setStyle(
        TableStyle(
            [
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                ("TOPPADDING", (0, 0), (-1, -1), 0),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ]
        )
    )
    target_story.append(table)


def _append_skill_lines(
    target_story: list,
    skills: object,
    style_skill_line: ParagraphStyle,
) -> None:
    if isinstance(skills, dict):
        skill_items = [
            f"{_safe_text(str(label))}: {_safe_text(str(values))}"
            for label, values in skills.items()
            if _safe_text(str(label)) and _safe_text(str(values))
        ]
    else:
        skill_items = list(skills or [])

    cleaned_skills = [_safe_text(skill) for skill in skill_items if _safe_text(skill)]
    for skill in cleaned_skills:
        if ":" in skill:
            label, values = skill.split(":", 1)
            target_story.append(
                Paragraph(f"<b>{escape(label.strip())}:</b> {escape(values.strip())}", style_skill_line)
            )
        else:
            target_story.append(Paragraph(escape(skill), style_skill_line))


def create_resume_pdf(
    resume_data: ResumeLike,
    header_title: str | None = None,
    top_margin: float | None = None,
    bottom_margin: float | None = None,
    side_margin: float | None = None,
    font_size: float | None = None,
    section_order: list[str] | None = None,
) -> bytes:
    """
    Generate a strict ATS-first, single-column, text-based PDF resume.
    Supports customizable margins, base font sizes, and flexible section ordering.
    """
    buffer = io.BytesIO()

    # Margins in inches (default 0.45 top/bottom, 0.55 sides)
    tm = float(top_margin) if top_margin is not None else 0.45
    bm = float(bottom_margin) if bottom_margin is not None else 0.45
    sm = float(side_margin) if side_margin is not None else 0.55

    doc = SimpleDocTemplate(
        buffer,
        pagesize=letter,
        leftMargin=sm * inch,
        rightMargin=sm * inch,
        topMargin=tm * inch,
        bottomMargin=bm * inch,
    )

    styles = getSampleStyleSheet()
    regular_font, bold_font = _register_arial_fonts()

    base_size = float(font_size) if font_size is not None else 12.0
    base_leading = base_size + 2.0

    style_name = ParagraphStyle(
        name="Name",
        parent=styles["Heading1"],
        fontName=bold_font,
        fontSize=base_size + 6.0,
        leading=base_size + 9.0,
        alignment=TA_LEFT,
        spaceAfter=4,
    )
    style_header_title = ParagraphStyle(
        name="HeaderTitle",
        parent=styles["Normal"],
        fontName=bold_font,
        fontSize=base_size + 1.0,
        leading=base_size + 3.0,
        alignment=TA_LEFT,
        spaceAfter=4,
    )
    style_contact = ParagraphStyle(
        name="Contact",
        parent=styles["Normal"],
        fontName=regular_font,
        fontSize=base_size,
        leading=base_leading,
        alignment=TA_LEFT,
        spaceAfter=2,
    )
    style_section_heading = ParagraphStyle(
        name="SectionHeading",
        parent=styles["Heading2"],
        fontName=bold_font,
        fontSize=base_size + 1.0,
        leading=base_size + 3.0,
        alignment=TA_LEFT,
        spaceBefore=6,
        spaceAfter=2,
    )
    style_body = ParagraphStyle(
        name="Body",
        parent=styles["Normal"],
        fontName=regular_font,
        fontSize=base_size,
        leading=base_leading,
        alignment=TA_LEFT,
        spaceAfter=2,
    )
    style_exp_company = ParagraphStyle(
        name="ExpCompany",
        parent=styles["Normal"],
        fontName=bold_font,
        fontSize=base_size + 0.5,
        leading=base_size + 2.5,
        alignment=TA_LEFT,
    )
    style_exp_company_date = ParagraphStyle(
        name="ExpCompanyDate",
        parent=styles["Normal"],
        fontName=bold_font,
        fontSize=base_size,
        leading=base_size + 2.5,
        alignment=TA_RIGHT,
    )
    style_exp_role_loc = ParagraphStyle(
        name="ExpRoleLoc",
        parent=styles["Normal"],
        fontName=regular_font,
        fontSize=base_size,
        leading=base_size + 2.0,
        alignment=TA_LEFT,
        textColor="#334155",
        spaceBefore=1,
        spaceAfter=3,
    )
    style_exp_project = ParagraphStyle(
        name="ExpProject",
        parent=styles["Normal"],
        fontName=bold_font,
        fontSize=base_size - 0.5,
        leading=base_size + 1.5,
        alignment=TA_LEFT,
    )
    style_exp_project_date = ParagraphStyle(
        name="ExpProjectDate",
        parent=styles["Normal"],
        fontName=regular_font,
        fontSize=base_size - 0.5,
        leading=base_size + 1.5,
        alignment=TA_RIGHT,
        textColor="#475569",
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
    style_meta_right = ParagraphStyle(
        name="MetaRight",
        parent=style_meta,
        alignment=TA_RIGHT,
    )
    style_bullet = ParagraphStyle(
        name="Bullet",
        parent=style_body,
        leftIndent=14,
        firstLineIndent=-10,
        spaceAfter=2,
    )

    story: list = []
    content_width = doc.width
    is_v2_resume = is_resume_v2_model(resume_data)

    if _safe_text(resume_data.name):
        story.append(Paragraph(escape(_safe_text(resume_data.name).upper()), style_name))
    display_title = _safe_text(header_title) or _safe_text(getattr(resume_data, "title", ""))
    if display_title:
        story.append(Paragraph(escape(display_title), style_header_title))

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

    # --- Section Renderers ---
    def render_summary():
        summary_text = _safe_text(resume_data.summary)
        if summary_text:
            _append_section_heading(
                story,
                "SUMMARY" if is_v2_resume else "PROFESSIONAL SUMMARY",
                style_section_heading,
            )
            story.append(Paragraph(escape(summary_text), style_body))

    def render_skills():
        if isinstance(resume_data.skills, dict):
            cleaned_skills = [
                f"{_safe_text(str(label))}: {_safe_text(str(values))}"
                for label, values in resume_data.skills.items()
                if _safe_text(str(label)) and _safe_text(str(values))
            ]
        else:
            cleaned_skills = [_safe_text(skill) for skill in resume_data.skills if _safe_text(skill)]
        if cleaned_skills:
            _append_section_heading(story, "SKILLS" if is_v2_resume else "TECHNICAL SKILLS", style_section_heading)
            _append_skill_lines(story, resume_data.skills, style_body)

    def render_experience():
        if not resume_data.experience:
            return
        _append_section_heading(
            story,
            "EXPERIENCE" if is_v2_resume else "PROFESSIONAL EXPERIENCE",
            style_section_heading,
        )
        for exp in resume_data.experience:
            comp_name = _safe_text(exp.company)
            comp_dates = _format_date_range(exp.start_date, exp.end_date)
            role_title = _safe_text(getattr(exp, "title", None) or getattr(exp, "job_title", None))
            loc = _safe_text(exp.location)

            # 1. Company Name + Employment Dates
            if comp_name or comp_dates:
                _append_left_right_line(
                    story,
                    comp_name,
                    comp_dates,
                    style_exp_company,
                    style_exp_company_date,
                    content_width,
                    col_ratio=0.72,
                )

            # 2. Job Title + Location directly underneath
            role_loc_parts = [role_title, loc]
            role_loc_parts = [p for p in role_loc_parts if p]
            if role_loc_parts:
                role_loc_str = " · ".join(role_loc_parts)
                story.append(Paragraph(escape(role_loc_str), style_exp_role_loc))

            # 3. Project blocks & achievements
            if is_v2_resume and getattr(exp, "project_blocks", None):
                for block in exp.project_blocks:
                    proj_name = _clean_project_title(block.project)
                    proj_dates = _format_date_range(block.period)

                    # Avoid duplicate identical dates
                    show_proj_dates = bool(
                        proj_dates and proj_dates.strip() != comp_dates.strip()
                    )

                    story.append(Spacer(1, 0.02 * inch))
                    if proj_name or (proj_dates and show_proj_dates):
                        if show_proj_dates:
                            _append_left_right_line(
                                story,
                                proj_name,
                                proj_dates,
                                style_exp_project,
                                style_exp_project_date,
                                content_width,
                                col_ratio=0.72,
                            )
                        else:
                            story.append(Paragraph(escape(proj_name), style_exp_project))

                    for bullet in (block.bullets or []):
                        _append_bullet_lines(story, str(bullet), style_bullet)
                story.append(Spacer(1, 0.05 * inch))
            else:
                if getattr(exp, "description", None):
                    _append_bullet_lines(story, exp.description, style_bullet)
                story.append(Spacer(1, 0.05 * inch))

    def render_projects():
        if not is_v2_resume and getattr(resume_data, "projects", None):
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

    def render_education():
        if not resume_data.education:
            return
        _append_section_heading(story, "EDUCATION", style_section_heading)
        for edu in resume_data.education:
            if is_v2_resume:
                if _safe_text(edu.degree):
                    story.append(Paragraph(escape(_safe_text(edu.degree)), style_role))
                _append_left_right_line(
                    story,
                    _safe_text(edu.institution),
                    _format_date_range(edu.period),
                    style_meta,
                    style_meta_right,
                    content_width,
                    col_ratio=0.72,
                )
                story.append(Spacer(1, 0.04 * inch))
                continue

            degree_parts = [_safe_text(edu.degree)]
            if _safe_text(edu.field_of_study):
                degree_parts.append(_safe_text(edu.field_of_study))
            degree_line = ", ".join([part for part in degree_parts if part])
            if degree_line:
                story.append(Paragraph(escape(degree_line), style_role))

            years = ""
            if _safe_text(edu.start_year) and _safe_text(edu.end_year):
                years = f"{_safe_text(edu.start_year)} - {_safe_text(edu.end_year)}"
            elif _safe_text(edu.end_year):
                years = _safe_text(edu.end_year)
            elif _safe_text(edu.start_year):
                years = _safe_text(edu.start_year)
            _append_left_right_line(
                story,
                _safe_text(edu.institution),
                years,
                style_meta,
                style_meta_right,
                content_width,
            )
            story.append(Spacer(1, 0.04 * inch))

    def render_certifications():
        if getattr(resume_data, "certifications", None):
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

    # --- Strict Canonical Section Order ---
    # 1. Summary
    # 2. Skills
    # 3. Experience
    # 4. Education
    render_summary()
    render_skills()
    render_experience()
    if not is_v2_resume and getattr(resume_data, "projects", None):
        render_projects()
    render_education()

    try:
        doc.build(story)
        logging.info("PDF generated successfully.")
    except Exception as exc:
        logging.error(f"Error building PDF: {exc}")
        raise

    pdf_bytes = buffer.getvalue()
    buffer.close()
    return pdf_bytes
