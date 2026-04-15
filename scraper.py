import requests
from bs4 import BeautifulSoup
from datetime import datetime, timedelta
import time 
import random 
import logging
import re
from urllib.parse import urlencode
from pydantic import BaseModel, Field
import config
import user_agents
import supabase_utils
from llm_client import primary_client
from markdownify import markdownify as md
import json

# --- Setup Logging ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
USE_CONFIG_GEO_ID = object()


class LinkedInPrefilterOutput(BaseModel):
    ranked_job_ids: list[str] = Field(default_factory=list)


class LinkedInFinalShortlistOutput(BaseModel):
    selected_job_ids: list[str] = Field(default_factory=list)

# Convert HTML description to Markdown
def convert_html_to_markdown(html: str) -> str | None:
    """
    Convert HTML to clean Markdown using BeautifulSoup (to strip unwanted tags)
    and markdownify (to convert the cleaned HTML to Markdown).
    No LLM API calls are made — this is entirely local.
    """
    if not html or not html.strip():
        logging.info("Received empty HTML for Markdown conversion, returning empty string.")
        return ""

    try:
        # Clean the HTML: remove scripts, styles, nav, and other non-content tags
        soup = BeautifulSoup(html, 'html.parser')
        for tag in soup.find_all(['script', 'style', 'nav', 'footer', 'header', 'iframe', 'noscript']):
            tag.decompose()

        cleaned_html = str(soup)

        # Convert cleaned HTML to Markdown
        markdown_text = md(
            cleaned_html,
            heading_style="ATX",
            bullets="-",
            strip=['img'],
        )

        # Clean up excessive blank lines
        lines = markdown_text.splitlines()
        cleaned_lines = []
        prev_blank = False
        for line in lines:
            if not line.strip():
                if not prev_blank:
                    cleaned_lines.append('')
                prev_blank = True
            else:
                cleaned_lines.append(line)
                prev_blank = False
        markdown_text = '\n'.join(cleaned_lines).strip()

        logging.info("Successfully converted HTML to Markdown.")
        return markdown_text if markdown_text else ""
    except Exception as e:
        logging.error(f"Error during HTML to Markdown conversion: {e}")
        return None

def _get_careers_future_job_company_name(job_item: dict) -> str | None:
    """Helper to extract company name, preferring hiringCompany."""
    if not isinstance(job_item, dict):
        return None
    
    hiring_company = job_item.get('hiringCompany')
    if isinstance(hiring_company, dict) and hiring_company.get('name'):
        return hiring_company['name']
    
    posted_company = job_item.get('postedCompany')
    if isinstance(posted_company, dict) and posted_company.get('name'):
        return posted_company['name']
        
    return None

def _is_linkedin_location_allowed(job_location: str | None) -> bool:
    """
    Enforces strict city-only filtering for LinkedIn jobs if configured.
    If LINKEDIN_ALLOWED_CITY_KEYWORDS is empty/missing, all locations are allowed.
    """
    allowed_keywords = getattr(config, "LINKEDIN_ALLOWED_CITY_KEYWORDS", None)
    if not allowed_keywords:
        return True

    if not job_location:
        return False

    normalized_location = job_location.strip().lower()
    for keyword in allowed_keywords:
        if keyword and keyword.strip().lower() in normalized_location:
            return True
    return False

def _is_linkedin_role_allowed(job_title: str | None, job_level: str | None) -> bool:
    """
    Filters out non-target senior/mobile roles and keeps early-mid career roles.
    """
    normalized_title = (job_title or "").strip().lower()
    normalized_level = (job_level or "").strip().lower()

    excluded_title_keywords = getattr(config, "LINKEDIN_EXCLUDED_TITLE_KEYWORDS", None) or []
    for keyword in excluded_title_keywords:
        if keyword and keyword.strip().lower() in normalized_title:
            return False

    required_title_keywords = getattr(config, "LINKEDIN_REQUIRED_TITLE_KEYWORDS", None) or []
    enforce_required_title_keywords = bool(
        getattr(config, "LINKEDIN_ENFORCE_REQUIRED_TITLE_KEYWORDS", False)
    )
    if enforce_required_title_keywords and required_title_keywords:
        has_required = any(
            keyword and keyword.strip().lower() in normalized_title
            for keyword in required_title_keywords
        )
        if not has_required:
            return False

    # LinkedIn often returns non-informative level labels; treat them as missing.
    non_informative_levels = {"", "not applicable", "not specified", "unspecified", "n/a", "na", "none", "-"}
    if normalized_level in non_informative_levels:
        return True

    # If level filtering is disabled/missing, don't block the job.
    allowed_level_keywords = getattr(config, "LINKEDIN_ALLOWED_LEVEL_KEYWORDS", None) or []
    if not allowed_level_keywords:
        return True

    return any(keyword.strip().lower() in normalized_level for keyword in allowed_level_keywords if keyword)


def _extract_experience_requirement(description: str | None) -> str | None:
    """
    Pull a concise years-of-experience requirement from the description when possible.
    """
    text = re.sub(r"\s+", " ", (description or "").lower()).strip()
    if not text:
        return None

    patterns = [
        r"(\d+)\s*(?:-|to)\s*(\d+)\s+years? of experience",
        r"(\d+)\s*-\s*(\d+)\s+years? experience",
        r"at least (\d+)\+?\s+years? of experience",
        r"minimum of (\d+)\+?\s+years? of experience",
        r"(\d+)\+?\s+years? of experience",
        r"(\d+)\+?\s+years? experience",
    ]

    for pattern in patterns:
        match = re.search(pattern, text)
        if not match:
            continue
        if match.lastindex and match.lastindex >= 2:
            return f"{match.group(1)}-{match.group(2)} years"
        return f"{match.group(1)}+ years"

    return None


def _get_min_years_experience(description: str | None) -> int | None:
    requirement = _extract_experience_requirement(description)
    if not requirement:
        return None
    match = re.search(r"(\d+)", requirement)
    if not match:
        return None
    try:
        return int(match.group(1))
    except ValueError:
        return None


def _build_description_excerpt(description: str | None, max_chars: int = 280) -> str:
    text = re.sub(r"\s+", " ", (description or "")).strip()
    if not text:
        return "N/A"
    return text[:max_chars].strip()


def _llm_rank_linkedin_candidates(
    candidate_cards: list[dict],
    search_query: str,
    location: str,
    top_k: int,
) -> list[str]:
    """
    Uses one LLM call to rank candidate LinkedIn job IDs by relevance before detail fetch.
    Falls back to empty list on any error.
    """
    if not candidate_cards or top_k <= 0:
        return []

    valid_ids = [str(card.get("job_id")) for card in candidate_cards if card.get("job_id")]
    if not valid_ids:
        return []

    base_resume = supabase_utils.get_base_resume() or {}
    resume_summary = (base_resume.get("summary") or "").strip()
    resume_skills = base_resume.get("skills") or []
    resume_skills_text = ", ".join([str(s).strip() for s in resume_skills if str(s).strip()][:40])

    candidate_lines = []
    for card in candidate_cards:
        cid = str(card.get("job_id") or "").strip()
        if not cid:
            continue
        title = str(card.get("job_title") or "").strip()
        company = str(card.get("company") or "").strip()
        loc = str(card.get("location") or "").strip()
        candidate_lines.append(f"- id: {cid} | title: {title} | company: {company} | location: {loc}")

    if not candidate_lines:
        return []

    system_prompt = (
        "You are a precise job triage assistant. Return only valid JSON. "
        "No markdown, no extra text."
    )
    prompt = f"""
Select the best {top_k} job IDs from the candidate list for this target profile.

Target profile:
- Search query: {search_query}
- Preferred location context: {location}
- Resume summary: {resume_summary}
- Resume skills: {resume_skills_text}

Prioritize:
1) Full Stack Engineer, Full Stack Developer, Software Engineer, Software Developer, Java Full Stack Developer, and adjacent early-career engineering roles.
2) Resume-aligned stack relevance such as Java, Spring Boot, React.js, Node.js, Go, Python, GraphQL, REST APIs, SQL, microservices, auth, cloud, and production systems.
3) Mid-level friendly roles around 0-4 years with practical full-stack ownership.

De-prioritize:
- Frontend-only roles
- Support, QA, SDET, DevOps-only, or non-software implementation roles
- Mobile roles
- Senior/Lead/Principal/Architect manager-heavy roles

Rules:
- Output only IDs from the candidate list.
- Do not invent IDs.
- Return at most {top_k} IDs.

Candidate list:
{chr(10).join(candidate_lines)}

Return JSON as:
{{
  "ranked_job_ids": ["id1", "id2", "id3"]
}}
"""
    try:
        raw = primary_client.generate_content(
            prompt=prompt,
            system_prompt=system_prompt,
            temperature=0.1,
            response_format=LinkedInPrefilterOutput,
        )
        parsed = LinkedInPrefilterOutput.model_validate_json(raw)
        seen = set()
        ranked = []
        valid_id_set = set(valid_ids)
        for jid in parsed.ranked_job_ids:
            jid_str = str(jid).strip()
            if jid_str and jid_str in valid_id_set and jid_str not in seen:
                ranked.append(jid_str)
                seen.add(jid_str)
        return ranked
    except Exception as e:
        logging.warning(f"LLM prefilter failed; falling back to non-LLM order. Error: {e}")
        return []


def _normalize_text(value: str | None) -> str:
    return (value or "").strip().lower()


def _local_job_fit_score(job: dict) -> int:
    """
    Lightweight fit score tuned for early/mid full-stack and software roles.
    Higher is better.
    """
    title = _normalize_text(job.get("job_title"))
    level = _normalize_text(job.get("level"))

    score = 0

    positive_title_keywords = [
        "full stack engineer",
        "full stack developer",
        "fullstack engineer",
        "fullstack developer",
        "software engineer",
        "software developer",
        "application developer",
        "java full stack",
        "java fullstack",
        "backend",
        "back-end",
        "sde",
        "java",
        "spring",
        "node",
        "python",
        "go",
        "graphql",
        "api",
        "microservice",
    ]
    if any(k in title for k in positive_title_keywords):
        score += 6

    mid_keywords = ["sde-1", "sde i", "engineer i", "engineer ii", "software engineer 2", "junior", "associate", "mid"]
    if any(k in title for k in mid_keywords):
        score += 3

    if "not applicable" in level or level == "":
        score += 1
    elif any(k in level for k in ["entry", "associate", "mid", "junior"]):
        score += 3

    min_years_experience = _get_min_years_experience(job.get("description"))
    if min_years_experience is not None:
        if min_years_experience <= 3:
            score += 3
        elif min_years_experience <= 5:
            score += 1
        elif min_years_experience >= 6:
            score -= 6

    negative_keywords = [
        "frontend", "front-end", "front end", "angular", "ui", "ux",
        "react native", "mobile", "android", "ios",
        "support engineer", "application support", "technical support",
        "qa", "sdet", "test engineer", "automation engineer",
        "devops", "site reliability", "sre",
        "senior", "lead", "principal", "architect", "manager", "director",
        "staff engineer", "head of", "vp ",
    ]
    for keyword in negative_keywords:
        if keyword in title:
            score -= 8

    if "intern" in title:
        score -= 10

    return score


def _llm_final_shortlist_linkedin_jobs(
    candidates: list[dict],
    target_count: int,
    max_per_company: int,
    location: str,
) -> list[str]:
    """
    One final LLM call: pick best IDs with diversity constraints.
    """
    if not candidates or target_count <= 0:
        return []

    valid_ids = [str(c.get("job_id")) for c in candidates if c.get("job_id")]
    valid_id_set = set(valid_ids)
    if not valid_ids:
        return []

    base_resume = supabase_utils.get_base_resume() or {}
    summary = (base_resume.get("summary") or "").strip()
    skills = ", ".join([str(s).strip() for s in (base_resume.get("skills") or []) if str(s).strip()][:35])

    lines = []
    for c in candidates:
        jid = str(c.get("job_id") or "").strip()
        if not jid:
            continue
        title = str(c.get("job_title") or "").strip()
        company = str(c.get("company") or "").strip()
        lvl = str(c.get("level") or "").strip()
        loc = str(c.get("location") or "").strip()
        experience_requirement = _extract_experience_requirement(c.get("description")) or "not stated"
        excerpt = _build_description_excerpt(c.get("description"))
        lines.append(
            f"- id: {jid} | title: {title} | company: {company} | level: {lvl} | "
            f"location: {loc} | experience: {experience_requirement} | summary: {excerpt}"
        )

    system_prompt = "You are a precise hiring-feed curator. Return only valid JSON."
    prompt = f"""
Select the best {target_count} jobs from the list for a candidate with ~2 years software development experience.
Prioritize full-stack and software engineering roles that align with the candidate's actual resume, and avoid obvious senior/frontend-only/support-role drift.

Constraints:
- Return only IDs from the list.
- At most {max_per_company} jobs per company.
- Prefer company diversity.
- Prefer jobs near location context: {location}
- Prefer roles that appear realistic for roughly 2 years of experience.
- Use the experience requirement and job summary, not just the title.
- It is okay to include related roles if the description is a strong fit for this resume.

Candidate context:
- Summary: {summary}
- Skills: {skills}

Strong-fit examples:
- Full Stack Engineer
- Full Stack Developer
- Software Engineer
- Software Developer
- Java Full Stack Developer

Candidate jobs:
{chr(10).join(lines)}

Return JSON:
{{
  "selected_job_ids": ["id1", "id2", "id3"]
}}
"""
    try:
        raw = primary_client.generate_content(
            prompt=prompt,
            system_prompt=system_prompt,
            temperature=0.1,
            response_format=LinkedInFinalShortlistOutput,
        )
        parsed = LinkedInFinalShortlistOutput.model_validate_json(raw)
        ordered = []
        seen = set()
        for jid in parsed.selected_job_ids:
            j = str(jid).strip()
            if j and j in valid_id_set and j not in seen:
                ordered.append(j)
                seen.add(j)
        return ordered
    except Exception as e:
        logging.warning(f"LLM final shortlist failed; using local ranking fallback. Error: {e}")
        return []


def _shortlist_with_company_diversity(
    candidates: list[dict],
    target_count: int,
    max_per_company: int,
) -> list[dict]:
    """
    Local fallback shortlist with company diversity and dedupe by company+title.
    """
    if target_count <= 0:
        return []

    # Dedupe by (company, title) first
    unique = []
    seen_company_title = set()
    for c in candidates:
        company = _normalize_text(c.get("company"))
        title = _normalize_text(c.get("job_title"))
        key = (company, title)
        if key in seen_company_title:
            continue
        seen_company_title.add(key)
        unique.append(c)

    # Rank best first
    ranked = sorted(unique, key=_local_job_fit_score, reverse=True)

    selected = []
    company_count: dict[str, int] = {}
    for c in ranked:
        if len(selected) >= target_count:
            break
        company = _normalize_text(c.get("company")) or "unknown_company"
        cnt = company_count.get(company, 0)
        if cnt >= max_per_company:
            continue
        selected.append(c)
        company_count[company] = cnt + 1

    strict_diversity = bool(getattr(config, "LINKEDIN_STRICT_COMPANY_DIVERSITY", False))

    # If still short, optionally fill regardless of company cap (but keep dedupe)
    if len(selected) < target_count and not strict_diversity:
        selected_ids = {str(c.get("job_id")) for c in selected if c.get("job_id")}
        for c in ranked:
            if len(selected) >= target_count:
                break
            jid = str(c.get("job_id") or "").strip()
            if not jid or jid in selected_ids:
                continue
            selected.append(c)
            selected_ids.add(jid)

    return selected

# --- LinkedIn Scraping Logic ---
def _fetch_linkedin_job_cards(search_query: str, location: str, geo_id_override=USE_CONFIG_GEO_ID) -> list[dict]:
    """Fetches job cards (id/title/company/location) from LinkedIn search pages."""

    job_cards = []
    seen_ids = set()
    start = 0
    max_start = config.LINKEDIN_MAX_START


    logging.info(f"--- Starting Phase 1: Scraping Job IDs (Max Start: {max_start}) ---")
    while start <= max_start:
        query_params = {
            "keywords": search_query,
            "location": location,
            "f_TPR": config.LINKEDIN_JOB_POSTING_DATE,
            "f_JT": config.LINKEDIN_JOB_TYPE,
            "start": start,
        }

        # Geo ID is optional. If not set, LinkedIn resolves from location text.
        geo_id = getattr(config, "LINKEDIN_GEO_ID", None) if geo_id_override is USE_CONFIG_GEO_ID else geo_id_override
        if geo_id:
            query_params["geoId"] = geo_id

        # Workplace type filter is optional (1=onsite, 2=remote, 3=hybrid).
        if getattr(config, "LINKEDIN_F_WT", None) in (1, 2, 3, "1", "2", "3"):
            query_params["f_WT"] = config.LINKEDIN_F_WT

        target_url = (
            "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search?"
            f"{urlencode(query_params)}"
        )

        if start > 0:
            min_delay = float(getattr(config, "LINKEDIN_SEARCH_PAGE_MIN_DELAY_SECONDS", 5.0))
            max_delay = float(getattr(config, "LINKEDIN_SEARCH_PAGE_MAX_DELAY_SECONDS", 15.0))
            sleep_time = random.uniform(min(min_delay, max_delay), max(min_delay, max_delay))
            logging.info(f"Waiting for {sleep_time:.2f} seconds before next request...")
            time.sleep(sleep_time)

        user_agent = random.choice(user_agents.USER_AGENTS)
        headers = {'User-Agent': user_agent}
    
        logging.info(f"Using User-Agent: {user_agent}")

    
        logging.info(f"Scraping URL: {target_url}")

        res = None 
        retries = 0
        while retries <= config.MAX_RETRIES:
            try:
                res = requests.get(target_url, headers=headers, timeout=config.REQUEST_TIMEOUT)
                res.raise_for_status()
                break
            except requests.exceptions.HTTPError as e:
                if e.response.status_code == 429 and retries < config.MAX_RETRIES:
                    retries += 1
                    wait_time = config.RETRY_DELAY_SECONDS + random.uniform(0, 5) 
                    
                    logging.warning(f"Error 429: Too Many Requests. Retrying attempt {retries}/{config.MAX_RETRIES} after {wait_time:.2f} seconds...")
                    time.sleep(wait_time)

                    user_agent = random.choice(user_agents.USER_AGENTS)
                    headers = {'User-Agent': user_agent}
                
                    logging.info(f"Retrying with new User-Agent: {user_agent}")
                    continue
                else:
                    
                    logging.error(f"HTTP Error fetching search results page: {e}")
                    res = None 
                    break
            except requests.exceptions.RequestException as e:
                
                logging.error(f"Request Exception fetching search results page: {e}")
                res = None
                break

        
        if res is None:
            logging.error(f"Failed to fetch {target_url} after {retries} retries. Stopping pagination for this query.")
            break 

        if not res.text:
            
             logging.info(f"Received empty response text at start={start}, stopping.")
             break

        soup = BeautifulSoup(res.text, 'html.parser')
        all_jobs_on_this_page = soup.find_all('li')

        if not all_jobs_on_this_page:
            
             logging.info(f"No job listings ('li' elements) found on page at start={start}, stopping.")
             break

    
        logging.info(f"Found {len(all_jobs_on_this_page)} potential job elements on this page.")

        jobs_found_this_iteration = 0
        for job_element in all_jobs_on_this_page:
            base_card = job_element.find("div", {"class": "base-card"})
            job_urn = base_card.get('data-entity-urn') if base_card else None
            if job_urn and 'jobPosting:' in job_urn:
                try:
                    jobid = job_urn.split(":")[3]
                    if jobid not in seen_ids:
                        seen_ids.add(jobid)
                        title_elem = job_element.find("h3", {"class": "base-search-card__title"})
                        company_elem = job_element.find("h4", {"class": "base-search-card__subtitle"})
                        location_elem = job_element.find("span", {"class": "job-search-card__location"})

                        title_text = title_elem.get_text(" ", strip=True) if title_elem else None
                        company_text = company_elem.get_text(" ", strip=True) if company_elem else None
                        location_text = location_elem.get_text(" ", strip=True) if location_elem else None

                        job_cards.append(
                            {
                                "job_id": jobid,
                                "job_title": title_text,
                                "company": company_text,
                                "location": location_text,
                            }
                        )
                        jobs_found_this_iteration += 1
                except IndexError:
                    
                    logging.warning(f"Could not parse job ID from URN: {job_urn}")
                    pass

    
        logging.info(f"Added {jobs_found_this_iteration} unique job IDs from this page.")

        if jobs_found_this_iteration == 0 and len(all_jobs_on_this_page) > 0:
        
            logging.info("Found list items but no new job IDs extracted, potentially end of relevant results or parsing issue.")
            break

        start += 10


    logging.info(f"--- Finished Phase 1: Found {len(job_cards)} unique job cards during scraping ---")
    return job_cards

def _fetch_linkedin_job_details(job_id: str) -> dict | None:
    """Fetches detailed information for a single job ID with delays, rotating user agents, and retries."""

    job_detail_url = f"https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{job_id}"

    logging.info(f"Preparing to fetch details for job ID: {job_id}")

    min_delay = float(getattr(config, "LINKEDIN_DETAIL_MIN_DELAY_SECONDS", 3.0))
    max_delay = float(getattr(config, "LINKEDIN_DETAIL_MAX_DELAY_SECONDS", 10.0))
    sleep_time = random.uniform(min(min_delay, max_delay), max(min_delay, max_delay))

    logging.info(f"Waiting for {sleep_time:.2f} seconds before fetching details...")
    time.sleep(sleep_time)

    user_agent = random.choice(user_agents.USER_AGENTS)
    headers = {'User-Agent': user_agent}

    logging.info(f"Using User-Agent for details: {user_agent}")


    logging.info(f"Fetching details from: {job_detail_url}")

    resp = None 
    retries = 0
    while retries <= config.MAX_RETRIES:
        try:
            resp = requests.get(job_detail_url, headers=headers, timeout=config.REQUEST_TIMEOUT)
            resp.raise_for_status()
            break
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 429 and retries < config.MAX_RETRIES:
                retries += 1
                wait_time = config.RETRY_DELAY_SECONDS + random.uniform(0, 5) 
                
                logging.warning(f"Error 429 for job ID {job_id}. Retrying attempt {retries}/{config.MAX_RETRIES} after {wait_time:.2f} seconds...")
                time.sleep(wait_time)
                user_agent = random.choice(user_agents.USER_AGENTS)
                headers = {'User-Agent': user_agent}
            
                logging.info(f"Retrying job {job_id} with new User-Agent: {user_agent}")
                continue
            else:
                
                logging.error(f"HTTP Error fetching details for job ID {job_id}: {e}")
                return None
        except requests.exceptions.RequestException as e:
            
            logging.error(f"Request Exception fetching details for job ID {job_id}: {e}")
            return None 

    
    if resp is None:
         logging.error(f"Failed to fetch details for job ID {job_id} after {retries} retries (unexpected state).")
         return None

    try:
        soup = BeautifulSoup(resp.text, 'html.parser')
        job_details = {"job_id": job_id}

        # --- Extract Company ---
        try:
            company_img = soup.find("div",{"class":"top-card-layout__card"}).find("a").find("img")
            if company_img:
                job_details["company"] = company_img.get('alt').strip()
            if not job_details.get("company"):
                 company_link = soup.find("a", {"class": "topcard__org-name-link"})
                 if company_link:
                      job_details["company"] = company_link.text.strip()
                 else:
                      sub_title_span = soup.find("span", {"class": "topcard__flavor"})
                      if sub_title_span:
                           job_details["company"] = sub_title_span.text.strip()

            if not job_details.get("company"):
                 job_details["company"] = None
                 print(f"Warning: Could not extract company for job ID {job_id}")
        except Exception as e:
            print(f"Error extracting company for job ID {job_id}: {e}")
            job_details["company"] = None

        # --- Extract Job Title ---
        try:
            title_link = soup.find("div",{"class":"top-card-layout__entity-info"}).find("a")
            job_details["job_title"] = title_link.text.strip() if title_link else None
            if not job_details["job_title"]:
                 title_h1 = soup.find("h1", {"class": "top-card-layout__title"})
                 if title_h1:
                      job_details["job_title"] = title_h1.text.strip()
        except Exception as e: 
            print(f"Error extracting job title for job ID {job_id}: {e}")
            job_details["job_title"] = None

        # --- Extract Seniority Level ---
        try:
            # Find all criteria items
            criteria_items = soup.find("ul",{"class":"description__job-criteria-list"}).find_all("li")
            job_details["level"] = None 
            for item in criteria_items:
                header = item.find("h3", {"class": "description__job-criteria-subheader"})
                if header and "Seniority level" in header.text:
                    level_text = item.find("span", {"class": "description__job-criteria-text"})
                    if level_text:
                        job_details["level"] = level_text.text.strip()
                        break 
        except Exception as e: 
            print(f"Error extracting seniority level for job ID {job_id}: {e}")
            job_details["level"] = None

        # --- Extract Location ---
        try:
           
            location_span = soup.find("span", {"class": "topcard__flavor topcard__flavor--bullet"})
            if location_span:
                job_details["location"] = location_span.text.strip()
            else:
                
                subtitle_div = soup.find("div", {"class": "topcard__flavor-row"})
                if subtitle_div:
                    location_span_fallback = subtitle_div.find("span", {"class": "topcard__flavor"})
                    if location_span_fallback:
                         job_details["location"] = location_span_fallback.text.strip()

            if not job_details.get("location"): 
                 job_details["location"] = None
                 print(f"Warning: Could not extract location for job ID {job_id}")
        except Exception as e:
            print(f"Error extracting location for job ID {job_id}: {e}")
            job_details["location"] = None

        # --- Extract Description ---
        description_html = "" 
        try:
            description_div = soup.find("div", {"class": "show-more-less-html__markup"})
            if description_div:
                description_html = str(description_div)
            else:
                logging.warning(f"Could not find description div for job ID {job_id}")
        except Exception as e:
                logging.error(f"Error extracting description HTML for job ID {job_id}: {e}")
                description_html = ""

        if description_html.strip():
            job_details["description"] = convert_html_to_markdown(description_html)
        else:
            job_details["description"] = None 
            logging.warning(f"Description HTML was empty for job ID {job_id}. Skipping conversion.") 

        # --- Set Provider ---
        job_details["provider"] = "linkedin"
        
        return job_details

    except Exception as e:
         
         logging.error(f"General Error processing details for job ID {job_id} after successful fetch: {e}")
         return None

def process_linkedin_query(
    search_query: str,
    location: str,
    limit: int = None,
    geo_id_override=USE_CONFIG_GEO_ID,
    enforce_location_filter: bool = True,
    already_seen_job_ids: set[str] | None = None,
    existing_job_ids: set[str] | None = None,
    existing_company_title_keys: set[tuple[str, str]] | None = None,
    already_seen_company_title_keys: set[tuple[str, str]] | None = None,
) -> list:
    """
    Orchestrates scraping and detail fetching for a single query,
    filtering against existing jobs in Supabase BEFORE fetching details.
    Returns a list of new job details found.
    """

    scraped_job_cards = _fetch_linkedin_job_cards(search_query, location, geo_id_override=geo_id_override)
    if not scraped_job_cards:
    
        logging.info("No job cards found in Phase 1. Skipping detail fetching.")
        return []

    logging.info(f"Found {len(scraped_job_cards)} unique scraped cards.")


    logging.info("\n--- Starting Filtering Step: Checking against Supabase ---")
    if existing_job_ids is None or existing_company_title_keys is None:
        job_ids_set, company_title_set = supabase_utils.get_existing_jobs_from_supabase()
    else:
        job_ids_set = existing_job_ids
        company_title_set = existing_company_title_keys
    seen_ids = already_seen_job_ids if already_seen_job_ids is not None else set()
    seen_company_title_keys = (
        already_seen_company_title_keys if already_seen_company_title_keys is not None else set()
    )
    candidate_cards = []
    for card in scraped_job_cards:
        jid = str(card.get("job_id") or "").strip()
        if not jid:
            continue
        if jid in job_ids_set or jid in seen_ids:
            continue

        normalized_company = _normalize_text(card.get("company"))
        normalized_title = _normalize_text(card.get("job_title"))
        company_title_key = None
        if normalized_company and normalized_title:
            company_title_key = (normalized_company, normalized_title)
            if company_title_key in company_title_set or company_title_key in seen_company_title_keys:
                continue
        candidate_cards.append(card)

    logging.info(f"Found {len(job_ids_set)} existing IDs in Supabase.")
    logging.info(f"Identified {len(candidate_cards)} new candidate cards before prefilter.")

    if not candidate_cards:
    
        logging.info("No new job IDs to process after filtering.")
        return []

    # Fast local title prefilter before expensive detail fetch.
    if getattr(config, "LINKEDIN_PREFILTER_BY_TITLE_BEFORE_DETAILS", True):
        local_filtered = [c for c in candidate_cards if _is_linkedin_role_allowed(c.get("job_title"), None)]
        logging.info(
            f"Local title prefilter kept {len(local_filtered)}/{len(candidate_cards)} candidates."
        )
        if local_filtered:
            candidate_cards = local_filtered

    # Optional LLM prefilter/ranking pass on candidate titles.
    if getattr(config, "LINKEDIN_ENABLE_LLM_TITLE_PREFILTER", False):
        llm_cap = max(1, int(getattr(config, "LINKEDIN_LLM_PREFILTER_CANDIDATE_CAP", 50)))
        llm_top_k = max(1, int(getattr(config, "LINKEDIN_LLM_PREFILTER_TOP_K", 30)))
        cards_for_llm = candidate_cards[:llm_cap]
        effective_top_k = min(
            len(cards_for_llm),
            max(llm_top_k, limit or 0),
        )
        ranked_ids = _llm_rank_linkedin_candidates(
            cards_for_llm,
            search_query=search_query,
            location=location,
            top_k=effective_top_k,
        )
        if ranked_ids:
            by_id = {str(c.get("job_id")): c for c in candidate_cards if c.get("job_id")}
            ranked_cards = [by_id[jid] for jid in ranked_ids if jid in by_id]
            remaining_cards = [c for c in candidate_cards if str(c.get("job_id")) not in set(ranked_ids)]
            candidate_cards = ranked_cards + remaining_cards
            logging.info(
                f"LLM prefilter ranked {len(ranked_cards)} candidates; using ranked-first order."
            )

    new_job_ids_to_process = [str(c.get("job_id")) for c in candidate_cards if c.get("job_id")]

    if limit is not None and len(new_job_ids_to_process) > limit:
        logging.info(f"Truncating new_job_ids_to_process from {len(new_job_ids_to_process)} to {limit} to stay within source limit.")
        new_job_ids_to_process = new_job_ids_to_process[:limit]

    # Mark selected IDs as seen so other queries/cities in the same run don't refetch them.
    seen_ids.update(new_job_ids_to_process)
    for card in candidate_cards:
        jid = str(card.get("job_id") or "").strip()
        if jid not in new_job_ids_to_process:
            continue
        normalized_company = _normalize_text(card.get("company"))
        normalized_title = _normalize_text(card.get("job_title"))
        if normalized_company and normalized_title:
            seen_company_title_keys.add((normalized_company, normalized_title))

    logging.info(f"\n--- Starting Phase 2: Fetching Job Details for {len(new_job_ids_to_process)} New IDs ---")
    detailed_new_jobs = []
    processed_count = 0

    ids_to_fetch = new_job_ids_to_process

    for job_id in ids_to_fetch:
        details = _fetch_linkedin_job_details(job_id)
        if details:
            location = details.get("location")
            if enforce_location_filter and not _is_linkedin_location_allowed(location):
                logging.info(f"Skipping job ID {job_id} due to strict city filter. Location: {location}")
                continue

            job_title = details.get("job_title")
            job_level = details.get("level")
            if not _is_linkedin_role_allowed(job_title, job_level):
                logging.info(
                    f"Skipping job ID {job_id} due to role filter. "
                    f"Title: {job_title}, Level: {job_level}"
                )
                continue

            description = details.get('description')
            if description and description.strip(): 
                if 'job_id' in details and details['job_id'] is not None:
                    detailed_new_jobs.append(details)
                    processed_count += 1
                else:
                    
                    logging.warning(f"Fetched details for {job_id} but missing 'job_id' key. Skipping.")
            else:
                
                logging.warning(f"Skipping job ID {job_id} due to missing or empty description.") 
        else:
            
            logging.warning(f"Skipping job ID {job_id} as detail fetching failed or returned no data.") 


    logging.info(f"--- Finished Phase 2: Successfully fetched details for {processed_count} new job(s) ---")
    return detailed_new_jobs

def _fetch_careers_future_jobs(search_query: str) -> list:
    """
    Fetches job items from CareersFuture based on the provided search query.
    This involves:
    1. Getting skill suggestions based on the search query.
    2. Using these skill UUIDs to search for jobs.
    3. Handling pagination to retrieve all job results.
    4. Returning a list of all collected job item dictionaries.

    Args:
        search_query (str): The job title or keywords to search for.

    Returns:
        list: A list of job item dictionaries. Returns an empty list if an error occurs
              or if no jobs are found.
    """


    careers_future_suggestions_api_url = "https://api.mycareersfuture.gov.sg/v2/skills/suggestions"
    careers_future_search_api_base_url =  "https://api.mycareersfuture.gov.sg/v2/search"

    skillUuids = []

    # --- 1. Get Skill Suggestions ---
    skills_suggestions_payload = {'jobTitle': search_query}

    try:
        logging.info(f"Fetching skill suggestions for query: '{search_query}' from {careers_future_suggestions_api_url}")
        skills_suggestions_response = requests.post(
            careers_future_suggestions_api_url, 
            data=skills_suggestions_payload,
            timeout=config.REQUEST_TIMEOUT
            )

        skills_suggestions_response.raise_for_status()
        skills_data = skills_suggestions_response.json()
        skills_list = skills_data.get('skills', [])
        skillUuids = [skill_dict['uuid'] for skill_dict in skills_list if 'uuid' in skill_dict]
        logging.info(f"Successfully retrieved {len(skillUuids)} skill UUIDs for '{search_query}'.")
        if not skillUuids:
            logging.warning(f"No skill UUIDs found for query '{search_query}'. Job search will proceed without specific skill filtering.")


    except requests.exceptions.HTTPError as http_err:
        status_code = http_err.response.status_code if http_err.response is not None else 'N/A'
        response_text = http_err.response.text if http_err.response is not None else 'N/A'
        logging.error(f"HTTP error during skill suggestions: {http_err} - Status: {status_code}")
        logging.debug(f"Skill suggestions error response content: {response_text[:500]}") 
        return []
    except requests.exceptions.RequestException as req_err: 
        logging.error(f"Request exception during skill suggestions: {req_err}")
        return []
    except json.JSONDecodeError:
        content_for_log = skills_suggestions_response.text if 'skills_suggestions_response' in locals() and skills_suggestions_response else "N/A"
        logging.error(f"Could not decode JSON response for skill suggestions. Content: {content_for_log[:500]}")
        return []

    # --- 2. Search for Jobs and Handle Pagination ---
    all_job_items = []
    total_api_calls_for_search = 0

    # Initial search URL with default limit and page
    current_search_url = f"{careers_future_search_api_base_url}?limit=100&page=0"
    search_payload = {
        'sessionId':"",
        'search': search_query,
        'categories':config.CAREERS_FUTURE_SEARCH_CATEGORIES,
        'employmentTypes': config.CAREERS_FUTURE_SEARCH_EMPLOYMENT_TYPES,
        'postingCompany' : [],
        'sortBy': ["new_posting_date"],
        'skillUuids': skillUuids,

    }

    try:
        while current_search_url:
            total_api_calls_for_search += 1
            logging.info(f"Job search API call {total_api_calls_for_search}: POST to {current_search_url}")
        
            search_response = requests.post(current_search_url, json=search_payload)
            search_response.raise_for_status()
            search_results_data  = search_response.json()

            current_page_jobs = search_results_data.get('results', [])
            all_job_items.extend(current_page_jobs)

            logging.info(f"Retrieved {len(current_page_jobs)} job items from this page. Total items collected: {len(all_job_items)}.")

            # Log total results reported by API 
            if 'total' in search_results_data and total_api_calls_for_search == 1:
                logging.info(f"API reports total potential jobs matching criteria: {search_results_data['total']}")
            
            # Get the next page URL. The API provides a full URL.
            next_page_link_info = search_results_data.get("_links", {}).get("next", {})
            current_search_url = next_page_link_info.get("href") if next_page_link_info else None 

            if current_search_url:
                logging.debug(f"Next page URL for job search: {current_search_url}")
            else:
                logging.info("No more job pages to fetch.")

        logging.info(f"Completed job search. Total API calls made for search: {total_api_calls_for_search}.")
    
    except requests.exceptions.HTTPError as http_err:
        status_code = http_err.response.status_code if http_err.response is not None else 'N/A'
        response_text = http_err.response.text if http_err.response is not None else 'N/A'
        logging.error(f"HTTP error during job search: {http_err} - Status: {status_code}")
        logging.debug(f"Job search error response content: {response_text[:500]}")
    except requests.exceptions.RequestException as req_err:
        logging.error(f"Request exception during job search: {req_err}")
    except json.JSONDecodeError:
        content_for_log = search_response.text if 'search_response' in locals() and search_response else "N/A"
        logging.error(f"Could not decode JSON response during job search. Content: {content_for_log[:500]}")

    # --- 3. Return all collected job items ---
    if not all_job_items:
        logging.info(f"No job items were collected for query '{search_query}'.")
        return [] 

    logging.info(f"Returning {len(all_job_items)} total job items for query '{search_query}'.")
    return all_job_items

def _fetch_careers_future_job_details(job_id: str) -> dict | None:
    """
    Fetch job details from CareersFuture based on the provided job ID.

    Args:
        job_id (str): The UUID of the job to fetch details for.

    Returns:
        dict | None: A dictionary containing the job details if successful,
                      None otherwise.
    """
    if not job_id:
        logging.warning("Job ID is missing or empty. Cannot fetch details.")
        return None

    api_url = f"https://api.mycareersfuture.gov.sg/v2/jobs/{job_id}"
    
    logging.info(f"Attempting to fetch job details for ID: {job_id} from URL: {api_url}")

    try:
        response = requests.get(api_url, timeout=config.REQUEST_TIMEOUT) 

        response.raise_for_status()

        job_data = response.json()
        logging.info(f"Successfully fetched and parsed job details for ID: {job_id}")

        raw_description_html = job_data.get('description', '')
        # Convert HTML description directly to Markdown (no LLM needed)
        markdown_description = None 
        if raw_description_html.strip(): 
            markdown_description = convert_html_to_markdown(raw_description_html)
        else:
            logging.warning(f"Raw description was empty for Careers Future job ID {job_id}. Skipping conversion.") 

        job_details = {
            'job_id': job_data.get('uuid'),
            'company': _get_careers_future_job_company_name(job_data),
            'job_title': job_data.get('title'),
            'location': 'Singapore',
            'level': job_data.get('positionLevels', [{'position': 'Not applicable'}])[0].get('position', 'Not applicable'),
            'provider': 'careers_future',
            'description': markdown_description, 
            'posted_at': job_data.get('metadata', {}).get('createdAt', ''),
        }

        return job_details

    except requests.exceptions.HTTPError as http_err:
        status_code = http_err.response.status_code if http_err.response is not None else 'N/A'
        response_text = http_err.response.text if http_err.response is not None else 'N/A'
        if status_code == 404:
            logging.warning(f"Job details not found (404) for ID: {job_id} at {api_url}.")
        else:
            logging.error(f"HTTP error occurred while fetching job details for ID '{job_id}': {http_err} - Status: {status_code}")
            logging.debug(f"Error response content: {response_text[:500]}") 
    except requests.exceptions.ConnectionError as conn_err:
        logging.error(f"Connection error occurred while fetching job details for ID '{job_id}': {conn_err}")
    except requests.exceptions.Timeout as timeout_err:
        logging.error(f"Timeout error occurred while fetching job details for ID '{job_id}': {timeout_err}")
    except requests.exceptions.RequestException as req_err: 
        logging.error(f"An error occurred during the request for job details for ID '{job_id}': {req_err}")
    except json.JSONDecodeError:
        content_for_log = response.text if 'response' in locals() and response else "N/A"
        logging.error(f"Failed to decode JSON response for job details for ID '{job_id}'. Content: {content_for_log[:500]}")
    
    return None # Return None in case of any error

def process_careers_future_query(search_query: str, limit: int = None) -> list:
    """
    Fetch jobs from CareersFuture and return them as a list of dictionaries.
    """
    # 1. Fetch all potential job items from CareersFuture search
    careers_future_jobs = _fetch_careers_future_jobs(search_query)
    if not careers_future_jobs:
        print("No job items found in Phase 1. Skipping detail fetching.")
        return []

    # 2. Fetch existing job identifiers from Supabase
    logging.info("Phase 2: Fetching existing job identifiers from Supabase...")
    try:
        job_ids_set_supabase, company_title_set_supabase = supabase_utils.get_existing_jobs_from_supabase()
        logging.info(f"Phase 2: Supabase returned {len(job_ids_set_supabase)} existing IDs and {len(company_title_set_supabase)} company/title pairs.")
    except Exception as e:
        logging.error(f"Failed to fetch existing jobs from Supabase: {e}")
        logging.warning("Proceeding without Supabase data; all fetched jobs will be considered new.")
        job_ids_set_supabase = set()
        company_title_set_supabase = set()

    # 3. Filter the fetched jobs
    logging.info("Phase 3: Filtering fetched jobs against Supabase data...")
    new_job_ids_to_process = []
    skipped_by_id_count = 0
    skipped_by_combo_count = 0

    for job_item in careers_future_jobs:
        if not isinstance(job_item, dict):
            logging.warning(f"Skipping invalid job item (not a dict): {str(job_item)[:100]}")
            continue

        job_uuid = str(job_item.get('uuid'))
        
        # Check 1: Does the UUID already exist in Supabase?
        if job_uuid and job_uuid in job_ids_set_supabase:
            logging.debug(f"Skipping job (ID exists in Supabase): UUID='{job_uuid}', Title='{job_item.get('title', 'N/A')}'")
            skipped_by_id_count += 1
            continue # Skip this job

        # Prepare for Check 2: Company & Title combination
        company_name = _get_careers_future_job_company_name(job_item)
        job_title = job_item.get('title')

        normalized_company = None
        normalized_title = None

        if company_name:
            normalized_company = company_name.strip().lower()
        if job_title:
            normalized_title = job_title.strip().lower()
        
        if normalized_company and normalized_title:
            company_title_key = (normalized_company, normalized_title)
            if company_title_key in company_title_set_supabase:
                logging.debug(f"Skipping job (Company/Title combo exists in Supabase): UUID='{job_uuid}', Company='{normalized_company}', Title='{normalized_title}'")
                skipped_by_combo_count +=1
                continue 
        elif job_uuid: 
            logging.debug(f"Job UUID='{job_uuid}' has no company/title for combo check. Will be added if ID is new.")
        else: 
             logging.warning(f"Job item has no UUID and insufficient company/title for matching: {str(job_item)[:100]}")


        new_job_ids_to_process.append(job_uuid) 

    # 4. Fetch details ONLY for the genuinely new job IDs
    if limit is not None and len(new_job_ids_to_process) > limit:
        logging.info(f"Truncating new_job_ids_to_process from {len(new_job_ids_to_process)} to {limit} to stay within source limit.")
        new_job_ids_to_process = new_job_ids_to_process[:limit]

    print(f"\n--- Phase 4: Fetching Job Details for {len(new_job_ids_to_process)} New Jobs ---")
    detailed_new_jobs = []
    processed_count = 0

    for job_id in new_job_ids_to_process:
        details = _fetch_careers_future_job_details(job_id)
        if details:
            # --- NEW: Check for description before adding ---
            description = details.get('description')
            if description and description.strip(): # Ensure it's not None or an empty/whitespace string
                if 'job_id' in details and details['job_id'] is not None:
                    detailed_new_jobs.append(details)
                    processed_count += 1
                else:
                    
                    logging.warning(f"Fetched details for {job_id} but missing 'job_id' key. Skipping.")
            else:
                
                logging.warning(f"Skipping job ID {job_id} due to missing or empty description.") 
        else:
            
            logging.warning(f"Skipping job ID {job_id} as detail fetching failed or returned no data.") 



    logging.info(f"--- Finished Phase 4: Successfully fetched details for {processed_count} new job(s) ---")
    return detailed_new_jobs

def _run_linkedin_queries_for_location(
    search_queries: list[str],
    location: str,
    max_jobs_per_search: int,
    max_jobs_remaining: int,
    seen_job_ids: set[str] | None = None,
    seen_company_title_keys: set[tuple[str, str]] | None = None,
    expanded_search_queries: list[str] | None = None,
    min_target_before_next_city: int = 0,
) -> int:
    """
    Runs LinkedIn queries for one location until either queries are exhausted
    or max_jobs_remaining is reached. Returns how many new jobs were saved.
    """
    if max_jobs_remaining <= 0:
        return 0

    existing_ids_for_run, existing_company_title_keys_for_run = supabase_utils.get_existing_jobs_from_supabase()
    logging.info(
        f"Loaded {len(existing_ids_for_run)} existing job IDs and "
        f"{len(existing_company_title_keys_for_run)} company/title keys once for this location run."
    )

    fetched_for_location: list[dict] = []
    candidate_limit = int(getattr(config, "LINKEDIN_FINAL_SHORTLIST_CANDIDATE_LIMIT", max_jobs_remaining * 3))
    candidate_limit = max(candidate_limit, max_jobs_remaining)

    def _run_query_batch(query_batch: list[str], batch_label: str) -> None:
        for query in query_batch:
            remaining_candidates = candidate_limit - len(fetched_for_location)
            if remaining_candidates <= 0:
                break

            per_query_limit = min(max_jobs_per_search, remaining_candidates)
            print(
                f"\n{'='*20} Processing {batch_label}: '{query}' | Location: '{location}' {'='*20}"
            )
            new_linkedin_job_details = process_linkedin_query(
                query,
                location,
                limit=per_query_limit,
                geo_id_override=USE_CONFIG_GEO_ID,
                enforce_location_filter=True,
                already_seen_job_ids=seen_job_ids,
                existing_job_ids=existing_ids_for_run,
                existing_company_title_keys=existing_company_title_keys_for_run,
                already_seen_company_title_keys=seen_company_title_keys,
            )

            if new_linkedin_job_details:
                fetched_for_location.extend(new_linkedin_job_details)
                print(
                    f"\nCollected {len(new_linkedin_job_details)} candidates for query '{query}' "
                    f"(pool size now {len(fetched_for_location)}/{candidate_limit})."
                )
            else:
                print(f"\nNo new job details were fetched or processed for query '{query}' in '{location}'.")

    _run_query_batch(search_queries, "Search Query")

    should_expand_queries = (
        bool(getattr(config, "LINKEDIN_ENABLE_QUERY_EXPANSION_BEFORE_NEXT_CITY", False))
        and expanded_search_queries
        and len(fetched_for_location) < max(
            1,
            int(
                getattr(
                    config,
                    "LINKEDIN_QUERY_EXPANSION_MIN_CANDIDATES",
                    min_target_before_next_city,
                )
            ),
        )
        and len(fetched_for_location) < candidate_limit
    )
    if should_expand_queries:
        logging.info(
            f"Location '{location}' produced {len(fetched_for_location)} candidates after primary queries, "
            f"below target {min_target_before_next_city}. Running expanded same-city queries."
        )
        _run_query_batch(expanded_search_queries, "Expanded Query")

    if not fetched_for_location:
        return 0

    # Dedupe by job_id across all query pools
    by_job_id = {}
    for job in fetched_for_location:
        jid = str(job.get("job_id") or "").strip()
        if jid and jid not in by_job_id:
            by_job_id[jid] = job
    pool = list(by_job_id.values())
    logging.info(f"Built final candidate pool for location '{location}': {len(pool)} unique jobs.")

    max_per_company = int(getattr(config, "LINKEDIN_MAX_JOBS_PER_COMPANY_PER_RUN", 1))
    max_per_company = max(1, max_per_company)

    # Optional one-call LLM final shortlist
    if getattr(config, "LINKEDIN_ENABLE_LLM_FINAL_SHORTLIST", False):
        llm_cap = max(1, int(getattr(config, "LINKEDIN_LLM_FINAL_SHORTLIST_CANDIDATE_CAP", 80)))
        pool_for_llm = pool[:llm_cap]
        llm_ids = _llm_final_shortlist_linkedin_jobs(
            candidates=pool_for_llm,
            target_count=max_jobs_remaining,
            max_per_company=max_per_company,
            location=location,
        )
        if llm_ids:
            by_id = {str(j.get("job_id")): j for j in pool if j.get("job_id")}
            ordered = [by_id[jid] for jid in llm_ids if jid in by_id]
            remainder = [j for j in pool if str(j.get("job_id")) not in set(llm_ids)]
            pool = ordered + remainder
            logging.info(f"LLM final shortlist prioritized {len(ordered)} jobs.")

    final_jobs = _shortlist_with_company_diversity(
        candidates=pool,
        target_count=max_jobs_remaining,
        max_per_company=max_per_company,
    )

    if final_jobs:
        logging.info(
            f"Saving final shortlisted jobs for '{location}': {len(final_jobs)} "
            f"(max per company: {max_per_company})."
        )
        supabase_utils.save_jobs_to_supabase(final_jobs)
        return len(final_jobs)

    return 0

# --- Main Execution ---
if __name__ == "__main__":

    total_new_jobs_saved = 0

    # Get jobs from LinkedIn
    if "linkedin" in config.SCRAPING_SOURCES:
        logging.info("\n--- Starting LinkedIn Job Scraping ---")
        seen_linkedin_job_ids_in_run: set[str] = set()
        seen_linkedin_company_titles_in_run: set[tuple[str, str]] = set()
        max_jobs_per_search = config.MAX_JOBS_PER_SEARCH.get("linkedin", getattr(config, 'DEFAULT_MAX_JOBS_PER_SEARCH', 10))
        linkedin_locations = getattr(config, "LINKEDIN_LOCATIONS", None) or [config.LINKEDIN_LOCATION]
        max_jobs_per_run = getattr(config, "LINKEDIN_MAX_NEW_JOBS_PER_RUN", 0)
        if max_jobs_per_run <= 0:
            max_jobs_per_run = 999999

        primary_location = linkedin_locations[0]
        secondary_locations = linkedin_locations[1:]
        expanded_search_queries = getattr(config, "LINKEDIN_EXPANDED_SEARCH_QUERIES", None) or []
        min_target_before_next_city = int(
            getattr(config, "LINKEDIN_MIN_TARGET_JOBS_BEFORE_NEXT_CITY", max_jobs_per_run)
        )

        logging.info(f"Primary city: {primary_location}")
        primary_saved = _run_linkedin_queries_for_location(
            search_queries=config.LINKEDIN_SEARCH_QUERIES,
            location=primary_location,
            max_jobs_per_search=max_jobs_per_search,
            max_jobs_remaining=max_jobs_per_run - total_new_jobs_saved,
            seen_job_ids=seen_linkedin_job_ids_in_run,
            seen_company_title_keys=seen_linkedin_company_titles_in_run,
            expanded_search_queries=expanded_search_queries,
            min_target_before_next_city=min_target_before_next_city,
        )
        total_new_jobs_saved += primary_saved

        enable_secondary = getattr(config, "LINKEDIN_ENABLE_SECONDARY_CITY_FALLBACK", True)
        should_run_secondary = (
            enable_secondary
            and secondary_locations
            and total_new_jobs_saved < min_target_before_next_city
            and total_new_jobs_saved < max_jobs_per_run
        )

        if should_run_secondary:
            logging.info(
                f"Primary city saved {primary_saved} jobs (< target {min_target_before_next_city}). "
                f"Running fallback city/cities: {secondary_locations}"
            )
            for fallback_location in secondary_locations:
                if total_new_jobs_saved >= max_jobs_per_run:
                    break
                fallback_saved = _run_linkedin_queries_for_location(
                    search_queries=config.LINKEDIN_SEARCH_QUERIES,
                    location=fallback_location,
                    max_jobs_per_search=max_jobs_per_search,
                    max_jobs_remaining=max_jobs_per_run - total_new_jobs_saved,
                    seen_job_ids=seen_linkedin_job_ids_in_run,
                    seen_company_title_keys=seen_linkedin_company_titles_in_run,
                    expanded_search_queries=expanded_search_queries,
                    min_target_before_next_city=min_target_before_next_city - total_new_jobs_saved,
                )
                total_new_jobs_saved += fallback_saved
    else:
        logging.info("\n--- Skipping LinkedIn Job Scraping per config ---")

    # Get jobs from Careers Future
    if "careers_future" in config.SCRAPING_SOURCES:
        logging.info(f"\n--- Starting Careers Future Job Scraping ---")
        max_jobs_per_search = config.MAX_JOBS_PER_SEARCH.get("careers_future", getattr(config, 'DEFAULT_MAX_JOBS_PER_SEARCH', 10))
        for query in config.CAREERS_FUTURE_SEARCH_QUERIES:
            logging.info(f"\n{'='*20} Processing Careers Future Search Query: '{query}' {'='*20}")

            # 1. Process the query: Scrape IDs, filter, fetch new details
            new_careers_future_job_details = process_careers_future_query(query, limit=max_jobs_per_search)

            # 2. Save the NEW scraped data to Supabase
            if new_careers_future_job_details:
                logging.info(f"\n--- Saving {len(new_careers_future_job_details)} new job(s) for query '{query}' ---")
                supabase_utils.save_jobs_to_supabase(new_careers_future_job_details)
                total_new_jobs_saved += len(new_careers_future_job_details)
            else:
                logging.info(f"\nNo new job details were fetched or processed for query '{query}'.")
    else:
        logging.info("\n--- Skipping Careers Future Job Scraping per config ---")

    # --- End of Script ---      
    logging.info(f"\n{'='*20} Job scraping script finished {'='*20}")
    logging.info(f"Total new jobs saved across all queries: {total_new_jobs_saved}")
