# Schedule Builder — four-workflow UAT

This is a separate UAT implementation. Production `main`, Render, existing staff schedules, student records, attendance and payments are unchanged. The pilot reads the existing staff roster and approved leave, and stores its own drafts, published revisions, availability, swaps, notifications and audit history in additive `scheduler_*` tables.

## Start on Windows

1. Download the **uat/staff-scheduling** branch ZIP from GitHub and choose **Extract All**.
2. Open the extracted folder containing `run_staff_scheduling_uat.py`.
3. Double-click `Start Staff Scheduling UAT.bat`, or open a terminal in that folder and run:

   ```bat
   py run_staff_scheduling_uat.py
   ```

4. Keep the terminal open. Once it reports the server is running, open:

   ```text
   http://127.0.0.1:8778/staff-scheduler.html
   ```

On macOS/Linux use `python3 run_staff_scheduling_uat.py`. Python 3.12 is recommended; 3.10+ is supported. Internet is needed for the first dependency installation. No Node.js setup is needed to use UAT.

The launcher creates a dedicated virtual environment and SQLite database under `.smp-staff-scheduling-uat` in your user home. It refuses production database/Supabase configuration. It binds to 127.0.0.1, so the URL is available only on that computer. Re-running preserves changes. Use `--port 8779` if 8778 is occupied, or `SMP_SCHEDULING_UAT_HOME` pointing to a different dedicated UAT folder for a fresh set of test data. Stop with Ctrl+C.

For staff portal integration, open `staffbase.html?scheduler_uat=1` on that same local server and select Schedule Builder/My Schedule. The new builder appears inside that section. The regular staff portal remains unchanged without this explicit query flag.

## Synthetic accounts

The initial local view is a local administrator. Use **Switch account** to exercise real staff JWT authentication:

| Test user | Email | Test-only password |
| --- | --- | --- |
| Manager | manager@schedule.uat | UatSchedule2026! |
| Alex | alex@schedule.uat | UatSchedule2026! |
| Jamie | jamie@schedule.uat | UatSchedule2026! |
| Taylor | taylor@schedule.uat | UatSchedule2026! |

These credentials exist only in the isolated UAT fixture database; they are not production accounts. The test-only password is compatible with the existing local staff portal verifier. Authentication hardening and replacing legacy authentication exceptions require a separate production review.

## 1. Copy a weekly schedule

A source schedule for the current week is seeded with Alex and Jamie teaching Tuesday 15:00–18:00. All synthetic staff have recurring 13:00–21:00 availability.

- As manager, go to the following week and choose **Copy a week**.
- Select the source Monday, Published or Draft, and Merge or Replace.
- Preview: verify dates move to the selected target week, conflicts are visible, and the target has not changed yet.
- Confirm: only the target draft changes. The last published target stays visible to staff.
- Merge skips exact duplicate shifts. Replace requires explicit selection. Neither copies acknowledgement flags. Source/target revisions are checked again on confirmation.
- Add/edit/remove shifts and save the draft. Staff IDs are handled as strings, supporting PostgreSQL UUIDs. Multiple shifts per day and unpaid breaks are supported. Overnight shifts are intentionally unsupported in this pilot.

## 2. Staff availability

- Sign in as Alex and open Availability. Set an effective date, then Available with a time window, Unavailable, or Not provided for each weekday. This pilot supports one availability window per weekday.
- Staff may edit only their own profile. Managers can maintain team profiles. Effective-dated revisions preserve earlier availability.
- Unknown availability gives a warning that must be accepted before publication. Unavailable hours, approved leave and overlapping shifts block publication.
- A staff availability change creates an in-app notification for linked manager accounts. It does not silently change published shifts.

## 3. Shift covers and swaps

- Publish the copied week, then sign in as Alex. In Shift swaps request cover of Alex's Tuesday shift by **Taylor**, who has no conflicting shift. Provide a reason.
- Sign in as Taylor and accept. The schedule must remain unchanged until manager approval.
- Sign in as Manager and approve with a reason. The publication revision changes, the shift moves to Taylor, audit history is updated and staff receive in-app updates.
- For a two-way exchange, request Jamie's Tuesday shift in return instead of cover-only. Both assignments change in the same transaction after acceptance and approval.
- Verify that a colleague can decline, an owner can cancel, and unrelated staff cannot decide the request.
- Test a cover request to Jamie without an exchange: Jamie already works Tuesday 15:00–18:00, so approval must fail with an overlap conflict.
- Approval rechecks current availability, leave, ownership and shift contents. Unpublished server drafts block approval to prevent overwriting a manager's work. A changed source shift requires cancelling/recreating the request.

## 4. Notifications and acknowledgement

- Publishing creates database-backed, per-recipient in-app messages. A republish without changes creates no duplicate messages.
- Staff see only their inbox. Mark read and acknowledge a specific publication revision. Older revisions cannot be acknowledged as the current schedule.
- Managers can see publication acknowledgement receipts without impersonating staff or marking their messages read.
- Swap offers go to the colleague; accepted requests notify linked managers; decisions notify both participants. Staff availability updates also notify managers.
- Data refreshes every 30 seconds while visible; a Refresh button is available. Unsaved draft/availability edits are protected from polling. Stale revisions are rejected by the server.
- Email, SMS, push and WhatsApp are **not sent**. Add a delivery provider/outbox and actual delivery receipts before describing those channels as delivered.

## Persistence and production boundary

- UAT uses synthetic local data. No live database or Homebase account is connected.
- The updated API is disabled unless `SMP_SCHEDULING_UAT=1`; the launcher supplies that flag only to the local child process. Merely serving the page on the existing production environment does not enable it.
- Existing `staff_schedules`, historical browser caches and published Homebase schedules are not automatically migrated, overwritten or synchronised by this pilot. The pilot's published schedule is available to staff through the new builder only; legacy clock-in/timesheet/mobile scheduling endpoints continue using their existing source. Migration and routing those consumers to the new publication store must be completed and tested before replacing production scheduling.
- Before promotion: finish human UAT, agree how existing published weeks will be imported, integrate legacy schedule consumers, verify real manager/staff authentication and branch selection, configure any desired email/SMS provider, and test rollout/rollback with a database backup. No production deployment is authorised by testing this local build.

## Verification

```sh
python -m unittest -v test_staff_scheduling test_attendance test_asset_responses
node --check staff-scheduler.js
```

Use the launcher's virtual environment for Python tests. PostgreSQL scheduling tests run against isolated disposable schemas with `SMP_TEST_POSTGRES_DSN` configured:

```sh
python -m unittest -v test_staff_scheduling_postgres
```

Validation includes draft privacy, selected-week persistence, optimistic locking, copy preview/confirmation, availability/leave conflicts, two-stage swap decisions, notification privacy, acknowledgement revisions and server-only PostgreSQL tables. Browser UAT exercises real synthetic account switching, copying/publication, availability editing, cover acceptance/approval, stale saves, and desktop/mobile layout.

Current results: 32 scheduling checks passed on SQLite and 32 on PostgreSQL, including two simultaneous editors. The 33 existing attendance/asset checks and 15 camera/service-worker checks also passed. Multi-account Chromium UAT passed for copying, publication, availability editing, notification acknowledgement, colleague acceptance, manager approval, stale-save rejection and desktop/mobile rendering. The explicit Staff Administration iframe integration was also verified.

Optional browser regression (requires Playwright and Chromium, and a running fresh synthetic UAT server): `node scripts/test_staff_scheduler_browser.cjs`. It is restricted to localhost and creates test schedules/requests. Set `SMP_SCHEDULER_TEST_URL` to the local test port and `CHROMIUM_PATH` if needed. The regular UAT launcher does not require these developer tools.

## Updated planning interface

The weekly workspace includes a date range, highlighted current day, staff initials, paid hours, and shift cards. Search by staff name, filter staff with or without shifts, and sort by first name, last name, or total hours. Reset filters restores the full authorized roster. Filters change only the display: saving, copying and publishing still use the complete week. Print uses a landscape layout for the currently filtered team.

Download and extract a fresh copy of the UAT branch to get this update, close the old server window, and run `Start Staff Scheduling UAT.bat` again. Existing test data is kept in the separate UAT directory. Refresh the browser if the old layout remains visible.
