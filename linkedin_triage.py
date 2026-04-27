from __future__ import annotations

import json
import re
from typing import Any

from linkedin_pipeline_models import LinkedInTriageResponse


TRIAGE_SYSTEM_PROMPT = (
    "You are a strict JSON classifier. "
    "Return only compact JSON. "
    "Classify each job as keep, borderline, or reject. "
    "Prefer backend or full-stack roles for about 2 years experience. "
    "Reject frontend-only, QA, SDET, support, mobile-only, analyst, sales, or clearly senior leadership roles. "
    "Do not output explanations, markdown, or extra text."
)


def build_candidate_profile_summary() -> str:
    return (
        "Candidate profile:\n"
        "- Around 2 years of software development experience\n"
        "- Looking for backend or full-stack software roles\n"
        "- Not interested in frontend-only roles\n"
        "- Open to Java, Python, C#, .NET, Go, Node.js, and generic software/backend stacks\n"
        "- Good fits include Software Engineer, Software Developer, Backend Engineer, Backend Developer, Full Stack Engineer, Full Stack Developer\n"
        "- Preferred cities in order: Hyderabad, Bengaluru, Chennai, Mumbai\n"
        "- LinkedIn freshness target is last 24 hours\n"
    )


def build_triage_prompt(jobs: list[dict[str, Any]]) -> str:
    lines: list[str] = [build_candidate_profile_summary(), "", "Jobs:"]
    for job in jobs:
        description_excerpt = " ".join(str(job.get("description") or "").split())[:320]
        lines.append(
            "\n".join(
                [
                    f"- id: {job.get('job_id')}",
                    f"  title: {job.get('job_title') or 'Unknown'}",
                    f"  company: {job.get('company') or 'Unknown'}",
                    f"  location: {job.get('location') or 'Unknown'}",
                    f"  level: {job.get('level') or 'Unknown'}",
                    f"  desc: {description_excerpt or 'N/A'}",
                ]
            )
        )

    lines.append(
        "\nReturn JSON only exactly like this:\n"
        '{\n'
        '  "jobs": [\n'
        '    {"job_id": "123", "decision": "keep"}\n'
        "  ]\n"
        "}\n"
    )
    return "\n".join(lines)


def build_score_prompt(job: dict[str, Any], resume_text: str) -> str:
    description_excerpt = " ".join(str(job.get("description") or "").split())[:5000]
    return (
        "You are scoring one job for one candidate.\n"
        "Return only strict JSON with keys score and experience_required.\n"
        "Score from 0 to 100.\n"
        "Prefer realistic backend or full-stack fit for around 2 years experience.\n"
        "Penalize frontend-only, clearly over-senior, unrelated, or low-overlap roles.\n\n"
        f"Candidate resume:\n{resume_text}\n\n"
        f"Job title: {job.get('job_title') or 'Unknown'}\n"
        f"Company: {job.get('company') or 'Unknown'}\n"
        f"Location: {job.get('location') or 'Unknown'}\n"
        f"Level: {job.get('level') or 'Unknown'}\n"
        f"Job description:\n{description_excerpt}\n\n"
        'Return JSON only: {"score": 0, "experience_required": "Not stated"}'
    )


def parse_triage_response(raw_response: str) -> LinkedInTriageResponse:
    text = str(raw_response or "").strip()
    if not text:
        return LinkedInTriageResponse()

    fenced = re.search(r"```(?:json)?\s*([\s\S]*?)```", text, flags=re.IGNORECASE)
    if fenced:
        text = fenced.group(1).strip()

    obj_match = re.search(r"\{[\s\S]*", text)
    if obj_match:
        text = obj_match.group(0)

    decoder = json.JSONDecoder()
    parsed_obj, _ = decoder.raw_decode(text)
    return LinkedInTriageResponse.model_validate(parsed_obj)


def parse_score_response(raw_response: str) -> tuple[int | None, str]:
    text = str(raw_response or "").strip()
    if not text:
        return None, "Not stated"

    fenced = re.search(r"```(?:json)?\s*([\s\S]*?)```", text, flags=re.IGNORECASE)
    if fenced:
        text = fenced.group(1).strip()

    obj_match = re.search(r"\{[\s\S]*\}", text)
    if obj_match:
        text = obj_match.group(0)

    score: int | None = None
    experience_required = "Not stated"
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            raw_score = parsed.get("score")
            if raw_score is not None:
                score = int(str(raw_score).strip())
            raw_experience = parsed.get("experience_required")
            if raw_experience:
                experience_required = str(raw_experience).strip()
    except Exception:
        match = re.search(r"\b(\d{1,3})\b", text)
        if match:
            try:
                score = int(match.group(1))
            except Exception:
                score = None

    return score, experience_required
