# Student Attendance — local UAT

This attendance build has NOT been pushed to GitHub or deployed to Render. The existing production site is unchanged.

## Run on your computer

1. Extract the supplied UAT archive into a new folder. Install Python 3.12 (3.10+ supported); on Windows select **Add Python to PATH**.
2. Open a terminal in that folder and run:

   ```sh
   python run_attendance_uat.py
   ```

   On macOS/Linux, use `python3` if necessary. The first run downloads dependencies through normal verified HTTPS. No Node.js installation is required.
3. Open `http://127.0.0.1:8771/?attendance=1` in Chrome or Opera. This address works on the computer running the command. It is not a production or public preview address.
4. Stop with Ctrl+C. Re-running preserves the UAT database and existing barcodes. If the port is occupied, use `python run_attendance_uat.py --port 8772`.

The launcher creates a virtual environment, SQLite database and backups under your user's `.smp-attendance-uat` folder (outside this source folder). It refuses terminals containing production database/Supabase configuration. You may override the location with `SMP_UAT_HOME` pointing to a dedicated UAT folder. Local UAT authentication is disabled; the launcher binds the server to 127.0.0.1 so only this computer can access it. Use synthetic data. Never point this launcher at production files or a copied production environment file.

## Tabs and test cases

**Student Attendance** records actual visits. **Student Barcodes** lists and prints permanent tags. **Student Presence** remains the planned weekly timetable.

- **UAT Alex Regular:** has today's scheduled lesson. Print a tag, scan it (or use manual lookup then Record scan), and verify a green Regular arrival. Scan immediately again: it must be ignored. Wait at least ten seconds and scan again: verify departure and duration.
- **UAT Morgan Makeup:** has one synthetic missed lesson from yesterday and no schedule today. Verify orange Makeup arrival, a reserved absence, and fulfilment only after checkout.
- **UAT Casey Unscheduled:** has no scheduled lesson or eligible absence. Verify an Unscheduled alert. The arrival is recorded for staff review.
- Add a student in Student Roster or Batch Enrolment. Verify a barcode exists immediately. Reprint the tag and confirm the code stays unchanged. Existing students are backfilled once.
- After checkout, scan again outside the duplicate window: verify the extra-visit confirmation and required reason. Decline it and verify no extra record appears.
- Use Review to complete a missed checkout with actual centre-local times and a reason. Check audit history. Excused lessons must not generate makeup entitlement.
- Change the date/status filters and export CSV. The export includes all rows for the displayed date (not just the status filter), with UTC timestamps.
- Use Student Barcodes to replace a lost tag with a reason. The old code must stop working; a normal reprint must never replace it.
- Test a USB/Bluetooth scanner in keyboard mode with an Enter suffix. Check Code 128 print readability on the actual pouch labels. Camera scanning is deferred.
- Disconnect the network during a scan. A pending request must be retried with its existing ID; do not enter another barcode until resolved. Pending confirmation survives a page reload in that browser tab. Scans cannot be recorded offline; full offline queuing is deferred.
- Test tablet width/full screen, manual student lookup, audible feedback and text alerts. The scanner field does not steal focus from review/settings forms.

## Rules and boundaries

- Scan times come from the server, stored in UTC; the centre timezone defaults to America/Edmonton. Review times use the centre timezone. Ambiguous/nonexistent daylight-saving times are rejected.
- The default duplicate window is ten seconds, configurable from 2–120 seconds. A unique request ID makes retries idempotent; branch-level transactions and unique indexes prevent simultaneous double check-ins or double makeup claims.
- The maintenance worker checks every minute. A dashboard request also reconciles lessons. Scheduled sessions become absent after their end plus the configured grace (15 minutes by default).
- Tracking starts when attendance is first initialised. No historical absences are inferred before that date. Each student's daily schedule is captured once; editing that schedule later does not rewrite the captured lesson. Cancel/review the captured lesson if necessary. Current schedules are used to catch up after server downtime; historic schedule versions are not available in the existing app.
- Enter holiday/closure dates before lessons begin. Existing expected/absent lessons on those dates become cancelled. Existing actual visits are preserved for review; removing a holiday does not automatically reinstate cancelled lessons.
- Only unexcused regular lessons create makeup entitlement. The oldest eligible absence is reserved at arrival on an unscheduled day, fulfilled on completed checkout, and released when the makeup visit is cancelled. Incomplete makeup visits keep their reservation pending staff review.
- No checkout is invented: a session older than the configured limit (four hours by default), or left open across the local date boundary, becomes incomplete. The student must have it reviewed before another visit.
- Owner/Admin/Office Manager can correct attendance, approve extra visits and replace tags. Owner/Admin manage attendance settings. Centre-authorised operational staff can scan/read/print; the backend enforces these permissions. The separate staff portal links to `/?attendance=1`, which requires the centre's main login; a staff-portal-only PIN session is not a substitute for that login. The existing first-configured-branch context is reused; this does not introduce a branch selector.
- A barcode identifies a student; it is not a login credential. Attendance APIs require the app's existing authentication when enabled. Broader authentication hardening is outside this module.

## Developer validation

```sh
python -m unittest -v test_attendance test_asset_responses
node --check attendance-ui.js
node --check app.js
node scripts/test_service_worker.cjs
```

Use the virtual environment created by the launcher for Python checks. Barcode rendering uses pinned `python-barcode==0.16.1`; no third-party script CDN is needed for barcode generation. `tzdata` provides Windows timezone support.

Before production approval: finish human UAT with real scanner/label hardware, test the additive tables and transactional workflow on a disposable Postgres database, verify actual Supabase logins and staff permissions, back up the production database, and review deployment/rollback steps. PostgreSQL paths are implemented but have not been validated against production or a live Postgres server in this environment. No production credentials are needed for local UAT.

## Current validation evidence

- 33 Python regression tests passed, covering attendance rules, branch isolation, permissions and HTTP asset responses.
- Seven service-worker checks passed, including prevention of HTML fallback for failed scripts.
- Chromium exercised automatic enrolment barcodes, separate tabs, scan/duplicate handling, audited correction, printable SVG tags, CSV download, invalid-code recovery and mobile layout.
- A browser test deliberately saved a scan but dropped its response, reloaded the tab and retried; it produced exactly one arrival with no browser exception.
- Four concurrent HTTP retries with the same request ID returned the same result and produced one arrival.
- The launcher was exercised in a fresh virtual environment and repeated without resetting existing data.

The synthetic regular fixture follows today's supported roster weekday. On Sunday/Monday, a clearly labelled synthetic lesson is supplied because the existing roster only supports Tuesday–Saturday. To start completely fresh UAT without deleting previous results, set SMP_UAT_HOME to a different dedicated UAT folder.
