import argparse
import asyncio
import hashlib
import html as html_lib
import json
import logging
import re
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse, urlunparse

import requests
from bs4 import BeautifulSoup
from markdownify import markdownify as md

import config
import custom_resume_generator
import resume_validator
import supabase_utils
from llm_client import primary_client
from models import ATSKeywordPlan, JobPostingIntakeOutput, Resume

try:
    from playwright.async_api import async_playwright
except Exception:  # pragma: no cover - optional runtime fallback
    async_playwright = None


logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

FETCH_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Cache-Control": "no-cache",
}
MAX_PAGE_CONTENT_CHARS = 24000


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize_url(raw_url: str) -> str:
    cleaned = str(raw_url or "").strip()
    if not cleaned:
        return ""
    if not re.match(r"^https?://", cleaned, flags=re.IGNORECASE):
        cleaned = "https://" + cleaned
    parsed = urlparse(cleaned)
    normalized = parsed._replace(fragment="")
    return urlunparse(normalized)


def _provider_from_url(url: str) -> str:
    hostname = urlparse(url).netloc.lower().strip()
    hostname = hostname[4:] if hostname.startswith("www.") else hostname
    return hostname or "manual_url"


def _build_manual_job_id(url: str) -> str:
    digest = hashlib.sha1(url.encode("utf-8")).hexdigest()[:16]
    return f"manual_{digest}"


def _clean_markdown_text(text: str) -> str:
    lines = str(text or "").splitlines()
    cleaned_lines: list[str] = []
    previous_blank = False
    for line in lines:
        compact = re.sub(r"\s+", " ", line).strip()
        if not compact:
            if not previous_blank:
                cleaned_lines.append("")
            previous_blank = True
            continue
        cleaned_lines.append(compact)
        previous_blank = False
    return "\n".join(cleaned_lines).strip()


def _html_to_markdown(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup.find_all(
        ["script", "style", "nav", "footer", "header", "iframe", "noscript", "form", "svg", "button", "aside"]
    ):
        tag.decompose()
    cleaned_html = str(soup)
    markdown_text = md(
        cleaned_html,
        heading_style="ATX",
        bullets="-",
        strip=["img"],
    )
    return _clean_markdown_text(markdown_text)


def _html_has_useful_text(html: str) -> bool:
    text = BeautifulSoup(html or "", "html.parser").get_text(" ", strip=True)
    text = re.sub(r"\s+", " ", text).strip()
    return len(text) >= 800


def _fetch_with_requests(url: str) -> tuple[str, str] | None:
    try:
        response = requests.get(
            url,
            headers=FETCH_HEADERS,
            timeout=getattr(config, "REQUEST_TIMEOUT", 30),
            allow_redirects=True,
        )
        html = response.text or ""
        final_url = response.url or url
        if html and _html_has_useful_text(html):
            logging.info("Fetched job page with requests: %s", final_url)
            return final_url, html
        logging.warning("Requests fetch for %s returned insufficient HTML content.", final_url)
    except Exception as exc:
        logging.warning("Requests fetch failed for %s: %s", url, exc)
    return None


def _extract_naukri_job_id(url: str) -> str:
    hostname = urlparse(url).netloc.lower().strip()
    if "naukri.com" not in hostname:
        return ""

    path = urlparse(url).path.rstrip("/")
    match = re.search(r"-(\d{10,})$", path)
    return match.group(1) if match else ""


def _normalize_api_html(raw_html: str | None) -> str:
    text = str(raw_html or "").strip()
    if not text:
        return ""
    markdown_text = _html_to_markdown(html_lib.unescape(text))
    return markdown_text or BeautifulSoup(html_lib.unescape(text), "html.parser").get_text(" ", strip=True)


def _format_experience(min_exp: str, max_exp: str) -> str:
    if min_exp and max_exp:
        return f"{min_exp}-{max_exp} years" if min_exp != max_exp else f"{min_exp} years"
    if min_exp:
        return f"{min_exp}+ years"
    if max_exp:
        return f"up to {max_exp} years"
    return ""


def _fetch_with_naukri_api(url: str) -> tuple[str, str] | None:
    job_id = _extract_naukri_job_id(url)
    if not job_id:
        return None

    headers = dict(FETCH_HEADERS)
    headers.update(
        {
            "Accept": "application/json,text/plain,*/*",
            "appid": "109",
            "systemid": "109",
            "origin": "https://www.naukri.com",
            "referer": url,
        }
    )

    for api_url in (
        f"https://www.naukri.com/jobapi/v2/job/{job_id}",
        f"https://www.naukri.com/jobapi/v1/job/{job_id}",
    ):
        try:
            response = requests.get(
                api_url,
                headers=headers,
                timeout=getattr(config, "REQUEST_TIMEOUT", 30),
            )
            response.raise_for_status()
            payload = response.json()
        except Exception as exc:
            logging.warning("Naukri API fetch failed for %s via %s: %s", job_id, api_url, exc)
            continue

        job_data = payload.get("job") if isinstance(payload, dict) else None
        if not isinstance(job_data, dict):
            continue

        final_url = str(job_data.get("job_static_url") or url).strip() or url
        if final_url.startswith("/"):
            final_url = "https://www.naukri.com" + final_url

        description = _normalize_api_html(job_data.get("jobDesc"))
        company_profile = _normalize_api_html(job_data.get("companyProfile"))
        keywords = str(job_data.get("keywords") or "").strip()
        title = str(job_data.get("post") or "").strip()
        company = str(job_data.get("companyName") or job_data.get("CONTCOM") or "").strip()
        location = str(job_data.get("location") or job_data.get("cityfield") or "").strip()
        min_exp = str(job_data.get("minExp") or "").strip()
        max_exp = str(job_data.get("maxExp") or "").strip()
        experience = _format_experience(min_exp, max_exp)
        employment_type = str(job_data.get("employmentType") or "").strip()

        structured_data = {
            "@context": "https://schema.org",
            "@type": "JobPosting",
            "title": title,
            "hiringOrganization": {"@type": "Organization", "name": company},
            "jobLocation": location,
            "employmentType": employment_type,
            "experienceRequirements": experience,
            "skills": keywords,
            "description": description,
            "url": final_url,
        }

        structured_json = json.dumps(structured_data, ensure_ascii=False).replace("</", "<\\/")
        html = f"""
<!doctype html>
<html>
<head>
  <title>{html_lib.escape(title)} - {html_lib.escape(company)}</title>
  <meta name="description" content="{html_lib.escape(description[:500])}">
  <script type="application/ld+json">{structured_json}</script>
</head>
<body>
  <main>
    <h1>{html_lib.escape(title)}</h1>
    <p>Company: {html_lib.escape(company)}</p>
    <p>Location: {html_lib.escape(location)}</p>
    <p>Experience: {html_lib.escape(experience)}</p>
    <p>Employment type: {html_lib.escape(employment_type)}</p>
    <p>Keywords: {html_lib.escape(keywords)}</p>
    <h2>Job Description</h2>
    <div>{html_lib.escape(description)}</div>
    <h2>Company Profile</h2>
    <div>{html_lib.escape(company_profile)}</div>
  </main>
</body>
</html>
""".strip()

        if html and _html_has_useful_text(html):
            logging.info("Fetched Naukri job page with API: %s", final_url)
            return final_url, html
        logging.warning("Naukri API fetch for %s returned insufficient content.", job_id)

    return None


async def _fetch_with_playwright(url: str) -> tuple[str, str] | None:
    if async_playwright is None:
        logging.warning("Playwright is not available for fallback fetching.")
        return None

    try:
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            page = await browser.new_page()
            await page.goto(url, wait_until="domcontentloaded", timeout=45000)
            await page.wait_for_timeout(1800)
            final_url = page.url or url
            html = await page.content() or ""
            await browser.close()
        if html and _html_has_useful_text(html):
            logging.info("Fetched job page with Playwright: %s", final_url)
            return final_url, html
        logging.warning("Playwright fetch for %s returned insufficient HTML content.", final_url)
    except Exception as exc:
        logging.warning("Playwright fetch failed for %s: %s", url, exc)
    return None


def _extract_page_payload(url: str, html: str) -> dict[str, Any]:
    soup = BeautifulSoup(html, "html.parser")
    page_title = soup.title.get_text(" ", strip=True) if soup.title else ""
    meta_description = ""
    meta_tag = soup.find("meta", attrs={"name": re.compile(r"description", re.I)}) or soup.find(
        "meta", attrs={"property": re.compile(r"description", re.I)}
    )
    if meta_tag:
        meta_description = str(meta_tag.get("content") or "").strip()

    json_ld_blocks: list[Any] = []
    for script_tag in soup.find_all("script", attrs={"type": re.compile(r"ld\+json", re.I)}):
        raw_block = (script_tag.string or script_tag.get_text() or "").strip()
        if not raw_block:
            continue
        try:
            parsed = json.loads(raw_block)
        except Exception:
            parsed = raw_block[:4000]
        json_ld_blocks.append(parsed)

    markdown_text = _html_to_markdown(html)
    if len(markdown_text) > MAX_PAGE_CONTENT_CHARS:
        markdown_text = markdown_text[:MAX_PAGE_CONTENT_CHARS].rsplit("\n", 1)[0].strip()

    return {
        "url": url,
        "page_title": page_title,
        "meta_description": meta_description,
        "json_ld": json_ld_blocks,
        "page_markdown": markdown_text,
    }


def _build_intake_prompt(page_payload: dict[str, Any]) -> str:
    return f"""
Extract structured job details and resume-targeting skills from this job-posting page.

Source URL:
{page_payload["url"]}

Page title:
{page_payload["page_title"]}

Meta description:
{page_payload["meta_description"]}

Structured data blocks:
{json.dumps(page_payload["json_ld"], indent=2, ensure_ascii=False)}

Main page content:
{page_payload["page_markdown"]}

Return:
- is_job_posting: true only if this looks like a real active job posting page
- job_title
- company
- location
- level
- description: a clean, ATS-usable plain-text job description summary built from the page content only. Capture the real responsibilities, skills, qualifications, and domain context. Remove navigation, legal text, cookie banners, and repeated filler. Make it detailed enough for resume tailoring.
- hard_skills: technical skills, tools, frameworks, platforms, databases, APIs, cloud/devops, testing, security, and concise engineering practices
- soft_skills: concise collaboration, communication, ownership, execution, teamwork, leadership, problem-solving, and work-style traits

Rules:
- Use only the supplied page data.
- Do not invent missing details.
- If the page looks expired, unavailable, generic, or not actually a job posting, set is_job_posting to false.
- Keep hard_skills and soft_skills concise, useful, and resume-usable.
- Prefer exact or near-exact wording from the posting when it helps.
- Remove duplicates.
- The description should be plain text, not markdown or bullets.
""".strip()


def _extract_job_details_with_llm(page_payload: dict[str, Any]) -> JobPostingIntakeOutput:
    system_prompt = """
You are an expert job-posting parser and a precise JSON generator.

Rules:
- Return exactly one valid JSON object matching the required schema.
- Do not output markdown, commentary, or extra text.
- Extract only what is supported by the provided page content.
- Focus on real job details that are useful for ATS-targeted resume generation.
- Reason privately and output only the final JSON.
""".strip()

    llm_output = primary_client.generate_content(
        prompt=_build_intake_prompt(page_payload),
        system_prompt=system_prompt,
        temperature=0.3,
        response_format=JobPostingIntakeOutput,
    )
    logging.info("Manual job-link extraction response:\n%s", llm_output)
    parsed = JobPostingIntakeOutput.model_validate_json(llm_output)
    return parsed


def _build_manual_job_row(
    *,
    job_id: str,
    job_url: str,
    intake: JobPostingIntakeOutput,
    existing_job: dict[str, Any] | None,
) -> dict[str, Any]:
    existing_status = str((existing_job or {}).get("status") or "").strip().lower()
    payload = {
        "job_id": job_id,
        "company": str(intake.company or "").strip(),
        "job_title": str(intake.job_title or "").strip(),
        "description": str(intake.description or "").strip(),
        "location": str(intake.location or "").strip() or None,
        "level": str(intake.level or "").strip() or None,
        "provider": _provider_from_url(job_url),
        "job_url": job_url,
        "scraped_at": _utc_now_iso(),
        "is_active": True,
        "job_state": "new",
        "status": "applied" if existing_status == "applied" else "new",
    }
    return payload


async def _generate_resume_for_manual_job(
    *,
    job_details: dict[str, Any],
    base_resume: Resume,
    keyword_plan: ATSKeywordPlan,
    email_override: str | None = None,
    resume_mode: str = custom_resume_generator.RESUME_MODE_ONE_PAGE,
) -> int:
    selected_resume_mode = custom_resume_generator._normalize_resume_mode(resume_mode)  # noqa: SLF001
    rewritten_resume = await custom_resume_generator.rewrite_resume_with_keyword_plan(
        full_resume=base_resume,
        job_details=job_details,
        keyword_plan=keyword_plan,
        resume_mode=selected_resume_mode,
    )

    personalized_resume = custom_resume_generator._apply_two_step_rewrite_to_resume(  # noqa: SLF001
        base_resume=base_resume,
        rewrite_output=rewritten_resume,
    )
    personalized_resume = custom_resume_generator._normalize_personalized_resume_output(  # noqa: SLF001
        base_resume=base_resume,
        personalized_resume=personalized_resume,
    )
    personalized_resume = custom_resume_generator._apply_resume_mode_constraints(  # noqa: SLF001
        personalized_resume,
        selected_resume_mode,
    )
    if (
        selected_resume_mode == custom_resume_generator.RESUME_MODE_PROJECTS
        and base_resume.projects
        and not personalized_resume.projects
    ):
        logging.info(
            "Project Mode returned no projects for manual job %s. Falling back to the strongest base project.",
            job_details.get("job_id"),
        )
        personalized_resume.projects = custom_resume_generator._fallback_projects_for_project_mode(  # noqa: SLF001
            base_resume,
            keyword_plan,
            max_projects=1,
        )
        personalized_resume = custom_resume_generator._apply_resume_mode_constraints(  # noqa: SLF001
            personalized_resume,
            selected_resume_mode,
        )

    sections_to_validate = ["experience"]
    if selected_resume_mode == custom_resume_generator.RESUME_MODE_PROJECTS:
        sections_to_validate.append("projects")

    for section_name in sections_to_validate:
        is_valid, reason = custom_resume_generator.validate_customization(
            section_name,
            getattr(base_resume, section_name),
            getattr(personalized_resume, section_name),
            allow_project_technology_changes=(section_name == "projects"),
        )
        if not is_valid:
            raise ValueError(f"Manual job-link validation failed for {section_name}: {reason}")

    header_title = custom_resume_generator._normalize_header_title(  # noqa: SLF001
        raw_title=job_details.get("job_title"),
        rewritten_title=rewritten_resume.header_title,
    )

    personalized_resume = custom_resume_generator._apply_job_contact_overrides(  # noqa: SLF001
        personalized_resume,
        job_details,
        email_override=email_override,
    )

    personalized_resume, pdf_bytes = custom_resume_generator.create_resume_pdf_for_mode(
        personalized_resume,
        header_title=header_title,
        resume_mode=selected_resume_mode,
    )
    pdf_is_valid, pdf_issues = resume_validator.validate_generated_resume_pdf(
        pdf_bytes=pdf_bytes,
        resume_data=personalized_resume,
        header_title=header_title,
    )
    if not pdf_is_valid:
        raise ValueError("PDF validation failed: " + "; ".join(pdf_issues))

    destination_path = custom_resume_generator._build_resume_filename(  # noqa: SLF001
        job_id=str(job_details.get("job_id") or "").strip(),
        company=job_details.get("company"),
    )
    resume_path = supabase_utils.upload_customized_resume_to_storage(pdf_bytes, destination_path)
    if not resume_path:
        raise ValueError("Failed to upload generated resume PDF.")

    customized_resume_id = supabase_utils.save_customized_resume(
        personalized_resume,
        resume_path,
        header_title=header_title,
    )
    if not customized_resume_id:
        raise ValueError("Failed to save customized resume row.")

    new_status = None if str(job_details.get("status") or "").strip().lower() == "applied" else "resume_generated"
    if not supabase_utils.update_job_with_resume_link(
        str(job_details.get("job_id") or "").strip(),
        customized_resume_id,
        new_status=new_status,
    ):
        raise ValueError("Failed to update the job with the generated resume.")

    logging.info(
        "Successfully generated resume for manual job %s with customized_resume_id %s.",
        job_details.get("job_id"),
        customized_resume_id,
    )
    return 0


async def process_job_link(
    job_url: str,
    email_override: str | None = None,
    resume_mode: str = custom_resume_generator.RESUME_MODE_ONE_PAGE,
) -> int:
    normalized_url = _normalize_url(job_url)
    if not normalized_url:
        logging.error("A valid job URL is required.")
        return 1

    base_resume = custom_resume_generator._load_base_resume_details()  # noqa: SLF001
    if not base_resume:
        logging.error("Could not load base resume details.")
        return 1

    fetched = _fetch_with_naukri_api(normalized_url) or _fetch_with_requests(normalized_url)
    if not fetched:
        fetched = await _fetch_with_playwright(normalized_url)
    if not fetched:
        logging.error("Could not fetch readable content from the job URL.")
        return 1

    final_url, html = fetched
    page_payload = _extract_page_payload(final_url, html)
    intake = _extract_job_details_with_llm(page_payload)

    if not intake.is_job_posting:
        logging.error("The provided URL does not appear to be an active job posting.")
        return 1

    keyword_plan = custom_resume_generator._postprocess_keyword_plan(  # noqa: SLF001
        ATSKeywordPlan(
            hard_skills=list(intake.hard_skills),
            soft_skills=list(intake.soft_skills),
        )
    )

    manual_job_id = _build_manual_job_id(final_url)
    existing_job = supabase_utils.get_job_by_id(manual_job_id)
    job_row = _build_manual_job_row(
        job_id=manual_job_id,
        job_url=final_url,
        intake=intake,
        existing_job=existing_job,
    )
    upserted_row = supabase_utils.upsert_job_record(job_row)
    if not upserted_row:
        logging.error("Failed to save the extracted job details to Supabase.")
        return 1

    logging.info("Saved manual job link as job_id %s.", manual_job_id)
    return await _generate_resume_for_manual_job(
        job_details=job_row,
        base_resume=base_resume,
        keyword_plan=keyword_plan,
        email_override=email_override,
        resume_mode=resume_mode,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Import a job from a pasted URL and generate a customized resume.")
    parser.add_argument("--job-url", required=True, help="The job posting URL to ingest.")
    parser.add_argument(
        "--email-override",
        help="Optional email override for this manual generation run.",
    )
    parser.add_argument(
        "--resume-mode",
        choices=[
            custom_resume_generator.RESUME_MODE_ONE_PAGE,
            custom_resume_generator.RESUME_MODE_PROJECTS,
        ],
        default=custom_resume_generator.RESUME_MODE_ONE_PAGE,
        help="Choose resume output mode. Defaults to one_page; use project_mode to include Projects.",
    )
    args = parser.parse_args()
    return asyncio.run(
        process_job_link(
            args.job_url,
            email_override=args.email_override,
            resume_mode=args.resume_mode,
        )
    )


if __name__ == "__main__":
    raise SystemExit(main())
