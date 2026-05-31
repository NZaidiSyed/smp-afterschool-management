-- SMP - After School Management Program
-- Supabase/Postgres starter schema for production.
--
-- Run this in Supabase SQL Editor after creating the project.

create extension if not exists pgcrypto;

create table if not exists public.organizations (
  id uuid primary key default gen_random_uuid(),
  name text not null,
  organization_type text,
  phone text,
  details text,
  subjects_offered text[] not null default array['Math', 'English'],
  current_month text not null default 'May-26',
  subscription_status text not null default 'trialing',
  trial_start timestamptz not null default now(),
  trial_end timestamptz not null default now() + interval '14 days',
  stripe_customer_id text,
  stripe_subscription_id text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table if not exists public.organization_members (
  id uuid primary key default gen_random_uuid(),
  organization_id uuid not null references public.organizations(id) on delete cascade,
  user_id uuid not null references auth.users(id) on delete cascade,
  role text not null default 'staff' check (role in ('owner', 'admin', 'manager', 'staff', 'readonly')),
  created_at timestamptz not null default now(),
  unique (organization_id, user_id)
);

create table if not exists public.app_users (
  id uuid primary key default gen_random_uuid(),
  organization_id uuid not null references public.organizations(id) on delete cascade,
  email text not null,
  display_name text,
  role text not null default 'Office Assistant' check (role in ('Admin', 'Office Manager', 'Office Assistant')),
  active boolean not null default true,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (organization_id, email)
);

create table if not exists public.students (
  id uuid primary key default gen_random_uuid(),
  organization_id uuid not null references public.organizations(id) on delete cascade,
  number integer,
  student_name text not null,
  parent_guardian text,
  status text not null default 'C' check (status in ('C', 'D')),
  enrol_date date,
  subjects text[] not null default '{}',
  rate_type text,
  std_monthly_fee numeric(10, 2) not null default 0,
  payment_method text,
  phone text,
  email text,
  siblings text,
  notes text,
  last_modification text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table if not exists public.payments (
  id uuid primary key default gen_random_uuid(),
  organization_id uuid not null references public.organizations(id) on delete cascade,
  student_id uuid not null references public.students(id) on delete cascade,
  month_label text not null,
  amount numeric(10, 2) not null default 0,
  payment_verified boolean not null default false,
  payment_source text,
  transaction_reference text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (student_id, month_label)
);

create table if not exists public.student_status_changes (
  id uuid primary key default gen_random_uuid(),
  organization_id uuid not null references public.organizations(id) on delete cascade,
  student_id uuid not null references public.students(id) on delete cascade,
  previous_status text not null,
  new_status text not null,
  changed_at timestamptz not null default now(),
  changed_month text not null,
  notes text
);

create table if not exists public.rates (
  id uuid primary key default gen_random_uuid(),
  organization_id uuid not null references public.organizations(id) on delete cascade,
  subject text not null,
  rate_type text not null,
  monthly_fee numeric(10, 2) not null default 0,
  description text,
  created_at timestamptz not null default now(),
  unique (organization_id, subject, rate_type)
);

create table if not exists public.payer_aliases (
  id uuid primary key default gen_random_uuid(),
  organization_id uuid not null references public.organizations(id) on delete cascade,
  student_id uuid not null references public.students(id) on delete cascade,
  alias text not null,
  source text,
  created_at timestamptz not null default now(),
  unique (student_id, alias)
);

create table if not exists public.payment_imports (
  id uuid primary key default gen_random_uuid(),
  organization_id uuid not null references public.organizations(id) on delete cascade,
  file_name text,
  source text,
  imported_by uuid references auth.users(id) on delete set null,
  created_at timestamptz not null default now()
);

create table if not exists public.payment_import_rows (
  id uuid primary key default gen_random_uuid(),
  import_id uuid references public.payment_imports(id) on delete cascade,
  organization_id uuid not null references public.organizations(id) on delete cascade,
  student_id uuid references public.students(id) on delete set null,
  transaction_date date,
  description text,
  amount numeric(10, 2) not null default 0,
  source text,
  month_label text,
  match_score integer not null default 0,
  match_status text not null default 'review',
  notes text,
  applied_at timestamptz
);

create table if not exists public.discount_codes (
  id uuid primary key default gen_random_uuid(),
  organization_id uuid references public.organizations(id) on delete cascade,
  code text not null,
  description text,
  percent_off numeric(6, 2) not null default 0,
  amount_off numeric(10, 2) not null default 0,
  active boolean not null default true,
  created_at timestamptz not null default now(),
  unique (organization_id, code)
);

create index if not exists students_org_status_idx on public.students(organization_id, status);
create index if not exists payments_org_month_idx on public.payments(organization_id, month_label);
create index if not exists status_changes_org_month_idx on public.student_status_changes(organization_id, changed_month);
create index if not exists members_user_idx on public.organization_members(user_id);
create index if not exists app_users_org_email_idx on public.app_users(organization_id, lower(email));

alter table public.organizations enable row level security;
alter table public.organization_members enable row level security;
alter table public.app_users enable row level security;
alter table public.students enable row level security;
alter table public.payments enable row level security;
alter table public.student_status_changes enable row level security;
alter table public.rates enable row level security;
alter table public.payer_aliases enable row level security;
alter table public.payment_imports enable row level security;
alter table public.payment_import_rows enable row level security;
alter table public.discount_codes enable row level security;

create or replace function public.is_org_member(target_org uuid)
returns boolean
language sql
security definer
set search_path = public
as $$
  select exists (
    select 1
    from public.organization_members
    where organization_id = target_org
      and user_id = auth.uid()
  );
$$;

create or replace function public.has_org_role(target_org uuid, allowed_roles text[])
returns boolean
language sql
security definer
set search_path = public
as $$
  select exists (
    select 1
    from public.organization_members
    where organization_id = target_org
      and user_id = auth.uid()
      and role = any(allowed_roles)
  );
$$;

create policy "members can view their organizations"
  on public.organizations for select
  using (public.is_org_member(id));

create policy "admins can update organizations"
  on public.organizations for update
  using (public.has_org_role(id, array['owner', 'admin']));

create policy "members can view organization membership"
  on public.organization_members for select
  using (public.is_org_member(organization_id));

create policy "admins can manage organization membership"
  on public.organization_members for all
  using (public.has_org_role(organization_id, array['owner', 'admin']))
  with check (public.has_org_role(organization_id, array['owner', 'admin']));

create policy "admins can view app users"
  on public.app_users for select
  using (public.has_org_role(organization_id, array['owner', 'admin']));

create policy "admins can manage app users"
  on public.app_users for all
  using (public.has_org_role(organization_id, array['owner', 'admin']))
  with check (public.has_org_role(organization_id, array['owner', 'admin']));

create policy "members can view students"
  on public.students for select
  using (public.is_org_member(organization_id));

create policy "managers can manage students"
  on public.students for all
  using (public.has_org_role(organization_id, array['owner', 'admin', 'manager']))
  with check (public.has_org_role(organization_id, array['owner', 'admin', 'manager']));

create policy "members can view payments"
  on public.payments for select
  using (public.is_org_member(organization_id));

create policy "managers can manage payments"
  on public.payments for all
  using (public.has_org_role(organization_id, array['owner', 'admin', 'manager']))
  with check (public.has_org_role(organization_id, array['owner', 'admin', 'manager']));

create policy "members can view status changes"
  on public.student_status_changes for select
  using (public.is_org_member(organization_id));

create policy "managers can manage status changes"
  on public.student_status_changes for all
  using (public.has_org_role(organization_id, array['owner', 'admin', 'manager']))
  with check (public.has_org_role(organization_id, array['owner', 'admin', 'manager']));

create policy "members can view rates"
  on public.rates for select
  using (public.is_org_member(organization_id));

create policy "admins can manage rates"
  on public.rates for all
  using (public.has_org_role(organization_id, array['owner', 'admin']))
  with check (public.has_org_role(organization_id, array['owner', 'admin']));

create policy "members can view reconciliation data"
  on public.payer_aliases for select
  using (public.is_org_member(organization_id));

create policy "managers can manage reconciliation data"
  on public.payer_aliases for all
  using (public.has_org_role(organization_id, array['owner', 'admin', 'manager']))
  with check (public.has_org_role(organization_id, array['owner', 'admin', 'manager']));

create policy "members can view imports"
  on public.payment_imports for select
  using (public.is_org_member(organization_id));

create policy "managers can manage imports"
  on public.payment_imports for all
  using (public.has_org_role(organization_id, array['owner', 'admin', 'manager']))
  with check (public.has_org_role(organization_id, array['owner', 'admin', 'manager']));

create policy "members can view import rows"
  on public.payment_import_rows for select
  using (public.is_org_member(organization_id));

create policy "managers can manage import rows"
  on public.payment_import_rows for all
  using (public.has_org_role(organization_id, array['owner', 'admin', 'manager']))
  with check (public.has_org_role(organization_id, array['owner', 'admin', 'manager']));

create policy "members can view discount codes"
  on public.discount_codes for select
  using (organization_id is null or public.is_org_member(organization_id));

create policy "admins can manage discount codes"
  on public.discount_codes for all
  using (organization_id is null or public.has_org_role(organization_id, array['owner', 'admin']))
  with check (organization_id is null or public.has_org_role(organization_id, array['owner', 'admin']));
