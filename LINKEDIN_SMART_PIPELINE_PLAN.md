# LinkedIn Smart Pipeline Plan

## Purpose

This document defines a new, isolated LinkedIn-only job-harvest pipeline that does not modify or disturb the existing production pipeline.

The new pipeline is intended to:

- fetch a broader and more useful pool of LinkedIn jobs
- prioritize Hyderabad first, then Bengaluru, then Chennai, then Mumbai
- reduce dependence on brittle local filters
- use low-cost LLM triage heavily when needed
- score all useful jobs without a hard cap
- maximize realistic high-fit jobs for daily application
- remain reusable, maintainable, efficient, and safe for future coding agents

This document is written as an implementation spec, not just a brainstorm.

## Non-Negotiable Constraints

- The old pipeline must remain unchanged.
- The new pipeline must live beside the old one, not inside it.
- The new pipeline must be LinkedIn-only.
- The new pipeline must prioritize Hyderabad above all other cities.
- The new pipeline must prefer Hyderabad and Bengaluru heavily.
- The new pipeline may use many low-output-token Sarvam calls.
- The new pipeline must avoid unnecessary local CPU work and unnecessary LLM calls.
- The new pipeline must avoid unnecessary persistence of raw or sensitive data.
- The new pipeline must be modular so future agents do not rewrite the same logic repeatedly.

## Existing Pipeline Protection Rules

The following files must not have their current behavior changed unless explicitly approved later:

- `scraper.py`
- `score_jobs.py`
- `daily_ops.py` existing commands
- existing config-driven scrape and score flow

Shared helper extraction is allowed only if:

- the old runtime behavior remains identical
- function signatures used by the old pipeline remain stable
- no change is introduced to old filtering thresholds or sequencing

If there is any risk of side effects, duplicate the helper logic into the new pipeline module instead of refactoring the old pipeline.

## Target Outcome

The new pipeline should optimize for:

- maximum realistic count of high-fit LinkedIn jobs per day
- highest concentration of Hyderabad jobs
- strong fallback to Bengaluru
- reduced loss of good jobs due to title weirdness or brittle regex filters
- clean observability so drop reasons are visible

Important honesty rule:

The system should optimize for the best realistic output. It must not be designed around a fake guarantee such as "always produce 30-40 jobs with score 90+ daily," because actual posting volume varies by day.

## Core Design Principle

Do not try to make scraping "smart" too early.

The scraper should be:

- broad
- fast
- deduplicated
- location-priority aware
- minimally filtered

The LLM layer should be the main intelligence layer.

In short:

`fetch broadly -> triage cheaply with LLM -> score survivors -> save ranked jobs`

## New Pipeline Name

Recommended module name:

- `linkedin_smart_pipeline.py`

Recommended CLI command:

- `python linkedin_smart_pipeline.py`

Alternative acceptable names:

- `linkedin_harvest.py`
- `smart_linkedin_harvest.py`

Avoid vague names like:

- `new_pipeline.py`
- `temp_scraper.py`
- `improved_script.py`

## High-Level Flow

The new pipeline should implement this sequence:

1. Load configuration for the new LinkedIn-only flow.
2. Load base resume context once.
3. Fetch LinkedIn search results in city-priority order.
4. Fetch full job details for broad candidate jobs.
5. Apply only minimal hard filters.
6. Run LLM triage in very small batches.
7. Score only triage-approved or borderline jobs.
8. Rank final jobs.
9. Save results and triage metadata to Supabase.
10. Print a run summary with counts and rejection reasons.

## City Priority Rules

Strict priority order:

1. Hyderabad
2. Bengaluru
3. Chennai
4. Mumbai

Design intent:

- Hyderabad should be exhausted first before expanding aggressively elsewhere.
- Bengaluru is the primary fallback.
- Chennai and Mumbai are secondary fallback sources.
- The pipeline should not spend too much effort on Chennai or Mumbai if Hyderabad and Bengaluru already provide enough strong jobs.

Recommended strategy:

- define per-city search budget
- define per-city candidate targets
- continue to next city only when current city under-delivers

## Experience Interpretation Rules

The pipeline should not reject jobs using naive local numeric parsing alone.

The target preference is approximately:

- 1+ years
- 2+ years
- reasonable 2-4, 2-5, 2-6 roles if overall fit is still realistic

The system should be skeptical of:

- 4+ years minimum when the role is clearly senior
- 5+ years minimum
- Staff, Lead, Principal, Architect, Manager, Director type roles

The system should not throw away a role simply because:

- the title is unusual
- the level label is noisy
- the experience line is written inconsistently

This is exactly where the LLM triage layer should make the decision.

## New Pipeline Architecture

Recommended file layout:

- `linkedin_smart_pipeline.py`
  - CLI entrypoint
  - orchestration logic
- `linkedin_smart_pipeline_config.py`
  - config defaults for the new pipeline only
  - optional; acceptable to store inside `config.py` under a dedicated namespace if isolation is preserved
- `linkedin_triage.py`
  - LLM triage prompt builder and response parsing
- `linkedin_pipeline_models.py`
  - Pydantic models for triage and scoring responses
- `linkedin_pipeline_storage.py`
  - isolated Supabase writes for the new pipeline
- `linkedin_pipeline_logging.py`
  - optional helper for structured summary logs

If this feels too fragmented for the first iteration, start with:

- `linkedin_smart_pipeline.py`
- `linkedin_triage.py`

Then split only when repeated logic appears.

## Reuse Strategy

Safe reuse is preferred over rewrites.

The new pipeline should reuse:

- LinkedIn card fetch logic if it can be called without changing old behavior
- LinkedIn detail fetch logic if it can be imported safely
- resume formatting logic from `score_jobs.py` if stable
- LLM client setup from `llm_client.py`
- Supabase connection from `supabase_utils.py`

The new pipeline should not reuse:

- aggressive old scrape-time filtering stages
- old multi-source orchestration
- old capped score-fetch logic if it conflicts with unlimited new scoring

If a helper is reused, preserve these properties:

- no hidden global coupling
- no old-pipeline config assumptions
- no mutation of shared objects that changes other scripts

## Hard Filters vs Soft Filters

### Hard Filters

These may run before LLM triage:

- duplicate job ID
- duplicate normalized URL
- duplicate normalized company-title-location signature
- missing job description
- missing job ID
- city not in allowed list
- obvious top-level seniority titles:
  - staff
  - principal
  - architect
  - director
  - vice president
  - head of
- clearly irrelevant role families:
  - support
  - QA
  - SDET
  - mobile-only
  - non-engineering implementation roles

These hard filters must remain intentionally small.

### Soft Filters

These should be decided mainly by LLM:

- weird title but possibly relevant
- odd experience wording
- unclear backend/full-stack fit
- "2-6 years" role that might still be realistic
- generic "Software Engineer" roles
- roles with partial stack overlap

## LLM Triage Design

The LLM triage stage is the core differentiator of the new pipeline.

### Why Triage Exists

The goal is to avoid:

- over-reliance on brittle regex and keyword filters
- wasting full scoring calls on clearly bad jobs
- throwing away good jobs because titles vary

### Triage Batch Size

Recommended batch size:

- 2 jobs per call to start

Allowed batch size:

- 2 or 3 jobs per call

Do not batch too many jobs per call because:

- prompt noise increases
- output reliability drops
- reasoning errors become harder to debug

### Triage Output

Each job should receive:

- `decision`
- `reason`
- `experience_bucket`
- `location_priority`
- `role_family`
- `confidence`

Recommended `decision` enum:

- `keep`
- `borderline`
- `reject`

Recommended `experience_bucket` enum:

- `ideal`
- `acceptable_stretch`
- `too_senior`
- `unclear`

Recommended `confidence` enum:

- `high`
- `medium`
- `low`

### Triage Prompt Intent

The prompt should ask the model:

- Is this job realistically worth applying to for this candidate?
- Is the role aligned with backend/full-stack/software engineering work?
- Is the experience requirement realistic for the candidate?
- Should we keep it, reject it, or mark it borderline?

The prompt should explicitly instruct the model:

- unusual titles may still be good
- "2-5" and "2-6" are not automatic rejects
- location priority matters
- Hyderabad is preferred most, Bengaluru next
- do not reward irrelevant but buzzword-heavy jobs
- return strict JSON only

### Triage Cost Discipline

Keep triage cheap by:

- including concise job excerpts, not full descriptions, where possible
- sending full descriptions only when title/excerpt is too ambiguous
- using low max tokens
- using strict JSON schema

## Full Scoring Stage

Only jobs with triage result:

- `keep`
- optionally `borderline`

should move to full scoring.

The full scoring stage may reuse the current resume-vs-job scoring prompt with targeted improvements.

### Scoring Rules

- no score cap per run
- process all triage survivors
- score should remain 0-100
- store extracted experience requirement separately
- do not force jobs into a narrow score band

### Recommended Stage Separation

Stage 1:

- triage for keep/reject

Stage 2:

- full scoring for survivors

This two-stage design is more efficient than scoring everything.

## Candidate Fetch Strategy

The new pipeline should fetch broadly from LinkedIn search results.

Recommended behavior:

- use search queries specialized for your profile
- use multiple queries per city
- page enough to get a strong raw pool
- do not prematurely cut based on local fit score

Recommended city loop behavior:

1. Hyderabad primary queries
2. Hyderabad expanded queries
3. Bengaluru primary queries
4. Bengaluru expanded queries
5. Chennai primary queries
6. Mumbai primary queries

Recommended stopping behavior:

- stop when search depth is exhausted
- or when enough high-fit jobs are already collected
- or when marginal value clearly drops

This stopping rule should be configurable.

## Data Model Additions

If schema changes are acceptable, add new optional fields to the jobs table or a new side-table for pipeline metadata.

Recommended metadata fields:

- `pipeline_source`
- `triage_decision`
- `triage_reason`
- `triage_confidence`
- `triage_experience_bucket`
- `triage_model`
- `triaged_at`
- `smart_pipeline_score`
- `smart_pipeline_scored_at`
- `smart_pipeline_run_id`

If changing the jobs table is risky, create a separate table such as:

- `job_pipeline_evaluations`

Recommended columns:

- `job_id`
- `pipeline_name`
- `run_id`
- `triage_decision`
- `triage_reason`
- `triage_confidence`
- `score`
- `experience_required`
- `model_name`
- `created_at`

Preferred design:

- keep the old `resume_score` behavior untouched
- store new-pipeline metadata separately if possible

## Persistence Policy

Do not save everything.

Save:

- accepted jobs
- borderline jobs if useful for review
- triage metadata needed for debugging

Do not save:

- raw full LLM prompt bodies
- unnecessary duplicate job descriptions
- verbose reasoning traces unless temporarily debugging

If logging reasoning traces, ensure they are:

- off by default
- truncated
- never stored in a way that leaks sensitive resume text unnecessarily

## Security and Data Safety Rules

The new pipeline must:

- avoid writing API keys anywhere
- avoid logging full secret-bearing environment content
- avoid storing entire resume prompt text in logs
- avoid storing full raw model responses unless debugging
- avoid leaking Supabase service role key anywhere
- avoid passing unnecessary personal data to the LLM

Only send to the LLM the minimum candidate context required:

- structured resume summary
- skills
- selected relevant experience/project summaries

Do not send:

- irrelevant personal identifiers
- full contact details unless absolutely required

## Performance Rules

Efficient means:

- avoid repeated resume formatting for each job
- avoid repeated Supabase full-table scans inside inner loops
- avoid repeated prompt construction for identical context
- avoid duplicate detail fetches for the same job in one run
- avoid full scoring for jobs already rejected by triage

Recommended optimizations:

- load base resume once
- fetch existing job match data once per run
- cache normalized resume context once
- cache city/query progress in memory per run
- cache triage results by job ID during a run

## Local Resource Rules

The pipeline should be efficient on local machine resources.

Do not:

- create large in-memory prompt histories
- duplicate huge job lists across many intermediate arrays without need
- over-log every candidate in full text
- perform unnecessary sorting repeatedly

Do:

- use iterators or streaming-style loops where reasonable
- rank only when needed
- preserve only compact candidate representations until detail fetch

## Coding Standards for Future Agents

Any coding agent implementing this plan must follow these rules.

### Naming

Use clear, intention-revealing names.

Good examples:

- `run_linkedin_smart_pipeline`
- `fetch_linkedin_candidates_for_city`
- `triage_linkedin_jobs_batch`
- `score_triaged_jobs`
- `persist_pipeline_results`

Bad examples:

- `run2`
- `do_jobs`
- `helper_final`
- `filter_data_again`

### Function Design

Each function should do one of these:

- fetch
- normalize
- triage
- score
- rank
- persist
- summarize

Avoid functions that do all of the above.

### Configuration

Do not scatter magic numbers.

Every major behavior should be configurable:

- cities
- query lists
- triage batch size
- max pages
- target accepted jobs
- score threshold
- whether borderline jobs are scored

### Logging

Logs should be high-signal.

Every run summary should include:

- raw cards fetched
- detailed jobs fetched
- hard-filter rejects
- triage keep count
- triage borderline count
- triage reject count
- fully scored count
- score bands
- final persisted count
- city-wise contribution

### Error Handling

Network and parsing errors should:

- fail gracefully
- not kill the whole run unless systemic
- be recorded in summary counters

### Testing

Every new parsing or triage response model should be testable in isolation.

At minimum, future implementation should include tests for:

- triage response parsing
- candidate dedupe logic
- city priority sequencing
- stopping logic
- score persistence

## Suggested Implementation Phases

### Phase 1: Skeleton

Create the new isolated script and configuration surface.

Deliverables:

- new entrypoint file
- no impact to old scripts
- dry-run mode
- basic logging

### Phase 2: Broad LinkedIn Fetch

Implement city-priority LinkedIn collection with minimal hard filters.

Deliverables:

- Hyderabad-first city order
- detail fetch
- dedupe
- no local fit-score dependence

### Phase 3: LLM Triage

Implement low-token Sarvam triage in batches of 2 or 3 jobs.

Deliverables:

- strict JSON output parsing
- keep/borderline/reject decisions
- triage reason capture

### Phase 4: Full Scoring

Score accepted jobs without a hard cap.

Deliverables:

- full score 0-100
- extracted experience requirement
- ranking

### Phase 5: Persistence and Summary

Persist accepted results and produce useful summaries.

Deliverables:

- Supabase writes
- city-wise summary
- reason-based drop summary

### Phase 6: Tuning

Tune prompt quality and stopping behavior based on actual results.

Deliverables:

- improved high-fit job yield
- fewer unnecessary calls
- better Hyderabad concentration

## Prompting Guidelines for Future Agents

### Triage Prompt Rules

- keep prompt short
- keep output schema strict
- give explicit candidate profile summary
- state city priority clearly
- say that weird titles may still be relevant
- say that 2-5 and 2-6 roles are not automatic rejects
- prohibit explanatory prose outside JSON

### Scoring Prompt Rules

- score holistically, not by buzzword counting
- prioritize real backend/full-stack evidence
- consider experience realism
- penalize clear mismatch
- return only structured JSON

### Token Discipline

- low max output tokens for triage
- moderate tokens only for full scoring
- do not ask for long reasoning
- do not request chain-of-thought

## Rollout Strategy

This pipeline should be introduced safely.

### Step 1

Implement as a new script only.

### Step 2

Run it manually alongside the old pipeline for comparison.

### Step 3

Compare:

- number of useful jobs
- city distribution
- score distribution
- false rejects

### Step 4

Tune prompts and thresholds.

### Step 5

Only after confidence is established, consider integrating it into convenience commands.

## Anti-Patterns to Avoid

- rewriting the old scraper instead of creating the new module
- copying large blocks of code without isolating common helpers carefully
- relying on local title scoring as the main gate
- using huge prompts for triage
- scoring obviously bad jobs before triage
- storing excessive raw LLM data
- fetching Supabase state repeatedly inside inner loops
- hard-coding city behavior in multiple places
- mixing old and new pipeline persistence semantics

## Minimum Deliverable Definition

The first acceptable version of the new pipeline must:

- run independently
- use LinkedIn only
- enforce Hyderabad -> Bengaluru -> Chennai -> Mumbai order
- fetch a broader pool than the old pipeline
- use LLM triage as the primary filter
- score triage survivors without a hard run cap
- store enough metadata to debug outcomes
- not change old pipeline behavior

## Agent Handoff Prompt

Future coding agents may be given the following instruction:

Build the new isolated LinkedIn-only smart pipeline described in `LINKEDIN_SMART_PIPELINE_PLAN.md`.
Do not modify the behavior of the old pipeline.
Prefer reuse of safe existing helpers, but do not refactor old runtime code if there is any risk of side effects.
Implement in small, testable modules with clear naming.
Use LLM triage as the main filter, not local heuristics.
Optimize for Hyderabad-first, then Bengaluru, then Chennai, then Mumbai.
Keep prompts compact, outputs structured, and logging high-signal.
Avoid unnecessary local work, unnecessary LLM calls, and unnecessary persistence of raw prompt data.

## Final Recommendation

This plan should be treated as the source-of-truth implementation spec for the new pipeline.

Any future implementation should aim to be:

- isolated
- modular
- observable
- cheap to run
- easy to tune
- safe for repeated agent-based development
