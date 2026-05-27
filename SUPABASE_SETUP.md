# Supabase Setup for SMP

Project created:

```text
https://supabase.com/dashboard/project/ccftzopeneykbcwkwizq
```

## Step 1: Create Tables

1. Open Supabase Dashboard.
2. Select the SMP project.
3. Open **SQL Editor**.
4. Create a new query.
5. Paste the contents of:

```text
supabase/schema.sql
```

6. Run the query.

This creates the production tables, multi-organization structure, roles, and row-level security starter policies.

## Step 2: Auth Settings

In Supabase:

1. Go to **Authentication**.
2. Enable email login / magic links.
3. Add Google provider when Google OAuth credentials are ready.
4. Add site URL:

```text
https://app.afterschools.org
```

5. Add redirect URL:

```text
https://app.afterschools.org/**
```

## Step 3: Environment Variables

When the app is migrated from SQLite to Supabase, Render will need these environment variables:

```text
SUPABASE_URL=
SUPABASE_ANON_KEY=
SUPABASE_SERVICE_ROLE_KEY=
SUPABASE_DB_URL=
```

Do not commit these values to GitHub.

## Step 4: Migration Plan

1. Export local Student Roster CSV.
2. Export local Fee Tracker CSV.
3. Import students into Supabase `students`.
4. Import monthly payments into Supabase `payments`.
5. Update SMP backend to read/write Supabase instead of SQLite.
6. Turn on real login and organization selection.

## Notes

- `organization_id` is included on all production business tables.
- Row-level security is enabled.
- Admin/manager/staff/read-only roles are represented in `organization_members`.
- Stripe billing fields are reserved on `organizations`.
