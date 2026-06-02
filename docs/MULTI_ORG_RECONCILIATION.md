# SMP Multi-Organization and Reconciliation Guide

This guide explains how SMP isolates organizations and how new branches can test the system safely.

## Organization Selection

Production PostgreSQL mode uses the `SMP_ORGANIZATION_ID` environment variable.

- Set `SMP_ORGANIZATION_ID` in Render for each deployed branch/app instance.
- The value must be the UUID from `public.organizations.id`.
- If one organization exists and `SMP_ORGANIZATION_ID` is blank, SMP can use that organization.
- If multiple organizations exist and `SMP_ORGANIZATION_ID` is blank, SMP stops instead of guessing.

This prevents one branch from accidentally opening another branch's data.

## Organization Details

Organization details live in:

- Supabase/PostgreSQL: `public.organizations`
- Local development: `app_meta`

The Settings page updates the currently selected organization profile:

- institution name
- phone
- centre details
- subjects offered
- current dashboard month

## Organization-Scoped Tables

In production, these tables include `organization_id` and are filtered by the backend:

- `students`
- `payments`
- `app_users`
- `rates`
- `payer_aliases`
- `payment_imports`
- `payment_import_rows`
- `student_status_changes`
- `audit_logs`
- `staff_members`
- `staff_schedules`
- `staff_shift_punches`
- `discount_codes`

## User Access

User access is managed through `app_users` for the active organization.

Supported app roles:

- `Admin`
- `Office Manager`
- `Office Assistant`
- `Staff`

`Staff` has no Student Administration permissions by default. Staff Administration remains limited to Admin and Office Manager until staff self-service is implemented.

## Creating a New Organization

1. Create a row in `public.organizations`.
2. Copy the new `id`.
3. Set Render environment variable:

```text
SMP_ORGANIZATION_ID=<organization uuid>
```

4. Add the organization's first Admin in `public.app_users`.
5. Open SMP and complete Settings.
6. Use demo seeding or CSV import to test.

## Demo Data

Admins can seed demo data for the current organization from Settings.

Demo seed creates:

- sample students
- sample staff members
- sample fee/payment records
- sample payer aliases

This is intended for trials and branch evaluation. It should not be run inside a live production branch with real student data.

## Payment Reconciliation

SMP supports separate upload modes:

- PAD
- E-Transfer
- Credit Card

Before upload, Admin chooses matching rules:

- Student ID
- Student Name
- Parent Name
- Email Address
- Payment Amount
- Payment Date
- Payment Method
- Organization ID
- Branch ID

Rows are previewed first. Nothing posts until Admin clicks approve.

## Posting Rules

When Admin approves:

- verified rows update Fee Tracker
- balances and dashboard metrics recalculate from Fee Tracker
- audit logs are written
- upload rows are stored for history

Rows requiring manual review, rejected rows, and outstanding rows can be exported as an exception report.
