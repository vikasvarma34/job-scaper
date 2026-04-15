import io
import logging
from reportlab.lib.pagesizes import letter
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, HRFlowable, Table, TableStyle
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT, TA_JUSTIFY
from reportlab.lib.units import inch
from reportlab.lib import colors
from models import Resume 

logging.basicConfig(level=logging.INFO)


def _append_bullet_lines(target_story: list, text: str, style_bullet: ParagraphStyle):
    """
    Render newline-separated text as bullet paragraphs.
    Falls back to sentence splitting when a plain paragraph is provided.
    """
    if not text or text == "NA":
        return

    if "\n" in text:
        bullets = text.split("\n")
        for bullet in bullets:
            if bullet.strip():
                bullet_text = bullet.strip()
                if not bullet_text.startswith("-") and not bullet_text.startswith("•"):
                    bullet_text = f"• {bullet_text}"
                elif bullet_text.startswith("-"):
                    bullet_text = f"• {bullet_text[1:].strip()}"
                target_story.append(Paragraph(bullet_text, style_bullet))
        return

    normalized = text.strip()
    normalized = normalized.replace("e.g.", "TEMP_EG")
    normalized = normalized.replace("i.e.", "TEMP_IE")
    normalized = normalized.replace("etc.", "TEMP_ETC")
    normalized = normalized.replace("vs.", "TEMP_VS")
    normalized = normalized.replace("Mr.", "TEMP_MR")
    normalized = normalized.replace("Mrs.", "TEMP_MRS")
    normalized = normalized.replace("Ms.", "TEMP_MS")
    normalized = normalized.replace("Dr.", "TEMP_DR")
    normalized = normalized.replace("St.", "TEMP_ST")
    normalized = normalized.replace("Ph.D.", "TEMP_PHD")
    normalized = normalized.replace("U.S.", "TEMP_US")
    normalized = normalized.replace("U.K.", "TEMP_UK")

    sentences = normalized.split(". ")
    for i, sentence in enumerate(sentences):
        if sentence:
            sentence = sentence.replace("TEMP_EG", "e.g.")
            sentence = sentence.replace("TEMP_IE", "i.e.")
            sentence = sentence.replace("TEMP_ETC", "etc.")
            sentence = sentence.replace("TEMP_VS", "vs.")
            sentence = sentence.replace("TEMP_MR", "Mr.")
            sentence = sentence.replace("TEMP_MRS", "Mrs.")
            sentence = sentence.replace("TEMP_MS", "Ms.")
            sentence = sentence.replace("TEMP_DR", "Dr.")
            sentence = sentence.replace("TEMP_ST", "St.")
            sentence = sentence.replace("TEMP_PHD", "Ph.D.")
            sentence = sentence.replace("TEMP_US", "U.S.")
            sentence = sentence.replace("TEMP_UK", "U.K.")

            if i < len(sentences) - 1 or sentence[-1] not in [".", "!", "?"]:
                sentence = sentence + "."

            target_story.append(Paragraph(f"• {sentence.strip()}", style_bullet))

def create_resume_pdf(resume_data: Resume, header_title: str | None = None) -> bytes:
    """
    Generates an ATS-friendly PDF resume with improved design from the provided Resume data object.
    Returns the PDF content as bytes.
    """
    buffer = io.BytesIO()
    
    # Document setup with slightly wider margins for better readability
    doc = SimpleDocTemplate(
        buffer, 
        pagesize=letter,
        leftMargin=0.6*inch, 
        rightMargin=0.6*inch,
        topMargin=0.6*inch, 
        bottomMargin=0.6*inch
    )
    
    # Create custom styles
    styles = getSampleStyleSheet()
    
    # Monochrome palette (black-first)
    primary_color = colors.HexColor('#000000')
    secondary_color = colors.HexColor('#000000')
    accent_color = colors.HexColor('#000000')
    text_color = colors.HexColor('#000000')
    light_text = colors.HexColor('#000000')
    background_color = colors.HexColor('#FFFFFF')
    
    # Create custom styles using ReportLab's built-in fonts
    style_name = ParagraphStyle(
        name='Name',
        parent=styles['Heading1'],
        fontSize=26,
        alignment=TA_LEFT,
        spaceAfter=10,
        fontName='Helvetica-Bold',
        textColor=primary_color,
    )
    
    style_section_heading = ParagraphStyle(
        name='SectionHeading',
        parent=styles['Heading2'],
        fontSize=12,
        spaceBefore=12,
        spaceAfter=4,
        fontName='Helvetica-Bold',
        textColor=primary_color,
        alignment=TA_LEFT,
    )
    
    style_normal = ParagraphStyle(
        name='Normal',
        parent=styles['Normal'],
        fontSize=10,
        leading=14,  
        fontName='Helvetica',
        textColor=text_color,
    )
    
    style_contact = ParagraphStyle(
        name='Contact',
        parent=styles['Normal'],
        alignment=TA_LEFT,
        fontSize=9,
        leading=12,
        spaceAfter=2,
        textColor=secondary_color,
    )

    style_header_title = ParagraphStyle(
        name='HeaderTitle',
        parent=styles['Normal'],
        alignment=TA_LEFT,
        fontSize=12,
        leading=14,
        spaceAfter=6,
        fontName='Helvetica-Bold',
        textColor=primary_color,
    )
    
    style_job_title = ParagraphStyle(
        name='JobTitle',
        parent=styles['Normal'],
        fontSize=12,  
        spaceAfter=4,
        fontName='Helvetica-Bold',
        textColor=primary_color,  
    )
    
    style_company = ParagraphStyle(
        name='Company',
        parent=styles['Normal'],
        spaceBefore=2,
        fontSize=10,
        fontName='Helvetica-Bold',  
        textColor=secondary_color,
    )
    
    style_dates = ParagraphStyle(
        name='Dates',
        parent=styles['Normal'],
        fontSize=9,
        alignment=TA_RIGHT,
        fontName='Helvetica-Oblique',
        textColor=light_text,
    )
    
    style_bullet = ParagraphStyle(
        name='Bullet',
        parent=styles['Normal'],
        fontSize=10,
        leading=14,
        leftIndent=15,
        bulletIndent=0,
        fontName='Helvetica',
        textColor=text_color,
        spaceAfter=4,
    )

    style_tech = ParagraphStyle(
        name='Technologies',
        parent=styles['Normal'],
        fontSize=9,
        fontName='Helvetica-Oblique',
        textColor=light_text,
        spaceAfter=8,
    )
    
    story =[]
    projects_section = []
    education_section = []

    # --- Header ---
    if resume_data.name:
        story.append(Paragraph(resume_data.name.upper(), style_name))
    if header_title:
        story.append(Paragraph(header_title, style_header_title))

    
    # --- Contact Information ---
    contact_info =[]
    if resume_data.email and resume_data.email != "NA": contact_info.append(resume_data.email)
    if resume_data.phone and resume_data.phone != "NA": contact_info.append(resume_data.phone)
    if resume_data.location and resume_data.location != "NA": contact_info.append(resume_data.location)
    if contact_info:
        story.append(Paragraph(" | ".join(contact_info), style_contact))
    
    # --- Links ---
    links =[]
    if resume_data.links:
        # Helper function to format links
        def format_link(url, label):
            # Ensure URL has protocol to be clickable in PDF
            clean_url = url if url.startswith('http') else f"https://{url}"
            # Escape ampersands for ReportLab's XML parser
            clean_url = clean_url.replace('&', '&amp;')
            # Return formatted HTML-like string with primary color and underline
            return f'<u><a href="{clean_url}"><font color="#000000">{label}</font></a></u>'

        if resume_data.links.linkedin and resume_data.links.linkedin != "NA": 
            links.append(format_link(resume_data.links.linkedin, "LinkedIn"))
        if resume_data.links.github and resume_data.links.github != "NA": 
            links.append(format_link(resume_data.links.github, "GitHub"))
        if resume_data.links.portfolio and resume_data.links.portfolio != "NA": 
            links.append(format_link(resume_data.links.portfolio, "Portfolio"))
            
    if links:
        story.append(Paragraph(" | ".join(links), style_contact))
    
    # --- Summary ---
    if resume_data.summary and resume_data.summary != "NA":
        story.append(Paragraph("PROFESSIONAL SUMMARY", style_section_heading))
        story.append(HRFlowable(width="100%", thickness=1, color=colors.HexColor('#000000'), spaceBefore=0, spaceAfter=8))
        
        # Remove leading/trailing double quotes from summary if they exist
        cleaned_summary = resume_data.summary
        if cleaned_summary.startswith('"') and cleaned_summary.endswith('"'):
            cleaned_summary = cleaned_summary[1:-1]

        cleaned_summary = " ".join(
            part.strip() for part in cleaned_summary.splitlines() if part.strip()
        )
        story.append(Paragraph(cleaned_summary, style_normal))
    
    # --- Skills ---
    if resume_data.skills and resume_data.skills != ["NA"]:
        # Filter out any "NA" skills just in case
        skills_list = [s for s in resume_data.skills if s != "NA"]
        
        if skills_list:
            story.append(Paragraph("PROFESSIONAL SKILLS", style_section_heading))
            story.append(HRFlowable(width="100%", thickness=1, color=colors.HexColor('#000000'), spaceBefore=0, spaceAfter=8))

            grouped_skills_mode = any(":" in skill for skill in skills_list)
            if grouped_skills_mode:
                for skill in skills_list:
                    story.append(Paragraph(f"• {skill}", style_bullet))
                story.append(Spacer(1, 0.1*inch))
            else:
                num_columns = 4 if len(skills_list) >= 24 else 3

                table_data =[]
                num_skills = len(skills_list)
                rows = (num_skills + num_columns - 1) // num_columns

                for i in range(rows):
                    row_items =[]
                    for j in range(num_columns):
                        skill_index = i * num_columns + j
                        if skill_index < num_skills:
                            skill_text = f"• {skills_list[skill_index]}"
                            row_items.append(Paragraph(skill_text, style_normal))
                        else:
                            row_items.append(Paragraph("", style_normal))
                    table_data.append(row_items)

                if table_data:
                    page_width_available = letter[0] - doc.leftMargin - doc.rightMargin
                    col_width = page_width_available / num_columns
                    colWidths = [col_width] * num_columns

                    skills_table = Table(table_data, colWidths=colWidths)
                    skills_table.setStyle(TableStyle([
                        ('VALIGN', (0,0), (-1,-1), 'TOP'),
                        ('LEFTPADDING', (0,0), (0,-1), 10),
                        ('RIGHTPADDING', (0,0), (-1,-1), 6),
                        ('BOTTOMPADDING', (0,0), (-1,-1), 3),
                    ]))
                    story.append(skills_table)
                    story.append(Spacer(1, 0.1*inch))
    
    # --- Experience ---
    if resume_data.experience:
        story.append(Paragraph("PROFESSIONAL EXPERIENCE", style_section_heading))
        story.append(HRFlowable(width="100%", thickness=1, color=colors.HexColor('#000000'), spaceBefore=0, spaceAfter=8))
        
        for exp in resume_data.experience:
            # Create a table for job header to align job title and dates
            job_title = f"{exp.job_title}" if exp.job_title != "NA" else ""
            
            company_parts =[]
            if exp.company and exp.company != "NA": company_parts.append(exp.company)
            if exp.location and exp.location != "NA": company_parts.append(exp.location)
            company_location = " | ".join(company_parts)
            
            dates = ""
            if exp.start_date and exp.start_date != "NA" and exp.end_date and exp.end_date != "NA": 
                dates = f"{exp.start_date} - {exp.end_date}"
            elif exp.start_date and exp.start_date != "NA": 
                dates = f"{exp.start_date} - Present"
            
            # Create two-column layout for position details
            data = [[Paragraph(job_title, style_job_title), Paragraph(dates, style_dates)]]
            tbl = Table(data, colWidths=[4.636*inch, 2.5*inch])
            tbl.setStyle(TableStyle([
                ('VALIGN', (0, 0), (-1, -1), 'TOP'),
                ('BOTTOMPADDING', (0, 0), (-1, -1), 2),  # Reduce padding for tighter layout
                ('LEFTPADDING', (0, 0), (0, -1), 0),  # No left padding for the first column
            ]))
            story.append(tbl)
            
            story.append(Paragraph(company_location, style_company))
            story.append(Spacer(1, 0.1*inch))
            
            if exp.description and exp.description != "NA":
                _append_bullet_lines(story, exp.description, style_bullet)
            
            story.append(Spacer(1, 0.15*inch))
    
    # --- Education ---
    if resume_data.education:
        education_section.append(Paragraph("EDUCATION", style_section_heading))
        education_section.append(HRFlowable(width="100%", thickness=1, color=colors.HexColor('#000000'), spaceBefore=0, spaceAfter=8))
        
        for edu in resume_data.education:
            # Degree info
            degree_info = f"<b>{edu.degree}</b>" if edu.degree != "NA" else ""
            if edu.field_of_study and edu.field_of_study != "NA": 
                degree_info += f", {edu.field_of_study}"
            
            # Year info
            years = ""
            if edu.start_year and edu.start_year != "NA" and edu.end_year and edu.end_year != "NA": 
                years = f"{edu.start_year} - {edu.end_year}"
            elif edu.start_year and edu.start_year != "NA": 
                years = f"Started {edu.start_year}"
            elif edu.end_year and edu.end_year != "NA": 
                years = f"Graduated {edu.end_year}"
            
            # Create two-column layout
            data = [[Paragraph(degree_info, style_normal), Paragraph(years, style_dates)]]
            tbl = Table(data, colWidths=[5.15*inch, 2*inch])
            tbl.setStyle(TableStyle([
                ('VALIGN', (0, 0), (-1, -1), 'TOP'),
                ('LEFTPADDING', (0, 0), (0, -1), 0),
            ]))
            education_section.append(tbl)
            
            if edu.institution and edu.institution != "NA":
                education_section.append(Paragraph(edu.institution, style_normal))
            education_section.append(Spacer(1, 0.15*inch))
    
    # --- Projects ---
    if resume_data.projects:
        projects_section.append(Paragraph("PROJECTS", style_section_heading))
        projects_section.append(HRFlowable(width="100%", thickness=1, color=colors.HexColor('#000000'), spaceBefore=0, spaceAfter=8))
        
        for proj in resume_data.projects:
            if proj.name and proj.name != "NA":
                projects_section.append(Paragraph(f"<b>{proj.name}</b>", style_job_title))
            
            if proj.description and proj.description != "NA":
                _append_bullet_lines(projects_section, proj.description, style_bullet)
            
            if proj.technologies and proj.technologies != ["NA"]:
                tech_list =[t for t in proj.technologies if t != "NA"]
                if tech_list:
                    tech_text = f"<i>Technologies:</i> {', '.join(tech_list)}"
                    projects_section.append(Paragraph(tech_text, style_tech))
            
            projects_section.append(Spacer(1, 0.15*inch))
    
    # --- Certifications ---
    if resume_data.certifications:
        story.append(Paragraph("CERTIFICATIONS", style_section_heading))
        story.append(HRFlowable(width="100%", thickness=1, color=colors.HexColor('#000000'), spaceBefore=0, spaceAfter=8))
        
        for cert in resume_data.certifications:
            if cert.name == "NA" and cert.issuer == "NA":
                continue

            cert_name = f"<b>{cert.name}</b>" if cert.name != "NA" else ""
            
            # Right aligned year if available
            year_text = ""
            if cert.year and cert.year != "NA":
                year_text = cert.year
            
            # Create a table for certification info
            data = [[Paragraph(cert_name, style_normal), Paragraph(year_text, style_dates)]]
            tbl = Table(data, colWidths=[5.3*inch, 2*inch])
            tbl.setStyle(TableStyle([
                ('VALIGN', (0, 0), (-1, -1), 'TOP'),
            ]))
            story.append(tbl)
            
            if cert.issuer and cert.issuer != "NA":
                story.append(Paragraph(cert.issuer, style_normal))
            
            story.append(Spacer(1, 0.1*inch))
    
    # --- Languages ---
    if resume_data.languages and resume_data.languages != ["NA"]:
        lang_list =[l for l in resume_data.languages if l != "NA"]
        if lang_list:
            story.append(Paragraph("LANGUAGES", style_section_heading))
            story.append(HRFlowable(width="100%", thickness=1, color=colors.HexColor('#000000'), spaceBefore=0, spaceAfter=8))
            story.append(Paragraph(", ".join(lang_list), style_normal))

    # Tail order required by user: projects second-last, education last.
    story.extend(projects_section)
    story.extend(education_section)
    
    try:
        doc.build(story)
        logging.info("PDF generated successfully.")
    except Exception as e:
        logging.error(f"Error building PDF: {e}")
        raise  # Re-raise the exception
    
    pdf_bytes = buffer.getvalue()
    buffer.close()
    return pdf_bytes
