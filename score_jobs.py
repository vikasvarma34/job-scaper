import time
import json
import logging
import re
from typing import List, Optional, Dict, Any
import requests
import io
import pdfplumber
import os

import config
import supabase_utils
from llm_client import scoring_client

# --- Setup Logging ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# --- Helper Functions ---

def _sarvam_model_id(raw_model: str) -> str:
    cleaned = str(raw_model or "").strip()
    if "/" in cleaned:
        provider, model_id = cleaned.split("/", 1)
        if provider.lower() == "openai":
            return model_id.strip() or "sarvam-105b"
    return cleaned or "sarvam-105b"


def _extract_chat_message_content(message: Any) -> str:
    if isinstance(message, str):
        return message.strip()
    if isinstance(message, list):
        parts: list[str] = []
        for item in message:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                text = item.get("text") or item.get("content") or ""
                if text:
                    parts.append(str(text))
        return "\n".join(part for part in parts if str(part).strip()).strip()
    if isinstance(message, dict):
        return str(message.get("content") or message.get("text") or "").strip()
    return str(message or "").strip()


def _looks_like_education_years(text: str) -> bool:
    normalized = re.sub(r"\s+", " ", str(text or "").lower()).strip()
    if not normalized:
        return False

    education_keywords = (
        "education",
        "degree",
        "degrees",
        "academic",
        "school",
        "college",
        "university",
        "graduation",
        "study",
        "studies",
    )
    return any(keyword in normalized for keyword in education_keywords)


def _request_score_with_sarvam_direct(
    prompt: str,
    system_prompt: str,
    *,
    reasoning_effort: str | None = None,
    max_tokens: int | None = None,
    log_reasoning_trace: bool | None = None,
) -> str:
    sarvam_key = str(config.SCORING_LLM_API_KEY or os.environ.get("SARVAM_API_KEY") or "").strip()
    if not sarvam_key:
        raise RuntimeError("SCORING_LLM_API_KEY/SARVAM_API_KEY is required for direct Sarvam scoring.")

    base_url = str(
        config.SCORING_LLM_API_BASE
        or config.SARVAM_API_BASE
        or "https://api.sarvam.ai/v1"
    ).rstrip("/")
    endpoint = f"{base_url}/chat/completions"
    model_id = _sarvam_model_id(config.SCORING_LLM_MODEL)

    if log_reasoning_trace is None:
        log_reasoning_trace = bool(getattr(config, "SCORING_LOG_REASONING_TRACE", False))

    payload = {
        "model": model_id,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0,
        "max_tokens": max(64, int(max_tokens or getattr(config, "SCORING_SARVAM_MAX_TOKENS", 1024))),
    }
    reasoning_effort = str(reasoning_effort or getattr(config, "SCORING_REASONING_EFFORT", "")).strip().lower()
    if reasoning_effort in {"low", "medium", "high"}:
        payload["reasoning_effort"] = reasoning_effort
    headers = {
        "Content-Type": "application/json",
        "api-subscription-key": sarvam_key,
        "Authorization": f"Bearer {sarvam_key}",
    }

    response = requests.post(endpoint, json=payload, headers=headers, timeout=90)
    if not response.ok:
        body_preview = response.text[:500].strip()
        raise RuntimeError(f"Sarvam API HTTP {response.status_code}: {body_preview}")

    try:
        data = response.json()
    except Exception as exc:
        raise RuntimeError(f"Sarvam API returned non-JSON response: {exc}") from exc

    choices = data.get("choices") or []
    if not choices:
        return ""
    first_choice = choices[0] or {}
    message = first_choice.get("message") or {}
    content = _extract_chat_message_content(message.get("content"))
    reasoning_trace = _extract_chat_message_content(message.get("reasoning_content"))
    if reasoning_trace and log_reasoning_trace:
        logging.info("Scoring reasoning trace (trimmed): %s", reasoning_trace[:300].replace("\n", " "))
    if not content and str(first_choice.get("finish_reason") or "").strip().lower() == "length":
        logging.warning(
            "Sarvam response hit max_tokens before final answer. Consider increasing SCORING_SARVAM_MAX_TOKENS (current=%s).",
            getattr(config, "SCORING_SARVAM_MAX_TOKENS", 512),
        )
    return content.strip()


def format_resume_to_text(resume_data: Dict[str, Any]) -> str:
    """
    Formats the structured resume data dictionary into a plain text string.
    """
    if not resume_data:
        return "Resume data is not available."

    lines = []

    # Basic Info
    lines.append(f"Name: {resume_data.get('name', 'N/A')}")
    lines.append(f"Email: {resume_data.get('email', 'N/A')}")
    if resume_data.get('phone'): lines.append(f"Phone: {resume_data['phone']}")
    if resume_data.get('location'): lines.append(f"Location: {resume_data['location']}")
    if resume_data.get('links'):
        links_str = ", ".join(f"{k}: {v}" for k, v in resume_data['links'].items() if v)
        if links_str: lines.append(f"Links: {links_str}")
    lines.append("\n---\n")

    # Summary
    if resume_data.get('summary'):
        lines.append("Summary:")
        lines.append(resume_data['summary'])
        lines.append("\n---\n")

    # Skills
    if resume_data.get('skills'):
        lines.append("Skills:")
        lines.append(", ".join(resume_data['skills']))
        lines.append("\n---\n")

    # Experience
    if resume_data.get('experience'):
        lines.append("Experience:")
        for exp in resume_data['experience']:
            lines.append(f"\n* {exp.get('job_title', 'N/A')} at {exp.get('company', 'N/A')}")
            if exp.get('location'): lines.append(f"  Location: {exp['location']}")
            date_range = f"{exp.get('start_date', '?')} - {exp.get('end_date', 'Present')}"
            lines.append(f"  Dates: {date_range}")
            if exp.get('description'):
                lines.append("  Description:")
                # Indent description lines
                desc_lines = exp['description'].split('\n')
                lines.extend([f"    - {line.strip()}" for line in desc_lines if line.strip()])
        lines.append("\n---\n")

    # Education
    if resume_data.get('education'):
        lines.append("Education:")
        for edu in resume_data['education']:
            degree_info = f"{edu.get('degree', 'N/A')}"
            if edu.get('field_of_study'): degree_info += f", {edu['field_of_study']}"
            lines.append(f"\n* {degree_info} from {edu.get('institution', 'N/A')}")
            year_range = f"{edu.get('start_year', '?')} - {edu.get('end_year', 'Present')}"
            lines.append(f"  Years: {year_range}")
        lines.append("\n---\n")

    # Projects
    if resume_data.get('projects'):
        lines.append("Projects:")
        for proj in resume_data['projects']:
            lines.append(f"\n* {proj.get('name', 'N/A')}")
            if proj.get('description'): lines.append(f"  Description: {proj['description']}")
            if proj.get('technologies'): lines.append(f"  Technologies: {', '.join(proj['technologies'])}")
        lines.append("\n---\n")

    # Certifications
    if resume_data.get('certifications'):
        lines.append("Certifications:")
        for cert in resume_data['certifications']:
            cert_info = f"{cert.get('name', 'N/A')}"
            if cert.get('issuer'): cert_info += f" ({cert['issuer']})"
            if cert.get('year'): cert_info += f" - {cert['year']}"
            lines.append(f"* {cert_info}")
        lines.append("\n---\n")

    # Languages
    if resume_data.get('languages'):
        lines.append("Languages:")
        lines.append(", ".join(resume_data['languages']))
        lines.append("\n---\n")

    return "\n".join(lines)


def _normalize_experience_required(raw_value: Any) -> str:
    text = str(raw_value or "").strip()
    if not text:
        return "Not stated"

    normalized = re.sub(r"\s+", " ", text.lower()).strip()
    if _looks_like_education_years(normalized) and not re.search(r"\b(experience|work|yoe)\b", normalized):
        return "Not stated"
    if re.search(r"\b(fresher|entry[-\s]?level|no experience)\b", normalized):
        return "0 years"

    patterns: list[tuple[str, str]] = [
        (r"(\d{1,2})\s*(?:-|–|—|to)\s*(\d{1,2})\s*(?:years?|yrs?|yr|yoe)?\b", "range"),
        (r"(\d{1,2})\s*\+(?:\s*(?:years?|yrs?|yr|yoe))?(?=\D|$)", "plus"),
        (r"(\d{1,2})\s*-\s*(?:years?|yrs?|yr|yoe)\b", "plus"),
        (r"(?:at least|minimum(?: of)?|minimum|required|requires|need|needs)\s*(\d{1,2})\s*(?:years?|yrs?|yr|yoe)?\b", "plus"),
        (r"\b(\d{1,2})\s*(?:years?|yrs?|yr|yoe)\b", "exact"),
    ]
    for pattern, mode in patterns:
        match = re.search(pattern, normalized)
        if not match:
            continue
        numbers = [int(group) for group in match.groups() if group is not None]
        if not numbers:
            continue
        if mode == "range" and len(numbers) >= 2:
            low, high = min(numbers), max(numbers)
            return f"{low}-{high} years"
        if mode == "plus":
            return f"{numbers[0]}+ years"
        return f"{numbers[0]} years"

    return "Not stated"


def _parse_score_and_experience(raw_response: str) -> tuple[Optional[int], str]:
    text = str(raw_response or "").strip()
    if not text:
        return None, "Not stated"

    parsed_score: Optional[int] = None
    parsed_experience = "Not stated"

    # Remove markdown fences if model wraps JSON in ```json ... ```
    fence_match = re.search(r"```(?:json)?\s*([\s\S]*?)```", text, flags=re.IGNORECASE)
    if fence_match:
        text = fence_match.group(1).strip()

    json_candidate = text
    object_match = re.search(r"\{[\s\S]*\}", text)
    if object_match:
        json_candidate = object_match.group(0)

    parsed_obj: Dict[str, Any] = {}
    try:
        maybe_obj = json.loads(json_candidate)
        if isinstance(maybe_obj, dict):
            parsed_obj = maybe_obj
    except Exception:
        parsed_obj = {}

    if parsed_obj:
        score_candidate = parsed_obj.get("score")
        if score_candidate is None:
            score_candidate = parsed_obj.get("resume_score")
        if score_candidate is None:
            score_candidate = parsed_obj.get("match_score")
        try:
            parsed_score = int(str(score_candidate).strip()) if score_candidate is not None else None
        except Exception:
            parsed_score = None

        experience_candidate = (
            parsed_obj.get("experience_required")
            or parsed_obj.get("required_experience")
            or parsed_obj.get("experience")
            or ""
        )
        parsed_experience = _normalize_experience_required(experience_candidate)

    if parsed_score is None:
        number_match = re.search(r"\b(\d{1,3})\b", text)
        if number_match:
            try:
                parsed_score = int(number_match.group(1))
            except Exception:
                parsed_score = None

    return parsed_score, parsed_experience


def _minimum_required_years(experience_required: str) -> Optional[int]:
    normalized = str(experience_required or "").strip().lower()
    if not normalized or normalized == "not stated":
        return None

    numbers = re.findall(r"\d{1,2}", normalized)
    if not numbers:
        return None
    try:
        return int(numbers[0])
    except ValueError:
        return None


def _extract_explicit_experience_from_description(description: str) -> str:
    text = re.sub(r"\s+", " ", str(description or "").lower()).strip()
    if not text or _looks_like_education_years(text) and not re.search(r"\b(experience|work|yoe)\b", text):
        return "Not stated"

    patterns = [
        r"(?:at least|minimum(?: of)?|required|requires|need|needs)\s*\d{1,2}\+?\s*(?:years?|yrs?|yr|yoe)\b(?:\s+of)?\s+(?:work\s+)?experience",
        r"\d{1,2}\s*(?:-|–|—|to)\s*\d{1,2}\s*(?:years?|yrs?|yr|yoe)\b(?:\s+of)?\s+(?:work\s+)?experience",
        r"\d{1,2}\+\s*(?:years?|yrs?|yr|yoe)\b(?:\s+of)?\s+(?:work\s+)?experience",
        r"\d{1,2}\s*(?:years?|yrs?|yr|yoe)\b(?:\s+of)?\s+(?:work\s+)?experience",
        r"(?:experience|work experience)\s*(?:of|:)?\s*\d{1,2}\s*(?:-|–|—|to)\s*\d{1,2}\s*(?:years?|yrs?|yr|yoe)?\b",
        r"(?:experience|work experience)\s*(?:of|:)?\s*\d{1,2}\+?\s*(?:years?|yrs?|yr|yoe)?\b",
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return _normalize_experience_required(match.group(0))
    return "Not stated"


def _apply_experience_score_override(
    score: int,
    experience_required: str,
    job_description: str,
    job_id: Any,
) -> tuple[int, str]:
    normalized_experience = _normalize_experience_required(experience_required)
    if normalized_experience == "Not stated":
        fallback_experience = _extract_explicit_experience_from_description(job_description)
        if fallback_experience != "Not stated":
            normalized_experience = fallback_experience

    min_years = _minimum_required_years(normalized_experience)
    if min_years is not None and min_years >= 4:
        logging.info(
            "Forcing score to 0 for job_id %s because explicit minimum experience is %s.",
            job_id,
            normalized_experience,
        )
        return 0, normalized_experience
    return score, normalized_experience


def get_resume_score_from_ai(resume_text: str, job_details: Dict[str, Any]) -> tuple[Optional[int], str]:
    """
    Sends resume and job details to Gemini to get a suitability score.
    Returns (score, experience_required).
    score is an integer (0-100) or None if scoring fails.
    """
    if not resume_text or not job_details or not job_details.get('description'):
        logging.warning(f"Missing resume text or job description for job_id {job_details.get('job_id')}. Skipping scoring.")
        return None, "Not stated"

    job_company = job_details.get('company', 'N/A')
    job_title = job_details.get('job_title', 'N/A')
    job_description = job_details.get('description', 'N/A')
    job_level = job_details.get('level', 'N/A')

    logging.info(f"Scoring job_id: {job_details.get('job_id')} with job_title: {job_title} and job_level: {job_level}")

    prompt = f"""
    You are an expert resume-to-job matching evaluator.
    You will be given one resume and one job description.

    Your task is to assess the candidate's fit for the role using a careful holistic review, not shallow keyword counting.

    Evaluate all of the following before deciding the score:
    1. Job title alignment
    2. Seniority / years-of-experience alignment
    3. Hard-skill overlap
    4. Stack / tool / framework overlap
    5. Relevance of actual work experience
    6. Relevance of projects
    7. Domain or platform relevance when clearly present
    8. Evidence of ownership, implementation depth, and production work

    Scoring guidance:
    - 90-100: Excellent fit; strong title, experience, and technical alignment
    - 75-89: Good fit; strong overlap with some gaps
    - 60-74: Moderate fit; some relevant overlap but meaningful gaps
    - 40-59: Weak fit; partial overlap only
    - 0-39: Poor fit; little real alignment

    Important rules:
    - Base the score only on the provided resume and job description.
    - Do not reward generic buzzwords unless supported by real evidence.
    - Give more weight to relevant hard skills and actual experience than to soft skills.
    - Give more weight to recent and directly relevant experience/projects than to minor mentions.
    - Do not penalize for missing skills that are clearly optional or nice-to-have unless the JD strongly emphasizes them.
    - Do not output any explanation.
    - Return JSON only, with no extra text.
    - Do not default to 85. Use the full range when evidence supports it.
    - If major required skills are missing, score should usually be below 75.
    - If title + skills + experience align strongly with direct evidence, score should usually be 85+.
    - When extracting experience_required, ignore education years, degree duration, graduation years, or other academic timelines.
    - Only return work-experience requirements that are explicit in the job description.
    - If the JD only mentions education years or no clear work-experience requirement, return "Not stated".

    Internal scoring method (do this mentally):
    - Title/Seniority fit: 20%
    - Hard-skill and stack fit: 35%
    - Relevant work experience evidence: 30%
    - Project/domain relevance: 15%
    - Apply penalty of 5-20 points for clear must-have gaps.

    Also extract the explicit experience requirement from the job description.
    Normalize experience_required to one of these forms when possible:
    - "X+ years"
    - "X-Y years"
    - "X years"
    - "Not stated" when not clearly present.
    Use numbers only; don't write words like "two".

    --- RESUME ---
    {resume_text}
    --- END RESUME ---

    --- JOB DESCRIPTION ---
    Job Title: {job_title}
    Company: {job_company}
    Level: {job_level}

    {job_description}
    --- END JOB DESCRIPTION ---

    Output format (strict JSON, no markdown):
    {{"score": <integer 0-100>, "experience_required": "<normalized text>"}}
    """

    strict_system_prompt = (
        "You are a scoring function. Return only JSON with keys "
        "'score' (integer 0-100) and 'experience_required' (string). "
        "No explanation, no markdown, no extra keys."
    )
    use_direct_sarvam = bool(
        config.SCORING_USE_DIRECT_SARVAM
        and "sarvam" in str(config.SCORING_LLM_MODEL).lower()
    )

    last_raw_response = ""
    primary_reasoning = str(getattr(config, "SCORING_REASONING_EFFORT", "medium")).strip().lower()
    fallback_reasoning = str(getattr(config, "SCORING_FALLBACK_REASONING_EFFORT", "low")).strip().lower()
    max_tokens = int(getattr(config, "SCORING_SARVAM_MAX_TOKENS", 1024))
    for attempt in range(1, 4):
        try:
            logging.info(
                "Requesting score for job_id: %s (attempt %s/3)",
                job_details.get('job_id'),
                attempt,
            )
            if use_direct_sarvam:
                score_text = _request_score_with_sarvam_direct(
                    prompt=prompt,
                    system_prompt=strict_system_prompt,
                    reasoning_effort=primary_reasoning,
                    max_tokens=max_tokens,
                    log_reasoning_trace=True,
                )
            else:
                score_text = scoring_client.generate_content(
                    prompt=prompt,
                    system_prompt=strict_system_prompt,
                    temperature=0,
                )
            last_raw_response = str(score_text or "").strip()

            if not last_raw_response:
                if use_direct_sarvam and fallback_reasoning != primary_reasoning:
                    logging.info(
                        "Retrying job_id %s immediately with fallback reasoning=%s.",
                        job_details.get('job_id'),
                        fallback_reasoning,
                    )
                    score_text = _request_score_with_sarvam_direct(
                        prompt=prompt,
                        system_prompt=strict_system_prompt,
                        reasoning_effort=fallback_reasoning,
                        max_tokens=max_tokens,
                        log_reasoning_trace=False,
                    )
                    last_raw_response = str(score_text or "").strip()
                    if last_raw_response:
                        logging.info(
                            "Fallback reasoning produced output for job_id %s on attempt %s/3.",
                            job_details.get('job_id'),
                            attempt,
                        )
                        score, experience_required = _parse_score_and_experience(last_raw_response)
                        if score is not None and 0 <= score <= 100:
                            score, experience_required = _apply_experience_score_override(
                                score,
                                experience_required,
                                job_description,
                                job_details.get('job_id'),
                            )
                            logging.info(
                                "Received score %s for job_id: %s (experience_required=%s)",
                                score,
                                job_details.get('job_id'),
                                experience_required,
                            )
                            return score, experience_required
                logging.warning(
                    "Empty score response for job_id %s on attempt %s/3.",
                    job_details.get('job_id'),
                    attempt,
                )
                continue

            score, experience_required = _parse_score_and_experience(last_raw_response)
            if score is None:
                logging.warning(
                    "No integer score found in response for job_id %s on attempt %s/3. Raw response: %r",
                    job_details.get('job_id'),
                    attempt,
                    last_raw_response,
                )
                continue

            if 0 <= score <= 100:
                score, experience_required = _apply_experience_score_override(
                    score,
                    experience_required,
                    job_description,
                    job_details.get('job_id'),
                )
                logging.info(
                    "Received score %s for job_id: %s (experience_required=%s)",
                    score,
                    job_details.get('job_id'),
                    experience_required,
                )
                return score, experience_required

            logging.warning(
                "Received score out of range (%s) for job_id %s on attempt %s/3. Raw response: %r",
                score,
                job_details.get('job_id'),
                attempt,
                last_raw_response,
            )
        except Exception as e:
            logging.error(
                "Error calling LLM API for job_id %s on attempt %s/3: %s",
                job_details.get('job_id'),
                attempt,
                e,
            )

    logging.error(
        "Could not parse integer score from LLM response for job_id %s after retries. Last raw response: %r",
        job_details.get('job_id'),
        last_raw_response,
    )
    return None, "Not stated"


def extract_text_from_pdf_url(pdf_url: str) -> Optional[str]:
    """
    Downloads a PDF from a URL and extracts text from it.
    """
    if not pdf_url:
        logging.warning("No PDF URL provided for text extraction.")
        return None
    try:
        logging.info(f"Downloading PDF from URL: {pdf_url}")
        response = requests.get(pdf_url, timeout=30) 
        response.raise_for_status()  # Raise an exception for bad status codes

        logging.info(f"Successfully downloaded PDF. Extracting text...")
        text = ""
        with io.BytesIO(response.content) as pdf_file:
            with pdfplumber.open(pdf_file) as pdf:
                for page in pdf.pages:
                    page_text = page.extract_text()
                    if page_text:
                        text += page_text + "\n"
        
        if not text.strip():
            logging.warning(f"Extracted no text from PDF at {pdf_url}. The PDF might be image-based or empty.")
            return None
            
        logging.info(f"Successfully extracted text from PDF URL: {pdf_url[:70]}...")
        return text.strip()

    except requests.exceptions.RequestException as e:
        logging.error(f"Error downloading PDF from {pdf_url}: {e}")
        return None
    except pdfplumber.exceptions.PDFSyntaxError: # Catch specific pdfplumber error
        logging.error(f"Error: Could not open PDF from {pdf_url}. It might be corrupted or not a PDF.")
        return None
    except Exception as e:
        logging.error(f"An unexpected error occurred while extracting text from PDF URL {pdf_url}: {e}")
        return None

def rescore_jobs_with_custom_resume():
    """Fetches jobs with custom resumes and re-scores them."""
    logging.info("--- Starting Job Re-scoring with Custom Resumes ---")
    rescore_start_time = time.time()

    jobs_to_rescore = supabase_utils.get_jobs_to_rescore(config.JOBS_TO_SCORE_PER_RUN)
    if not jobs_to_rescore:
        logging.info("No jobs require re-scoring with custom resumes at this time.")
        logging.info("--- Job Re-scoring Finished (No Jobs) ---")
        return

    logging.info(f"Processing {len(jobs_to_rescore)} jobs for re-scoring...")
    successful_rescores = 0
    failed_rescores = 0
    logging.info("Experience prefilter disabled for re-scoring; every job with a description will be sent to the LLM.")

    for i, job in enumerate(jobs_to_rescore):
        job_id = job.get('job_id')
        resume_link = job.get('resume_link')
        customized_resume_id = job.get('customized_resume_id')

        if not job_id:
            logging.warning(f"Skipping re-scoring for job due to missing job_id: {job}")
            failed_rescores += 1
            continue

        logging.info(f"--- Re-scoring Job {i+1}/{len(jobs_to_rescore)} (ID: {job_id}) ---")

        custom_resume_text = None

        # Try to get resume data from database first
        if customized_resume_id:
            logging.info(f"Targeting customized_resume_id: {customized_resume_id}")
            db_resume_data = supabase_utils.get_customized_resume(customized_resume_id)
            if db_resume_data:
                logging.info(f"Successfully retrieved customized resume data from DB for job {job_id}")
                custom_resume_text = format_resume_to_text(db_resume_data)
            else:
                logging.warning(f"Could not find customized resume data in DB for ID {customized_resume_id}. Falling back to PDF.")

        # Fallback to PDF extraction if DB retrieval failed or ID was missing
        if not custom_resume_text and resume_link:
            logging.info(f"Attempting to extract text from custom resume PDF from {resume_link[:70]}...")
            custom_resume_text = extract_text_from_pdf_url(resume_link)

        if not custom_resume_text:
            logging.error(f"Failed to obtain custom resume text for job_id {job_id} from both DB and PDF. Skipping.")
            failed_rescores += 1
            if i < len(jobs_to_rescore) - 1:
                logging.debug(f"Waiting {config.LLM_REQUEST_DELAY_SECONDS} seconds before next job...")
                time.sleep(config.LLM_REQUEST_DELAY_SECONDS)
            continue
        
        logging.debug(f"Custom resume text for job {job_id} (first 200 chars): {custom_resume_text[:200]}")
        score, experience_required = get_resume_score_from_ai(custom_resume_text, job)

        if score is not None:
            if supabase_utils.update_job_score(
                job_id,
                score,
                resume_score_stage="custom",
                experience_required=experience_required,
            ):
                successful_rescores += 1
            else:
                failed_rescores += 1 
        else:
            failed_rescores += 1 

        if i < len(jobs_to_rescore) - 1: 
            logging.debug(f"Waiting {config.LLM_REQUEST_DELAY_SECONDS} seconds before next API call...")
            time.sleep(config.LLM_REQUEST_DELAY_SECONDS)

    rescore_end_time = time.time()
    logging.info("--- Job Re-scoring Finished ---")
    logging.info(f"Successfully re-scored: {successful_rescores}")
    logging.info(f"Failed/Skipped re-scores: {failed_rescores}")
    logging.info(f"Total re-scoring time: {rescore_end_time - rescore_start_time:.2f} seconds")

# --- Main Execution ---

def main():
    """Main function to score jobs based on the target resume."""
    logging.info("--- Starting Job Scoring Script ---")
    logging.info(
        "Scoring model configured as: %s%s%s | reasoning=%s",
        config.SCORING_LLM_MODEL,
        f" (api_base={config.SCORING_LLM_API_BASE})" if config.SCORING_LLM_API_BASE else "",
        " [direct Sarvam API]" if config.SCORING_USE_DIRECT_SARVAM and "sarvam" in str(config.SCORING_LLM_MODEL).lower() else "",
        getattr(config, "SCORING_REASONING_EFFORT", "default"),
    )
    overall_start_time = time.time()

    # --- Phase 1: Initial Scoring with Default Resume ---
    logging.info("--- Phase 1: Initial Scoring with Default Resume ---")
    initial_score_start_time = time.time()
    
    resume_path = getattr(config, 'BASE_RESUME_PATH', 'resume.json')
    
    # Try fetching resume from Supabase first, fall back to local file
    default_resume_data = supabase_utils.get_base_resume()
    
    if default_resume_data:
        logging.info("Successfully loaded base resume from Supabase database.")
    elif os.path.exists(resume_path):
        logging.info(f"Supabase fetch failed. Falling back to local file: {resume_path}")
        try:
            with open(resume_path, 'r', encoding='utf-8') as f:
                default_resume_data = json.load(f)
        except Exception as e:
            logging.error(f"Failed to read or decode {resume_path}: {e}")
            default_resume_data = None
    else:
        logging.error(f"Base resume not found in Supabase or at '{resume_path}'. Please run the 'Parse Resume' workflow first.")

    if default_resume_data:
        # 2. Format Resume to Text
        default_resume_text = format_resume_to_text(default_resume_data)
        logging.info("Default resume data formatted to text.")
        logging.info("Experience prefilter disabled for initial scoring; every job with a description will be sent to the LLM.")

        # 3. Fetch Jobs to Score
        jobs_to_score_initially = supabase_utils.get_jobs_to_score(config.JOBS_TO_SCORE_PER_RUN)
        if not jobs_to_score_initially:
            logging.info("No jobs require initial scoring at this time.")
        else:
            logging.info(f"Processing {len(jobs_to_score_initially)} jobs for initial scoring...")
            successful_initial_scores = 0
            failed_initial_scores = 0

            # 4. Loop Through Jobs and Score Them
            for i, job in enumerate(jobs_to_score_initially):
                job_id = job.get('job_id')
                if not job_id:
                    logging.warning("Found job data without job_id during initial scoring. Skipping.")
                    failed_initial_scores +=1
                    continue

                logging.info(f"--- Initial Scoring Job {i+1}/{len(jobs_to_score_initially)} (ID: {job_id}) ---")
                score, experience_required = get_resume_score_from_ai(default_resume_text, job)

                if score is not None:
                    if supabase_utils.update_job_score(
                        job_id,
                        score,
                        resume_score_stage="initial",
                        experience_required=experience_required,
                    ):
                        successful_initial_scores += 1
                    else:
                        failed_initial_scores += 1
                else:
                    failed_initial_scores += 1

                if i < len(jobs_to_score_initially) - 1:
                    logging.debug(f"Waiting {config.LLM_REQUEST_DELAY_SECONDS} seconds before next API call...")
                    time.sleep(config.LLM_REQUEST_DELAY_SECONDS)
            
            initial_score_end_time = time.time()
            logging.info("--- Initial Scoring Phase Finished ---")
            logging.info(f"Successfully initially scored: {successful_initial_scores}")
            logging.info(f"Failed/Skipped initial scores: {failed_initial_scores}")
            logging.info(f"Total initial scoring time: {initial_score_end_time - initial_score_start_time:.2f} seconds")

    # # --- Phase 2: Re-scoring with Custom Resumes ---
    rescore_jobs_with_custom_resume() 

    overall_end_time = time.time()
    logging.info("--- Job Scoring Script Finished (All Phases) ---")
    logging.info(f"Total script execution time: {overall_end_time - overall_start_time:.2f} seconds")


if __name__ == "__main__":
    if not config.SCORING_LLM_API_KEY:
        logging.error("No scoring API key configured. Set SCORING_LLM_API_KEY or SARVAM_API_KEY.")
    elif not config.SUPABASE_URL or not config.SUPABASE_SERVICE_ROLE_KEY:
        logging.error("Supabase URL or Key environment variable not set.")
    else:
        main()
