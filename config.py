import os
from dotenv import load_dotenv

load_dotenv()

# --- DO NOT MODIFY THE BELOW SECTION ---

# =================================================================
# 1. CORE SYSTEM CONFIGURATION (Do Not Modify)
# =================================================================
SUPABASE_URL: str = os.environ.get("SUPABASE_URL")
SUPABASE_SERVICE_ROLE_KEY: str = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
SUPABASE_TABLE_NAME: str = "jobs"
SUPABASE_CUSTOMIZED_RESUMES_TABLE_NAME = "customized_resumes"
SUPABASE_STORAGE_BUCKET="personalized_resumes"
SUPABASE_RESUME_STORAGE_BUCKET="resumes"
SUPABASE_BASE_RESUME_TABLE_NAME = "base_resume"
BASE_RESUME_PATH = "resume.json"

# API keys — set only the key(s) needed for your chosen provider.
LLM_API_KEY = os.environ.get("LLM_API_KEY") or os.environ.get("GEMINI_API_KEY") or os.environ.get("GEMINI_FIRST_API_KEY")

# =================================================================
# 2. USER PREFERENCES (Editable)
# =================================================================

# --- LLM Settings ---
# Use any model supported by LiteLLM (gemini, openai/gpt-4o-mini, groq/llama-3.3-70b-versatile)
# Full list of supported models & naming: https://docs.litellm.ai/docs/providers
LLM_MODEL = "openai/gpt-5.4"
RESUME_GENERATION_FLOW = "legacy"  # Options: "legacy", "two_step_ai"

# --- Search Configuration ---
LINKEDIN_SEARCH_QUERIES = [
    "full stack engineer",
    "software engineer",
    "full stack developer",
    "software developer",
    "java full stack developer"
]
LINKEDIN_EXPANDED_SEARCH_QUERIES = [
    "associate software engineer",
    "software development engineer",
    "application developer",
    "java developer",
    "backend developer",
]
LINKEDIN_LOCATION = "India"
# Strict city mode: scraper will run searches per city in this list.
LINKEDIN_LOCATIONS = [
    "Hyderabad, Telangana, India",
    "Bengaluru, Karnataka, India",
    "Mumbai, Maharashtra, India",
    "Chennai, Tamil Nadu, India",
    "Delhi, India",
]
# Optional geoId. Set None when using LINKEDIN_LOCATIONS to avoid country-wide overriding.
LINKEDIN_GEO_ID = None
# Priority behavior:
# 1) Run Hyderabad first.
# 2) If Hyderabad still does not reach the target, try expanded Hyderabad queries.
# 3) Only then move to Bangalore, then later cities if still needed.
LINKEDIN_ENABLE_SECONDARY_CITY_FALLBACK = True
# Final number of shortlisted jobs to save into Supabase for scoring.
LINKEDIN_MAX_NEW_JOBS_PER_RUN = 100
# Do not move to the next city until the current city has failed to reach this target.
LINKEDIN_MIN_TARGET_JOBS_BEFORE_NEXT_CITY = LINKEDIN_MAX_NEW_JOBS_PER_RUN
# If the initial query set for a city underperforms, try broader backup queries in the same city first.
LINKEDIN_ENABLE_QUERY_EXPANSION_BEFORE_NEXT_CITY = True
LINKEDIN_QUERY_EXPANSION_MIN_CANDIDATES = 100
# Strict post-filter keywords for final job location validation.
LINKEDIN_ALLOWED_CITY_KEYWORDS = [
    "hyderabad",
    "bengaluru",
    "bangalore",
    "mumbai",
    "chennai",
    "delhi",
    "new delhi",
    "gurgaon",
    "gurugram",
    "noida",
]
# Role-quality filters before saving to DB.
LINKEDIN_EXCLUDED_TITLE_KEYWORDS = [
    "senior",
    "lead",
    "principal",
    "architect",
    "manager",
    "frontend",
    "front-end",
    "front end",
    "angular",
    "ui",
    "ux",
    "android",
    "ios",
    "flutter",
    "react native",
    "mobile",
    "devops",
    "site reliability",
    "sre",
    "qa",
    "sdet",
    "quality assurance",
    "test engineer",
    "automation engineer",
    "support engineer",
    "technical support",
    "application support",
    "it support",
    "network engineer",
    "system administrator",
    "salesforce",
    "servicenow",
    "sap",
    ".net",
    "dotnet",
    "php",
    "wordpress",
    "ruby on rails",
    "mainframe",
    "embedded",
    "firmware",
    "data scientist",
    "data engineer",
    "business analyst",
    "product manager",
    "machine learning",
    "prompt engineer",
]
LINKEDIN_REQUIRED_TITLE_KEYWORDS = [
    "full stack engineer",
    "full stack",
    "fullstack",
    "full stack developer",
    "software engineer",
    "software developer",
    "java full stack",
    "java fullstack",
    "backend engineer",
    "backend developer",
    "java developer",
    "java engineer",
    "sde",
]
LINKEDIN_ENFORCE_REQUIRED_TITLE_KEYWORDS = False
LINKEDIN_ALLOWED_LEVEL_KEYWORDS = [
    "entry",
    "associate",
    "mid",
    "mid-senior",
    "junior",
]
LINKEDIN_JOB_TYPE = "F" # F=Full-time, C=Contract, P=Part-time, T=Temporary, I=Internship
LINKEDIN_JOB_POSTING_DATE = "r86400" # r86400=Past 24h, r604800=Past week
LINKEDIN_F_WT = None # Set 1=Onsite, 2=Remote, 3=Hybrid, or None for all
# Prefilter controls to reduce wasted detail-fetch calls.
LINKEDIN_PREFILTER_BY_TITLE_BEFORE_DETAILS = True
# Use an LLM pass to rank broader LinkedIn card results before detail fetch.
LINKEDIN_ENABLE_LLM_TITLE_PREFILTER = False
LINKEDIN_LLM_PREFILTER_CANDIDATE_CAP = 180
LINKEDIN_LLM_PREFILTER_TOP_K = 80
# Final shortlist controls (applied after gathering candidate pool across queries).
LINKEDIN_FINAL_SHORTLIST_CANDIDATE_LIMIT = 320
LINKEDIN_MAX_JOBS_PER_COMPANY_PER_RUN = 3
LINKEDIN_STRICT_COMPANY_DIVERSITY = False
LINKEDIN_ENABLE_LLM_FINAL_SHORTLIST = False
LINKEDIN_LLM_FINAL_SHORTLIST_CANDIDATE_CAP = 240

CAREERS_FUTURE_SEARCH_QUERIES = ["IT Support", "Full Stack Web Developer", "Application Support", "Cybersecurity Analyst", "fresher developer"]
CAREERS_FUTURE_SEARCH_CATEGORIES = ["Information Technology"]
CAREERS_FUTURE_SEARCH_EMPLOYMENT_TYPES = ["Full Time"]

# --- Processing Limits ---
SCRAPING_SOURCES = ["linkedin"] # "linkedin", "careers_future"
# Set 0 to score all unscored jobs currently in Supabase.
JOBS_TO_SCORE_PER_RUN = 0
JOBS_TO_CUSTOMIZE_PER_RUN = 20
MIN_SCORE_FOR_CUSTOM_RESUME = 85
# Optional percent mode for custom resume generation.
# 0 = disabled (uses JOBS_TO_CUSTOMIZE_PER_RUN).
# Example: set to 20 for "top 20%" of currently eligible scored jobs.
JOBS_TO_CUSTOMIZE_TOP_PERCENT = 0
MAX_JOBS_PER_SEARCH = {
    "linkedin": 60,
    "careers_future": 10,
}

# =================================================================
# 3. ADVANCED SYSTEM SETTINGS (Modify with Caution)
# =================================================================
LLM_MAX_RPM = 15
LLM_MAX_RETRIES = 3
LLM_RETRY_BASE_DELAY = 10
LLM_DAILY_REQUEST_BUDGET = 0
LLM_REQUEST_DELAY_SECONDS = 1

LINKEDIN_MAX_START = 60
LINKEDIN_SEARCH_PAGE_MIN_DELAY_SECONDS = 0.5
LINKEDIN_SEARCH_PAGE_MAX_DELAY_SECONDS = 1.5
LINKEDIN_DETAIL_MIN_DELAY_SECONDS = 1.0
LINKEDIN_DETAIL_MAX_DELAY_SECONDS = 2.0
REQUEST_TIMEOUT = 30
MAX_RETRIES = 3
RETRY_DELAY_SECONDS = 15

JOB_EXPIRY_DAYS = 30
JOB_CHECK_DAYS = 3
JOB_DELETION_DAYS = 60
JOB_CHECK_LIMIT = 50
ACTIVE_CHECK_TIMEOUT = 20
ACTIVE_CHECK_MAX_RETRIES = 2
ACTIVE_CHECK_RETRY_DELAY = 10
