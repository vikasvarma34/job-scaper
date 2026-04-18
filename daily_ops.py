import argparse
import csv
import logging
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import config
import supabase_utils
from supabase_utils import supabase


logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")


def _run_python_script(script_name: str, args: list[str] | None = None) -> bool:
    script_path = Path(script_name)
    if not script_path.exists():
        logging.error(f"Script not found: {script_name}")
        return False

    logging.info(f"Running {script_name}...")
    cmd = [sys.executable, str(script_path), *(args or [])]
    result = subprocess.run(cmd, check=False)
    if result.returncode != 0:
        logging.error(f"{script_name} failed with exit code {result.returncode}.")
        return False

    logging.info(f"{script_name} completed successfully.")
    return True


def run_cycle(skip_scrape: bool, skip_score: bool, skip_customize: bool, customize_limit: int | None) -> int:
    steps: list[tuple[str, list[str]]] = []
    if not skip_scrape:
        steps.append(("scraper.py", []))
    if not skip_score:
        steps.append(("score_jobs.py", []))
    if not skip_customize:
        customize_args = ["--limit", str(customize_limit)] if customize_limit is not None else []
        steps.append(("custom_resume_generator.py", customize_args))

    if not steps:
        logging.warning("No steps selected. Nothing to run.")
        return 0

    for step, step_args in steps:
        ok = _run_python_script(step, step_args)
        if not ok:
            return 1
    return 0


def mark_applied(job_ids: list[str]) -> int:
    updated, requested = supabase_utils.mark_jobs_as_applied(job_ids)
    if requested == 0:
        return 1
    if updated == 0:
        logging.warning("No jobs were updated. Check job IDs.")
    return 0


def _list_root_file_paths(bucket_name: str) -> list[str]:
    """
    Lists file paths in a bucket recursively.
    """
    paths: list[str] = []
    pending_dirs = [""]

    while pending_dirs:
        current_dir = pending_dirs.pop()
        offset = 0
        page_size = 100
        while True:
            items = supabase.storage.from_(bucket_name).list(
                current_dir,
                {
                    "limit": page_size,
                    "offset": offset,
                    "sortBy": {"column": "name", "order": "asc"},
                },
            )
            if not items:
                break

            for item in items:
                name = item.get("name")
                if not name:
                    continue
                item_id = item.get("id")
                child_path = f"{current_dir}/{name}".strip("/")
                if item_id is None:
                    pending_dirs.append(child_path)
                else:
                    paths.append(child_path)

            if len(items) < page_size:
                break
            offset += page_size

    return paths


def _remove_bucket_files(bucket_name: str, keep_files: set[str] | None = None) -> int:
    keep = keep_files or set()
    all_paths = _list_root_file_paths(bucket_name)
    to_delete = [p for p in all_paths if p not in keep]
    if not to_delete:
        logging.info(f"No files to delete in bucket '{bucket_name}'.")
        return 0

    logging.info(f"Deleting {len(to_delete)} files from bucket '{bucket_name}'.")
    supabase.storage.from_(bucket_name).remove(to_delete)
    return len(to_delete)


def cleanup_for_free_tier(delete_base_resume: bool, delete_source_resume: bool) -> int:
    """
    Clears run artifacts so free-tier storage stays low.
    By default keeps:
    - base_resume row
    - resumes/resume.pdf
    """
    try:
        applied_jobs_response = (
            supabase.table(config.SUPABASE_TABLE_NAME)
            .select("job_id, customized_resume_id")
            .eq("status", "applied")
            .execute()
        )
        applied_jobs = applied_jobs_response.data or []
        applied_job_ids = [str(row.get("job_id") or "").strip() for row in applied_jobs if str(row.get("job_id") or "").strip()]
        applied_resume_ids = [
            str(row.get("customized_resume_id") or "").strip()
            for row in applied_jobs
            if str(row.get("customized_resume_id") or "").strip()
        ]

        # 1) Delete all personalized PDFs from Storage.
        _remove_bucket_files(config.SUPABASE_STORAGE_BUCKET)

        # 2) Delete generated cover-letter rows not tied to applied jobs.
        cover_letter_table = getattr(config, "SUPABASE_CUSTOMIZED_COVER_LETTERS_TABLE_NAME", "")
        if cover_letter_table:
            cover_rows = (
                supabase.table(cover_letter_table)
                .select("job_id")
                .execute()
            ).data or []
            delete_cover_job_ids = [
                str(row.get("job_id") or "").strip()
                for row in cover_rows
                if str(row.get("job_id") or "").strip() and str(row.get("job_id") or "").strip() not in set(applied_job_ids)
            ]
            if delete_cover_job_ids:
                supabase.table(cover_letter_table).delete().in_("job_id", delete_cover_job_ids).execute()
            if applied_job_ids:
                supabase.table(cover_letter_table).update({"cover_letter_link": None}).in_("job_id", applied_job_ids).execute()
            logging.info("Cleared non-applied customized_cover_letters rows.")

        # 3) Delete generated resume rows not tied to applied jobs.
        all_resume_rows = (
            supabase.table(config.SUPABASE_CUSTOMIZED_RESUMES_TABLE_NAME)
            .select("id")
            .execute()
        ).data or []
        delete_resume_ids = [
            str(row.get("id") or "").strip()
            for row in all_resume_rows
            if str(row.get("id") or "").strip() and str(row.get("id") or "").strip() not in set(applied_resume_ids)
        ]
        if delete_resume_ids:
            supabase.table(config.SUPABASE_CUSTOMIZED_RESUMES_TABLE_NAME).delete().in_("id", delete_resume_ids).execute()
        if applied_resume_ids:
            supabase.table(config.SUPABASE_CUSTOMIZED_RESUMES_TABLE_NAME).update({"resume_link": None}).in_("id", applied_resume_ids).execute()
        logging.info("Cleared non-applied customized_resumes rows.")

        # 4) Delete jobs so next day starts clean, but keep applied history.
        all_job_rows = (
            supabase.table(config.SUPABASE_TABLE_NAME)
            .select("job_id")
            .execute()
        ).data or []
        delete_job_ids = [
            str(row.get("job_id") or "").strip()
            for row in all_job_rows
            if str(row.get("job_id") or "").strip() and str(row.get("job_id") or "").strip() not in set(applied_job_ids)
        ]
        if delete_job_ids:
            supabase.table(config.SUPABASE_TABLE_NAME).delete().in_("job_id", delete_job_ids).execute()
        logging.info("Cleared non-applied jobs rows.")

        # 5) Optional base resume cleanup.
        if delete_base_resume:
            supabase.table(config.SUPABASE_BASE_RESUME_TABLE_NAME).delete().neq(
                "id", "00000000-0000-0000-0000-000000000000"
            ).execute()
            logging.info("Cleared base_resume table.")

        # 6) Optional source resume cleanup.
        if delete_source_resume:
            _remove_bucket_files(config.SUPABASE_RESUME_STORAGE_BUCKET)
        else:
            _remove_bucket_files(config.SUPABASE_RESUME_STORAGE_BUCKET, keep_files={"resume.pdf"})

        logging.info("Cleanup completed.")
        return 0
    except Exception as e:
        logging.error(f"Cleanup failed: {e}")
        return 1


def export_applied_jobs_csv(output_path: str) -> int:
    """
    Exports applied jobs to CSV so history is retained even after cleanup.
    """
    try:
        response = (
            supabase.table(config.SUPABASE_TABLE_NAME)
            .select(
                "job_id, company, job_title, location, provider, job_url, status, "
                "application_date, posted_at, scraped_at, customized_resume_id, notes"
            )
            .eq("status", "applied")
            .order("application_date", desc=True)
            .execute()
        )
        rows = response.data or []

        resume_ids = sorted(
            {
                str(r.get("customized_resume_id")).strip()
                for r in rows
                if r.get("customized_resume_id")
            }
        )
        resume_link_map: dict[str, str] = {}
        if resume_ids:
            resume_response = (
                supabase.table(config.SUPABASE_CUSTOMIZED_RESUMES_TABLE_NAME)
                .select("id, resume_link")
                .in_("id", resume_ids)
                .execute()
            )
            for rr in resume_response.data or []:
                rid = str(rr.get("id") or "").strip()
                if rid:
                    resume_link_map[rid] = rr.get("resume_link") or ""

        output_file = Path(output_path)
        output_file.parent.mkdir(parents=True, exist_ok=True)

        fieldnames = [
            "job_id",
            "company",
            "job_title",
            "location",
            "provider",
            "job_url",
            "status",
            "application_date",
            "posted_at",
            "scraped_at",
            "linkedin_job_url",
            "customized_resume_id",
            "customized_resume_path",
            "resume_bucket",
            "notes",
        ]
        with output_file.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            for row in rows:
                provider = (row.get("provider") or "").strip().lower()
                job_id = str(row.get("job_id") or "").strip()
                linkedin_url = f"https://www.linkedin.com/jobs/view/{job_id}/" if provider == "linkedin" and job_id else ""
                job_url = str(row.get("job_url") or "").strip() or linkedin_url

                resume_id = str(row.get("customized_resume_id") or "").strip()
                resume_path = resume_link_map.get(resume_id, "")

                writer.writerow(
                    {
                        "job_id": row.get("job_id"),
                        "company": row.get("company"),
                        "job_title": row.get("job_title"),
                        "location": row.get("location"),
                        "provider": row.get("provider"),
                        "job_url": job_url,
                        "status": row.get("status"),
                        "application_date": row.get("application_date"),
                        "posted_at": row.get("posted_at"),
                        "scraped_at": row.get("scraped_at"),
                        "linkedin_job_url": linkedin_url,
                        "customized_resume_id": resume_id,
                        "customized_resume_path": resume_path,
                        "resume_bucket": config.SUPABASE_STORAGE_BUCKET,
                        "notes": row.get("notes"),
                    }
                )

        logging.info(f"Exported {len(rows)} applied jobs to {output_file}.")
        return 0
    except Exception as e:
        logging.error(f"Failed to export applied jobs: {e}")
        return 1


def _load_job_ids_from_file(file_path: str) -> list[str]:
    path = Path(file_path)
    if not path.exists():
        logging.error(f"IDs file not found: {file_path}")
        return []
    ids = []
    for line in path.read_text(encoding="utf-8").splitlines():
        value = line.strip()
        if value:
            ids.append(value)
    return ids


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Daily helper ops for the job scraper project.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    cycle_parser = subparsers.add_parser("run-cycle", help="Run scrape -> score -> customize sequence.")
    cycle_parser.add_argument("--skip-scrape", action="store_true")
    cycle_parser.add_argument("--skip-score", action="store_true")
    cycle_parser.add_argument("--skip-customize", action="store_true")
    cycle_parser.add_argument(
        "--customize-limit",
        type=int,
        help="Override how many top jobs to customize in this run.",
    )

    applied_parser = subparsers.add_parser("mark-applied", help="Mark one or more jobs as applied.")
    applied_parser.add_argument("job_ids", nargs="*", help="Job IDs to mark as applied.")
    applied_parser.add_argument("--ids-file", help="Optional text file with one job_id per line.")

    cleanup_parser = subparsers.add_parser("cleanup", help="Clean run artifacts for free-tier usage.")
    cleanup_parser.add_argument(
        "--delete-base-resume",
        action="store_true",
        help="Also clear base_resume table.",
    )
    cleanup_parser.add_argument(
        "--delete-source-resume",
        action="store_true",
        help="Also delete files in resumes bucket (including resume.pdf).",
    )

    export_parser = subparsers.add_parser("export-applied", help="Export applied jobs to a local CSV file.")
    export_parser.add_argument(
        "--output",
        default=f"applied_jobs_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv",
        help="Destination CSV path.",
    )

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    if args.command == "run-cycle":
        if args.customize_limit is not None and args.customize_limit <= 0:
            logging.error("--customize-limit must be a positive integer.")
            return 1
        return run_cycle(args.skip_scrape, args.skip_score, args.skip_customize, args.customize_limit)

    if args.command == "mark-applied":
        ids = list(args.job_ids or [])
        if args.ids_file:
            ids.extend(_load_job_ids_from_file(args.ids_file))
        return mark_applied(ids)

    if args.command == "cleanup":
        return cleanup_for_free_tier(
            delete_base_resume=args.delete_base_resume,
            delete_source_resume=args.delete_source_resume,
        )

    if args.command == "export-applied":
        return export_applied_jobs_csv(args.output)

    parser.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
