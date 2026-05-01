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
        "location": job_details.get("location", ""),
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


def _resolve_applicant_location(customized_resume: Resume) -> str:
    default_location = _load_default_resume_location()
    if default_location:
        return default_location
    return str(customized_resume.location or "").strip()


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
Write a short, human cover letter for this technology job.

Target job:
{_serialize_job_for_prompt(job_details)}

Customized resume:
{_serialize_resume_for_cover_letter(customized_resume)}

Recruiter mindset:
- Write like a confident candidate, not like someone begging for an opportunity.
- Make it feel like a fair give-and-take: the company has a need, and Vikas can contribute useful skills and delivery experience.
- Use the job description to understand what the team needs.
- Use the resume only as evidence. Do not retell the resume or copy its bullets.
- Choose only 1 or 2 relevant proof points from the resume, then explain how they connect to this role.
- Keep the tone simple, calm, and natural. It should sound like a real person wrote it.
- If useful, say that Vikas is based in Toronto, Canada and would be happy to relocate to India for this role.
- Never say or imply that Vikas is currently based in Hyderabad.
- Do not invent facts, companies, dates, technologies, metrics, or achievements.

Structure:
- Include a greeting.
- Write only 2 or 3 short paragraphs total after the greeting.
- Paragraph 1: mention the role/company and the clearest reason Vikas matches what they need.
- Paragraph 2: connect one or two relevant resume examples to how Vikas can help the team deliver.
- Optional paragraph 3: close with calm interest and mention relocation to India if it fits the job context.
- Keep the full letter roughly 130 to 210 words.

Mandatory formatting:
- Separate every block with one blank line.
- Do not return the cover letter as one continuous paragraph.
- Keep the greeting on its own line.
- Keep each paragraph as its own block.
- Do not include the date; the PDF renderer adds it separately.
- End with exactly this sign-off format, with the name on a separate line:

Sincerely,

{customized_resume.name}

Writing rules:
- Mention the company and job title naturally when possible.
- Focus on contribution: what Vikas can help build, improve, support, or deliver for this team.
- Prefer concrete evidence over broad claims.
- Match the role family from the job description. For frontend roles, use UI and user-facing product evidence. For backend roles, use APIs, services, databases, integrations, reliability, or security evidence. For AI/LLM, testing, cloud/devops, data, or other roles, choose the strongest matching evidence.
- Keep the tone warm, capable, and natural, not fancy.
- Use common words. Prefer "used", "built", "worked on", "improved", and "helped" over formal words like "leveraged", "spearheaded", "orchestrated", "harnessed", or "utilized".
- Keep sentences short and readable.
- Avoid phrases like "please consider my application", "I would be grateful", "given the opportunity", "I hope to hear from you", "I am thrilled", "proven track record", "dynamic", "results-oriented", "robust", "cutting-edge", "seamlessly", "transformative", and "uniquely positioned".
- Job keywords are allowed only when they fit naturally and connect to real work.
- Avoid generic clichés such as "I am writing to express my interest" unless phrased naturally.
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


def generate_cover_letter(job_details: Dict[str, Any], customized_resume: Resume) -> str:
    prompt = _build_cover_letter_prompt(job_details, customized_resume)
    system_prompt = """
You are a practical technology recruiter, cover letter writer, and precise JSON generator.

Rules:
- Return exactly one valid JSON object matching the required schema.
- Do not output markdown, commentary, or extra text.
- Use the job description to understand the employer's need.
- Use the customized resume only as evidence for what Vikas can contribute.
- Treat the selected job as user-verified for fit.
- Match the job's role family without copying generic job-posting keywords as filler.
- Do not repeat the resume line by line.
- Do not invent unsupported facts.
- Write 2 or 3 short paragraphs after the greeting.
- Keep the cover letter concise, human, specific, confident, and ATS-friendly.
- Write like a capable candidate explaining how he can help the company, not like someone asking for a favor.
- Make the letter feel like give-and-take: company need plus candidate contribution.
- Use simple plain English for India-focused technology applications. Avoid over-polished AI language, buzzwords, needy phrasing, dramatic enthusiasm, and formal corporate phrasing.
- Do not favor backend, frontend, Java, Go, Python, AI/LLM, cloud, testing, or any other role family by default. Let the job description decide the emphasis.
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
