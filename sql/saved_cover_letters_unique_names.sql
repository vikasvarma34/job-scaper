-- OPTIONAL follow-up migration for review only; do not run automatically.
-- Exact, case-sensitive company + filename matching, as used by the backend.
-- First inspect existing duplicates (no rows are modified):
SELECT company_name, file_name, count(*) AS record_count
FROM public.saved_cover_letters
GROUP BY company_name, file_name
HAVING count(*) > 1;

-- If duplicates exist, stop and decide manually which records to retain or rename.
-- This index will fail rather than delete or merge those records.
-- Run during a controlled migration window; the operation takes a table lock.
CREATE UNIQUE INDEX IF NOT EXISTS saved_cover_letters_company_filename_unique
ON public.saved_cover_letters (company_name, file_name);
