-- Additive standalone Cover Letter Builder migration. Run manually after review.
-- Flask uses SUPABASE_SERVICE_ROLE_KEY (server-side service-role configuration).
-- No anon/authenticated policy: only the backend service role accesses this table.
BEGIN;
CREATE TABLE IF NOT EXISTS public.saved_cover_letters (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    company_name TEXT NOT NULL,
    file_name TEXT NOT NULL,
    cover_letter_data JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
ALTER TABLE public.saved_cover_letters ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.saved_cover_letters FROM anon, authenticated;
GRANT ALL ON public.saved_cover_letters TO service_role;
CREATE INDEX IF NOT EXISTS idx_saved_cover_letters_created_at ON public.saved_cover_letters (created_at DESC);
CREATE INDEX IF NOT EXISTS idx_saved_cover_letters_company_name ON public.saved_cover_letters (company_name);
CREATE OR REPLACE FUNCTION public.saved_cover_letters_touch_updated_at()
RETURNS trigger LANGUAGE plpgsql SET search_path = public AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$;
DROP TRIGGER IF EXISTS saved_cover_letters_updated_at ON public.saved_cover_letters;
CREATE TRIGGER saved_cover_letters_updated_at BEFORE UPDATE ON public.saved_cover_letters
FOR EACH ROW EXECUTE FUNCTION public.saved_cover_letters_touch_updated_at();
COMMIT;
