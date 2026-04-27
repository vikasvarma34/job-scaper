import io
import json
import os
import re
import subprocess
import sys
import threading
import uuid
from collections import deque
from datetime import datetime, timezone
from pathlib import Path

from flask import Flask, abort, jsonify, redirect, render_template, request, send_file, url_for
from pydantic import ValidationError

import config
import pdf_generator
import supabase_utils
from cover_letter_pdf import create_cover_letter_pdf
from models import Resume


ROOT_DIR = Path(__file__).resolve().parent
CURRENT_APPLIED_STATUS = "applied"
HISTORICAL_APPLIED_STATUSES = {"applied", "previously_applied"}

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("FLASK_SECRET_KEY", "job-scraper-dashboard")


_state_lock = threading.Lock()
_task_state = {
    "id": None,
    "label": "Idle",
    "command": [],
    "status": "idle",
    "started_at": None,
    "finished_at": None,
    "returncode": None,
    "logs": deque(maxlen=1500),
}
_task_process: subprocess.Popen | None = None
_stop_requested = False
_RESUME_PROVIDER_ALIASES = {
    "gemini": "gemini",
    "google": "gemini",
    "sarvam": "sarvam",
}


def _env_flag(name: str, default: bool = False) -> bool:
    value = str(os.environ.get(name, "")).strip().lower()
    if not value:
        return default
    return value in {"1", "true", "yes", "on"}


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sanitize_filename_token(value: object, default: str = "UNKNOWN") -> str:
    text = str(value or "").strip()
    if not text:
        return default
    text = re.sub(r"[^A-Za-z0-9]+", "_", text)
    text = re.sub(r"_+", "_", text).strip("_")
    return (text or default).upper()


def _build_resume_storage_path(job_id: str, company: object) -> str:
    company_token = _sanitize_filename_token(company, default="COMPANY")
    job_token = _sanitize_filename_token(job_id, default="JOB")
    return f"VIKAS_POKALA_{company_token}_{job_token}.pdf"


def _snapshot_state() -> dict:
    with _state_lock:
        return {
            "id": _task_state["id"],
            "label": _task_state["label"],
            "command": list(_task_state["command"]),
            "status": _task_state["status"],
            "started_at": _task_state["started_at"],
            "finished_at": _task_state["finished_at"],
            "returncode": _task_state["returncode"],
            "logs": list(_task_state["logs"]),
        }


def _set_state(**updates: object) -> None:
    with _state_lock:
        for key, value in updates.items():
            _task_state[key] = value


def _append_log(line: str) -> None:
    clean_line = str(line or "").rstrip()
    if not clean_line:
        return
    with _state_lock:
        _task_state["logs"].append(clean_line)


def _set_task_process(process: subprocess.Popen | None) -> None:
    global _task_process
    with _state_lock:
        _task_process = process


def _get_task_process() -> subprocess.Popen | None:
    with _state_lock:
        return _task_process


def _clear_task_process(process: subprocess.Popen) -> None:
    global _task_process
    with _state_lock:
        if _task_process is process:
            _task_process = None


def _normalize_resume_provider(provider: str | None) -> str:
    cleaned = str(provider or "").strip().lower()
    return _RESUME_PROVIDER_ALIASES.get(cleaned, "gemini")


def _resume_generation_env_overrides(provider: str | None) -> dict[str, str]:
    normalized_provider = _normalize_resume_provider(provider)
    if normalized_provider == "sarvam":
        sarvam_api_key = str(os.environ.get("SARVAM_API_KEY") or "").strip()
        if not sarvam_api_key:
            raise ValueError("SARVAM_API_KEY is required to generate resumes with Sarvam.")
        return {
            "LLM_MODEL": "sarvam-105b",
            "LLM_API_KEY": sarvam_api_key,
            "LLM_API_BASE": os.environ.get("SARVAM_API_BASE", "https://api.sarvam.ai/v1"),
        }

    # Use the existing Gemini/default configuration for all other cases.
    return {
        "LLM_MODEL": str(config.LLM_MODEL if "gemini" in str(config.LLM_MODEL).lower() else "gemini/gemini-3.1-pro-preview"),
        "LLM_API_KEY": str(
            os.environ.get("GEMINI_API_KEY")
            or os.environ.get("GEMINI_FIRST_API_KEY")
            or (config.LLM_API_KEY if "gemini" in str(config.LLM_MODEL).lower() else "")
        ),
        "LLM_API_BASE": None,
    }


def _run_command_in_background(label: str, command: list[str], env_overrides: dict[str, str | None] | None = None) -> None:
    global _stop_requested
    env = os.environ.copy()
    for key, value in (env_overrides or {}).items():
        if value is None:
            env.pop(key, None)
        elif value:
            env[key] = value
    env["PYTHONUNBUFFERED"] = "1"

    task_id = str(uuid.uuid4())
    _set_state(
        id=task_id,
        label=label,
        command=command,
        status="running",
        started_at=_utc_now_iso(),
        finished_at=None,
        returncode=None,
        logs=deque([f"Starting: {' '.join(command)}"], maxlen=1500),
    )
    with _state_lock:
        _stop_requested = False

    def _worker() -> None:
        global _stop_requested
        process = subprocess.Popen(
            command,
            cwd=str(ROOT_DIR),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            env=env,
        )
        _set_task_process(process)
        with _state_lock:
            should_stop_now = _stop_requested
        if should_stop_now and process.poll() is None:
            _append_log("Stop was requested before process startup completed. Terminating now...")
            process.terminate()

        assert process.stdout is not None
        for line in process.stdout:
            _append_log(line)

        returncode = process.wait()
        _clear_task_process(process)
        current = _snapshot_state()
        stopped = current["status"] == "stopping"
        if stopped:
            _append_log(f"Stopped with exit code {returncode}.")
        else:
            _append_log(f"Finished with exit code {returncode}.")
        _set_state(
            status="stopped" if stopped else ("success" if returncode == 0 else "failed"),
            finished_at=_utc_now_iso(),
            returncode=returncode,
        )
        with _state_lock:
            _stop_requested = False

    threading.Thread(target=_worker, daemon=True).start()


def _build_command(
    action: str,
    job_id: str | None,
    count: int | None,
    email_override: str | None = None,
    job_url: str | None = None,
    resume_provider: str | None = None,
) -> tuple[str, list[str], dict[str, str | None]]:
    python = sys.executable
    cleaned_email_override = str(email_override or "").strip()
    env_overrides: dict[str, str | None] = {}
    selected_provider = _normalize_resume_provider(resume_provider)
    provider_suffix = " [Sarvam]" if selected_provider == "sarvam" else " [Gemini]"

    def _append_email_override(command: list[str]) -> list[str]:
        if cleaned_email_override:
            command.extend(["--email-override", cleaned_email_override])
        return command

    def _with_resume_provider(command: list[str]) -> list[str]:
        provider_overrides = _resume_generation_env_overrides(resume_provider)
        env_overrides.update(provider_overrides)
        return command

    if action == "scrape":
        return "Scrape Jobs", [python, "scraper.py"], {}
    if action == "score":
        return "Score Jobs", [python, "score_jobs.py"], {}
    if action == "linkedin_smart":
        return (
            "LinkedIn Smart Pipeline",
            [
                python,
                "linkedin_smart_pipeline.py",
                "--city-limit",
                "0",
                "--query-limit",
                "0",
                "--limit",
                "0",
            ],
            {},
        )
    if action == "import_job_url":
        cleaned_job_url = str(job_url or "").strip()
        if not cleaned_job_url:
            raise ValueError("Job URL is required.")
        command = _with_resume_provider(
            _append_email_override([python, "job_link_processor.py", "--job-url", cleaned_job_url])
        )
        return (
            f"Import Job Link and Generate Resume{provider_suffix}",
            command,
            env_overrides,
        )
    if action == "generate_next":
        command = _with_resume_provider(
            _append_email_override([python, "custom_resume_generator.py", "--flow", "two_step_ai", "--limit", "1"])
        )
        return (
            f"Generate Next Resume{provider_suffix}",
            command,
            env_overrides,
        )
    if action == "generate_selected":
        if count is None or count <= 0:
            raise ValueError("Resume count must be a positive number.")
        command = _with_resume_provider(
            _append_email_override([python, "custom_resume_generator.py", "--flow", "two_step_ai", "--limit", str(count)])
        )
        return (
            f"Generate {count} Selected Resume{'s' if count != 1 else ''}{provider_suffix}",
            command,
            env_overrides,
        )
    if action == "cleanup":
        return "Cleanup", [python, "daily_ops.py", "cleanup"], {}
    if action == "generate_job":
        if not job_id:
            raise ValueError("Job ID is required.")
        job_record = supabase_utils.get_job_by_id(job_id)
        if not job_record:
            raise ValueError(f"Could not find job_id {job_id}.")

        existing_resume_id = str(job_record.get("customized_resume_id") or "").strip()
        command = _with_resume_provider(
            _append_email_override([python, "custom_resume_generator.py", "--job-id", job_id, "--flow", "two_step_ai"])
        )
        label = f"Generate Resume for {job_id}"
        if existing_resume_id:
            command.append("--force-regenerate")
            label = f"Regenerate Resume for {job_id}"
        return (
            f"{label}{provider_suffix}",
            command,
            env_overrides,
        )
    if action == "generate_cover_letter":
        if not job_id:
            raise ValueError("Job ID is required.")
        job_record = supabase_utils.get_job_by_id(job_id)
        if not job_record:
            raise ValueError(f"Could not find job_id {job_id}.")
        existing_resume_id = str(job_record.get("customized_resume_id") or "").strip()
        if not existing_resume_id:
            raise ValueError("Generate a customized resume first before creating a cover letter.")
        existing_cover_letter = supabase_utils.get_cover_letter_by_job_id(job_id)
        label = f"Generate Cover Letter for {job_id}"
        if existing_cover_letter:
            label = f"Regenerate Cover Letter for {job_id}"
        return (
            label,
            _append_email_override([python, "cover_letter_generator.py", "--job-id", job_id]),
            {},
        )

    raise ValueError("Unknown action.")


def _fetch_resume_links_by_id(resume_ids: list[str]) -> dict[str, str]:
    cleaned_ids = [str(resume_id).strip() for resume_id in resume_ids if str(resume_id).strip()]
    if not cleaned_ids:
        return {}

    try:
        results: dict[str, str] = {}
        chunk_size = 100
        for start in range(0, len(cleaned_ids), chunk_size):
            chunk = cleaned_ids[start : start + chunk_size]
            response = (
                supabase_utils.supabase.table(config.SUPABASE_CUSTOMIZED_RESUMES_TABLE_NAME)
                .select("id, resume_link")
                .in_("id", chunk)
                .execute()
            )
            rows = response.data or []
            for row in rows:
                resume_id = str(row.get("id") or "").strip()
                if resume_id:
                    results[resume_id] = str(row.get("resume_link") or "").strip()
        return results
    except Exception as exc:
        return {"__error__": f"Failed to load resume links: {exc}"}


def _build_linkedin_url(provider: str | None, job_id: str | None) -> str:
    if (provider or "").strip().lower() == "linkedin" and str(job_id or "").strip():
        return f"https://www.linkedin.com/jobs/view/{job_id}/"
    return ""


def _build_job_url(stored_url: str | None, provider: str | None, job_id: str | None) -> str:
    cleaned_stored_url = str(stored_url or "").strip()
    if cleaned_stored_url:
        return cleaned_stored_url
    return _build_linkedin_url(provider, job_id)


def _extract_experience_requirement(description: str | None, job_title: str | None = None) -> str:
    """
    Parse an experience requirement from job text using local regex logic only.
    Returns a short human-readable string like '2-5 years', '3+ years', '2 years',
    or 'Not stated' when no clear requirement is found.
    """
    text = " ".join(
        [
            str(job_title or ""),
            str(description or ""),
        ]
    ).strip()
    if not text:
        return "Not stated"

    normalized = re.sub(r"\s+", " ", text.lower())

    patterns: list[tuple[str, str]] = [
        # Standard and compact ranges: 2-5 years, 2 - 5 yrs, 2to5 yoe
        (r"(\d{1,2})\s*(?:-|–|—|to)\s*(\d{1,2})\s*(?:years?|yrs?|yr|yoe)\b", "range"),
        # Explicit minimum phrasing
        (
            r"(?:at least|minimum(?: of)?|minimum|required|requires|need|needs)\s*(\d{1,2})\+?\s*(?:years?|yrs?|yr|yoe)\b",
            "plus",
        ),
        # Plus-style forms with/without spaces/units: 2+, 2 +, 2+yrs, 2 + yrs
        (r"(\d{1,2})\s*\+\s*(?:years?|yrs?|yr|yoe)?\b", "plus"),
        (r"(\d{1,2})\+\s*(?:years?|yrs?|yr|yoe)\b", "plus"),
        (r"(\d{1,2})\s*(?:or more|and above|plus)\s*(?:years?|yrs?|yr|yoe)\b", "plus"),
        # Hyphen-only minimum shorthand: 2- years, 2 - years, 2-yr
        (r"(\d{1,2})\s*-\s*(?:years?|yrs?|yr|yoe)\b", "plus"),
        (r"(\d{1,2})\s*(?:years?|yrs?|yr|yoe)\b(?:\s+of)?\s+experience", "exact"),
    ]

    for pattern, mode in patterns:
        match = re.search(pattern, normalized)
        if not match:
            continue
        nums = [int(g) for g in match.groups() if g is not None]
        if not nums:
            continue
        if mode == "range" and len(nums) >= 2:
            low, high = min(nums), max(nums)
            return f"{low}-{high} years"
        if mode == "plus":
            return f"{nums[0]}+ years"
        return f"{nums[0]} years"

    return "Not stated"


def _fetch_cover_letter_links_by_job_id(job_ids: list[str]) -> dict[str, dict]:
    cleaned_ids = [str(job_id).strip() for job_id in job_ids if str(job_id).strip()]
    if not cleaned_ids:
        return {}

    table_name = getattr(config, "SUPABASE_CUSTOMIZED_COVER_LETTERS_TABLE_NAME", "")
    if not table_name:
        return {}

    try:
        results: dict[str, dict] = {}
        chunk_size = 100
        for start in range(0, len(cleaned_ids), chunk_size):
            chunk = cleaned_ids[start : start + chunk_size]
            response = (
                supabase_utils.supabase.table(table_name)
                .select("job_id, id, cover_letter_link")
                .in_("job_id", chunk)
                .execute()
            )
            rows = response.data or []
            for row in rows:
                job_id = str(row.get("job_id") or "").strip()
                if job_id:
                    results[job_id] = {
                        "id": str(row.get("id") or "").strip(),
                        "link": str(row.get("cover_letter_link") or "").strip(),
                    }
        return results
    except Exception as exc:
        return {"__error__": {"message": f"Failed to load cover letter links: {exc}"}}


def _job_score_value(job: dict) -> int:
    score = job.get("resume_score")
    try:
        return int(score)
    except (TypeError, ValueError):
        return -1


def _sort_jobs_for_dashboard(jobs: list[dict]) -> list[dict]:
    return sorted(
        jobs,
        key=lambda job: (
            _job_score_value(job),
            str(job.get("scraped_at") or ""),
        ),
        reverse=True,
    )


def _looks_like_email(value: str) -> bool:
    return bool(re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", str(value or "").strip()))


def _sanitize_filename_token(value: object, default: str = "UNKNOWN") -> str:
    text = str(value or "").strip()
    if not text:
        return default
    text = re.sub(r"[^A-Za-z0-9]+", "_", text)
    text = re.sub(r"_+", "_", text).strip("_")
    return (text or default).upper()


def _build_cover_letter_storage_path(job_id: str, company: object) -> str:
    company_token = _sanitize_filename_token(company, default="COMPANY")
    job_token = _sanitize_filename_token(job_id, default="JOB")
    return f"cover_letters/VIKAS_POKALA_{company_token}_{job_token}_COVER_LETTER.pdf"


def _resume_to_pretty_json(resume: Resume) -> str:
    return json.dumps(resume.model_dump(), indent=2, ensure_ascii=False)


def _fetch_all_jobs(batch_size: int = 500) -> tuple[list[dict], str | None]:
    try:
        all_jobs: list[dict] = []
        offset = 0

        while True:
            response = (
                supabase_utils.supabase.table(config.SUPABASE_TABLE_NAME)
                .select("*")
                .order("scraped_at", desc=True)
                .range(offset, offset + batch_size - 1)
                .execute()
            )
            rows = response.data or []
            if not rows:
                break
            all_jobs.extend(rows)
            if len(rows) < batch_size:
                break
            offset += batch_size

        return all_jobs, None
    except Exception as exc:
        return [], f"Failed to load jobs: {exc}"


def _fetch_dashboard_data() -> dict:
    jobs, jobs_error = _fetch_all_jobs()
    jobs = _sort_jobs_for_dashboard(jobs)
    resume_ids = [str(job.get("customized_resume_id") or "").strip() for job in jobs]
    resume_links = _fetch_resume_links_by_id(resume_ids)
    cover_letter_map = _fetch_cover_letter_links_by_job_id([str(job.get("job_id") or "").strip() for job in jobs])
    if "__error__" in resume_links:
        return {
            "jobs": [],
            "jobs_error": resume_links["__error__"],
            "stats": {
                "total_jobs": 0,
                "scored_jobs": 0,
                "resumes_generated": 0,
                "pending_jobs": 0,
                "applied_jobs": 0,
                "not_available_jobs": 0,
                "cover_letters_ready": 0,
                "ready_to_apply": 0,
            },
        }
    if "__error__" in cover_letter_map:
        return {
            "jobs": [],
            "jobs_error": cover_letter_map["__error__"]["message"],
            "stats": {
                "total_jobs": 0,
                "scored_jobs": 0,
                "resumes_generated": 0,
                "pending_jobs": 0,
                "applied_jobs": 0,
                "not_available_jobs": 0,
                "cover_letters_ready": 0,
                "ready_to_apply": 0,
            },
        }

    for job in jobs:
        resume_id = str(job.get("customized_resume_id") or "").strip()
        resume_link = str(resume_links.get(resume_id) or "").strip() if resume_id else ""
        cover_letter_record = cover_letter_map.get(str(job.get("job_id") or "").strip(), {})
        job["job_url"] = _build_job_url(job.get("job_url"), job.get("provider"), job.get("job_id"))
        stored_experience_required = str(job.get("experience_required") or "").strip()
        job["experience_required"] = stored_experience_required or _extract_experience_requirement(
            job.get("description"),
            job.get("job_title"),
        )
        job["resume_download_url"] = f"/resume/{resume_id}/download" if resume_link else ""
        job["has_resume"] = bool(resume_id)
        job["resume_pdf_available"] = bool(resume_link)
        job["cover_letter_download_url"] = (
            f"/cover-letter/{job.get('job_id')}/download"
            if cover_letter_record.get("link")
            else ""
        )
        job["has_cover_letter"] = bool(cover_letter_record.get("link"))
        job["job_id"] = str(job.get("job_id") or "").strip()

    active_jobs = [
        job
        for job in jobs
        if str(job.get("status") or "").strip().lower() != "previously_applied"
    ]

    stats = {
        "total_jobs": len(active_jobs),
        "scored_jobs": sum(1 for job in active_jobs if job.get("resume_score") is not None),
        "resumes_generated": sum(1 for job in active_jobs if str(job.get("customized_resume_id") or "").strip()),
        "pending_jobs": sum(1 for job in active_jobs if not str(job.get("customized_resume_id") or "").strip()),
        "applied_jobs": sum(
            1
            for job in active_jobs
            if str(job.get("status") or "").strip().lower() == CURRENT_APPLIED_STATUS
        ),
        "not_available_jobs": sum(1 for job in active_jobs if str(job.get("status") or "").strip().lower() == "not_available"),
        "cover_letters_ready": sum(1 for job in active_jobs if bool(job.get("has_cover_letter"))),
        "ready_to_apply": sum(
            1
            for job in active_jobs
            if bool(job.get("has_resume"))
            and str(job.get("status") or "").strip().lower() not in HISTORICAL_APPLIED_STATUSES | {"not_available"}
        ),
    }

    return {
        "jobs": jobs,
        "jobs_error": jobs_error,
        "stats": stats,
    }


@app.get("/")
def index():
    return render_template("index.html", state=_snapshot_state(), data=_fetch_dashboard_data())


@app.get("/status")
def status():
    return jsonify(_snapshot_state())


@app.post("/stop")
def stop_action():
    global _stop_requested
    current = _snapshot_state()
    if current["status"] != "running":
        return jsonify({"ok": False, "error": "No running task to stop."}), 409

    with _state_lock:
        _stop_requested = True
    process = _get_task_process()
    if process is None:
        _set_state(status="stopping")
        _append_log("Stop requested. Waiting for process startup...")
        return jsonify({"ok": True})
    if process.poll() is not None:
        return jsonify({"ok": False, "error": "Task already finished."}), 409

    _set_state(status="stopping")
    _append_log("Stop requested. Attempting to terminate the running command...")
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        _append_log("Terminate timed out. Force killing the process...")
        process.kill()

    return jsonify({"ok": True})


@app.get("/data")
def data():
    return jsonify(_fetch_dashboard_data())


@app.post("/jobs/<job_id>/applied")
def mark_job_applied(job_id: str):
    cleaned_job_id = str(job_id or "").strip()
    if not cleaned_job_id:
        return jsonify({"ok": False, "error": "Job ID is required."}), 400

    updated, requested = supabase_utils.mark_jobs_as_applied([cleaned_job_id])
    if updated <= 0:
        return jsonify({"ok": False, "error": f"Could not mark job {cleaned_job_id} as applied."}), 400

    return jsonify({"ok": True, "updated": updated, "requested": requested, "job_id": cleaned_job_id})


@app.post("/jobs/<job_id>/not-available")
def mark_job_not_available(job_id: str):
    cleaned_job_id = str(job_id or "").strip()
    if not cleaned_job_id:
        return jsonify({"ok": False, "error": "Job ID is required."}), 400

    updated, requested = supabase_utils.mark_jobs_as_not_available([cleaned_job_id])
    if updated <= 0:
        return jsonify({"ok": False, "error": f"Could not mark job {cleaned_job_id} as not available."}), 400

    return jsonify({"ok": True, "updated": updated, "requested": requested, "job_id": cleaned_job_id})


@app.post("/scores/clear")
def clear_scores():
    updated_count, previously_scored_count, error_message = supabase_utils.clear_all_job_scores()
    if error_message:
        return jsonify({"ok": False, "error": f"Could not clear scores: {error_message}"}), 500
    return jsonify(
        {
            "ok": True,
            "updated": updated_count,
            "previously_scored": previously_scored_count,
        }
    )


@app.post("/jobs/<job_id>/contact-email")
def update_job_contact_email(job_id: str):
    cleaned_job_id = str(job_id or "").strip()
    if not cleaned_job_id:
        return jsonify({"ok": False, "error": "Job ID is required."}), 400

    raw_email = (
        request.form.get("email")
        if request.form
        else None
    )
    if raw_email is None and request.is_json:
        raw_email = (request.get_json(silent=True) or {}).get("email")
    cleaned_email = str(raw_email or "").strip()

    if cleaned_email and not _looks_like_email(cleaned_email):
        return jsonify({"ok": False, "error": "Enter a valid email address."}), 400

    success = supabase_utils.update_job_contact_email_override(
        cleaned_job_id,
        cleaned_email or None,
    )
    if not success:
        return jsonify({"ok": False, "error": f"Could not update contact email for {cleaned_job_id}."}), 400

    return jsonify(
        {
            "ok": True,
            "job_id": cleaned_job_id,
            "contact_email_override": cleaned_email,
            "cleared": not bool(cleaned_email),
        }
    )


@app.post("/jobs/<job_id>/restore-resume")
def restore_job_resume(job_id: str):
    cleaned_job_id = str(job_id or "").strip()
    if not cleaned_job_id:
        return jsonify({"ok": False, "error": "Job ID is required."}), 400

    job_record = supabase_utils.get_job_by_id(cleaned_job_id)
    if not job_record:
        return jsonify({"ok": False, "error": f"Could not find job {cleaned_job_id}."}), 404

    customized_resume_id = str(job_record.get("customized_resume_id") or "").strip()
    if not customized_resume_id:
        return jsonify({"ok": False, "error": "This job does not have saved resume data to rebuild."}), 400

    customized_resume_record = supabase_utils.get_customized_resume(customized_resume_id)
    if not customized_resume_record:
        return jsonify({"ok": False, "error": "Saved resume data could not be found."}), 404

    stored_header_title = str(customized_resume_record.get("header_title") or "").strip()
    header_title = stored_header_title or str(job_record.get("job_title") or "").strip()

    try:
        current_resume = Resume.model_validate(customized_resume_record)
    except Exception as exc:
        return jsonify({"ok": False, "error": f"Failed to parse saved resume data: {exc}"}), 500

    try:
        resume_pdf = pdf_generator.create_resume_pdf(
            current_resume,
            header_title=header_title,
        )
    except Exception as exc:
        return jsonify({"ok": False, "error": f"Failed to rebuild resume PDF: {exc}"}), 500

    destination_path = (
        str(customized_resume_record.get("resume_link") or "").strip()
        or _build_resume_storage_path(cleaned_job_id, job_record.get("company"))
    )
    uploaded_resume_path = supabase_utils.upload_customized_resume_to_storage(
        resume_pdf,
        destination_path,
    )
    if not uploaded_resume_path:
        return jsonify({"ok": False, "error": "Failed to upload rebuilt resume PDF."}), 500

    updated = supabase_utils.update_customized_resume(
        customized_resume_id,
        current_resume,
        uploaded_resume_path,
        header_title=header_title,
    )
    if not updated:
        return jsonify({"ok": False, "error": "Failed to update saved resume metadata after rebuild."}), 500

    return jsonify(
        {
            "ok": True,
            "job_id": cleaned_job_id,
            "customized_resume_id": customized_resume_id,
            "resume_download_url": url_for("download_resume", resume_id=customized_resume_id),
        }
    )


@app.get("/resume/<resume_id>/download")
def download_resume(resume_id: str):
    resume_path_map = _fetch_resume_links_by_id([resume_id])
    if "__error__" in resume_path_map:
        abort(500, resume_path_map["__error__"])

    resume_path = str(resume_path_map.get(str(resume_id).strip()) or "").strip()
    if not resume_path:
        abort(404)

    if resume_path.startswith("http://") or resume_path.startswith("https://"):
        return redirect(resume_path)

    try:
        file_bytes = supabase_utils.supabase.storage.from_(config.SUPABASE_STORAGE_BUCKET).download(resume_path)
    except Exception as exc:
        abort(500, f"Failed to download resume: {exc}")

    file_name = Path(resume_path).name or f"{resume_id}.pdf"
    return send_file(
        io.BytesIO(file_bytes),
        mimetype="application/pdf",
        as_attachment=True,
        download_name=file_name,
    )


@app.get("/cover-letter/<job_id>/download")
def download_cover_letter(job_id: str):
    record = supabase_utils.get_cover_letter_by_job_id(job_id)
    if not record:
        abort(404)

    cover_letter_path = str(record.get("cover_letter_link") or "").strip()
    if not cover_letter_path:
        abort(404)

    if cover_letter_path.startswith("http://") or cover_letter_path.startswith("https://"):
        return redirect(cover_letter_path)

    try:
        file_bytes = supabase_utils.supabase.storage.from_(config.SUPABASE_STORAGE_BUCKET).download(cover_letter_path)
    except Exception as exc:
        abort(500, f"Failed to download cover letter: {exc}")

    file_name = Path(cover_letter_path).name or f"{job_id}_cover_letter.pdf"
    return send_file(
        io.BytesIO(file_bytes),
        mimetype="application/pdf",
        as_attachment=True,
        download_name=file_name,
    )


@app.route("/edit/<job_id>", methods=["GET", "POST"])
def edit_documents(job_id: str):
    cleaned_job_id = str(job_id or "").strip()
    if not cleaned_job_id:
        abort(404)

    job_record = supabase_utils.get_job_by_id(cleaned_job_id)
    if not job_record:
        abort(404)
    job_record["job_url"] = _build_job_url(job_record.get("job_url"), job_record.get("provider"), job_record.get("job_id"))

    customized_resume_id = str(job_record.get("customized_resume_id") or "").strip()
    if not customized_resume_id:
        abort(400, "This job does not have a generated resume yet.")

    customized_resume_record = supabase_utils.get_customized_resume(customized_resume_id)
    if not customized_resume_record:
        abort(404)

    cover_letter_record = supabase_utils.get_cover_letter_by_job_id(cleaned_job_id) or {}
    stored_header_title = str(customized_resume_record.get("header_title") or "").strip()
    header_title = stored_header_title or str(job_record.get("job_title") or "").strip()
    resume_download_url = (
        url_for("download_resume", resume_id=customized_resume_id)
        if str(customized_resume_record.get("resume_link") or "").strip()
        else ""
    )

    try:
        current_resume = Resume.model_validate(customized_resume_record)
    except Exception as exc:
        abort(500, f"Failed to parse customized resume: {exc}")

    save_error = ""
    save_success = request.args.get("saved") == "1"
    resume_json_text = _resume_to_pretty_json(current_resume)
    cover_letter_text = str(cover_letter_record.get("cover_letter_text") or "").strip()

    if request.method == "POST":
        header_title = str(request.form.get("header_title") or "").strip()
        resume_json_text = str(request.form.get("resume_json") or "").strip()
        cover_letter_text = str(request.form.get("cover_letter_text") or "").strip()

        try:
            updated_resume = Resume.model_validate_json(resume_json_text)
        except ValidationError as exc:
            save_error = str(exc)
        else:
            resume_path = str(customized_resume_record.get("resume_link") or "").strip()
            if not resume_path:
                abort(500, "Existing resume path is missing.")

            try:
                resume_pdf = pdf_generator.create_resume_pdf(
                    updated_resume,
                    header_title=header_title,
                )
            except Exception as exc:
                save_error = f"Failed to regenerate resume PDF: {exc}"
            else:
                uploaded_resume_path = supabase_utils.upload_customized_resume_to_storage(
                    resume_pdf,
                    resume_path,
                )
                if not uploaded_resume_path:
                    save_error = "Failed to upload the updated resume PDF."
                elif not supabase_utils.update_customized_resume(
                    customized_resume_id,
                    updated_resume,
                    uploaded_resume_path,
                    header_title=header_title,
                ):
                    save_error = "Failed to update the customized resume record."
                else:
                    if cover_letter_text:
                        cover_letter_path = str(cover_letter_record.get("cover_letter_link") or "").strip()
                        if not cover_letter_path:
                            cover_letter_path = _build_cover_letter_storage_path(
                                cleaned_job_id,
                                job_record.get("company"),
                            )

                        try:
                            cover_letter_pdf = create_cover_letter_pdf(
                                applicant_name=updated_resume.name,
                                email=updated_resume.email,
                                phone=updated_resume.phone,
                                location=updated_resume.location,
                                cover_letter_text=cover_letter_text,
                            )
                        except Exception as exc:
                            save_error = f"Resume saved, but cover letter PDF failed: {exc}"
                        else:
                            uploaded_cover_letter_path = supabase_utils.upload_cover_letter_to_storage(
                                cover_letter_pdf,
                                cover_letter_path,
                            )
                            if not uploaded_cover_letter_path:
                                save_error = "Resume saved, but cover letter upload failed."
                            else:
                                cover_letter_id = supabase_utils.save_customized_cover_letter(
                                    job_id=cleaned_job_id,
                                    customized_resume_id=customized_resume_id,
                                    company=str(job_record.get("company") or "").strip(),
                                    job_title=str(job_record.get("job_title") or "").strip(),
                                    cover_letter_text=cover_letter_text,
                                    cover_letter_path=uploaded_cover_letter_path,
                                    llm_model="manual_edit",
                                )
                                if not cover_letter_id:
                                    save_error = "Resume saved, but cover letter record update failed."

                    if not save_error:
                        return redirect(url_for("edit_documents", job_id=cleaned_job_id, saved=1))

            if not save_error:
                save_error = "Unable to save your manual edits."

    return render_template(
        "edit_documents.html",
        job=job_record,
        header_title=header_title,
        resume_json=resume_json_text,
        cover_letter_text=cover_letter_text,
        save_error=save_error,
        save_success=save_success,
        resume_download_url=resume_download_url,
        cover_letter_download_url=(
            url_for("download_cover_letter", job_id=cleaned_job_id)
            if cover_letter_record.get("cover_letter_link")
            else ""
        ),
    )


@app.post("/run")
def run_action():
    current = _snapshot_state()
    if current["status"] == "running":
        return jsonify({"ok": False, "error": "Another task is already running."}), 409

    action = (request.form.get("action") or "").strip()
    job_id = (request.form.get("job_id") or "").strip()
    job_url = (request.form.get("job_url") or "").strip()
    email_override = (request.form.get("email_override") or "").strip()
    resume_provider = (request.form.get("resume_provider") or "").strip()
    count_raw = (request.form.get("count") or "").strip()
    count = None
    if count_raw:
        try:
            count = int(count_raw)
        except ValueError:
            return jsonify({"ok": False, "error": "Resume count must be a valid integer."}), 400

    try:
        label, command, env_overrides = _build_command(
            action,
            job_id or None,
            count,
            email_override or None,
            job_url or None,
            resume_provider or None,
        )
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400

    _run_command_in_background(label, command, env_overrides=env_overrides)
    if request.accept_mimetypes.best == "application/json":
        return jsonify({"ok": True})
    return redirect(url_for("index"))


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5000"))
    debug_enabled = _env_flag("APP_DEBUG") or _env_flag("FLASK_DEBUG")
    app.run(
        host="0.0.0.0",
        port=port,
        debug=debug_enabled,
        use_reloader=debug_enabled,
    )
