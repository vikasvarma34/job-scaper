import argparse
import json
import logging
import os
import re
import time
from typing import Any, Dict

import config
import supabase_utils
from cover_letter_pdf import create_cover_letter_pdf
from llm_client import primary_client
from models import CoverLetterOutput, ResumeLike, is_resume_v2_model, parse_resume_data


logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")


def _sanitize_filename_token(value: Any, default: str = "UNKNOWN") -> str:
    text = str(value or "").strip()
    if not text:
        return default
    text = re.sub(r"[^A-Za-z0-9]+", "_", text)
    text = re.sub(r"_+", "_", text).strip("_")
    return (text or default).upper()


def _build_cover_letter_filename(job_id: str, company: Any) -> str:
    company_token = _sanitize_filename_token(company, default="COMPANY")
    job_token = _sanitize_filename_token(job_id, default="JOB")
    return f"cover_letters/VIKAS_POKALA_{company_token}_{job_token}_COVER_LETTER.pdf"


def _serialize_job_for_prompt(job_details: Dict[str, Any]) -> str:
    payload = {
        "job_title": job_details.get("job_title", ""),
        "company": job_details.get("company", ""),
        "level": job_details.get("level", ""),
        "location": job_details.get("location", ""),
        "description": job_details.get("description", ""),
    }
    return json.dumps(payload, indent=2)


def _serialize_resume_for_cover_letter(resume: ResumeLike) -> str:
    lines: list[str] = [
        f"Name: {resume.name}",
        f"Location: {resume.location}",
        f"Email: {resume.email}",
        f"Phone: {resume.phone}",
        "",
        "Professional Summary:",
        resume.summary.strip(),
        "",
        "Technical Skills:",
    ]

    if isinstance(resume.skills, dict):
        lines.extend(
            [
                f"{str(category).strip()}: {str(values).strip()}"
                for category, values in resume.skills.items()
                if str(category).strip() and str(values).strip()
            ]
        )
    else:
        lines.extend([skill.strip() for skill in resume.skills if str(skill).strip()])
    lines.append("")
    lines.append("Experience:")
    for exp in resume.experience:
        if is_resume_v2_model(resume):
            lines.extend(
                [
                    f"{exp.title} | {exp.company} | {exp.location}",
                    f"{exp.start_date} - {exp.end_date}",
                ]
            )
            for block in exp.project_blocks:
                block_label = " | ".join(
                    part
                    for part in [str(block.project or "").strip(), str(block.period or "").strip()]
                    if part
                )
                if block_label:
                    lines.append(block_label)
                lines.extend([f"- {str(bullet).strip()}" for bullet in block.bullets if str(bullet).strip()])
            lines.append("")
            continue

        lines.extend(
            [
                f"{exp.job_title} | {exp.company} | {exp.location}",
                f"{exp.start_date} - {exp.end_date}",
            ]
        )
        lines.extend([f"- {line.strip()}" for line in exp.description.splitlines() if line.strip()])
        lines.append("")

    if not is_resume_v2_model(resume) and resume.projects:
        lines.append("Projects:")
        for project in resume.projects:
            lines.append(project.name)
            lines.extend([f"- {line.strip()}" for line in project.description.splitlines() if line.strip()])
            if project.technologies:
                lines.append(f"Technologies: {', '.join(project.technologies)}")
            lines.append("")

    if resume.education:
        lines.append("Education:")
        for edu in resume.education:
            if is_resume_v2_model(resume):
                lines.extend(
                    [
                        str(edu.degree or "").strip(),
                        " | ".join(
                            part
                            for part in [str(edu.institution or "").strip(), str(edu.period or "").strip()]
                            if part
                        ),
                    ]
                )
            else:
                period = " - ".join(
                    part
                    for part in [str(edu.start_year or "").strip(), str(edu.end_year or "").strip()]
                    if part
                )
                lines.extend(
                    [
                        str(edu.degree or "").strip(),
                        " | ".join(
                            part
                            for part in [str(edu.institution or "").strip(), period]
                            if part
                        ),
                    ]
                )
            lines.append("")

    return "\n".join(line for line in lines if line is not None).strip()


def _load_default_resume_email() -> str:
    resume_path = getattr(config, "BASE_RESUME_PATH", "resume.json")
    if not os.path.exists(resume_path):
        return ""
    try:
        with open(resume_path, "r", encoding="utf-8") as f:
            raw_resume = json.load(f)
        return str(raw_resume.get("email") or "").strip()
    except Exception:
        return ""


def _load_default_resume_location() -> str:
    resume_path = getattr(config, "BASE_RESUME_PATH", "resume.json")
    if not os.path.exists(resume_path):
        return ""
    try:
        with open(resume_path, "r", encoding="utf-8") as f:
            raw_resume = json.load(f)
        return str(raw_resume.get("location") or "").strip()
    except Exception:
        return ""


def _resolve_applicant_location(customized_resume: ResumeLike) -> str:
    default_location = _load_default_resume_location()
    if default_location:
        return default_location
    return str(customized_resume.location or "").strip()


def _resolve_contact_email(
    job_details: Dict[str, Any],
    customized_resume: ResumeLike,
    email_override: str | None = None,
) -> str:
    manual_override = str(email_override or "").strip()
    if manual_override:
        return manual_override
    override = str(job_details.get("contact_email_override") or "").strip()
    if override:
        return override
    default_email = _load_default_resume_email()
    if default_email:
        return default_email
    return str(customized_resume.email or "").strip()


def _build_cover_letter_prompt(job_details: Dict[str, Any], customized_resume: ResumeLike) -> str:
    return f"""
Write a focused cover letter for this job.

Target job:
{_serialize_job_for_prompt(job_details)}

Customized resume (use as evidence, not as a script):
{_serialize_resume_for_cover_letter(customized_resume)}

What this letter must do:

1. Identify the company, role, and the top 2 or 3 job needs from the job description. Use those needs quietly to decide what matters most, but do not explain the company's own job description back to them.

2. Open with a natural, human connection between the role and Vikas's recent work. Do not restate the company's needs. Do not copy the job description. The opening should sound like a developer recognizing familiar work, not like a sales pitch.

3. Paragraph 1: briefly explain why the role fits the kind of work Vikas has already been doing. Keep it warm, practical, and confident. Do not start by saying the company needs or requires something.
   Preferred opening style: "Appbay's Backend Programmer role feels close to the kind of work I have been doing over the last two years. Most of my recent work has been around building APIs, improving slow backend flows, and making database-backed features easier to maintain."
   Another acceptable style: "This role caught my attention because the work lines up with what I have been doing in production: building APIs, working through backend performance issues, and supporting database-backed products used by real users."

4. Paragraph 2: use one strong proof point from the resume. Pick the best match for the job: CliniScripts performance work, CliniAssess backend/data model/rule engine, TELUS Java/Spring Boot microservices and GraphQL, Kafka email notifications, WebSockets/Recall.ai, API/security/rate limiting, or another clearly relevant item. Do not list everything.

5. Paragraph 3: connect that experience back to the company and say how Vikas can contribute. Keep it practical, not salesy.

6. Do not mention location, relocation, Toronto, Canada, willingness to move, or availability. The job location field alone is not permission to add a location sentence. Only if the job description explicitly asks the applicant to state current location, the acceptable sentence is: "I am currently based in Hyderabad."

Banned moves:
- Never open with the pattern "Company needs X. Candidate has done X."
- Do not open with "Company needs X", "Company is looking for X", "This role requires X", "Your job description requires X", "Appbay Technologies needs", or "The role needs someone who".
- Do not use the first paragraph to list technologies. Mention at most one or two broad work areas, such as APIs, backend performance, database-backed systems, frontend integration, or production systems.
- Do not invent facts, metrics, technologies, companies, dates, lessons, or hidden project context.
- Do not repeat resume bullets line by line.
- Do not dump skills. No sentence should list more than two technologies unless the sentence truly needs them.
- Do not force every resume technology into the letter.
- Do not over-polish the language. Keep it simple, direct, and professional.
- Do not use fake excitement, desperate phrasing, or generic lines such as "I would be glad to bring my experience", "I hope to hear from you", "passionate", "proven track record", "results-driven", "dynamic", "fast-paced environment", "cutting-edge", "leveraging", "robust", "seamless", "optimize your backend services", "I am confident that my background", "I would be thrilled", "I believe I am a great fit", or "uniquely positioned".
- Do not import job-posting adjectives into Vikas's work. If the resume says "Java/Spring Boot microservices", do not call them "scalable enterprise-grade microservices" unless the resume itself supports that exact wording.
- Avoid starting too many sentences with "I".
- Avoid mentioning Toronto even as a resume location reference unless the company or job description specifically requires that institution or city.

Fact accuracy rules:
- Do not say "250 active users". If mentioning CliniScripts scale, say "250+ doctors".
- Do not say "data generation time". Say "note-generation time" or "AI note-generation time".
- Do not claim CliniScripts used Node.js unless the resume source clearly says that.
- It is okay to mention Node.js and MongoDB for CliniAssess.
- Do not mention Toronto as Vikas's current location.
- Do not mention relocation unless the job specifically requires it.
- Vikas is currently in Hyderabad.

Format:
- Greeting on its own line. Use the company name from the job, such as "Dear Acme Hiring Team". If the company name is missing, use "Dear Hiring Team". Never use "To whom it may concern" or "Dear Sir/Madam".
- Exactly 3 main body paragraphs after the greeting.
- Keep each paragraph short and readable.
- No bullet points. No date.
- Around one page maximum.
- End with:

Sincerely,

{customized_resume.name}

Length: usually 250-350 words. Aim for 260-320 words. Do not compress the letter under 240 words unless the job description is extremely thin.

Self-check before returning:
- Does the opening sound like a developer recognizing familiar work, not like "Company needs X. I have done X"? If not, rewrite it.
- Does the opening copy the job description or tell the company what it needs? If yes, rewrite it.
- Did you pick only the most relevant resume examples? If not, cut the weaker ones.
- Does the letter explain why those examples matter for this role instead of just restating the resume? If not, rewrite.
- Did you mention location, Toronto, Canada, relocation, willingness to move, or availability? If yes, remove that line unless the JD explicitly asks for current location; then say only that Vikas is currently based in Hyderabad.
- Does it have exactly 3 body paragraphs after the greeting? If not, fix the structure.
- Does it sound like a real developer wrote it, not a polished AI template? If not, simplify.

Return the final cover letter text inside the required JSON schema.
""".strip()


def _split_sentences(text: str) -> list[str]:
    return [sentence.strip() for sentence in re.split(r"(?<=[.!?])\s+", text) if sentence.strip()]


def _chunk_sentences(sentences: list[str], target_blocks: int = 3) -> list[str]:
    if not sentences:
        return []
    block_count = min(target_blocks, len(sentences))
    blocks: list[str] = []
    for index in range(block_count):
        start = round(index * len(sentences) / block_count)
        end = round((index + 1) * len(sentences) / block_count)
        block = " ".join(sentences[start:end]).strip()
        if block:
            blocks.append(block)
    return blocks


def _normalize_cover_letter_text(cover_letter_text: str, applicant_name: str) -> str:
    """
    Keep cover letters readable even when an LLM returns cramped text.

    Gemini sometimes returns a valid JSON string but collapses paragraphs or puts
    "Sincerely" and the candidate name on one line. This normalizer protects the
    saved text and the rendered PDF from that formatting drift.
    """
    text = str(cover_letter_text or "").strip()
    if not text:
        return ""

    text = re.sub(r"^```(?:json|text)?\s*|\s*```$", "", text, flags=re.IGNORECASE).strip()
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    if "\\n" in text and "\n" not in text:
        text = text.replace("\\n", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n[ \t]+", "\n", text)

    name = str(applicant_name or "").strip()
    signoff_pattern = re.compile(
        r"(?is)\b(Sincerely|Best regards|Kind regards|Regards|Thank you),?\s*"
        r"([A-Za-z][A-Za-z .'-]{1,80})?\s*$"
    )
    signoff_match = signoff_pattern.search(text)
    body_text = text
    signoff_word = "Sincerely"
    signoff_name = name
    if signoff_match:
        body_text = text[: signoff_match.start()].strip()
        signoff_word = signoff_match.group(1).strip() or "Sincerely"
        detected_name = str(signoff_match.group(2) or "").strip()
        signoff_name = detected_name or name

    raw_blocks = [
        re.sub(r"\s+", " ", block).strip()
        for block in re.split(r"\n\s*\n+", body_text)
        if block.strip()
    ]

    blocks: list[str]
    if len(raw_blocks) >= 2:
        blocks = raw_blocks
    else:
        compact_body = re.sub(r"\s+", " ", body_text).strip()
        greeting_match = re.match(r"(?is)^(Dear\s+[^,.!?:]+[:,])\s*(.*)$", compact_body)
        greeting = ""
        remaining_body = compact_body
        if greeting_match:
            greeting = greeting_match.group(1).strip()
            remaining_body = greeting_match.group(2).strip()

        blocks = [greeting] if greeting else []
        blocks.extend(_chunk_sentences(_split_sentences(remaining_body), target_blocks=3))

    cleaned_blocks = [block for block in blocks if block]
    greeting = ""
    if cleaned_blocks and re.match(r"(?i)^(Dear|Hello|Hi)\b", cleaned_blocks[0]):
        greeting = cleaned_blocks[0]
        cleaned_blocks = cleaned_blocks[1:]
    if len(cleaned_blocks) > 3:
        cleaned_blocks = cleaned_blocks[:2] + [" ".join(cleaned_blocks[2:]).strip()]
    if greeting:
        cleaned_blocks = [greeting] + cleaned_blocks

    if signoff_name:
        cleaned_blocks.extend([f"{signoff_word},", signoff_name])
    else:
        cleaned_blocks.append(f"{signoff_word},")

    return "\n\n".join(cleaned_blocks).strip()


def _job_explicitly_requests_current_location(job_details: Dict[str, Any]) -> bool:
    description = str(job_details.get("description") or "").lower()
    explicit_markers = (
        "current location",
        "currently located",
        "currently based",
        "must be based",
        "must be located",
        "should be based",
        "candidate must be located",
        "candidates must be located",
        "local candidates only",
    )
    return any(marker in description for marker in explicit_markers)


def _strip_unrequested_location_sentences(cover_letter_text: str) -> str:
    sentence_pattern = re.compile(
        r"(?is)(^|(?<=[.!?])\s+)([^.!?\\n]*(?:currently based|currently located|relocat|willing to move|open to move|ready to join|availability)[^.!?\\n]*[.!?]?)"
    )
    return sentence_pattern.sub(lambda match: match.group(1), str(cover_letter_text or "")).strip()


def _soften_unrequested_toronto_references(cover_letter_text: str) -> str:
    text = str(cover_letter_text or "")
    replacements = {
        "at the University of Toronto": "at a university",
        "the University of Toronto": "a university",
        "University of Toronto": "a university",
        "across Toronto clinics": "across clinics",
        "Toronto clinics": "clinics",
    }
    for old, new in replacements.items():
        text = text.replace(old, new)
    text = re.sub(r"\bToronto\b,?\s*", "", text)
    return re.sub(r"\s{2,}", " ", text)


def generate_cover_letter(job_details: Dict[str, Any], customized_resume: ResumeLike) -> str:
    prompt = _build_cover_letter_prompt(job_details, customized_resume)
    system_prompt = """
You are writing a practical cover letter for a working software engineer applying to a real job in 2026. Recruiters reject letters that sound generic, repeat the resume, stuff skills, or read like a polished template.

The letter's job is to connect the job description to one or two real pieces of the candidate's work, explain why those examples matter for this role, and stop. It should sound like a real developer wrote it: simple, direct, specific, and professional.

Hard rules:
- Use the job description to identify the company, role, and 2 or 3 real requirements, but do not explain the company's own requirements back to them.
- Use the customized resume only as evidence. Do not invent facts beyond it.
- Do not rewrite or summarize the whole resume.
- Use one or two relevant experiences, not a list of projects.
- Explain the practical connection between those examples and the target role.
- Open with a natural, human connection between the role and Vikas's recent work. The opening should sound like a developer recognizing familiar work, not a sales pitch.
- Prefer openings like "This role feels close to the kind of work I have been doing..." or "This role caught my attention because the work lines up with..." over openings that diagnose the company's needs.
- Never open with the pattern "Company needs X. Candidate has done X."
- Do not open with "Company needs X", "Company is looking for X", "This role requires X", "Your job description requires X", "Appbay Technologies needs", or "The role needs someone who".
- Do not use the first paragraph to list technologies. Mention at most one or two broad work areas, such as APIs, backend performance, database-backed systems, frontend integration, or production systems.
- Write exactly 3 main body paragraphs after the greeting, then a short sign-off.
- Keep it around one page maximum, usually 250-350 words. Aim for 260-320 words and do not default to a very short letter.
- Do not make it a formal AI essay or a paragraph version of the resume.
- Do not mention location, relocation, Toronto, Canada, willingness to move, or availability. The job location field alone is not permission to add a location sentence. Only if the job description explicitly asks the applicant to state current location, say: "I am currently based in Hyderabad."
- Avoid fake excitement, generic closers, over-polished AI language, and desperate phrasing.
- Avoid starting too many sentences with "I".
- Do not stuff every technology from the resume into the letter.
- Do not import job-posting adjectives like "scalable", "robust", "fault-tolerant", "cloud-native", "distributed", "enterprise-grade", "high-performance", "mission-critical", "RESTful", or "modern" into the candidate's work unless the resume already supports that exact quality.
- Avoid banned phrases such as "results-driven", "passionate", "dynamic", "fast-paced environment", "cutting-edge", "leveraging", "robust", "seamless", "I am confident that my background", "I would be thrilled", and "I believe I am a great fit".
- Do not say "250 active users"; say "250+ doctors" if that metric appears.
- Do not say "data generation time"; say "note-generation time" or "AI note-generation time".
- Do not claim CliniScripts used Node.js unless the resume source clearly says that.
- It is okay to mention Node.js and MongoDB for CliniAssess.

Return exactly one valid JSON object matching the required schema. The cover letter text goes inside the schema. No markdown, no commentary outside the schema.
""".strip()

    use_direct_gemini = primary_client._is_gemini_model(str(config.LLM_MODEL or ""))  # noqa: SLF001
    logging.info(
        "Cover letter LLM configuration: provider=%s, model=%s, transport=%s",
        str(config.LLM_MODEL or "").split("/", 1)[0] or "unknown",
        str(config.LLM_MODEL or "").strip() or "unknown",
        "direct_gemini" if use_direct_gemini else "litellm",
    )

    try:
        if use_direct_gemini:
            llm_output = primary_client.generate_content_direct_gemini(
                prompt=prompt,
                system_prompt=system_prompt,
                temperature=0.45,
                response_format=CoverLetterOutput,
            )
        else:
            llm_output = primary_client.generate_content(
                prompt=prompt,
                system_prompt=system_prompt,
                temperature=0.45,
                response_format=CoverLetterOutput,
            )
    except Exception as exc:
        if not _is_retryable_cover_letter_llm_error(exc):
            raise
        logging.warning(
            "Cover-letter model failed with a temporary provider error. Retrying once. Error: %s",
            exc,
        )
        time.sleep(2)
        if use_direct_gemini:
            llm_output = primary_client.generate_content_direct_gemini(
                prompt=prompt,
                system_prompt=system_prompt,
                temperature=0.45,
                response_format=CoverLetterOutput,
            )
        else:
            llm_output = primary_client.generate_content(
                prompt=prompt,
                system_prompt=system_prompt,
                temperature=0.45,
                response_format=CoverLetterOutput,
                model_override="gemini",
            )

    parsed_output = CoverLetterOutput.model_validate_json(llm_output)
    cover_letter_text = parsed_output.cover_letter
    if not _job_explicitly_requests_current_location(job_details):
        cover_letter_text = _strip_unrequested_location_sentences(cover_letter_text)
        cover_letter_text = _soften_unrequested_toronto_references(cover_letter_text)
    return _normalize_cover_letter_text(cover_letter_text, customized_resume.name)


def _is_retryable_cover_letter_llm_error(exc: Exception) -> bool:
    error_text = str(exc).lower()
    return any(
        marker in error_text
        for marker in (
            "500",
            "502",
            "503",
            "504",
            "serviceunavailable",
            "service unavailable",
            "currently unavailable",
            "temporarily unavailable",
            "status\": \"unavailable",
            "timeout",
            "timed out",
            "connection error",
        )
    )


def generate_cover_letter_for_job(job_id: str, email_override: str | None = None) -> int:
    cleaned_job_id = str(job_id or "").strip()
    if not cleaned_job_id:
        logging.error("job_id is required.")
        return 1

    job_record = supabase_utils.get_job_by_id(cleaned_job_id)
    if not job_record:
        logging.error(f"Could not find job_id {cleaned_job_id}.")
        return 1

    customized_resume_id = str(job_record.get("customized_resume_id") or "").strip()
    if not customized_resume_id:
        logging.error(f"Job {cleaned_job_id} does not have a customized resume yet.")
        return 1

    customized_resume_record = supabase_utils.get_customized_resume(customized_resume_id)
    if not customized_resume_record:
        logging.error(f"Could not load customized resume {customized_resume_id} for job {cleaned_job_id}.")
        return 1

    try:
        customized_resume = parse_resume_data(customized_resume_record)
    except Exception as exc:
        logging.error(f"Failed to parse customized resume {customized_resume_id}: {exc}")
        return 1
    customized_resume.location = _resolve_applicant_location(customized_resume)

    existing_cover_letter = supabase_utils.get_cover_letter_by_job_id(cleaned_job_id)
    if existing_cover_letter:
        logging.info(f"Existing cover letter found for job_id {cleaned_job_id}. It will be regenerated.")

    logging.info(f"Generating cover letter for job_id: {cleaned_job_id}")
    cover_letter_text = generate_cover_letter(job_record, customized_resume)
    if not cover_letter_text:
        logging.error(f"Cover letter generation returned empty text for job_id {cleaned_job_id}.")
        return 1

    pdf_bytes = create_cover_letter_pdf(
        applicant_name=customized_resume.name,
        email=_resolve_contact_email(job_record, customized_resume, email_override=email_override),
        phone=customized_resume.phone,
        location=customized_resume.location,
        linkedin=customized_resume.links.linkedin if customized_resume.links else "",
        cover_letter_text=cover_letter_text,
    )
    if not pdf_bytes:
        logging.error(f"Failed to render cover letter PDF for job_id {cleaned_job_id}.")
        return 1

    destination_path = _build_cover_letter_filename(cleaned_job_id, job_record.get("company"))
    cover_letter_path = supabase_utils.upload_cover_letter_to_storage(pdf_bytes, destination_path)
    if not cover_letter_path:
        logging.error(f"Failed to upload cover letter PDF for job_id {cleaned_job_id}.")
        return 1

    saved_id = supabase_utils.save_customized_cover_letter(
        job_id=cleaned_job_id,
        customized_resume_id=customized_resume_id,
        company=str(job_record.get("company") or "").strip(),
        job_title=str(job_record.get("job_title") or "").strip(),
        cover_letter_text=cover_letter_text,
        cover_letter_path=cover_letter_path,
        llm_model=str(config.LLM_MODEL or "").strip(),
    )
    if not saved_id:
        logging.error(f"Failed to save cover letter record for job_id {cleaned_job_id}.")
        return 1

    logging.info(f"Successfully generated cover letter for job_id {cleaned_job_id} with record ID: {saved_id}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate a cover letter for a specific job.")
    parser.add_argument("--job-id", required=True, help="Job ID to generate the cover letter for.")
    parser.add_argument(
        "--email-override",
        help="Optional email override for this cover letter generation run.",
    )
    args = parser.parse_args()
    return generate_cover_letter_for_job(args.job_id, email_override=args.email_override)


if __name__ == "__main__":
    raise SystemExit(main())
