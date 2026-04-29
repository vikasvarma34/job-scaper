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
from models import CoverLetterOutput, Resume


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
        "description": job_details.get("description", ""),
    }
    return json.dumps(payload, indent=2)


def _serialize_resume_for_cover_letter(resume: Resume) -> str:
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

    lines.extend([skill.strip() for skill in resume.skills if str(skill).strip()])
    lines.append("")
    lines.append("Experience:")
    for exp in resume.experience:
        lines.extend(
            [
                f"{exp.job_title} | {exp.company} | {exp.location}",
                f"{exp.start_date} - {exp.end_date}",
            ]
        )
        lines.extend([f"- {line.strip()}" for line in exp.description.splitlines() if line.strip()])
        lines.append("")

    if resume.projects:
        lines.append("Projects:")
        for project in resume.projects:
            lines.append(project.name)
            lines.extend([f"- {line.strip()}" for line in project.description.splitlines() if line.strip()])
            if project.technologies:
                lines.append(f"Technologies: {', '.join(project.technologies)}")
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


def _resolve_contact_email(
    job_details: Dict[str, Any],
    customized_resume: Resume,
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


def _build_cover_letter_prompt(job_details: Dict[str, Any], customized_resume: Resume) -> str:
    return f"""
Write a concise, ATS-friendly cover letter for this software engineering job.

Target job:
{_serialize_job_for_prompt(job_details)}

Customized resume:
{_serialize_resume_for_cover_letter(customized_resume)}

Goals:
- Sound human, specific, and professional.
- Use simple, direct English. It should sound like a real application note from Vikas, not an AI-generated letter.
- Do not sound robotic, generic, over-polished, or dramatic.
- Do not restate the resume section by section.
- Do not copy the summary or bullet points verbatim.
- Add fresh framing, motivation, and role fit.
- Use important job-description keywords naturally.
- Keep it to one page, roughly 180 to 280 words.
- Use simple ATS-safe formatting and plain paragraphs.
- Do not invent facts, employers, dates, technologies, metrics, or achievements not supported by the customized resume.

Structure:
- Include a greeting.
- Write 3 to 5 short paragraphs.
- Opening: interest in the role and brief fit.
- Middle: strongest relevant experience and outcomes, with selective evidence from the customized resume.
- Closing: clear interest in moving forward and a professional sign-off.

Mandatory formatting:
- Separate every block with one blank line.
- Do not return the cover letter as one continuous paragraph.
- Keep the greeting on its own line.
- Keep each paragraph as its own block.
- End with exactly this sign-off format, with the name on a separate line:

Sincerely,

{customized_resume.name}

Writing rules:
- Mention the company and job title naturally when possible.
- Focus on why this candidate is a strong fit, not on repeating the full resume.
- Prefer concrete evidence over buzzwords.
- Keep the tone warm, capable, and natural, but not fancy.
- Use common words. Prefer "used", "built", "worked on", "improved", and "helped" over formal words like "leveraged", "spearheaded", "orchestrated", "harnessed", or "utilized".
- Avoid phrases like "I am thrilled", "I am excited to bring", "proven track record", "dynamic", "results-oriented", "robust", "cutting-edge", "seamlessly", "transformative", and "uniquely positioned".
- Avoid generic clichés such as "I am writing to express my interest" unless phrased naturally.
- Keep each paragraph short, around 2 to 4 sentences.
- No bullet points.

Return only the final cover letter text.
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
    if signoff_name:
        cleaned_blocks.extend([f"{signoff_word},", signoff_name])
    else:
        cleaned_blocks.append(f"{signoff_word},")

    return "\n\n".join(cleaned_blocks).strip()


def generate_cover_letter(job_details: Dict[str, Any], customized_resume: Resume) -> str:
    prompt = _build_cover_letter_prompt(job_details, customized_resume)
    system_prompt = """
You are a practical software-engineering cover letter writer and a precise JSON generator.

Rules:
- Return exactly one valid JSON object matching the required schema.
- Do not output markdown, commentary, or extra text.
- Use the job description for targeting and the customized resume for evidence.
- Do not repeat the resume line by line.
- Do not invent unsupported facts.
- Keep the cover letter concise, human, specific, and ATS-friendly.
- Use plain English. Avoid over-polished AI language, buzzwords, dramatic enthusiasm, and formal corporate phrasing.
""".strip()

    try:
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
            "Primary cover-letter model failed with a temporary provider error. "
            "Retrying once with the Gemini fallback pool. Error: %s",
            exc,
        )
        time.sleep(2)
        llm_output = primary_client.generate_content(
            prompt=prompt,
            system_prompt=system_prompt,
            temperature=0.45,
            response_format=CoverLetterOutput,
            model_override="gemini",
        )

    parsed_output = CoverLetterOutput.model_validate_json(llm_output)
    return _normalize_cover_letter_text(parsed_output.cover_letter, customized_resume.name)


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
        customized_resume = Resume.model_validate(customized_resume_record)
    except Exception as exc:
        logging.error(f"Failed to parse customized resume {customized_resume_id}: {exc}")
        return 1

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
