create extension if not exists pgcrypto;

create or replace function public.set_updated_at()
returns trigger
language plpgsql
as $$
begin
  new.updated_at = now();
  return new;
end;
$$;

create table if not exists public.customized_cover_letters (
  id uuid primary key default gen_random_uuid(),
  job_id text not null references public.jobs(job_id) on delete cascade,
  customized_resume_id uuid not null references public.customized_resumes(id) on delete cascade,
  company text,
  job_title text,
  cover_letter_text text not null,
  cover_letter_link text,
  llm_model text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint customized_cover_letters_job_id_key unique (job_id)
);

create index if not exists idx_customized_cover_letters_job_id
  on public.customized_cover_letters (job_id);

create index if not exists idx_customized_cover_letters_resume_id
  on public.customized_cover_letters (customized_resume_id);

drop trigger if exists trg_customized_cover_letters_updated_at on public.customized_cover_letters;

create trigger trg_customized_cover_letters_updated_at
before update on public.customized_cover_letters
for each row
execute function public.set_updated_at();
