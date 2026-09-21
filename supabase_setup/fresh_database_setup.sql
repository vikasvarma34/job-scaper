-- ==============================================================================
-- Clean Fresh Database Setup for Supabase
-- Run this in your new project's SQL Editor (Singapore / Mumbai)
-- ==============================================================================

-- 1. SAVED RESUMES (Resume Builder - JSON Only)
CREATE TABLE IF NOT EXISTS public.saved_resumes (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    company_name TEXT NOT NULL,
    file_name TEXT NOT NULL,
    resume_data JSONB NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT timezone('utc'::text, now()) NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT timezone('utc'::text, now()) NOT NULL
);

ALTER TABLE public.saved_resumes ENABLE ROW LEVEL SECURITY;
CREATE POLICY "Allow all access to saved_resumes" ON public.saved_resumes FOR ALL USING (true) WITH CHECK (true);
CREATE INDEX IF NOT EXISTS idx_saved_resumes_created_at ON public.saved_resumes (created_at DESC);
CREATE INDEX IF NOT EXISTS idx_saved_resumes_company ON public.saved_resumes (company_name);


-- 2. BASE RESUME (Master Base Resume)
CREATE TABLE IF NOT EXISTS public.base_resume (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    resume_data JSONB NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT timezone('utc'::text, now()) NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT timezone('utc'::text, now()) NOT NULL
);

ALTER TABLE public.base_resume ENABLE ROW LEVEL SECURITY;
CREATE POLICY "Allow all access to base_resume" ON public.base_resume FOR ALL USING (true) WITH CHECK (true);


-- 3. JOBS TABLE (Ensures Main Dashboard loads cleanly without errors)
CREATE TABLE IF NOT EXISTS public.jobs (
    job_id TEXT PRIMARY KEY,
    company TEXT,
    job_title TEXT,
    level TEXT,
    location TEXT,
    description TEXT,
    status TEXT DEFAULT 'new',
    is_active BOOLEAN DEFAULT true,
    application_date TIMESTAMP WITH TIME ZONE,
    resume_score SMALLINT,
    notes TEXT,
    scraped_at TIMESTAMP WITH TIME ZONE DEFAULT now(),
    last_checked TIMESTAMP WITH TIME ZONE DEFAULT now(),
    job_state TEXT DEFAULT 'new',
    resume_score_stage TEXT DEFAULT 'initial' NOT NULL,
    is_interested BOOLEAN,
    customized_resume_id UUID,
    provider TEXT,
    posted_at TIMESTAMP WITH TIME ZONE,
    experience_required TEXT,
    job_url TEXT,
    contact_email_override TEXT
);

ALTER TABLE public.jobs ENABLE ROW LEVEL SECURITY;
CREATE POLICY "Allow all access to jobs" ON public.jobs FOR ALL USING (true) WITH CHECK (true);
CREATE INDEX IF NOT EXISTS idx_jobs_status ON public.jobs (status);
CREATE INDEX IF NOT EXISTS idx_jobs_scraped_at ON public.jobs (scraped_at DESC);


-- 4. STORAGE BUCKETS
INSERT INTO storage.buckets (id, name, public)
VALUES ('resumes', 'resumes', false)
ON CONFLICT (id) DO NOTHING;

INSERT INTO storage.buckets (id, name, public) 
VALUES ('personalized_resumes', 'personalized_resumes', false)
ON CONFLICT (id) DO NOTHING;


-- 5. AUTO-UPDATE TIMESTAMP FUNCTION & TRIGGERS
CREATE OR REPLACE FUNCTION public.handle_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = timezone('utc'::text, now());
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS set_saved_resumes_updated_at ON public.saved_resumes;
CREATE TRIGGER set_saved_resumes_updated_at
    BEFORE UPDATE ON public.saved_resumes
    FOR EACH ROW
    EXECUTE FUNCTION public.handle_updated_at();

DROP TRIGGER IF EXISTS set_base_resume_updated_at ON public.base_resume;
CREATE TRIGGER set_base_resume_updated_at
    BEFORE UPDATE ON public.base_resume
    FOR EACH ROW
    EXECUTE FUNCTION public.handle_updated_at();
