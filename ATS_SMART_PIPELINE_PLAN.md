# ATS Smart Pipeline Plan

## Purpose

This document defines a new, isolated daily job-collection pipeline optimized for the best possible daily coverage from public ATS-powered job sources rather than depending only on LinkedIn.

The main objective is:

- collect more matching jobs every day
- prefer direct-source public job feeds over board mirrors
- maximize roles that match the candidate's actual profile
- use low-cost low-token LLM triage
- use stronger LLM scoring only on likely good jobs
- preserve the old pipeline unchanged

This plan is the source-of-truth implementation spec for the new ATS-first pipeline.

## Live Verification Status

The public ATS endpoints below were manually sanity-checked on 2026-04-24 from this workspace.

### Verified Working Without Authentication

- Greenhouse public board endpoint
- Ashby public job board endpoint
- Lever public postings endpoint

### Verification Notes

The following live unauthenticated requests returned data successfully:

#### Greenhouse

```bash
curl -s 'https://boards-api.greenhouse.io/v1/boards/stripe/jobs?content=false'
```

Observed result:

- returned JSON successfully
- no authentication required
- included fields such as title, location, id, URL, and timestamps

#### Ashby

```bash
curl -s 'https://api.ashbyhq.com/posting-api/job-board/Ashby?includeCompensation=false'
```

Observed result:

- returned JSON successfully
- no authentication required
- included fields such as title, location, description, published timestamp, job URL, and apply URL

#### Lever

```bash
curl -s 'https://api.lever.co/v0/postings/leverdemo?mode=json&limit=1'
```

Observed result:

- returned JSON successfully
- no authentication required
- included posting data from Lever's demo site

### Important Caveat

These public endpoints work only when the company actually uses that ATS and exposes a public board.

That means the pipeline must discover or maintain valid board identifiers such as:

- Greenhouse board token
- Ashby job board name
- Lever site name

## India Coverage Validation

The public endpoint format is not just US-only. During research, public India and Hyderabad/Bengaluru-relevant jobs were found on these ATS-backed sources.

### Verified India-Relevant Examples

- Ashby:
  - Notion had a `Software Engineer, Developer Experience` role in `Hyderabad, India`
  - Cygnify had a `Senior Software Engineer` role in `Hyderabad, India`
  - Sarvam had a `Forward Deployed Software Engineer, Model API` role in `Bengaluru`
- Lever:
  - JumpCloud had a `Platform Software Engineer - India` role listing `Hyderabad, India - Remote` and `Bangalore, India - Remote`
  - Coupa had a `Software Engineer` role surfaced for `Hyderabad, India`
- Greenhouse:
  - Crunchyroll had a `Senior Software Engineer` role in `Hyderabad, Telangana, India`

### Important Interpretation

This proves:

- the ATS endpoint families can expose India jobs
- Hyderabad and Bengaluru can appear in public ATS feeds
- the issue is not whether the endpoints are US-only
- the real challenge is building and maintaining a good company/board list focused on companies that hire in India

### Practical Conclusion

The ATS-first strategy is viable for India, but its quality depends heavily on:

- selecting the right companies and board identifiers
- applying strict India freshness and location filtering
- using triage well after collection

## Public Endpoint Reference

This section gives the concrete endpoint shapes the implementation should use.

### Greenhouse Public Job Board API

Official base pattern:

```text
GET https://boards-api.greenhouse.io/v1/boards/{board_token}/jobs
```

Useful query params:

- `content=true`
- `content=false`

Recommended usage:

- first call with `content=false` for lighter list retrieval
- fetch richer content only when needed

Example:

```bash
curl -s 'https://boards-api.greenhouse.io/v1/boards/stripe/jobs?content=false'
```

How to identify `board_token`:

- visit the company's Greenhouse-hosted careers page
- the board token is usually the board path token

Expected useful fields:

- `id`
- `title`
- `absolute_url`
- `location.name`
- `updated_at`
- `first_published`
- `company_name`

Authentication:

- not required for public job retrieval

### Ashby Public Job Postings API

Official base pattern:

```text
GET https://api.ashbyhq.com/posting-api/job-board/{JOB_BOARD_NAME}?includeCompensation={true|false}
```

Recommended usage:

- use `includeCompensation=false` by default to keep payload lighter

Example:

```bash
curl -s 'https://api.ashbyhq.com/posting-api/job-board/Ashby?includeCompensation=false'
```

How to identify `JOB_BOARD_NAME`:

- from a public Ashby jobs URL such as:
  - `https://jobs.ashbyhq.com/Ashby`
- the trailing path segment is the job board name

Expected useful fields:

- `jobs[].title`
- `jobs[].location`
- `jobs[].secondaryLocations`
- `jobs[].descriptionPlain`
- `jobs[].descriptionHtml`
- `jobs[].publishedAt`
- `jobs[].employmentType`
- `jobs[].workplaceType`
- `jobs[].jobUrl`
- `jobs[].applyUrl`

Authentication:

- not required for public job retrieval

### Lever Public Postings API

Official base pattern:

```text
GET https://api.lever.co/v0/postings/{SITE}?mode=json&skip={X}&limit={Y}
```

Recommended usage:

- always request `mode=json`
- use pagination with `skip` and `limit`

Example:

```bash
curl -s 'https://api.lever.co/v0/postings/leverdemo?mode=json&limit=1'
```

How to identify `SITE`:

- from a Lever-hosted jobs site such as:
  - `https://jobs.lever.co/{SITE}`

Expected useful fields:

- posting id
- plain text description content
- categories such as location/team/commitment
- hosted apply URL

Authentication:

- not required for published public postings

### Workday and Other Public Career Systems

Do not treat Workday as a clean unauthenticated universal API source in phase 1.

Reason:

- each company often exposes different public page structures
- extraction patterns are less standardized than Greenhouse, Ashby, and Lever
- implementation cost is higher for less predictable yield

Recommendation:

- phase 1 should skip Workday
- phase 2 may add targeted support for selected companies only

## Why This Pipeline Exists

LinkedIn is useful, but it is not the best sole source if the goal is maximum daily coverage.

Public ATS-backed career feeds often provide:

- fresher jobs
- more complete jobs
- more jobs than LinkedIn indexes quickly
- direct-source descriptions
- cleaner structured metadata

This pipeline is designed to collect from public job feeds first, then optionally add LinkedIn later if desired.

## Non-Negotiable Constraints

- The old pipeline must remain unchanged.
- The new pipeline must be fully isolated from the old scrape/score flow.
- The new pipeline must prioritize fit, not source loyalty.
- The new pipeline must only keep jobs that match the candidate's background and target direction.
- All triage calls to Sarvam must use low output tokens.
- Triage should use a lighter model such as a 30B-class model.
- Final scoring should use a stronger model such as Sarvam 105B.
- Triage responses must be short, structured, and explanation-light.
- The pipeline must avoid unnecessary CPU, network, and token waste.
- The pipeline must be modular and reusable so future coding agents can extend it safely.

## Candidate Target Profile

This pipeline must be optimized for the following candidate profile.

### Experience Level

- approximately 2 years of software development experience

### Primary Role Targets

- Full Stack Developer
- Backend Developer
- Software Developer
- Software Engineer

### Role Flexibility Rule

The pipeline should stay flexible across languages and stacks as long as the role is realistically software-development oriented and not frontend-only.

Examples of acceptable backend or full-stack language families:

- Java
- Python
- C#
- .NET
- Go
- Node.js
- TypeScript when the role is still backend-heavy or clearly full-stack

The pipeline should not require one exact language family at triage time if the job still looks like a realistic backend or full-stack software role.

### Acceptable Adjacent Role Targets

- Full Stack Engineer
- Backend Engineer
- Java Developer
- Java Software Engineer
- Application Developer
- Member of Technical Staff only if the job content is still clearly early/mid-career and realistic

### Strong Skill/Stack Alignment

Jobs should be preferred when they align with most or several of these:

- Java
- Spring Boot
- REST APIs
- backend services
- databases / SQL
- microservices
- full-stack work where backend is substantial
- distributed systems at a realistic level for 2 years experience

### Fit Definition For Triage

At triage time, the real question is:

- "Is this realistically worth keeping for a candidate with about 2 years experience in backend/full-stack/software development?"

This should matter more than exact role-title matching.

The triage system should allow:

- backend jobs in multiple mainstream languages
- full-stack jobs where backend work is substantial
- generic software engineering jobs if the description clearly matches

The triage system should reject:

- purely frontend roles
- jobs where backend ownership is absent

### Lower-Priority But Still Acceptable

- generic Software Engineer roles
- generic Software Developer roles
- full-stack roles using adjacent stacks if backend depth is still strong

### Strongly Avoid

- frontend-only roles
- QA roles
- SDET roles
- testing-only roles
- technical support roles
- application support roles
- IT support roles
- DevOps-only roles
- SRE-only roles
- mobile-only roles
- data-only roles
- analyst roles
- senior / lead / principal / architect / manager roles

## Daily Freshness Rule

This pipeline is intended to run every day.

Therefore:

- only jobs from the last 24 hours should be considered eligible by default

This should be treated as a mandatory rule in the normal daily run, not as a soft preference.

The freshness rule should be applied as early as possible if the source exposes publish/update timestamps.

If a source does not expose a trustworthy timestamp:

- mark freshness as `unclear`
- allow triage only if the source is otherwise high-value
- optionally reject by default in strict mode

## Location Priority Rules

Strict location priority order:

1. Hyderabad
2. Bengaluru
3. Chennai
4. Mumbai

Design goals:

- maximize Hyderabad volume
- use Bengaluru as the main fallback
- only rely more heavily on Chennai and Mumbai if Hyderabad and Bengaluru do not provide enough good jobs

The pipeline should support city-priority aware ranking, not just source-wide ranking.

## Experience Interpretation Rules

The system must not reject jobs too aggressively based only on local regex logic.

Ideal experience signals:

- 1+ years
- 2+ years
- 1-3 years
- 2-4 years
- 2-5 years
- 2-6 years when the overall role still looks realistic

Usually reject:

- 4+ years minimum for clearly senior roles
- 5+ years minimum
- roles where the full job content indicates experience far beyond target level

Important:

- weird experience wording should be interpreted by LLM triage, not by brittle local rules alone

## Source Strategy

### Primary Sources

The first version of the pipeline should support:

- Greenhouse public job board feeds
- Ashby public job posting feeds
- Lever public job posting feeds

### Secondary Sources

Add only after the primary sources are stable:

- Workday-powered public external career pages
- company-hosted careers pages with structured JSON or sitemap patterns

### LinkedIn Status

LinkedIn should not be the main dependency for this new pipeline.

LinkedIn may be added later as an optional supplementary source, but not as the core source.

## Source Coverage Philosophy

The goal is not "collect every job from every company."

The goal is:

- collect from the richest public sources
- normalize them well
- remove duplicates
- triage efficiently
- keep only realistic jobs

## Recommended New Module Names

Recommended top-level script:

- `ats_smart_pipeline.py`

Recommended helpers:

- `ats_source_collectors.py`
- `ats_job_normalizer.py`
- `ats_triage.py`
- `ats_scoring.py`
- `ats_pipeline_models.py`
- `ats_pipeline_storage.py`
- `ats_pipeline_config.py`

If starting smaller, acceptable first version:

- `ats_smart_pipeline.py`
- `ats_triage.py`
- `ats_scoring.py`

## High-Level Flow

The new pipeline should execute in this order:

1. Load ATS-first pipeline config.
2. Load base resume context once.
3. Fetch jobs from each supported ATS source.
4. Normalize all jobs into one internal schema.
5. Apply minimal hard filters.
6. Deduplicate across all sources.
7. Apply low-cost LLM triage in very small batches.
8. Score only triage-approved jobs with stronger scoring model.
9. Rank final jobs.
10. Persist accepted jobs and useful metadata.
11. Print a concise run summary.

## Internal Job Schema

All sources should normalize into one shared shape.

Recommended fields:

- `source`
- `source_company_identifier`
- `job_id`
- `job_url`
- `apply_url`
- `company`
- `job_title`
- `location`
- `secondary_locations`
- `country`
- `city`
- `employment_type`
- `workplace_type`
- `description_text`
- `description_html`
- `posted_at`
- `freshness_status`
- `raw_experience_text`
- `normalized_experience_hint`

Optional metadata:

- `department`
- `team`
- `salary_range`
- `source_payload_hash`

## Minimal Hard Filters

Hard filters should remain intentionally small.

Allowed hard filters:

- missing job ID
- missing job URL and missing apply URL
- missing description text
- city outside allowed target cities
- freshness older than 24 hours when reliable timestamp exists
- obvious seniority title rejects:
  - staff
  - lead
  - principal
  - architect
  - manager
  - director
  - vice president
  - head of
- obvious irrelevant role family rejects:
  - QA
  - SDET
  - testing-only
  - support
  - mobile-only
  - analyst

Everything else should be left to LLM triage.

## Deduplication Rules

Cross-source dedupe is critical.

Use a layered dedupe strategy:

- exact apply URL
- exact job URL
- normalized company + normalized title + normalized city
- source payload hash when useful

The dedupe system must avoid discarding distinct roles from the same company unless they are clearly identical.

## LLM Triage Strategy

This is the most important stage in the new pipeline.

### Goal

Cheaply decide whether a job is worth keeping for full scoring.

### Model

Recommended:

- Sarvam 30B-class model for triage

### Token Discipline

All triage calls must be low-token.

Rules:

- batch 2 jobs per call initially
- keep prompt compact
- keep response compact
- do not ask for explanations
- do not ask for reasoning
- do not request long summaries
- use low max output tokens
- use strict JSON only

### Triage Prompt Objective

For each job, the model should answer:

- should we keep this job for scoring?

The output should be short and strict.

Recommended output schema:

```json
{
  "jobs": [
    {
      "job_id": "abc",
      "decision": "keep"
    },
    {
      "job_id": "def",
      "decision": "reject"
    }
  ]
}
```

Allowed `decision` values:

- `keep`
- `borderline`
- `reject`

The default user-facing behavior should be effectively binary:

- keep it
- do not keep it

No big explanations should be generated.

### Triage Input Content

Provide only the minimum useful context:

- normalized candidate profile summary
- key skills
- years of experience target
- target roles
- location priority
- concise job title
- company
- location
- short description excerpt
- explicit experience text if available

Do not send:

- full personal details
- full contact information
- unnecessary large resume content

### Triage Decision Intent

The model should keep jobs when:

- they appear realistically matchable for a candidate with about 2 years experience
- they fit backend/full-stack/software development direction
- they are in preferred cities
- they are from the last 24 hours

The model should reject jobs when:

- they are clearly wrong role family
- they are clearly too senior
- they are clearly weak-fit or irrelevant

### Recommended Triage Prompt Shape

The triage prompt should be short and operational.

Recommended response target:

```json
{
  "jobs": [
    {"job_id": "job_1", "decision": "keep"},
    {"job_id": "job_2", "decision": "reject"}
  ]
}
```

Do not ask the model for:

- long reasons
- prose explanation
- ranking paragraphs
- chain-of-thought

## Full Scoring Strategy

Only jobs with triage result:

- `keep`
- optionally `borderline`

should move to scoring.

### Model

Recommended:

- Sarvam 105B for scoring

Recommended model split:

- triage: Sarvam 30B-class model
- scoring: Sarvam 105B

### Scoring Objective

Score only serious candidates for application.

Recommended structured output:

```json
{
  "score": 93,
  "experience_required": "2-5 years"
}
```

### Scoring Rules

- no long explanation output
- no chain-of-thought
- no decorative prose
- return only strict JSON
- keep output tokens low
- return only score plus normalized experience requirement

### Scoring Without Hard Run Cap

The new pipeline should not enforce a hard scoring count cap by default.

It should score all triage-approved jobs from the daily run.

Config may still support optional safeguards, but default behavior should not artificially stop early.

## Ranking Rules

After scoring, rank jobs using a weighted strategy.

Suggested ranking priority:

1. higher score
2. Hyderabad before Bengaluru
3. Bengaluru before Chennai
4. Chennai before Mumbai
5. fresher timestamp
6. stronger source confidence

This ranking should make Hyderabad win ties whenever reasonable.

## Persistence Strategy

The old persistence model must remain untouched.

Preferred approach:

- store new pipeline results in a separate table or metadata layer

Recommended table:

- `ats_pipeline_jobs`

Recommended columns:

- `job_id`
- `source`
- `job_url`
- `apply_url`
- `company`
- `job_title`
- `location`
- `posted_at`
- `triage_decision`
- `score`
- `experience_required`
- `run_id`
- `created_at`

Optional debug table:

- `ats_pipeline_evaluations`

Use this only if extra observability is needed.

## Sarvam Integration Plan

The implementation should explicitly support separate model routing for triage and scoring.

### Required Environment Variables

Preferred configuration:

```env
SARVAM_API_KEY=...
SARVAM_API_BASE=https://api.sarvam.ai/v1

ATS_TRIAGE_LLM_MODEL=openai/<sarvam-30b-model-name>
ATS_SCORING_LLM_MODEL=openai/sarvam-105b
ATS_TRIAGE_MAX_TOKENS=120
ATS_SCORING_MAX_TOKENS=220
```

If direct Sarvam routing is used instead of the general LLM abstraction, the pipeline should still preserve two model settings:

- triage model
- scoring model

### Triage Call Strategy

For triage:

- use low output tokens
- return only small structured JSON
- do not spend tokens on explanation

### Scoring Call Strategy

For scoring:

- use 105B only after triage approval
- still keep output compact
- only ask for score plus normalized experience requirement

### Fallback Rule

If the chosen 30B model name changes over time, keep the config environment-driven.

Use a config key like:

- `ATS_TRIAGE_LLM_MODEL`

instead of embedding one fixed unofficial string throughout the codebase.

## Data Safety Rules

The pipeline must:

- not log secrets
- not log API keys
- not log full resume text unnecessarily
- not store full raw prompts
- not store full raw model responses unless debug mode is explicitly enabled
- not send unnecessary personal data to the model

## Efficiency Rules

### Network Efficiency

- load resume context once
- reuse HTTP sessions where possible
- avoid refetching the same ATS endpoint multiple times in one run
- cache dedupe keys in memory for the current run

### LLM Efficiency

- triage first
- score second
- low tokens for triage
- compact JSON output only
- no explanation output

### Local CPU Efficiency

- avoid repeated normalization passes
- avoid repeated sorts over full data unnecessarily
- keep candidate representation compact until needed

## Source Collector Design

Each source should implement the same collector interface.

Recommended shape:

- `fetch_jobs() -> list[NormalizedJob]`

Each collector should:

- fetch raw source data
- normalize to internal schema
- return normalized jobs only

The orchestrator should not contain source-specific parsing logic.

Recommended collector modules:

- `collect_greenhouse_jobs`
- `collect_ashby_jobs`
- `collect_lever_jobs`
- `collect_workday_jobs`

## API Call Implementation Guidance

The implementation should use lightweight source-specific fetch helpers.

Recommended signatures:

```python
def fetch_greenhouse_jobs(board_token: str) -> list[dict]:
    ...

def fetch_ashby_jobs(job_board_name: str) -> list[dict]:
    ...

def fetch_lever_jobs(site_name: str, skip: int = 0, limit: int = 50) -> list[dict]:
    ...
```

Recommended source config shape:

```python
ATS_SOURCE_TARGETS = {
    "greenhouse": ["stripe", "example_company"],
    "ashby": ["Ashby", "example_company"],
    "lever": ["leverdemo"],
}
```

### Important Design Rule

The phase-1 pipeline should not try to discover every possible ATS board on the internet automatically.

Instead, phase 1 should:

- support curated ATS identifiers
- fetch efficiently from those identifiers
- prove the filtering and scoring pipeline works well

Phase 2 may add internet-scale source discovery if needed.

## Suggested Source-Specific Notes

### Greenhouse

Use public job board endpoints.

Notes:

- GET access is public
- many companies expose jobs via Greenhouse boards
- good structured fields
- verified working without authentication

### Ashby

Use public job posting API.

Notes:

- returns published jobs
- includes publish timestamp
- includes plain description and HTML
- strong source for modern tech companies
- verified working without authentication

### Lever

Use public postings endpoints.

Notes:

- published jobs are public
- good structured text fields
- useful location/team metadata
- verified working without authentication

### Workday

Treat as second-phase support.

Notes:

- inconsistent public structures
- often requires career page extraction patterns instead of one universal public API

## Best Practical Phase-1 Coverage Strategy

To maximize useful jobs quickly, phase 1 should:

1. maintain a curated list of companies or ATS board identifiers
2. fetch all jobs from their public ATS feeds
3. keep only jobs from the last 24 hours
4. location-filter to Hyderabad, Bengaluru, Chennai, Mumbai
5. triage with Sarvam 30B
6. score survivors with Sarvam 105B

## Logging and Observability

Every run should print a concise but useful summary:

- source counts
- raw fetched count
- post-hard-filter count
- deduped count
- triage keep count
- triage borderline count
- triage reject count
- scored count
- score bands
- city distribution
- final kept count

Recommended score bands:

- `90+`
- `80-89`
- `70-79`
- `<70`

## Quality Threshold Philosophy

The system should optimize for:

- more realistic good jobs

not:

- inflating counts with poor-fit jobs

The user hopes to see 40-50 strong jobs daily, but the pipeline must be honest:

- maximize realistic fit
- do not pretend weak jobs are strong jobs

## Suggested Implementation Phases

### Phase 1

Build isolated ATS-first skeleton.

Deliverables:

- new script
- isolated config
- dry-run support

### Phase 2

Implement Greenhouse, Ashby, and Lever collectors.

Deliverables:

- normalized jobs from all three sources
- basic dedupe

### Phase 3

Implement low-token Sarvam triage.

Deliverables:

- 30B triage calls
- `keep/reject/borderline`
- compact JSON output

### Phase 4

Implement strong scoring.

Deliverables:

- 105B scoring calls
- score output
- final ranking

### Phase 5

Persist results and expose daily summary.

Deliverables:

- run summary
- optional storage table

### Phase 6

Tune quality and city weighting.

Deliverables:

- improved Hyderabad concentration
- improved high-fit yield

## Prompting Rules for Future Agents

### Triage Prompt Rules

- compact candidate profile
- compact job input
- low output tokens
- strict JSON
- no explanations
- binary keep/reject behavior preferred

### Scoring Prompt Rules

- strict JSON only
- no reasoning text
- no markdown
- no long narrative

## Agent Handoff Prompt

Future coding agents may be instructed:

Implement the ATS-first daily job pipeline described in `ATS_SMART_PIPELINE_PLAN.md`.
Do not change the old pipeline.
Use Greenhouse, Ashby, and Lever as primary sources.
Use Sarvam 30B-class model for low-token triage and Sarvam 105B for final scoring.
Keep triage output minimal: only whether to keep the job or not, plus optional borderline.
Optimize for Hyderabad first, then Bengaluru, then Chennai, then Mumbai.
Only include jobs from the last 24 hours.
Keep the system modular, reusable, efficient, and safe.

## Final Recommendation

This ATS-first strategy is the best practical path for maximum daily coverage with strong-fit filtering and controlled LLM cost.

It should replace the LinkedIn-only experimental direction as the preferred next implementation path.
