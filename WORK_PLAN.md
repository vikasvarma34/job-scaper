# Work Plan

## Current Flow

1. Scrape jobs
2. Score jobs
3. Generate resumes using the legacy flow
4. Generate PDF

## Current Scraping Target

- Save up to 100 jobs
- Try to get most jobs from Hyderabad
- If Hyderabad is not enough, then use Bangalore
- Current controls are mainly in `config.py` and noted in `QUICK_COMMANDS.md`

## Current Resume Generation

- Resume generation is legacy-only
- For each job, resume generation currently goes section by section:
  - summary
  - experience
  - projects
  - skills

## Main Plan

### Step 1

Understand and verify the scraping flow properly.

- Confirm how jobs are being collected
- Confirm the Hyderabad-first behavior
- Confirm the Bangalore fallback behavior
- Confirm the 100-job limit

### Step 2

Improve the scoring logic.

- Review how jobs are currently scored
- Decide what needs to change
- Make scoring more reliable before changing resume generation further

### Step 3

Change the resume-generation logic.

- Right now the old internal/local keyword logic has been removed
- Next, add a clean AI-only keyword step
- First OpenAI call: extract the best keywords for the job and resume
- Second OpenAI call: use those keywords to generate the resume content
- Then send that content to PDF generation

### Step 4

Improve PDF quality and ATS friendliness.

- Make the PDF cleaner
- Remove awkward spacing issues
- Keep the PDF ATS-friendly

### Step 5

Improve prompting carefully.

- Give better instructions to OpenAI
- Do not overcomplicate the prompt
- Keep prompts short, clear, and focused
- Too much prompt detail can make the result worse

## Notes For Tomorrow

- Do not spend more time on scraping for now
- Do not spend more time on scoring for now
- Focus only on:
  - changing the resume-generation flow
  - improving prompts
  - improving PDF styling/output
- Review the exact prompt currently used for section rewriting
- Review how the resume content is being rewritten section by section
- Review how the PDF is being styled/generated
- Make the PDF ATS-friendly and cleaner
- Remove awkward spacing issues in the PDF
- Improve keyword placement across the resume naturally
- Use LLM only for keyword creation
- Do not reintroduce local keyword logic
- Keep prompts simple and focused so the output does not get worse

## Important Files

- `scraper.py`
- `score_jobs.py`
- `custom_resume_generator.py`
- `pdf_generator.py`
- `config.py`
- `QUICK_COMMANDS.md`
- `resume_run.log`

## Session Note

### 2026-04-14

- Resume generation is legacy-only now
- Keyword-targeting logic was removed
- Scraping flow and scoring flow are good enough for now
- Main problems now are:
  - the PDF look/format
  - the rewritten section quality
  - school/education section quality
  - spacing issues in the generated resume
- Tomorrow's focus is only:
  - prompt quality
  - resume-generation flow
  - ATS-friendly PDF styling
  - LLM-only keyword generation and natural keyword distribution
