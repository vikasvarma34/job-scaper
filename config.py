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
SUPABASE_CUSTOMIZED_COVER_LETTERS_TABLE_NAME = "customized_cover_letters"
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
TARGET_SAVED_JOBS_PER_RUN = 50
SCRAPER_LOG_LEVEL = "INFO"  # Use "DEBUG" only when you want full request-by-request tracing.

# --- Search Configuration ---
LINKEDIN_SEARCH_QUERIES = [
    "java full stack developer",
    "full stack developer java react",
    "backend developer java spring boot",
    "java backend developer",
    "developer associate java",
    "associate software engineer java",
]
LINKEDIN_EXPANDED_SEARCH_QUERIES = [
    "java software engineer",
    "software engineer ii java",
    "application developer java",
    "associate java developer",
    "full stack engineer java",
    "backend engineer java microservices",
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
# Legacy LinkedIn-only target. Multi-source runs now use TARGET_SAVED_JOBS_PER_RUN
# together with SCRAPER_SOURCE_FINAL_CAPS below.
LINKEDIN_MAX_NEW_JOBS_PER_RUN = 18
# Do not move to the next city until the current city has failed to reach this target.
LINKEDIN_MIN_TARGET_JOBS_BEFORE_NEXT_CITY = LINKEDIN_MAX_NEW_JOBS_PER_RUN
# If the initial query set for a city underperforms, try broader backup queries in the same city first.
LINKEDIN_ENABLE_QUERY_EXPANSION_BEFORE_NEXT_CITY = True
LINKEDIN_QUERY_EXPANSION_MIN_CANDIDATES = 14
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
    "software engineer in test",
    "verification",
    "testing",
    "quality assurance",
    "test engineer",
    "automation engineer",
    "analyst",
    "consultant",
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
    "d365",
    "oracle apps",
    "erp",
    "product testing",
    "walk in drive",
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
    "java full stack",
    "java fullstack",
    "backend engineer",
    "backend developer",
    "java backend",
    "java developer",
    "java engineer",
    "java software engineer",
    "associate software engineer",
    "developer associate",
    "associate java developer",
    "application developer",
    "software engineer ii",
    "engineer ii",
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
LINKEDIN_MIN_CARD_FIT_SCORE = 4
LINKEDIN_MIN_DETAIL_FIT_SCORE = 7
LINKEDIN_MIN_SHORTLIST_FIT_SCORE = 7
# Reject only when the stated minimum required experience is above this value.
# Examples allowed: 0+, 1+, 2+, 2-3, 2-4, 2-5, 3 years, 3+ years.
# Examples rejected: 4 years, 4+ years, 5-7 years.
LINKEDIN_MAX_ALLOWED_MIN_EXPERIENCE_YEARS = 3
# Backward-compatible alias for older code paths.
LINKEDIN_MAX_ALLOWED_EXPERIENCE_YEARS = LINKEDIN_MAX_ALLOWED_MIN_EXPERIENCE_YEARS
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

NAUKRI_SEARCH_QUERIES = LINKEDIN_SEARCH_QUERIES
NAUKRI_EXPANDED_SEARCH_QUERIES = LINKEDIN_EXPANDED_SEARCH_QUERIES
NAUKRI_LOCATIONS = LINKEDIN_LOCATIONS
NAUKRI_RESULTS_PER_PAGE = 20
NAUKRI_MAX_PAGES_PER_QUERY = 3
NAUKRI_FRESHNESS_DAYS = 1

INDEED_INDIA_SEARCH_QUERIES = LINKEDIN_SEARCH_QUERIES
INDEED_INDIA_LOCATIONS = LINKEDIN_LOCATIONS

SCRAPER_SOURCE_CANDIDATE_LIMITS = {
    "linkedin": 28,
    "naukri": 28,
    "indeed_india": 20,
}
SCRAPER_SOURCE_FINAL_CAPS = {
    "linkedin": 18,
    "naukri": 18,
    "indeed_india": 14,
}
SCRAPER_SOURCE_PRIORITY = {
    "linkedin": 3,
    "naukri": 3,
    "indeed_india": 2,
}

CAREERS_FUTURE_SEARCH_QUERIES = ["IT Support", "Full Stack Web Developer", "Application Support", "Cybersecurity Analyst", "fresher developer"]
CAREERS_FUTURE_SEARCH_CATEGORIES = ["Information Technology"]
CAREERS_FUTURE_SEARCH_EMPLOYMENT_TYPES = ["Full Time"]

# --- Processing Limits ---
SCRAPING_SOURCES = ["linkedin", "naukri", "indeed_india"] # "linkedin", "naukri", "indeed_india", "careers_future"
# Set 0 to score all unscored jobs currently in Supabase.
JOBS_TO_SCORE_PER_RUN = 0
JOBS_TO_CUSTOMIZE_PER_RUN = 20
MIN_SCORE_FOR_CUSTOM_RESUME = 85
# Optional percent mode for custom resume generation.
# 0 = disabled (uses JOBS_TO_CUSTOMIZE_PER_RUN).
# Example: set to 20 for "top 20%" of currently eligible scored jobs.
JOBS_TO_CUSTOMIZE_TOP_PERCENT = 0
MAX_JOBS_PER_SEARCH = {
    "linkedin": 12,
    "naukri": 12,
    "indeed_india": 10,
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
