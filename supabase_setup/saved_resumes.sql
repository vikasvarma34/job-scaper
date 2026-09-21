-- ==============================================================================
-- Migration: Create `saved_resumes` table
-- Purpose: Store custom user resumes as lightweight JSON without saving bulky PDFs.
-- Run this in your Supabase project SQL Editor.
-- ==============================================================================

-- 1. Create the saved_resumes table
CREATE TABLE IF NOT EXISTS public.saved_resumes (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    company_name TEXT NOT NULL,
    file_name TEXT NOT NULL,
    resume_data JSONB NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT timezone('utc'::text, now()) NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT timezone('utc'::text, now()) NOT NULL
);

-- 2. Enable Row Level Security (RLS) if desired, or allow authenticated/anon access
ALTER TABLE public.saved_resumes ENABLE ROW LEVEL SECURITY;

-- Allow full access to service_role / backend anon client used by this application
CREATE POLICY "Allow full access to saved_resumes" 
    ON public.saved_resumes 
    FOR ALL 
    USING (true) 
    WITH CHECK (true);

-- 3. Indexes for fast retrieval by company name and creation order
CREATE INDEX IF NOT EXISTS idx_saved_resumes_created_at ON public.saved_resumes (created_at DESC);
CREATE INDEX IF NOT EXISTS idx_saved_resumes_company_name ON public.saved_resumes (company_name);

-- 4. Auto-update updated_at timestamp function and trigger
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

-- Confirmation note
COMMENT ON TABLE public.saved_resumes IS 'Stores custom tailored resumes purely as JSON data without storing PDF binaries.';
