import argparse
import logging
import supabase_utils
import config # Assuming config holds necessary configurations like a default email
from typing import Dict, Any
import json # Import json for parsing LLM output
import pdf_generator 
import re
import asyncio 
import math
import resume_validator
import requests
from llm_client import primary_client
from models import (
    Resume, SummaryOutput, SkillsOutput, SingleExperienceOutput,
    SingleProjectOutput,
    ATSKeywordPlan, ATSResumeRewriteOutput
)
import time
import os
# --- Logging Setup ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

RESUME_GENERATION_TEMPERATURE = 0.6


def _log_keyword_plan_response(job_id: Any, llm_output: str) -> None:
    """
    Log the raw first-step planner response so keyword selection is easy to inspect.
    """
    pretty_output = str(llm_output or "").strip()
    try:
        pretty_output = json.dumps(json.loads(pretty_output), indent=2, ensure_ascii=False)
    except Exception:
        pass
    logging.info("ATS keyword plan response for job_id %s:\n%s", job_id, pretty_output)


def _postprocess_keyword_plan(plan: ATSKeywordPlan) -> ATSKeywordPlan:
    """
    Light cleanup for first-pass keyword extraction so the second rewrite call
    sees resume-usable skill keywords instead of low-signal admin phrases.
    """
    hard_blocklist = (
        "microsoft office",
        "pc skills",
        "office suite",
        "computer literacy",
        "software demo content",
    )
    soft_blocklist = (
        "status updates",
        "issue escalation",
        "vendor team coordination",
        "leadership support",
        "fluency in english",
        "knowledge transfer",
    )

    def _clean(items: list[str], *, blocklist: tuple[str, ...], max_items: int) -> list[str]:
        cleaned: list[str] = []
        seen: set[str] = set()
        for raw_item in items:
            item = re.sub(r"\s+", " ", str(raw_item or "")).strip(" ,.-")
            if not item:
                continue
            normalized = item.lower()
            if normalized in seen:
                continue
            if any(blocked in normalized for blocked in blocklist):
                continue
            seen.add(normalized)
            cleaned.append(item)
            if len(cleaned) >= max_items:
                break
        return cleaned

    return ATSKeywordPlan(
        hard_skills=_clean(list(plan.hard_skills), blocklist=hard_blocklist, max_items=16),
        soft_skills=_clean(list(plan.soft_skills), blocklist=soft_blocklist, max_items=10),
    )


def _extract_json_payload(raw_text: Any) -> str:
    """
    Strip markdown fences and keep the most likely JSON object payload.
    This makes structured outputs resilient when the model wraps JSON in ```json fences.
    """
    text = str(raw_text or "").strip()
    if not text:
        return text

    fence_match = re.search(r"```(?:json)?\s*([\s\S]*?)```", text, flags=re.IGNORECASE)
    if fence_match:
        text = fence_match.group(1).strip()

    object_match = re.search(r"\{[\s\S]*\}", text)
    if object_match:
        return object_match.group(0).strip()

    return text


def _is_sarvam_resume_model() -> bool:
    return "sarvam" in str(getattr(config, "LLM_MODEL", "")).lower()


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


def _request_sarvam_direct(
    prompt: str,
    system_prompt: str,
    *,
    temperature: float = RESUME_GENERATION_TEMPERATURE,
    max_tokens: int | None = None,
) -> str:
    sarvam_key = str(config.LLM_API_KEY or os.environ.get("SARVAM_API_KEY") or "").strip()
    if not sarvam_key:
        raise RuntimeError("SARVAM_API_KEY is required for Sarvam resume generation.")

    base_url = str(
        getattr(config, "LLM_API_BASE", None)
        or os.environ.get("SARVAM_API_BASE")
        or "https://api.sarvam.ai/v1"
    ).rstrip("/")
    endpoint = f"{base_url}/chat/completions"
    payload = {
        "model": _sarvam_model_id(config.LLM_MODEL),
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt},
        ],
        "temperature": temperature,
        "max_tokens": max(64, int(max_tokens or getattr(config, "LLM_SARVAM_MAX_TOKENS", 32768))),
    }
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
    if content:
        return content.strip()
    if str(first_choice.get("finish_reason") or "").strip().lower() == "length":
        logging.warning(
            "Sarvam response hit max_tokens before final answer. Consider increasing LLM_SARVAM_MAX_TOKENS."
        )
    return ""


def _coerce_description_text(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        parts: list[str] = []
        for item in value:
            if isinstance(item, dict):
                text = item.get("text") or item.get("content") or item.get("description") or ""
                if isinstance(text, list):
                    text = _coerce_description_text(text)
                text = str(text or "").strip()
            else:
                text = str(item or "").strip()
            if text:
                parts.append(text)
        return "\n".join(parts).strip()
    if isinstance(value, dict):
        return str(value.get("content") or value.get("text") or value.get("description") or "").strip()
    return str(value or "").strip()


def _normalize_resume_rewrite_payload(payload: Any) -> Any:
    if not isinstance(payload, dict):
        return payload

    normalized = dict(payload)
    normalized["header_title"] = str(normalized.get("header_title") or "").strip()
    normalized["summary"] = _coerce_description_text(normalized.get("summary"))
    skills = normalized.get("skills") or []
    if isinstance(skills, list):
        flattened_skills: list[str] = []
        for skill in skills:
            if isinstance(skill, list):
                flattened_skills.extend(
                    [
                        str(item).strip()
                        for item in skill
                        if str(item).strip()
                    ]
                )
            else:
                cleaned = str(skill).strip()
                if cleaned:
                    flattened_skills.append(cleaned)
        normalized["skills"] = flattened_skills

    for section_key in ("experience", "projects"):
        section_value = normalized.get(section_key)
        if isinstance(section_value, list):
            cleaned_items: list[dict[str, Any]] = []
            for item in section_value:
                if not isinstance(item, dict):
                    continue
                item_copy = dict(item)
                item_copy["description"] = _coerce_description_text(item_copy.get("description"))
                cleaned_items.append(item_copy)
            normalized[section_key] = cleaned_items
        elif isinstance(section_value, dict):
            item_copy = dict(section_value)
            item_copy["description"] = _coerce_description_text(item_copy.get("description"))
            normalized[section_key] = item_copy

    if isinstance(normalized.get("project"), dict):
        project_copy = dict(normalized["project"])
        project_copy["description"] = _coerce_description_text(project_copy.get("description"))
        normalized["project"] = project_copy

    if isinstance(normalized.get("experience"), dict):
        experience_copy = dict(normalized["experience"])
        experience_copy["description"] = _coerce_description_text(experience_copy.get("description"))
        normalized["experience"] = experience_copy

    return normalized


def _parse_structured_json_output(raw_output: Any) -> dict[str, Any]:
    payload_text = _extract_json_payload(raw_output)
    parsed = json.loads(payload_text)
    if not isinstance(parsed, dict):
        raise ValueError("Structured output did not contain a JSON object.")
    return parsed


def _generate_structured_output(
    prompt: str,
    system_prompt: str,
    response_model: Any,
    *,
    temperature: float = RESUME_GENERATION_TEMPERATURE,
    max_tokens: int | None = None,
) -> Any:
    if _is_sarvam_resume_model():
        raw_output = _request_sarvam_direct(
            prompt=prompt,
            system_prompt=system_prompt,
            temperature=temperature,
            max_tokens=max_tokens,
        )
    else:
        raw_output = primary_client.generate_content(
            prompt=prompt,
            system_prompt=system_prompt,
            temperature=temperature,
            response_format=response_model,
        )

    parsed = _normalize_resume_rewrite_payload(_parse_structured_json_output(raw_output))
    return response_model.model_validate(parsed)


def _sanitize_filename_token(value: Any, default: str = "UNKNOWN") -> str:
    """
    Convert arbitrary text into a safe uppercase filename token.
    Example: "Tata Consultancy Services" -> "TATA_CONSULTANCY_SERVICES"
    """
    text = str(value or "").strip()
    if not text:
        return default
    text = re.sub(r"[^A-Za-z0-9]+", "_", text)
    text = re.sub(r"_+", "_", text).strip("_")
    return (text or default).upper()

def _build_resume_filename(job_id: str, company: Any) -> str:
    """
    Build a readable resume filename for storage.
    """
    company_token = _sanitize_filename_token(company, default="COMPANY")
    job_token = _sanitize_filename_token(job_id, default="JOB")
    return f"VIKAS_POKALA_{company_token}_{job_token}.pdf"


def _serialize_resume_for_prompt(resume: Resume) -> str:
    """
    Convert the resume model to JSON for LLM prompt context.
    """
    return json.dumps(resume.model_dump(), indent=2)


def _serialize_job_for_prompt(job_details: Dict[str, Any]) -> str:
    """
    Convert the selected job fields to JSON for LLM prompt context.
    """
    job_payload = {
        "job_title": job_details.get("job_title", ""),
        "company": job_details.get("company", ""),
        "level": job_details.get("level", ""),
        "description": job_details.get("description", ""),
    }
    return json.dumps(job_payload, indent=2)


def _load_base_resume_details() -> Resume | None:
    """
    Load the base resume for generation.
    Local resume.json is the primary source of truth so it can be edited manually.
    Supabase is only used as a fallback when the local file is missing.
    """
    resume_path = getattr(config, "BASE_RESUME_PATH", "resume.json")
    raw_resume_details = None

    if os.path.exists(resume_path):
        logging.info(f"Loading base resume from local source-of-truth file: {resume_path}")
        try:
            with open(resume_path, "r", encoding="utf-8") as f:
                raw_resume_details = json.load(f)
        except Exception as e:
            logging.error(f"Failed to read or decode {resume_path}: {e}")
            return None
    else:
        logging.info(
            f"Local base resume file '{resume_path}' not found. Falling back to Supabase base_resume."
        )
        raw_resume_details = supabase_utils.get_base_resume()
        if raw_resume_details:
            logging.info("Successfully loaded base resume from Supabase database.")

    if not raw_resume_details:
        logging.error(
            f"Base resume not found in local file '{resume_path}' or Supabase. "
            "Create/update resume.json before generating resumes."
        )
        return None

    try:
        for key in ["skills", "experience", "education", "projects", "certifications", "languages"]:
            if raw_resume_details.get(key) is None:
                raw_resume_details[key] = []
        return Resume(**raw_resume_details)
    except Exception as e:
        logging.error(f"Error parsing base resume details into Pydantic model: {e}")
        logging.error(f"Raw base resume data: {raw_resume_details}")
        return None


def _apply_job_contact_overrides(
    resume: Resume,
    job_details: Dict[str, Any],
    email_override: str | None = None,
) -> Resume:
    """
    Apply per-job contact overrides without changing the source-of-truth resume.json.
    """
    updated_resume = resume.model_copy(deep=True)
    manual_email_override = str(email_override or "").strip()
    if manual_email_override:
        updated_resume.email = manual_email_override
        return updated_resume
    contact_email_override = str(job_details.get("contact_email_override") or "").strip()
    if contact_email_override:
        updated_resume.email = contact_email_override
    return updated_resume


def _paragraphize_summary(text: str) -> str:
    """
    Convert multi-line summary text into one readable paragraph.
    """
    if not text:
        return text
    parts = [part.strip() for part in str(text).splitlines() if part.strip()]
    return " ".join(parts) if parts else str(text).strip()


def _normalize_skills_output(base_skills: list[str], rewritten_skills: list[str]) -> list[str]:
    """
    Prefer grouped skill lines when the base resume already uses grouped categories.
    Keep the model's selected skills when possible instead of snapping back to the full base list.
    """
    cleaned_base = [skill for skill in (base_skills or []) if str(skill).strip()]
    cleaned_rewritten = [skill for skill in (rewritten_skills or []) if str(skill).strip()]

    base_is_grouped = any(":" in str(skill) for skill in cleaned_base)
    rewritten_is_grouped = any(":" in str(skill) for skill in cleaned_rewritten)

    if base_is_grouped and not rewritten_is_grouped and cleaned_rewritten:
        grouped_base_map: dict[str, list[tuple[str, str]]] = {}
        ordered_group_names: list[str] = []

        for skill_line in cleaned_base:
            if ":" not in str(skill_line):
                continue
            group_name, raw_items = str(skill_line).split(":", 1)
            group_name = group_name.strip()
            if not group_name:
                continue
            ordered_group_names.append(group_name)
            grouped_base_map[group_name] = []
            for item in raw_items.split(","):
                original_item = item.strip()
                if original_item:
                    grouped_base_map[group_name].append(
                        (original_item.lower(), original_item)
                    )

        grouped_selected: dict[str, list[str]] = {group: [] for group in ordered_group_names}
        uncategorized: list[str] = []
        seen_items: set[str] = set()

        for rewritten_skill in cleaned_rewritten:
            rewritten_text = str(rewritten_skill).strip()
            normalized_rewritten = rewritten_text.lower()
            matched_group = None

            for group_name in ordered_group_names:
                group_items = grouped_base_map.get(group_name, [])
                if any(
                    normalized_rewritten == normalized_item
                    or normalized_rewritten in normalized_item
                    or normalized_item in normalized_rewritten
                    for normalized_item, _ in group_items
                ):
                    matched_group = group_name
                    break

            if matched_group:
                if rewritten_text.lower() not in seen_items:
                    grouped_selected[matched_group].append(rewritten_text)
                    seen_items.add(rewritten_text.lower())
            else:
                if rewritten_text.lower() not in seen_items:
                    uncategorized.append(rewritten_text)
                    seen_items.add(rewritten_text.lower())

        regrouped_lines: list[str] = []
        for group_name in ordered_group_names:
            items = grouped_selected[group_name]
            if items:
                regrouped_lines.append(f"{group_name}: {', '.join(items)}")

        if uncategorized:
            regrouped_lines.append(f"Additional: {', '.join(uncategorized)}")

        if regrouped_lines:
            return regrouped_lines

    return cleaned_rewritten or cleaned_base


def _normalize_personalized_resume_output(
    base_resume: Resume,
    personalized_resume: Resume,
) -> Resume:
    """
    Enforce the final output shape even if the model drifts from formatting
    instructions, without manually clipping bullet counts.
    """
    normalized_resume = personalized_resume.model_copy(deep=True)
    normalized_resume.summary = _paragraphize_summary(normalized_resume.summary)
    normalized_resume.skills = _normalize_skills_output(
        base_resume.skills,
        normalized_resume.skills,
    )

    return normalized_resume


async def generate_keyword_plan_with_llm(
    job_details: Dict[str, Any],
) -> ATSKeywordPlan:
    """
    First step of the new AI flow: extract the important hard and soft skills
    from the job description only.
    """
    prompt = f"""
    Extract the important resume keywords from this target software engineering job.

    Target job:
    {_serialize_job_for_prompt(job_details)}

    Return only two arrays:
    - hard_skills: named technical skills, tools, technologies, frameworks, platforms, databases, APIs, testing keywords, cloud/devops/security items, and concise named engineering methods from the job description
    - soft_skills: concise interpersonal, collaboration, ownership, communication, problem-solving, teamwork, leadership, or execution traits from the job description

    Rules:
    - Use only the job description.
    - Do not compare against the resume.
    - Do not explain anything.
    - Do not add extra fields.
    - Keep the lists concise, useful, ATS-friendly, and suitable for a software-engineering resume rewrite.
    - Prefer exact or near-exact job wording where helpful.
    - Remove obvious duplicates.
    - Hard skills must be resume-usable technical keywords, not broad responsibility phrases or generic capability statements.
    - Soft skills must be short, resume-usable people/work-style traits, not administrative process phrases.
    - Exclude office-productivity tools, generic computer-literacy items, language-fluency requirements, status-reporting phrases, escalation phrases, and other low-signal administrative wording unless they are clearly central to the engineering role.
    - Prefer concrete named technologies and concise named practices over long descriptive phrases.
    - Prefer roughly 8 to 16 hard_skills and 4 to 10 soft_skills.
    """

    system_prompt = """
    You are a senior resume-targeting strategist for software engineering resumes and a precise JSON generator.

    Your task is to convert a target job description into two keyword lists for resume rewriting.

    Rules:
    - Return exactly one valid JSON object matching the required schema.
    - Do not output markdown, commentary, or extra text.
    - Use only information present in the provided job details.
    - Treat the job details as the source of truth for target requirements.
    - Optimize specifically for software engineering, backend, platform, and full-stack job descriptions.
    - Return only hard_skills and soft_skills.
    - Reason privately and output only the final JSON.
    """

    llm_output = _generate_structured_output(
        prompt=prompt,
        system_prompt=system_prompt,
        response_model=ATSKeywordPlan,
        temperature=RESUME_GENERATION_TEMPERATURE,
    )
    _log_keyword_plan_response(job_id=job_details.get("job_id"), llm_output=llm_output.model_dump_json())
    return _postprocess_keyword_plan(llm_output)


def _apply_two_step_rewrite_to_resume(
    base_resume: Resume,
    rewrite_output: ATSResumeRewriteOutput,
) -> Resume:
    """
    Merge the second-step AI rewrite output into a copy of the base resume.
    """
    personalized_resume = base_resume.model_copy(deep=True)
    personalized_resume.summary = rewrite_output.summary
    personalized_resume.skills = rewrite_output.skills
    personalized_resume.experience = rewrite_output.experience
    personalized_resume.projects = rewrite_output.projects
    return personalized_resume


def _clean_header_title_candidate(raw_value: Any) -> str:
    text = " ".join(str(raw_value or "").strip().split())
    if not text:
        return ""

    text = re.sub(r"[\u2013\u2014]+", "-", text)
    text = re.sub(r"\s*/\s*", " / ", text)
    text = re.sub(r"\s*-\s*", " - ", text)

    noise_patterns = [
        r"\b(?:urgent(?:ly)?|hiring|opening(?:s)?|walk in drive|immediate joiners only)\b",
        r"\bfor the role of(?: an?|)\b",
        r"\b(?:job\s*id|req(?:uisition)?(?:\s*id)?|reference code)\b[:#\s-]*[a-z0-9-]*",
        r"₹\s?[\d,.\-a-zA-Z]+",
        r"\b(?:salary|sal|ctc|lpa)\b[:\s-]*[\d,.\-a-zA-Z]*",
        r"\b\d+\s*lpa\b",
        r"\b\d+\+?\s*(?:-|to)?\s*\d*\+?\s*years?(?:\s*of experience)?\b",
        r"\bexp(?:erience)?\b[:\s-]*\d+\s*(?:-|to)?\s*\d*\+?\s*years?",
        r"\bloc(?:ation)?\b[:\s-]*.*$",
    ]
    for pattern in noise_patterns:
        text = re.sub(pattern, " ", text, flags=re.IGNORECASE)

    text = re.sub(r"\(([^)]*)\)", lambda match: " " if re.search(r"(year|exp|location|hyderabad|bangalore|bengaluru|pune|chennai|delhi)", match.group(1), flags=re.IGNORECASE) else match.group(0), text)
    text = re.sub(r"\bat\s+[A-Za-z0-9&.,' -]+$", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s+", " ", text).strip(" ,-/")
    return text


def _derive_clean_header_title(raw_value: Any) -> str:
    cleaned = _clean_header_title_candidate(raw_value)
    if not cleaned:
        return ""

    normalized = cleaned.lower()
    role_patterns = [
        ("java full stack developer", "Java Full Stack Developer"),
        ("java fullstack developer", "Java Full Stack Developer"),
        ("java full stack engineer", "Java Full Stack Engineer"),
        ("java fullstack engineer", "Java Full Stack Engineer"),
        ("full stack developer", "Full Stack Developer"),
        ("full stack engineer", "Full Stack Engineer"),
        ("fullstack developer", "Full Stack Developer"),
        ("fullstack engineer", "Full Stack Engineer"),
        ("java backend developer", "Java Backend Developer"),
        ("java backend engineer", "Java Backend Engineer"),
        ("backend developer", "Backend Developer"),
        ("backend engineer", "Backend Engineer"),
        ("associate software engineer", "Associate Software Engineer"),
        ("developer associate", "Developer Associate"),
        ("associate java developer", "Associate Java Developer"),
        ("java software engineer", "Java Software Engineer"),
        ("software engineer ii", "Software Engineer II"),
        ("software engineer 2", "Software Engineer II"),
        ("software engineer i", "Software Engineer I"),
        ("member of technical staff", "Member of Technical Staff"),
        ("application developer", "Application Developer"),
        ("java developer", "Java Developer"),
        ("software engineer", "Software Engineer"),
        ("software developer", "Software Developer"),
        ("engineer", "Engineer"),
        ("developer", "Developer"),
    ]
    base_role = ""
    for pattern, display in role_patterns:
        if pattern in normalized:
            base_role = display
            break

    stack_modifiers = [
        ("java", "Java"),
        ("spring boot", "Spring Boot"),
        ("springboot", "Spring Boot"),
        ("react", "React"),
        ("angularjs", "AngularJS"),
        ("angular", "Angular"),
        ("node.js", "Node.js"),
        ("node", "Node.js"),
        ("microservice", "Microservices"),
        ("golang", "Golang"),
        ("go ", "Go"),
        ("sql", "SQL"),
    ]

    modifiers: list[str] = []
    if base_role:
        for pattern, display in stack_modifiers:
            if pattern in normalized and display.lower() not in base_role.lower() and display not in modifiers:
                modifiers.append(display)

        max_modifiers = 1 if any(token in base_role.lower() for token in ["full stack", "backend", "java"]) else 2
        if modifiers:
            return f"{base_role}, {' / '.join(modifiers[:max_modifiers])}"
        return base_role

    fallback_parts = re.split(r"\s+\|\s+|\s+-\s+|@", cleaned)
    for part in fallback_parts:
        candidate = " ".join(part.split()).strip(" ,-/")
        if candidate:
            return candidate[:80].strip()

    return cleaned[:80].strip()


def _normalize_header_title(raw_title: Any, rewritten_title: Any) -> str:
    cleaned_raw = _derive_clean_header_title(raw_title)
    if cleaned_raw:
        return cleaned_raw
    cleaned_rewritten = _derive_clean_header_title(rewritten_title)
    if cleaned_rewritten:
        return cleaned_rewritten
    return ""


async def rewrite_resume_with_keyword_plan(
    full_resume: Resume,
    job_details: Dict[str, Any],
    keyword_plan: ATSKeywordPlan,
) -> ATSResumeRewriteOutput:
    """
    Second step of the new AI flow: rewrite the resume using the AI-produced keyword plan.
    """
    prompt = f"""
    Rewrite the base resume for this target software engineering job using the keyword strategy.

    Target job:
    {_serialize_job_for_prompt(job_details)}

    Important hard and soft skills found in the first pass:
    {json.dumps(keyword_plan.model_dump(), indent=2)}

    Base resume:
    {_serialize_resume_for_prompt(full_resume)}

    Primary objective:
    Produce a stronger, more relevant, ATS-friendly software engineering resume that improves match quality for this job.

    Order of priority:
    1. role relevance and ATS match for the target job
    2. use the first-pass hard_skills and soft_skills throughout the resume in a natural, useful way
    3. clarity and strength of writing
    4. factual consistency for concrete experience details
    5. concision

    Rewrite instructions:

    General
    - Treat the base resume as the source of truth for concrete employment history, project history, dates, titles, companies, and measurable outcomes already written there.
    - CRITICAL INSTRUCTION: The first-pass hard_skills and soft_skills are user-verified skills. The candidate has actually performed these skills in real work. You are explicitly authorized and encouraged to incorporate them naturally into the resume even if they are not explicitly written or mentioned anywhere in the current base resume wording. These are not made-up skills — the user has confirmed the candidate possesses them.
    - Use the first-pass hard_skills and soft_skills as active keyword guidance while rewriting.
    - Do not ignore high-value hard_skills from the first pass. Use them where they improve ATS match and role relevance.
    - Do not ignore soft_skills from the first pass, but use them only through natural phrasing in the summary and bullet wording, not as standalone skills.
    - Substantially improve wording, relevance, and clarity where needed.
    - Do not mirror the base resume mechanically.
    - Do not fabricate companies, titles, dates, projects, technologies, scope, ownership, or achievements.
    - Do not spend effort deciding whether the candidate can do the job.
    - Use the job keywords naturally and uniformly across the resume without obvious stuffing.
    - Do not force every keyword into every section.
    - If an important hard skill from the first pass is missing from the current wording, prefer to incorporate it into the summary or skills section before dropping it entirely.
    - The resume should visibly reflect the first-pass keyword list, not just vaguely align with it.
    - Create a clean header_title for the resume subtitle based on the target job title.
    - The header_title should be human-readable and professional.
    - Remove noisy job-board text such as salaries, locations, "Hiring", IDs, company/internal prefixes, or awkward separators when they do not belong in a resume title.
    - Preserve meaningful stack or specialization signals when they are part of the real role title.
    - If the original target title already looks clean and professional, keep it close to the original meaning.
    - Keep the header_title concise. Prefer a nearby professional role title over a keyword-loaded rewrite.
    - Do not stuff the header_title with more than one or two stack signals.

    Summary
    - Write the professional summary as one concise paragraph.
    - It should sound like a strong software engineering candidate, not a generic profile.
    - Include role fit, core technical strengths, and business or engineering impact when evidenced by the base resume.
    - Use several of the highest-value hard_skills in the summary when that improves ATS match and readability.
    - Reflect relevant soft_skills naturally through the phrasing and emphasis of the summary when useful.
    - You may mention user-verified first-pass skills in the summary even when they are not explicitly stated in the current base resume wording.
    - Keep it tight and readable.

    Skills
    - Present skills in meaningful grouped categories, not as one long flat list.
    - The skills section must contain technical skills only: languages, frameworks, libraries, platforms, databases, APIs, security/auth technologies, testing tools, cloud/devops tools, and named engineering methodologies or patterns.
    - Do not place soft skills in the skills section. Do not add categories like "Soft Skills", "Interpersonal Skills", or similar.
    - Use soft_skills in the professional summary and in experience/project wording instead, through natural sentences rather than standalone entries.
    - The skills section must not contain narrative phrases, responsibility phrases, business-impact phrases, or broad capability labels.
    - Prefer concise named items over descriptive phrases.
    - Engineering practices are allowed only when they are concise and named.
    - Do not turn whole resume themes into skills. If something reads like a responsibility, strength, working style, or narrative phrase instead of a named tool, technology, platform, or concise practice, keep it out of the skills section.
    - Treat the skills section as the primary place to cover first-pass hard_skills.
    - Keep the skills section broad enough for ATS, but focused on the strongest relevant technologies, platforms, and named engineering practices for the target job.
    - Prefer to include important first-pass hard_skills in the skills section even if the current base wording under-emphasizes or does not mention them at all, because those first-pass skills are user-verified.
    - Remove weak, redundant, or low-signal items when they dilute relevance.
    - Avoid repeating the same concept across multiple categories unless there is a very strong reason.
    - Keep each grouped line concise and scannable rather than cramming too many loosely related items into one category.

    Experience
    - Keep the same number and order of experience items.
    - Keep each item's job_title, company, location, start_date, and end_date unchanged.
    - For each role, write exactly 5 strong bullet-ready lines.
    - Each bullet should aim to communicate some combination of action, scope, technical stack, and result.
    - Prefer specific, meaningful bullets over generic responsibility statements.
    - Use metrics only when evidenced by the base resume.
    - De-emphasize weaker or less relevant details if stronger material exists.
    - Use hard_skills from the first pass when they fit naturally with the role and improve ATS match.
    - Use soft_skills from the first pass by reflecting them naturally through the bullet wording rather than listing them explicitly.
    - Do not invent fake experience claims. If a first-pass skill is user-verified but not clearly tied to a specific job bullet in the base resume, prefer to surface it in the summary or skills section instead of attaching it to a fabricated work claim.

    Projects
    - Keep the same number and order of project items.
    - Keep each project's name and link unchanged.
    - For each project, write 4 to 5 strong bullet-ready lines.
    - Highlight the most relevant engineering work, architecture, implementation, integrations, and outcomes evidenced by the base resume.
    - You may refine each project's technologies list by removing weaker or less relevant items already present in the base resume.
    - Use hard_skills from the first pass when they fit naturally with the project and improve ATS match.
    - Use soft_skills from the first pass through natural phrasing in the project bullets rather than explicit soft-skill labels.
    - Do not invent fake project claims. If a first-pass skill is user-verified but not clearly tied to a specific project in the base resume, prefer to use it in the summary or skills section instead of fabricating project details.

    Keyword behavior
    - Distribute hard_skills across summary, skills, experience, and projects where they fit naturally.
    - Use soft_skills only in the summary, experience, and projects. Never place soft_skills in the skills section.
    - Prefer natural repetition over obvious repetition.
    - Use job-description wording selectively when useful.
    - It is acceptable to add first-pass hard_skills to the summary or skills section to improve ATS coverage, even when they are not strongly emphasized in the current base wording, because those skills are user-verified.
    - Do not add a concrete technology, implementation detail, metric, or environment claim to experience or project bullets unless it is evidenced by the base resume.
    - Avoid keyword stuffing, awkward phrasing, and buzzword clustering.

    Output scope
    - Return only the fields requested by the schema: header_title, summary, skills, experience, and projects.
    """

    system_prompt = """
    You are a senior software-engineering resume writer and a precise JSON generator.

    Your task is to rewrite a base resume for a target software engineering job using a job-keyword plan.

    Rules:
    - Return exactly one valid JSON object matching the required schema.
    - Do not output markdown, commentary, or extra text.
    - Base resume facts are the source of truth for concrete jobs, projects, dates, companies, and measurable claims.
    - The keyword plan contains user-verified hard_skills and soft_skills that are allowed to be used in the rewrite, even if the current base resume wording does not mention every one of them explicitly.
    - Soft skills must never appear as standalone entries inside the skills section; they should appear only through natural wording in the summary or bullets.
    - The skills section should contain concise named technologies, tools, platforms, databases, APIs, security items, testing tools, and named engineering practices only.
    - The header_title must be a cleaned, professional version of the target job title suitable for a resume subtitle.
    - Prefer a short, natural role title for header_title. Keep it close to the actual role name instead of inventing a keyword-heavy title.
    - Never invent fake experience or project claims that are not evidenced by the base resume.
    - Optimize for both ATS match and human credibility.
    - Write like an experienced real resume writer: concise, specific, relevant, and natural.
    - Avoid robotic wording, buzzword stacking, and generic filler.
    - Reason privately and output only the final JSON.
    """

    return _generate_structured_output(
        prompt=prompt,
        system_prompt=system_prompt,
        response_model=ATSResumeRewriteOutput,
        temperature=RESUME_GENERATION_TEMPERATURE,
    )


async def personalize_resume_with_two_step_ai(
    base_resume_details: Resume,
    job_details: Dict[str, Any],
) -> tuple[Resume, str]:
    """
    Two-step AI-only resume generation flow:
    1. Generate keyword plan from the job description.
    2. Rewrite the resume using that plan.
    """
    job_id = job_details.get("job_id")
    logging.info(f"Generating keyword plan for job_id: {job_id}")
    keyword_plan = await generate_keyword_plan_with_llm(
        job_details=job_details,
    )
    logging.info(f"Rewriting resume with AI keyword plan for job_id: {job_id}")
    rewritten_resume = await rewrite_resume_with_keyword_plan(
        full_resume=base_resume_details,
        job_details=job_details,
        keyword_plan=keyword_plan,
    )

    personalized_resume_data = _apply_two_step_rewrite_to_resume(
        base_resume=base_resume_details,
        rewrite_output=rewritten_resume,
    )
    personalized_resume_data = _normalize_personalized_resume_output(
        base_resume=base_resume_details,
        personalized_resume=personalized_resume_data,
    )

    for section_name in ("experience", "projects"):
        original_content = getattr(base_resume_details, section_name)
        customized_content = getattr(personalized_resume_data, section_name)
        is_valid, reason = validate_customization(
            section_name,
            original_content,
            customized_content,
            allow_project_technology_changes=(section_name == "projects"),
        )
        if not is_valid:
            raise ValueError(
                f"Two-step AI validation failed for {section_name}: {reason}"
            )
        logging.info(
            f"Two-step AI validation passed for section {section_name}. Reason: {reason}"
        )

    header_title = _normalize_header_title(
        raw_title=job_details.get("job_title"),
        rewritten_title=rewritten_resume.header_title,
    )

    return personalized_resume_data, header_title


async def personalize_section_with_llm(
    section_name: str,
    section_content: Any,
    full_resume: Resume,
    job_details: Dict[str, Any]
    ) -> Any:
    """
    Uses the configured LLM to personalize a specific section of the resume for the given job.
    """
    if not section_content or section_content == "NA":
        logging.warning(f"Skipping personalization for empty or 'NA' section: {section_name}")
        return section_content # Return original if empty or NA

    output_model_map = {
        "summary": (SummaryOutput, "summary"),
        "skills": (SkillsOutput, "skills"),
        "experience": (SingleExperienceOutput, "experience"),
        "projects": (SingleProjectOutput, "project"),
    }

    if section_name not in output_model_map:
        logging.error(f"Unsupported section_name for LLM personalization: {section_name}")
        return section_content # Fallback for unsupported sections

    OutputModel, output_key = output_model_map[section_name]

    # Prepare full resume context string (excluding the section being personalized)
    resume_context_dict = full_resume.model_dump(exclude={section_name})
    # Limit context size if necessary, especially for large fields like experience descriptions
    # For simplicity here, we convert the whole dict (minus the current section) to string
    resume_context = json.dumps(resume_context_dict, indent=2)

    # Convert section_content to JSON serializable format if it's a list of models
    if isinstance(section_content, list) and section_content and hasattr(section_content[0], 'model_dump'):
        serializable_section_content = [item.model_dump() for item in section_content]
    else:
        serializable_section_content = section_content # Assume it's already serializable (like str or list[str])

    prompts = []

    # Construct the prompt based on the section
    prompt_intro = f"""
    **Task:** Enhance the specified resume section for the target job application.

    **Target Job**
    - Title: {job_details['job_title']}
    - Company: {job_details['company']}
    - Seniority Level: {job_details['level']}
    - Job Description: {job_details['description']}

    ---

    **Full Resume Context (excluding the section being edited):**
    {resume_context}

    **Resume Section to Enhance:** {section_name}
    """

    system_prompt = f"""
    You are an expert resume writer and a precise JSON generation assistant.
    Your primary function is to enhance specified sections of a resume to better align with a target job description, based on the provided resume context and original section content.

    **CRITICAL OUTPUT REQUIREMENTS:**
    1.  You MUST ALWAYS output a single, valid JSON object.
    2.  Your entire response MUST be *only* the JSON object.
    3.  Do NOT include any introductory text, explanations, apologies, markdown formatting (like ```json or ```), or any text outside of the JSON structure itself.

    **CORE RESUME WRITING PRINCIPLES:**
    1.  **Adhere to Instructions:** Meticulously follow all specific instructions provided in the user prompt for the given section.
    2.  **No Fabrication:** NEVER invent new information, skills, projects, job titles, or responsibilities not explicitly found in the original resume materials. Rephrasing and emphasizing existing facts is allowed; fabrication is strictly forbidden.
    3.  **Relevance:** Focus on aligning the candidate's existing experience and skills with the target job.
    4.  **Fact-Based:** All enhancements must be grounded in the provided "Full Resume Context" or "Original Content of This Section."

    You will receive the target job details, full resume context (excluding the section being edited), the specific section name to enhance, its original content, and section-specific instructions. Follow the output format example provided in the user prompt for the structure of the JSON.
    """

    specific_instructions = ""

    if(section_name == "summary"):
        specific_instructions = f"""
        **Original Content of This Section:**
        {json.dumps(serializable_section_content, indent=2)}

        ---
        **Instructions:**
        - Rewrite **only** the summary to be concise, impactful, and highly relevant to the Target Job.
        - Return the summary as one concise paragraph, not bullet points.
        - The paragraph should communicate profile, core stack, technical strengths, business/product impact, and role fit.
        - **CRITICAL: The core professional identity and experience level (e.g., "IT Support and Cybersecurity Specialist with 4+ years") from the "Original Content of This Section" MUST be preserved.** Do NOT change the candidate's stated primary role or invent a new one like "Frontend Engineer" if it wasn't their original title. The goal is to make their *existing* role and experience sound relevant, not to misrepresent their primary job function.
        - Highlight 2-3 key qualifications or experiences from the "Full Resume Context" or "Original Content of This Section" that ALIGN with the "Job Description." These highlighted aspects should be FACTUALLY based on the provided resume materials.
        - Use strong action verbs and keywords from the "Job Description" where appropriate, but ONLY when describing actual experiences or skills present in the resume.
        - You do not have to mirror the original wording. Improve phrasing aggressively while staying factually grounded.
        - **ABSOLUTELY DO NOT INVENT new information, skills, projects, job titles, or responsibilities not explicitly found in the original resume materials.** Rephrasing and emphasizing existing facts is allowed; fabrication is not.
        - For example, if the original summary says "IT Support Specialist who developed a tool using React," do NOT change this to "Experienced Frontend Engineer." Instead, you might say "IT Support Specialist with experience developing user-facing tools using React, such as Click4IT..."
        ---
        **Expected JSON Output Structure:** {{"summary": "A dynamic and results-oriented Software Engineer with X years of experience..."}}
        """
        prompt = prompt_intro + specific_instructions

        prompts.append(prompt)

    elif(section_name == "experience"):
        for exp_item_content  in serializable_section_content:
            specific_instructions = f"""
             **Original Content of This Specific Experience Item:**
            {json.dumps(exp_item_content, indent=2)}

            ---
            **Instructions for this experience item:**
            - Enhance the 'description' field ONLY. All other fields (job_title, company, dates, etc.) MUST remain UNCHANGED within this specific experience item.
            - Integrate relevant skills from the "Full Resume Context" (especially any explicit skills list) and keywords from the "Target Job Description" naturally into the description.
            - Show HOW these skills were applied and what the IMPACT or achievement was. Quantify achievements if possible, based on the original content.
            - Return the description as exactly 5 concise bullet-ready lines separated by newline characters.
            - Prefer the strongest and most relevant bullets rather than preserving every lower-value detail from the original wording.
            - You may combine or reshape original points into stronger bullets as long as the result stays truthful to the resume evidence.
            - Example: Instead of "Used Python for scripting," try "Automated data processing tasks using Python scripts, reducing manual effort by 20%."
            - Do NOT invent skills or experiences. Stick to the candidate's actual background as reflected in the provided materials.
            ---
            **Expected JSON Output Structure:** {{"experience": {{"job_title": "Original Job Title", "company": "Original Company", "start_date": "Original Start Date", "end_date": "Original End Date", "description": "Enhanced description...", "location": "Original Location (if present)"}}}}
            """ 
            prompt = prompt_intro + specific_instructions
            prompts.append(prompt)

    elif(section_name == "projects"):
        for project_item_content  in serializable_section_content:
            specific_instructions = f"""
            **Original Content of This Specific Project Item:**
            {json.dumps(project_item_content, indent=2)}

            ---
            **Instructions for this project item:**
            - Enhance the 'description' field ONLY. All other fields (name, technologies, link, etc.) MUST remain UNCHANGED within this specific project item.
            - Integrate relevant skills from the "Full Resume Context" and keywords from the "Target Job Description" naturally into the description.
            - Show HOW these skills were applied.
            - Return the description as 4 or 5 concise bullet-ready lines separated by newline characters.
            - Prefer the most impressive and most job-relevant outcomes instead of trying to preserve every lower-value detail.
            - You may combine or reshape original points into stronger bullets as long as the result stays truthful to the resume evidence.
            - Example: Instead of "Project using React," try "Developed a responsive UI for [Project Purpose] using React and Redux, improving user engagement."
            - Do NOT invent skills or experiences.
            ---
            **Expected JSON Output Structure (for this single project item):** {{"project": {{"name": "Original Project Name", "technologies": ["Tech1", "Tech2"], "description": "Enhanced description...", "link": "Original Link (if present)"}}}}
            """
            prompt = prompt_intro + specific_instructions 
            prompts.append(prompt)

    elif(section_name == "skills"):
        specific_instructions = f"""
        **Original Content of This Section (Candidate's Initial Skills List):**
        {json.dumps(serializable_section_content, indent=2)}

        ---
        **Instructions for Generating the Curated Skills List:**

        **1. Identify Candidate's Actual Skills:**
        - Review the 'Full Resume Context' (which includes the candidate's summary, all experience descriptions, and all project descriptions/technologies).
        - Also, review the 'Original Content of This Section (Candidate's Initial Skills List)' provided above.
        - Compile a temporary list of all skills that are *explicitly written and mentioned* in these specific parts of the resume materials.
        - **CRITICAL RULE: DO NOT infer, assume, or invent any skills. If a skill is not literally written down in the provided resume materials (summary, experience, projects, original skills list), you MUST NOT include it in your temporary list.** For example, if the resume states "developed responsive web applications," do not assume "JavaScript" or "React" unless "JavaScript" or "React" are explicitly written elsewhere as skills or technologies used.

        **2. Select and Refine for the Target Job and Conciseness:**
        - From your temporary list of the candidate's *actual, explicitly mentioned* skills, select only those that are most relevant to the 'Target Job Description'.
        - Return the skills section as grouped category lines like "Languages: ...", "Backend: ...", or "Cloud / DevOps: ...", not as a long flat list of individual skills.
        - If the original skills section is written in grouped categories like "Languages: ..." or "Cloud / DevOps: ...", preserve that style unless a cleaner grouped structure is clearly better.
        - Keep the list broad and ATS-friendly. Preserve a strong range of relevant supported skills instead of shrinking it to a tiny shortlist.
        - When the resume supports it, prefer roughly 6 to 12 grouped lines or roughly 15 to 30 individual skills. If fewer are truly supported and relevant, list only those.
        - Prioritize skills that are directly mentioned in the 'Target Job Description' AND are confirmed to be in the candidate's actual, explicitly written skills.
        - Remove or de-emphasize lower-value items when they dilute focus. Examples can include vague AI terms like "AI summarization" or generic phrases like "production support" when more specific skills are available.
        - Avoid redundancy. If a skill is a more general version of another already included (e.g., "Cloud Computing" vs. "AWS"), prefer the more specific one if relevant and explicitly mentioned, or the one that best matches the job description.
        - This skills list should still be readable, but do not throw away meaningful skills just to force a short list.

        ---
        **Expected JSON Output Structure:** {{"skills": ["Python", "JavaScript", "React", "Node.js", "AWS (EC2, S3, Lambda)", "Docker", "Kubernetes", "Agile Methodologies", "CI/CD Pipelines", "SQL", "Git"]}}
        """
        prompt = prompt_intro + specific_instructions 
        prompts.append(prompt)

    logging.info(f"Number of prompts: {len(prompts)}")

    responses = []
    for prompt in prompts:
        logging.info(f"Sending prompt to LLM for section: {section_name} with structured output schema.")

        # messages = [
        # {'role': 'system', 'content': 'You are an expert resume writer. Only rewrite or generate the specified resume section. Never return the full resume or any unrelated content. Output strictly in the JSON format defined by the provided schema. Do not add any explanatory text before or after the JSON object.'},
        # {'role': 'user', 'content': prompt}
        # ]

        try:
            llm_output = _generate_structured_output(
                prompt=prompt,
                system_prompt=system_prompt,
                response_model=OutputModel,
                temperature=RESUME_GENERATION_TEMPERATURE,
            )
            
            logging.info(f"Received response from LLM for section: {section_name}")
            responses.append(llm_output)

        except Exception as e:
            logging.error(f"Error calling LLM or processing response for section {section_name}: {e}")
            # Fallback: return original content if LLM call fails
            return section_content

    logging.info(f"Received {len(responses)} responses from LLM for section: {section_name}")

    if(section_name == "summary"):
        return getattr(responses[0], output_key)
    elif(section_name == "skills"):
        return getattr(responses[0], output_key)
    elif(section_name == "experience"):
        experience_list = []
        for response in responses:
            experience_list.append(getattr(response, output_key))
        return experience_list
    elif(section_name == "projects"):
        project_list = []
        for response in responses:
            project_list.append(getattr(response, output_key))
        return project_list

def validate_customization(
    section_name: str, 
    original_content: Any, 
    customized_content: Any,
    allow_project_technology_changes: bool = False,
) -> tuple[bool, str]:
    """
    Programmatically validates that the customized content hasn't altered
    core facts like job titles, dates, companies, or project details.
    """
    if not original_content or not customized_content:
        return True, "Empty content, nothing to validate."

    if section_name == "experience":
        # Ensure we have lists of the same length
        if not isinstance(original_content, list) or not isinstance(customized_content, list):
            return False, "Experience content is not a list."
        if len(original_content) != len(customized_content):
            return False, f"Experience count changed from {len(original_content)} to {len(customized_content)}."

        for orig, cust in zip(original_content, customized_content):
            # Extract dict if it's a Pydantic model
            o_dict = orig.model_dump() if hasattr(orig, 'model_dump') else orig
            c_dict = cust.model_dump() if hasattr(cust, 'model_dump') else cust

            # Check core fields haven't changed
            for field in ['job_title', 'company', 'location', 'start_date', 'end_date']:
                o_val = str(o_dict.get(field, '')).strip()
                c_val = str(c_dict.get(field, '')).strip()
                # Use case-insensitive comparison to avoid false positives on minor formatting
                if o_val.lower() != c_val.lower():
                    return False, f"Core experience field '{field}' was changed from '{o_val}' to '{c_val}'."
        
        return True, "Experience validation passed."

    elif section_name == "projects":
        if not isinstance(original_content, list) or not isinstance(customized_content, list):
            return False, "Projects content is not a list."
        if len(original_content) != len(customized_content):
            return False, f"Projects count changed from {len(original_content)} to {len(customized_content)}."

        for orig, cust in zip(original_content, customized_content):
            o_dict = orig.model_dump() if hasattr(orig, 'model_dump') else orig
            c_dict = cust.model_dump() if hasattr(cust, 'model_dump') else cust

            for field in ['name', 'link']:
                o_val = str(o_dict.get(field, '')).strip()
                c_val = str(c_dict.get(field, '')).strip()
                if o_val.lower() != c_val.lower():
                    return False, f"Core project field '{field}' was changed from '{o_val}' to '{c_val}'."

            if not allow_project_technology_changes:
                o_tech = o_dict.get('technologies', [])
                c_tech = c_dict.get('technologies', [])
                if sorted([str(t).lower().strip() for t in o_tech]) != sorted([str(t).lower().strip() for t in c_tech]):
                     return False, f"Technologies list was changed from '{o_tech}' to '{c_tech}'."

        return True, "Projects validation passed."
        
    # For skills and summary, we trust the LLM since the prompt restricts fabrication
    # and they don't have strictly rigid structures like experience/projects.
    return True, f"Validation passed (no strict checks for {section_name})."


# --- Main Processing Logic ---
async def process_job(
    job_details: Dict[str, Any],
    base_resume_details: Resume,
    generation_flow: str = "legacy",
    email_override: str | None = None,
):
    """
    Processes a single job: personalizes resume, generates PDF, uploads, updates status.
    """
    job_id = job_details.get("job_id")
    if not job_id:
        logging.error("Job details missing job_id.")
        return

    logging.info(f"--- Starting processing for job_id: {job_id} ---")

    try:
        # 1. Personalize Resume Sections
        header_title = str(job_details.get("job_title") or "").strip()
        if generation_flow == "two_step_ai":
            logging.info(
                f"Using two-step AI resume generation flow for job_id: {job_id}"
            )
            personalized_resume_data, header_title = await personalize_resume_with_two_step_ai(
                base_resume_details=base_resume_details,
                job_details=job_details,
            )
        else:
            logging.info(
                f"Using legacy section-by-section resume generation flow for job_id: {job_id}"
            )
            personalized_resume_data = base_resume_details.model_copy(deep=True)
            sections_to_personalize = {
                "summary": base_resume_details.summary,
                "experience": base_resume_details.experience,
                "projects": base_resume_details.projects,
                "skills": base_resume_details.skills,
            }

            sleep_time = config.LLM_REQUEST_DELAY_SECONDS

            for section_name, section_content in sections_to_personalize.items():
                if section_content and section_content != "NA":
                    logging.info(f"Waiting for {sleep_time} seconds before next request...")
                    time.sleep(sleep_time)

                    logging.info(f"Personalizing section: {section_name} for job_id: {job_id}")
                    personalized_content = await personalize_section_with_llm(
                        section_name,
                        section_content,
                        base_resume_details,
                        job_details
                    )

                    logging.info(f"Validating customization for section: {section_name} for job_id: {job_id}")
                    is_valid, reason = validate_customization(
                        section_name,
                        section_content,
                        personalized_content
                    )

                    if is_valid:
                        logging.info(f"Customization for section {section_name} is valid. Reason: {reason}")
                        setattr(personalized_resume_data, section_name, personalized_content)
                    else:
                        logging.warning(f"VALIDATION FAILED for section {section_name} for job_id {job_id}. Reason: {reason}")
                        logging.warning(f"Falling back to original {section_name} content for job_id {job_id}.")

                    logging.info(f"Finished processing section: {section_name} for job_id: {job_id}")
                else:
                    logging.info(f"Skipping empty section: {section_name} for job_id: {job_id}")

        personalized_resume_data = _normalize_personalized_resume_output(
            base_resume=base_resume_details,
            personalized_resume=personalized_resume_data,
        )
        personalized_resume_data = _apply_job_contact_overrides(
            personalized_resume_data,
            job_details,
            email_override=email_override,
        )

        # 2. Generate PDF
        logging.info(f"Generating PDF for job_id: {job_id}")
        try:
            raw_header_title = str(job_details.get("job_title") or "").strip()
            if raw_header_title and header_title != raw_header_title:
                logging.info(
                    "LLM-normalized resume header title for job_id %s: '%s' -> '%s'",
                    job_id,
                    raw_header_title,
                    header_title,
                )
            pdf_bytes = pdf_generator.create_resume_pdf(
                personalized_resume_data,
                header_title=header_title,
            )
            if not pdf_bytes:
                raise ValueError("PDF generation returned empty bytes.")
            pdf_is_valid, pdf_issues = resume_validator.validate_generated_resume_pdf(
                pdf_bytes=pdf_bytes,
                resume_data=personalized_resume_data,
                header_title=header_title,
            )
            if not pdf_is_valid:
                raise ValueError(
                    "PDF validation failed: " + "; ".join(pdf_issues)
                )
            logging.info(f"PDF generation complete for job_id: {job_id}")
        except Exception as e:
            logging.error(f"Failed to generate PDF for job_id {job_id}: {e}")
            # Skip to the next job if PDF generation fails
            return # Stop processing this job

        # 3. Upload PDF to Supabase Storage
        destination_path = _build_resume_filename(
            job_id=str(job_id),
            company=job_details.get("company"),
        )
        logging.info(f"Uploading PDF to {destination_path} for job_id: {job_id}")
        resume_path = supabase_utils.upload_customized_resume_to_storage(pdf_bytes, destination_path)

        if not resume_path:
            logging.error(f"Failed to upload resume PDF for job_id: {job_id}")
            # Skip updating the job record if upload fails
            return # Stop processing this job

        logging.info(f"Successfully uploaded PDF for job_id: {job_id}. Path: {resume_path}")

        # 4. Add Customized Resume to Supabase
        logging.info("Adding customized resume to Supabase")
        customized_resume_id = supabase_utils.save_customized_resume(
            personalized_resume_data,
            resume_path,
            header_title=header_title,
        )


        # 4. Update Job Record in Supabase
        logging.info(f"Updating job record for job_id: {job_id} with resume path.")
        # Optionally set a new status like "resume_generated" or "ready_to_apply"
        update_success = supabase_utils.update_job_with_resume_link(job_id, customized_resume_id, new_status="resume_generated")

        if update_success:
            logging.info(f"Successfully updated job record for job_id: {job_id}")
        else:
            logging.error(f"Failed to update job record for job_id: {job_id}")

        logging.info(f"--- Finished processing for job_id: {job_id} ---")

    except Exception as e:
        logging.error(f"An unexpected error occurred while processing job_id {job_id}: {e}", exc_info=True)
        # Log the error but continue to the next job

async def run_job_processing_cycle(
    limit_override: int | None = None,
    target_job_id: str | None = None,
    force_regenerate: bool = False,
    generation_flow: str | None = None,
    email_override: str | None = None,
):
    """
    Fetches top jobs and processes them one by one.
    """
    logging.info("Starting new job processing cycle...")
    selected_generation_flow = generation_flow or getattr(
        config,
        "RESUME_GENERATION_FLOW",
        "legacy",
    )
    if selected_generation_flow not in {"legacy", "two_step_ai"}:
        raise SystemExit(
            f"Unsupported resume generation flow: {selected_generation_flow}. "
            "Use 'legacy' or 'two_step_ai'."
        )
    logging.info(f"Selected resume generation flow: {selected_generation_flow}")

    # 1. Retrieve Base Resume Details from local source of truth (with Supabase fallback)
    base_resume_details = _load_base_resume_details()
    if not base_resume_details:
        logging.error("Could not load valid base resume details. Aborting cycle.")
        return

    # 2. Fetch Top Jobs to Process
    jobs_limit = limit_override if limit_override is not None else config.JOBS_TO_CUSTOMIZE_PER_RUN

    if force_regenerate and not target_job_id:
        raise SystemExit("--force-regenerate requires --job-id.")

    if target_job_id:
        if limit_override is not None:
            logging.info(
                f"--job-id {target_job_id} provided. Ignoring --limit {limit_override} and targeting this job directly."
            )
        logging.info(f"Manual job selection detected for job_id {target_job_id}.")
        job_record = supabase_utils.get_job_by_id(target_job_id)
        if not job_record:
            logging.error(f"Could not find job_id {target_job_id}.")
            return

        existing_resume_id = str(job_record.get("customized_resume_id") or "").strip()
        if existing_resume_id and not force_regenerate:
            logging.info(
                f"job_id {target_job_id} already has a generated resume ({existing_resume_id}). "
                "Re-run with --force-regenerate to create a new one and relink the job."
            )
            return

        if existing_resume_id and force_regenerate:
            logging.info(
                f"Force regenerate enabled for job_id {target_job_id}. "
                "A new customized resume will be created and linked to this job."
            )

        await process_job(job_record, base_resume_details, selected_generation_flow, email_override=email_override)
        logging.info("Finished job processing cycle.")
        return

    manual_limit_mode = limit_override is not None
    min_score_for_custom = int(getattr(config, "MIN_SCORE_FOR_CUSTOM_RESUME", 50))
    effective_min_score = 0 if manual_limit_mode else min_score_for_custom
    top_percent = 0 if manual_limit_mode else (getattr(config, "JOBS_TO_CUSTOMIZE_TOP_PERCENT", 0) or 0)

    if manual_limit_mode:
        logging.info(
            f"Manual limit override detected ({limit_override}). "
            "Selecting the next highest not-yet-generated jobs regardless of MIN_SCORE_FOR_CUSTOM_RESUME."
        )

    if top_percent > 0:
        eligible_count = supabase_utils.count_jobs_for_resume_generation_candidates(min_score=min_score_for_custom)
        if eligible_count > 0:
            jobs_limit = max(1, math.ceil((eligible_count * top_percent) / 100.0))
            logging.info(
                f"Top-percent mode enabled: {top_percent}% of {eligible_count} eligible jobs => {jobs_limit} jobs "
                f"(min score {min_score_for_custom})."
            )
        else:
            jobs_limit = 0
            logging.info("Top-percent mode enabled but no eligible jobs found.")

    logging.info(f"Fetching top {jobs_limit} scored jobs to apply for...")
    jobs_to_process = supabase_utils.get_top_scored_jobs_for_resume_generation(limit=jobs_limit)
    if jobs_to_process and effective_min_score > 0:
        filtered_jobs = []
        for job in jobs_to_process:
            score_val = job.get("resume_score")
            if score_val is None:
                continue
            try:
                if int(score_val) >= effective_min_score:
                    filtered_jobs.append(job)
            except (TypeError, ValueError):
                continue
        jobs_to_process = filtered_jobs

    if not jobs_to_process:
        if effective_min_score > 0:
            logging.info(
                f"No new jobs found to process in this cycle with score >= {effective_min_score}."
            )
        else:
            logging.info("No new jobs found to process in this cycle.")
        return

    if effective_min_score > 0:
        logging.info(
            f"Found {len(jobs_to_process)} jobs to process with score >= {effective_min_score}."
        )
    else:
        logging.info(f"Found {len(jobs_to_process)} jobs to process.")

    # 3. Process each job sequentially to avoid overwhelming LLM/resources
    for job_details in jobs_to_process:
        await process_job(job_details, base_resume_details, selected_generation_flow, email_override=email_override)

    logging.info("Finished job processing cycle.")

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Generate customized resumes for top scored jobs.")
    parser.add_argument(
        "--limit",
        type=int,
        help="Override how many top jobs to customize in this run.",
    )
    parser.add_argument(
        "--job-id",
        help="Generate a resume for one specific job_id. Ignores --limit and score threshold filters.",
    )
    parser.add_argument(
        "--force-regenerate",
        action="store_true",
        help="With --job-id, create a new resume even if that job already has one linked.",
    )
    parser.add_argument(
        "--flow",
        choices=["legacy", "two_step_ai"],
        help="Choose which resume generation flow to run. Defaults to config.RESUME_GENERATION_FLOW.",
    )
    parser.add_argument(
        "--email-override",
        help="Optional email override for this generation run. If omitted, the default resume email is used.",
    )
    return parser


# --- Script Entry Point ---
if __name__ == "__main__":
    logging.info("Script started.")
    try:
        args = build_parser().parse_args()
        if args.limit is not None and args.limit <= 0:
            raise SystemExit("--limit must be a positive integer.")
        asyncio.run(
            run_job_processing_cycle(
                limit_override=args.limit,
                target_job_id=args.job_id,
                force_regenerate=args.force_regenerate,
                generation_flow=args.flow,
                email_override=args.email_override,
            )
        )
        logging.info("Rresume processing completed successfully.")
    except Exception as e:
        logging.error(f"Error during task execution: {e}", exc_info=True)
