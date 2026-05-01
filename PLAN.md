# Resume Strategy Implementation Plan

## Goal

Improve generated resumes and cover letters for India-focused software engineering applications by balancing ATS keyword coverage with recruiter readability.

The current system is strong for keyword matching, but the generated resume can become too dense, repetitive, and AI-written. The new direction is to make the default resume sharper, shorter, and easier for a recruiter to scan.

## Core Decisions

1. Default resume mode should target one page.
2. Projects should not be included by default.
3. Projects should be optional through a user-controlled toggle.
4. Resume and cover letter language should use simple, direct professional English.
5. The resume should not sound like AI-generated marketing copy.
6. Skills should be targeted per job instead of listing every known technology.
7. Professional work projects should usually be represented inside experience bullets, not repeated in a separate Projects section.
8. PDF layout should use space more efficiently, especially in the Professional Experience header.
9. If the user selects a job for resume generation, treat the job fit as user-verified and do not spend prompt effort judging whether the candidate can do the job.

## Resume Modes

### One-Page Mode

This is the default mode for most applications.

Rules:

- Do not include a Projects section.
- Keep the resume to one page.
- Use enough content to fill the page cleanly, not a sparse half-page resume.
- Use 7 to 8 strong experience lines when they fit.
- Keep the summary to 4 or 5 short sentences.
- Keep skills compact and targeted to the job.
- Use 5 to 8 grouped skill lines when they fit naturally.
- Include factual quantified results when supported by the base resume.
- Prefer the strongest evidence over full background coverage.
- Avoid repeating the same achievement in multiple places.

Best for:

- Java Developer
- Python Developer
- Go Developer
- Backend Developer
- Full Stack Developer
- Software Engineer
- Enterprise application roles
- Most India-based job applications

### Project Mode

This mode is enabled only when the user chooses to include projects.

Rules:

- Include a Projects section only when it helps prove a role-specific skill.
- Experience bullets should become shorter if Projects are included.
- Allow more than one page if needed, but keep the resume concise.
- Projects should not repeat experience bullets word for word.
- Use project bullets to prove implementation depth, not to add filler.

Best for:

- AI Engineer
- LLM Engineer
- RAG Developer
- Healthcare AI roles
- Roles asking for OpenAI, Pinecone, embeddings, transcription, or vector search
- Roles asking for project or portfolio examples

## Resume JSON Strategy

`resume.json` should act more like a truthful evidence bank than a final resume.

It should contain:

- factual employment details
- strongest measurable outcomes
- supported technologies
- project evidence that can be selected when useful
- concise proof points for each major stack

It should avoid:

- repeated bullets across experience and projects
- long generic responsibilities
- inflated wording
- trying to show every skill in every generated resume

The LLM should be instructed to select from the evidence bank, not copy all of it.

## User-Verified Job Fit Rule

The user reviews job descriptions before generating a resume or cover letter. If a job is selected for generation, assume the user has already confirmed that the candidate has done the required work or has the required skills.

The LLM should:

- Treat the job description as the target direction.
- Use job-description skills and wording as user-verified targeting guidance.
- Avoid wasting output or reasoning on whether the candidate is qualified.
- Have enough freedom to include relevant job keywords even when they are not written word for word in `resume.json`.
- Use the job description to choose the strongest angle for the resume.
- Prefer natural, believable phrasing over mechanically copying the base resume.

The LLM should still not invent:

- employers
- dates
- job titles
- degrees
- fake projects
- fake metrics
- fake production ownership
- specific implementation details that conflict with known resume facts

Practical rule:

- For summary and skills, the LLM may use user-verified job keywords more freely.
- For experience and project bullets, the LLM should connect keywords to real work patterns from the resume and avoid making up exact claims that are not believable from the candidate history.

## Skill Targeting Strategy

The skills section should rotate based on the target job.

For Java roles, emphasize:

- Java
- Spring Boot
- Spring Cloud
- Hibernate / JPA
- REST APIs
- Microservices
- SQL
- Spring Security
- JWT / OAuth
- AWS / Docker / Jenkins where relevant

For Go roles, emphasize:

- Go
- GraphQL
- REST APIs
- Middleware
- Microservices
- SQL
- API integrations
- Debugging and integration testing

For Python roles, emphasize:

- Python
- REST APIs
- backend workflows
- automation where supported
- SQL
- AWS
- AI or data workflow experience when relevant

For AI or LLM roles, emphasize:

- OpenAI
- Gemini if relevant
- RAG workflows
- Pinecone
- embeddings
- transcription workflows
- AWS Transcribe
- S3
- secure private-data retrieval

General rule:

- Include direct job keywords.
- Include adjacent supporting technologies.
- Remove unrelated technologies that dilute the profile.
- Keep enough breadth to look real, but not so much that the candidate identity becomes unclear.

## Gemini Prompt Direction

Gemini should receive clear constraints because it can become verbose, polished, or generic if the prompt is too open.

Prompt rules to add or strengthen:

- Use India-focused software engineering resume style.
- Use simple professional English.
- Use short sentences.
- Avoid fancy corporate wording.
- Avoid dramatic enthusiasm.
- Avoid AI-sounding phrases.
- Prefer direct verbs like "built", "used", "worked on", "improved", "integrated", "fixed", and "supported".
- Avoid words like "spearheaded", "leveraged", "orchestrated", "transformative", "cutting-edge", "robust", and "proven track record".
- Do not include all available information.
- Select only the most relevant and strongest points for the target job.
- Keep ATS keywords natural and controlled.
- Keywords should support the candidate story, not become the story.
- Assume the selected job is already user-verified for fit.
- Do not judge whether the candidate can do the job.
- Use the job description as the source of targeting keywords and role emphasis.
- Allow relevant user-verified job keywords in the summary and skills even if the exact wording is not already present in `resume.json`.

## Cover Letter Direction

Cover letters should follow the same tone rules as resumes.

Rules:

- Keep the letter short and direct.
- Use simple English.
- Match the job description without copying it.
- Mention 1 or 2 strongest fit points only.
- Avoid generic AI openings and dramatic enthusiasm.
- Do not restate the full resume.
- Keep paragraphs short.
- Prefer a practical job-application tone suitable for Indian recruiters.

## PDF Layout Plan

The current experience header uses too much vertical space:

```text
Full Stack Developer | Markitech AI
Toronto, ON, CANADA
Feb 2024 - Feb 2026
```

Preferred compact layout:

```text
Full Stack Developer
Markitech AI | Toronto, ON, Canada                         Feb 2024 - Feb 2026
```

Alternative compact layout:

```text
Full Stack Developer | Markitech AI | Toronto, ON, Canada   Feb 2024 - Feb 2026
```

Preferred option:

- Use the title on the first line.
- Use company and location on the second line.
- Right-align the dates on the same second line.

Other PDF improvements:

- Reduce wasted spacing between role metadata and bullets.
- Keep ATS-safe single-column text.
- Keep headings clear and simple.
- Avoid complex tables if they hurt text extraction.
- Ensure generated text remains extractable from the PDF.

## Education Layout Plan

Education should be compact but still readable.

Preferred order:

1. Postgraduate Diploma
2. Bachelor of Technology

Preferred layout:

```text
Postgraduate Diploma, Quality Engineering Management
Lambton College, Sarnia                                  Sep 2021 - May 2023

Bachelor of Technology, Mechanical Engineering
National Institute of Technology, Warangal               Aug 2016 - Aug 2020
```

Rules:

- Show the most recent education first.
- Put the degree and field on the first line.
- Put institution and location on the second line.
- Right-align the study period on the same line as institution and location.
- Use `Lambton College, Sarnia`.
- Use `National Institute of Technology, Warangal`.
- Keep this section compact so it does not push experience content onto another page.

## Validation Plan

After implementation, validate generated resumes with:

- PDF page count.
- Extracted text check.
- Word count.
- One-page mode excludes Projects.
- Project mode includes Projects only when enabled.
- No duplicated CliniScripts or TELUS bullets across sections.
- Skills are relevant to the target job type.
- Resume still contains important ATS keywords.
- Experience dates and core facts remain unchanged.
- Cover letter paragraphs are readable and not AI-sounding.

## Implementation Phases

### Phase 1: Planning

- Create this plan on a separate branch.
- Review and finalize decisions before code changes.

### Phase 2: Resume Generation Modes

- Add a resume mode flag for one-page mode and project mode.
- Wire the UI toggle to the generation flow.
- Ensure default mode excludes Projects.

### Phase 3: Prompt Updates

- Update resume prompts for mode-specific behavior.
- Add stricter India-focused plain-English tone rules.
- Add controlled keyword-selection rules.
- Add no-duplication rules for Experience and Projects.

### Phase 4: PDF Layout Updates

- Update Professional Experience rendering.
- Put dates on the right side of the company/location line.
- Keep the PDF ATS-readable.

### Phase 5: Resume JSON Cleanup

- Reduce repeated content.
- Convert the base resume into a cleaner evidence bank.
- Keep only strong, truthful, reusable proof points.

### Phase 6: Cover Letter Updates

- Align cover letter prompt with the same plain-English, non-AI tone.
- Keep cover letters concise and recruiter-friendly.

### Phase 7: Testing

- Generate sample one-page resume.
- Generate sample project-mode resume.
- Generate sample cover letter.
- Validate PDF extraction and page count.
- Review output manually from recruiter perspective.

## Open Items

- Decide final branch name for implementation if different from this planning branch.
- Decide exact UI label for the Projects toggle.
- Decide whether project mode should allow 1.5 pages or strictly target 2 pages max.
- Decide whether location should show India only, Canada only, or a more application-specific location value.
