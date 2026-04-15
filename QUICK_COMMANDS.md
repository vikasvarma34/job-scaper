# Quick Commands (Daily Use)

This file is the fastest way to run the project without searching across code.

## 1. Change Numbers In One Place
Edit: `config.py`

## 0. Base Resume Source Of Truth

Edit: `resume.json`

- `resume.json` is now the primary source of truth for resume generation
- You can manually maintain your summary, skills, experience bullets, project bullets, education, and links there
- Resume generation reads local `resume.json` first and only falls back to Supabase if the file is missing
- You do not need to re-run the resume parser when you are updating your resume manually
- For best output:
  - keep `skills` as a full list of real skills
  - keep `experience.description` as newline-separated bullet text
  - keep `projects.description` as newline-separated bullet text

Main numeric controls:

- `LINKEDIN_MAX_NEW_JOBS_PER_RUN` = max shortlisted jobs saved to Supabase after scraping/filtering
- `MAX_JOBS_PER_SEARCH["linkedin"]` = jobs processed per query
- `JOBS_TO_SCORE_PER_RUN` = how many jobs to score in one run
  - Set `0` to score all unscored jobs currently in Supabase
- `JOBS_TO_CUSTOMIZE_PER_RUN` = how many resumes to generate
- `MIN_SCORE_FOR_CUSTOM_RESUME` = minimum score required for resume generation
  - Set `0` to disable score filtering
- `LINKEDIN_MAX_START` = how deep LinkedIn pagination goes

For your current target (scrape a bigger raw pool, save only the top 100 jobs, score everything saved, customize top 20 with score >= 85), use:

```python
LINKEDIN_MAX_NEW_JOBS_PER_RUN = 100
MAX_JOBS_PER_SEARCH = {"linkedin": 60, "careers_future": 10}
JOBS_TO_SCORE_PER_RUN = 0
JOBS_TO_CUSTOMIZE_PER_RUN = 20
MIN_SCORE_FOR_CUSTOM_RESUME = 85
LINKEDIN_MAX_START = 60
```

If you want a tiny test run, set these to `1`:

```python
LINKEDIN_MAX_NEW_JOBS_PER_RUN = 1
MAX_JOBS_PER_SEARCH = {"linkedin": 1, "careers_future": 1}
JOBS_TO_SCORE_PER_RUN = 1
JOBS_TO_CUSTOMIZE_PER_RUN = 1
MIN_SCORE_FOR_CUSTOM_RESUME = 0
LINKEDIN_MAX_START = 0
```

## 2. Python Commands (Normal Flow)

Run from project root (inside `.venv`):

```bash
python scraper.py
python score_jobs.py
python custom_resume_generator.py
```

`python custom_resume_generator.py` uses the flow selected in `config.py` and reads `resume.json` as the primary base resume source.

Current flow switch:

```python
RESUME_GENERATION_FLOW = "legacy"      # or "two_step_ai"
```

If you want to run the legacy flow explicitly for just this run:

```bash
python custom_resume_generator.py --flow legacy
```

If you want to run the new two-step AI flow explicitly for just this run:

```bash
python custom_resume_generator.py --flow two_step_ai
```

If you want to choose how many top jobs to customize for just this run:

```bash
python custom_resume_generator.py --limit 10
python custom_resume_generator.py --limit 20
python custom_resume_generator.py --limit 22
```

When you use `--limit`, the script now treats it as a manual run and picks the next highest not-yet-generated jobs even if they are below `MIN_SCORE_FOR_CUSTOM_RESUME`.

If you want to generate for one exact job:

```bash
python custom_resume_generator.py --job-id 4399298072
```

If you want to generate for one exact job using the new two-step AI flow:

```bash
python custom_resume_generator.py --job-id 4399298072 --flow two_step_ai
```

If that job already has a resume and you want to regenerate it with the current legacy flow:

```bash
python custom_resume_generator.py --job-id 4399298072 --force-regenerate
```

If that job already has a resume and you want to regenerate it with the new two-step AI flow:

```bash
python custom_resume_generator.py --job-id 4399298072 --flow two_step_ai --force-regenerate
```

If you want to run the full cycle but override only the customize count:

```bash
python daily_ops.py run-cycle --customize-limit 10
python daily_ops.py run-cycle --customize-limit 20
```

## 2.1 Resume Editing Workflow

When you want to update your base resume:

1. Edit `resume.json`
2. Run a single-job test:

```bash
python custom_resume_generator.py --job-id 4399298072 --flow two_step_ai
```

3. If needed, regenerate:

```bash
python custom_resume_generator.py --job-id 4399298072 --flow two_step_ai --force-regenerate
```

## 3. Mark Jobs As Applied

After manual apply on LinkedIn:

```bash
python daily_ops.py mark-applied JOB_ID_1 JOB_ID_2 JOB_ID_3
```

Example:

```bash
python daily_ops.py mark-applied 4395995496 4399415983 4399298072
```

## 4. Export Applied History To CSV

```bash
python daily_ops.py export-applied --output applied_jobs_today.csv
```

## 5. Cleanup / Clear Session (End of Day)

```bash
python daily_ops.py cleanup
```

Optional full cleanup (also remove base resume + source resume file from `resumes` bucket):

```bash
python daily_ops.py cleanup --delete-base-resume --delete-source-resume
```

## 6. SQL: Get LinkedIn Links For Generated Resumes

Run in Supabase SQL Editor:

```sql
select
  j.job_id,
  j.company,
  j.job_title,
  j.status,
  j.resume_score,
  j.location,
  'https://www.linkedin.com/jobs/view/' || j.job_id || '/' as linkedin_job_url,
  cr.id as customized_resume_id,
  cr.resume_link as resume_pdf_path,
  j.scraped_at
from public.jobs j
join public.customized_resumes cr
  on cr.id = j.customized_resume_id
where j.status = 'resume_generated'
order by j.resume_score desc nulls last, j.scraped_at desc;
```

## 7. SQL: See Applied Jobs + Links

```sql
select
  j.job_id,
  j.company,
  j.job_title,
  j.status,
  j.application_date,
  'https://www.linkedin.com/jobs/view/' || j.job_id || '/' as linkedin_job_url,
  cr.resume_link as resume_pdf_path
from public.jobs j
left join public.customized_resumes cr
  on cr.id = j.customized_resume_id
where j.status = 'applied'
order by j.application_date desc nulls last;
```
