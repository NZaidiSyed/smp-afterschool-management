# Schedule Builder release

The approved weekly workspace is the default Schedule Builder in Staff Administration. Teacher Daily Schedule remains available through its existing button. The new API is enabled without a Render environment-variable change. No UAT seeding script runs at production startup.

Startup adds the scheduler tables to the existing database, with PostgreSQL RLS and server-only grants. It does not delete old schedules, student records, punches, payments or attendance data. An existing schedule week is imported once as a draft when first opened. Managers review availability warnings and publish it. Original rows stay available for rollback. Homebase and browser-only schedules are not automatically imported.

After publication, staff/manager mobile schedule reads and the staff dashboard use the new revision for the configured branch. Older dashboards summarize multiple daily shifts as first start/last end; use Schedule Builder for exact separate shifts and unpaid breaks. Staff acknowledge the complete publication revision through its Notifications section. The obsolete schedule-writing and per-day acknowledgement endpoints return a message directing clients to the builder. Notifications are in-app only.

## Deployment validation

1. Let Render deploy the approved `main` commit using its existing Git integration. Confirm the deploy is Live in Render; a Git push alone does not prove this.
2. Open Staff Administration → Schedule Builder with an existing manager account. Confirm the real roster, date navigation, staff filtering and Teacher Daily Schedule.
3. Review any imported week before publishing. Check the staff account sees its own published shifts and notification, then acknowledge that revision. Avoid publishing artificial shifts on the live roster just for testing.
4. Confirm existing attendance, student roster and timesheet pages still load.

The development environment has no Render API credentials or direct production database credentials. Production smoke checks require access to the live service; network-policy denial must be reported instead of treating local tests as proof of deployment.

## Rollback

Previous production commit: `09e1c92ce1da7a0842e8a7285e68e62cc40b41b3`.

Redeploy that commit from Render to restore the previous interface and consumers, retaining all database tables. New scheduler publications are preserved in `scheduler_*` for recovery but are not copied back into old tables. Do not drop tables or seed test data. `SMP_SCHEDULING_ENABLED=0` immediately disables new scheduling routes after restart; reverting the release is also needed to restore the old interface. Existing old schedule rows remain as they were before this release.
