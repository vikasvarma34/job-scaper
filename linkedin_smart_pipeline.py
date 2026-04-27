from __future__ import annotations

import argparse
import json
import logging
import os
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import config
from linkedin_triage import (
    TRIAGE_SYSTEM_PROMPT,
    build_score_prompt,
    build_triage_prompt,
    parse_score_response,
    parse_triage_response,
)
from scraper import (
    _build_job_match_keys,
    _collect_job_match_keys,
    _fetch_linkedin_job_cards,
    _fetch_linkedin_job_details,
)

try:
    import supabase_utils
except ModuleNotFoundError:
    supabase_utils = None

try:
    from llm_client import LLMClient, primary_client, scoring_client
except ModuleNotFoundError:
    LLMClient = None
    primary_client = None
    scoring_client = None


logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


CITY_ORDER = [
    "Hyderabad, Telangana, India",
    "Bengaluru, Karnataka, India",
    "Chennai, Tamil Nadu, India",
    "Mumbai, Maharashtra, India",
]
PRIMARY_QUERIES = [
    "backend developer",
    "backend engineer",
    "software engineer",
    "software developer",
    "full stack developer",
    "full stack engineer",
]
NEGATIVE_TITLE_KEYWORDS = [
    "frontend",
    "front-end",
    "front end",
    "qa",
    "sdet",
    "support",
    "mobile",
    "android",
    "ios",
    "react native",
    "designer",
    "analyst",
    "sales",
    "customer success",
]
HARD_REJECT_SENIORITY = [
    "principal",
    "architect",
    "director",
    "vice president",
    "head of",
    "manager",
]
TARGET_ROLE_HINTS = [
    "software engineer",
    "software developer",
    "backend engineer",
    "backend developer",
    "full stack",
    "fullstack",
    "application developer",
    ".net",
    "c#",
    "java",
    "python",
    "node",
    "golang",
]
TRIAGE_MODEL = os.environ.get("LINKEDIN_SMART_TRIAGE_MODEL") or (
    "openai/sarvam-105b" if os.environ.get("SARVAM_API_KEY") else config.SCORING_LLM_MODEL
)
SCORING_MODEL = os.environ.get("LINKEDIN_SMART_SCORING_MODEL") or (
    "openai/sarvam-105b" if os.environ.get("SARVAM_API_KEY") else config.SCORING_LLM_MODEL
)
TRIAGE_BATCH_SIZE = int(os.environ.get("LINKEDIN_SMART_TRIAGE_BATCH_SIZE", "1"))
DETAIL_FETCH_CAP_PER_QUERY = int(os.environ.get("LINKEDIN_SMART_DETAIL_FETCH_CAP_PER_QUERY", "10"))
MAX_SCORED_JOBS_PER_RUN = int(os.environ.get("LINKEDIN_SMART_MAX_SCORED_JOBS_PER_RUN", "0"))
MIN_SAVE_SCORE = int(os.environ.get("LINKEDIN_SMART_MIN_SAVE_SCORE", "70"))
TRIAGE_MAX_TOKENS = int(os.environ.get("LINKEDIN_SMART_TRIAGE_MAX_TOKENS", "1400"))
SCORING_MAX_TOKENS = int(os.environ.get("LINKEDIN_SMART_SCORING_MAX_TOKENS", "1024"))


def _build_sarvam_client(model: str):
    if LLMClient is None:
        return None

    sarvam_key = str(os.environ.get("SARVAM_API_KEY") or "").strip()
    if not sarvam_key:
        return None

    return LLMClient(
        model=model,
        api_key=sarvam_key,
        api_base=str(os.environ.get("SARVAM_API_BASE") or "https://api.sarvam.ai/v1").strip(),
        max_rpm=config.LLM_MAX_RPM,
        max_retries=config.LLM_MAX_RETRIES,
        retry_base_delay=config.LLM_RETRY_BASE_DELAY,
        daily_budget=config.LLM_DAILY_REQUEST_BUDGET,
        request_delay=config.LLM_REQUEST_DELAY_SECONDS,
    )


TRIAGE_RUNTIME_CLIENT = _build_sarvam_client(TRIAGE_MODEL) if "sarvam" in TRIAGE_MODEL.lower() else primary_client
SCORING_RUNTIME_CLIENT = _build_sarvam_client(SCORING_MODEL) if "sarvam" in SCORING_MODEL.lower() else scoring_client


def _normalize_text(value: str | None) -> str:
    return str(value or "").strip().lower()


def _city_name(location: str) -> str:
    return str(location or "").split(",", 1)[0].strip() or str(location or "").strip()


def _load_resume_text() -> str:
    resume_data = None
    if supabase_utils is not None:
        resume_data = supabase_utils.get_base_resume()
    if not resume_data:
        fallback_path = Path(config.BASE_RESUME_PATH)
        if fallback_path.exists():
            resume_data = json.loads(fallback_path.read_text())
        else:
            raise RuntimeError("Base resume not found in Supabase or local resume.json.")
    return _format_resume_to_text(resume_data)


def _format_resume_to_text(resume_data: dict[str, Any]) -> str:
    if not resume_data:
        return "Resume data is not available."

    lines: list[str] = []
    lines.append(f"Name: {resume_data.get('name', 'N/A')}")
    lines.append(f"Email: {resume_data.get('email', 'N/A')}")
    if resume_data.get("summary"):
        lines.extend(["", "Summary:", str(resume_data["summary"])])
    if resume_data.get("skills"):
        lines.extend(["", "Skills:", ", ".join(str(skill) for skill in resume_data["skills"])])
    if resume_data.get("experience"):
        lines.append("")
        lines.append("Experience:")
        for item in resume_data["experience"]:
            lines.append(
                f"- {item.get('job_title', 'N/A')} at {item.get('company', 'N/A')}"
            )
            if item.get("description"):
                lines.append(f"  {str(item['description']).strip()}")
    if resume_data.get("projects"):
        lines.append("")
        lines.append("Projects:")
        for item in resume_data["projects"]:
            lines.append(f"- {item.get('name', 'N/A')}: {item.get('description', '')}")
    return "\n".join(lines).strip()


def _is_obviously_wrong_role(job: dict[str, Any]) -> bool:
    title = _normalize_text(job.get("job_title"))
    level = _normalize_text(job.get("level"))
    if any(keyword in title for keyword in HARD_REJECT_SENIORITY):
        return True
    if any(keyword in level for keyword in HARD_REJECT_SENIORITY):
        return True
    if any(keyword in title for keyword in NEGATIVE_TITLE_KEYWORDS):
        return True
    return False


def _looks_broadly_relevant(job: dict[str, Any]) -> bool:
    title = _normalize_text(job.get("job_title"))
    description = _normalize_text(job.get("description"))[:700]
    if any(keyword in title for keyword in TARGET_ROLE_HINTS):
        return True
    if any(keyword in description for keyword in TARGET_ROLE_HINTS):
        return True
    return bool(re.search(r"\b(engineer|developer|sde)\b", title))


def _hard_filter_job(job: dict[str, Any], location: str) -> tuple[bool, str]:
    if not str(job.get("job_id") or "").strip():
        return False, "missing_job_id"
    if not str(job.get("description") or "").strip():
        return False, "missing_description"
    if _city_name(location).lower() not in _normalize_text(job.get("location")):
        return False, "wrong_city"
    if _is_obviously_wrong_role(job):
        return False, "obvious_role_reject"
    if not _looks_broadly_relevant(job):
        return False, "not_broadly_relevant"
    return True, "keep"


def _chunked(items: list[dict[str, Any]], size: int) -> list[list[dict[str, Any]]]:
    return [items[index:index + size] for index in range(0, len(items), size)]


def _fetch_existing_match_state() -> tuple[set[str], set[str]]:
    if supabase_utils is None:
        logger.warning("supabase_utils is unavailable; duplicate check will use current-run state only.")
        return set(), set()
    existing_ids, existing_rows = supabase_utils.get_existing_job_match_data_from_supabase()
    return existing_ids, _collect_job_match_keys(existing_rows)


def _collect_candidates(
    *,
    queries: list[str],
    city_locations: list[str],
    detail_cap_per_query: int,
) -> tuple[list[dict[str, Any]], Counter]:
    existing_ids, existing_match_keys = _fetch_existing_match_state()
    seen_ids: set[str] = set()
    seen_match_keys: set[str] = set()
    counters: Counter = Counter()
    detailed_jobs: list[dict[str, Any]] = []

    for location in city_locations:
        for query in queries:
            cards = _fetch_linkedin_job_cards(query, location, geo_id_override=None)
            counters["raw_cards"] += len(cards)
            fresh_cards: list[dict[str, Any]] = []
            for card in cards:
                job_id = str(card.get("job_id") or "").strip()
                if not job_id:
                    counters["missing_card_job_id"] += 1
                    continue
                if job_id in existing_ids or job_id in seen_ids:
                    counters["duplicate_job_id"] += 1
                    continue
                card["provider"] = "linkedin"
                card_keys = _build_job_match_keys(card)
                if card_keys & existing_match_keys or card_keys & seen_match_keys:
                    counters["duplicate_match_key"] += 1
                    continue
                fresh_cards.append(card)

            for card in fresh_cards[:detail_cap_per_query]:
                job_id = str(card.get("job_id") or "").strip()
                details = _fetch_linkedin_job_details(job_id)
                counters["detail_attempts"] += 1
                if not details:
                    counters["detail_fetch_failed"] += 1
                    continue

                allowed, reason = _hard_filter_job(details, location)
                if not allowed:
                    counters[reason] += 1
                    continue

                details["provider"] = "linkedin"
                details["job_state"] = "new"
                details["status"] = "new"
                details["is_active"] = True
                details["scraped_at"] = datetime.now(timezone.utc).isoformat()
                details["smart_pipeline_source"] = "linkedin_smart_pipeline"
                detailed_jobs.append(details)
                seen_ids.add(job_id)
                seen_match_keys.update(_build_job_match_keys(details))
                counters["hard_filter_kept"] += 1

    return detailed_jobs, counters


def _triage_jobs(jobs: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], Counter]:
    if TRIAGE_RUNTIME_CLIENT is None:
        raise RuntimeError("LLM client dependencies are unavailable, so triage mode cannot run.")
    counters: Counter = Counter()
    kept: list[dict[str, Any]] = []
    for batch in _chunked(jobs, max(1, TRIAGE_BATCH_SIZE)):
        prompt = build_triage_prompt(batch)
        raw = TRIAGE_RUNTIME_CLIENT.generate_content(
            prompt=prompt,
            system_prompt=TRIAGE_SYSTEM_PROMPT,
            temperature=0,
            model_override=TRIAGE_MODEL,
            reasoning_effort=None,
            max_tokens=TRIAGE_MAX_TOKENS,
        )
        parsed = parse_triage_response(raw)
        decision_map = {item.job_id: item.decision for item in parsed.jobs}
        for job in batch:
            decision = decision_map.get(str(job.get("job_id")), "reject")
            counters[f"triage_{decision}"] += 1
            job["triage_decision"] = decision
            if decision in {"keep", "borderline"}:
                kept.append(job)
    return kept, counters


def _score_jobs(jobs: list[dict[str, Any]], resume_text: str) -> tuple[list[dict[str, Any]], Counter]:
    if SCORING_RUNTIME_CLIENT is None:
        raise RuntimeError("LLM client dependencies are unavailable, so score mode cannot run.")
    counters: Counter = Counter()
    scored_jobs: list[dict[str, Any]] = []
    selected_jobs = jobs[:MAX_SCORED_JOBS_PER_RUN] if MAX_SCORED_JOBS_PER_RUN > 0 else jobs
    for job in selected_jobs:
        prompt = build_score_prompt(job, resume_text)
        raw = SCORING_RUNTIME_CLIENT.generate_content(
            prompt=prompt,
            system_prompt="Return only strict JSON.",
            temperature=0,
            model_override=SCORING_MODEL,
            reasoning_effort="medium",
            max_tokens=SCORING_MAX_TOKENS,
        )
        score, experience_required = parse_score_response(raw)
        if score is None:
            counters["score_parse_failed"] += 1
            continue
        job["resume_score"] = score
        job["experience_required"] = experience_required
        scored_jobs.append(job)
        counters["scored"] += 1
    return scored_jobs, counters


def _persist_jobs(scored_jobs: list[dict[str, Any]]) -> tuple[int, int]:
    if supabase_utils is None:
        raise RuntimeError("Supabase support is unavailable in this environment, so save mode cannot run.")
    jobs_to_save = []
    for job in scored_jobs:
        if int(job.get("resume_score") or 0) < MIN_SAVE_SCORE:
            continue
        payload = dict(job)
        payload.pop("triage_decision", None)
        payload.pop("smart_pipeline_source", None)
        jobs_to_save.append(payload)

    saved = supabase_utils.save_jobs_to_supabase(jobs_to_save)
    updated_scores = 0
    for job in jobs_to_save:
        if supabase_utils.update_job_score(
            job_id=str(job.get("job_id")),
            score=int(job.get("resume_score") or 0),
            resume_score_stage="initial",
            experience_required=str(job.get("experience_required") or "Not stated"),
        ):
            updated_scores += 1
    return saved, updated_scores


def _format_table(jobs: list[dict[str, Any]]) -> str:
    header = "| City | Query Source | Company | Title | Score | Triage | URL |\n|---|---|---|---|---|---|---|"
    rows = [header]
    for job in jobs:
        rows.append(
            "| {city} | linkedin | {company} | {title} | {score} | {triage} | {url} |".format(
                city=_city_name(str(job.get("location") or "")),
                company=str(job.get("company") or ""),
                title=str(job.get("job_title") or ""),
                score=str(job.get("resume_score") or ""),
                triage=str(job.get("triage_decision") or ""),
                url=str(job.get("job_url") or ""),
            )
        )
    return "\n".join(rows)


def run_pipeline(
    *,
    no_save: bool,
    no_score: bool,
    show_prompt_only: bool,
    limit: int,
    detail_cap_per_query: int,
    city_limit: int,
    query_limit: int,
) -> int:
    queries = list(dict.fromkeys(getattr(config, "LINKEDIN_SEARCH_QUERIES", []) + PRIMARY_QUERIES))
    if query_limit > 0:
        queries = queries[:query_limit]
    city_locations = CITY_ORDER[:city_limit] if city_limit > 0 else CITY_ORDER
    resume_text = _load_resume_text()
    candidates, fetch_counts = _collect_candidates(
        queries=queries,
        city_locations=city_locations,
        detail_cap_per_query=detail_cap_per_query,
    )
    logger.info("Fetch summary: %s", dict(fetch_counts))

    if show_prompt_only and candidates:
        print(build_triage_prompt(candidates[:TRIAGE_BATCH_SIZE]))
        return 0

    triaged_jobs = candidates
    triage_counts: Counter = Counter()
    if candidates:
        triaged_jobs, triage_counts = _triage_jobs(candidates)
        logger.info("Triage summary: %s", dict(triage_counts))

    final_jobs = triaged_jobs
    score_counts: Counter = Counter()
    if triaged_jobs and not no_score:
        final_jobs, score_counts = _score_jobs(triaged_jobs, resume_text)
        logger.info("Score summary: %s", dict(score_counts))

    final_jobs = sorted(final_jobs, key=lambda item: int(item.get("resume_score") or 0), reverse=True)
    selected = final_jobs[:limit] if limit > 0 else final_jobs
    print(_format_table(selected))

    if no_save or no_score:
        return 0

    saved_count, updated_score_count = _persist_jobs(selected)
    logger.info(
        "Persistence summary: saved=%s, score_updates=%s, min_save_score=%s",
        saved_count,
        updated_score_count,
        MIN_SAVE_SCORE,
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="New isolated LinkedIn smart pipeline.")
    parser.add_argument("--limit", type=int, default=25, help="Max jobs to print/save after scoring.")
    parser.add_argument(
        "--detail-cap-per-query",
        type=int,
        default=DETAIL_FETCH_CAP_PER_QUERY,
        help="How many card IDs to expand into full LinkedIn detail fetches per query.",
    )
    parser.add_argument("--no-save", action="store_true", help="Do not write anything to Supabase.")
    parser.add_argument("--no-score", action="store_true", help="Skip final scoring calls.")
    parser.add_argument("--show-prompt-only", action="store_true", help="Print the triage prompt and exit.")
    parser.add_argument("--city-limit", type=int, default=1, help="Number of priority cities to run in this pass. 0 = all.")
    parser.add_argument("--query-limit", type=int, default=2, help="Number of queries to run in this pass. 0 = all.")
    args = parser.parse_args()
    return run_pipeline(
        no_save=args.no_save,
        no_score=args.no_score,
        show_prompt_only=args.show_prompt_only,
        limit=args.limit,
        detail_cap_per_query=args.detail_cap_per_query,
        city_limit=args.city_limit,
        query_limit=args.query_limit,
    )


if __name__ == "__main__":
    raise SystemExit(main())
