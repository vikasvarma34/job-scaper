import io
import os
import subprocess
import sys
import threading
import uuid
from collections import deque
from datetime import datetime, timezone
from pathlib import Path

from flask import Flask, abort, jsonify, redirect, render_template, request, send_file, url_for

import config
import supabase_utils


ROOT_DIR = Path(__file__).resolve().parent

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


def _env_flag(name: str, default: bool = False) -> bool:
    value = str(os.environ.get(name, "")).strip().lower()
    if not value:
        return default
    return value in {"1", "true", "yes", "on"}


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


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


def _run_command_in_background(label: str, command: list[str]) -> None:
    env = os.environ.copy()
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

    def _worker() -> None:
        process = subprocess.Popen(
            command,
            cwd=str(ROOT_DIR),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            env=env,
        )

        assert process.stdout is not None
        for line in process.stdout:
            _append_log(line)

        returncode = process.wait()
        _append_log(f"Finished with exit code {returncode}.")
        _set_state(
            status="success" if returncode == 0 else "failed",
            finished_at=_utc_now_iso(),
            returncode=returncode,
        )

    threading.Thread(target=_worker, daemon=True).start()


def _build_command(action: str, job_id: str | None, count: int | None) -> tuple[str, list[str]]:
    python = sys.executable

    if action == "scrape":
        return "Scrape Jobs", [python, "scraper.py"]
    if action == "score":
        return "Score Jobs", [python, "score_jobs.py"]
    if action == "generate_next":
        return (
            "Generate Next Resume",
            [python, "custom_resume_generator.py", "--flow", "two_step_ai", "--limit", "1"],
        )
    if action == "generate_selected":
        if count is None or count <= 0:
            raise ValueError("Resume count must be a positive number.")
        return (
            f"Generate {count} Selected Resume{'s' if count != 1 else ''}",
            [python, "custom_resume_generator.py", "--flow", "two_step_ai", "--limit", str(count)],
        )
    if action == "cleanup":
        return "Cleanup", [python, "daily_ops.py", "cleanup"]
    if action == "generate_job":
        if not job_id:
            raise ValueError("Job ID is required.")
        job_record = supabase_utils.get_job_by_id(job_id)
        if not job_record:
            raise ValueError(f"Could not find job_id {job_id}.")

        existing_resume_id = str(job_record.get("customized_resume_id") or "").strip()
        command = [python, "custom_resume_generator.py", "--job-id", job_id, "--flow", "two_step_ai"]
        label = f"Generate Resume for {job_id}"
        if existing_resume_id:
            command.append("--force-regenerate")
            label = f"Regenerate Resume for {job_id}"
        return (
            label,
            command,
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


def _fetch_all_jobs(batch_size: int = 500) -> tuple[list[dict], str | None]:
    try:
        all_jobs: list[dict] = []
        offset = 0

        while True:
            response = (
                supabase_utils.supabase.table(config.SUPABASE_TABLE_NAME)
                .select(
                    "job_id, company, job_title, description, location, provider, status, application_date, "
                    "resume_score, scraped_at, customized_resume_id"
                )
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
    if "__error__" in resume_links:
        return {
            "jobs": [],
            "jobs_error": resume_links["__error__"],
            "stats": {
                "total_jobs": 0,
                "scored_jobs": 0,
                "resumes_generated": 0,
                "pending_jobs": 0,
            },
        }

    for job in jobs:
        resume_id = str(job.get("customized_resume_id") or "").strip()
        job["job_url"] = _build_linkedin_url(job.get("provider"), job.get("job_id"))
        job["resume_download_url"] = f"/resume/{resume_id}/download" if resume_id else ""
        job["has_resume"] = bool(resume_id)
        job["job_id"] = str(job.get("job_id") or "").strip()

    stats = {
        "total_jobs": len(jobs),
        "scored_jobs": sum(1 for job in jobs if job.get("resume_score") is not None),
        "resumes_generated": sum(1 for job in jobs if str(job.get("customized_resume_id") or "").strip()),
        "pending_jobs": sum(1 for job in jobs if not str(job.get("customized_resume_id") or "").strip()),
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


@app.post("/run")
def run_action():
    current = _snapshot_state()
    if current["status"] == "running":
        return jsonify({"ok": False, "error": "Another task is already running."}), 409

    action = (request.form.get("action") or "").strip()
    job_id = (request.form.get("job_id") or "").strip()
    count_raw = (request.form.get("count") or "").strip()
    count = None
    if count_raw:
        try:
            count = int(count_raw)
        except ValueError:
            return jsonify({"ok": False, "error": "Resume count must be a valid integer."}), 400

    try:
        label, command = _build_command(action, job_id or None, count)
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400

    _run_command_in_background(label, command)
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
