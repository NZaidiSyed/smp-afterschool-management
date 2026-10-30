# SMP - After School Management Program

SMP is a local web application for after-school learning centres to manage student rosters, fee tracking, dashboard reporting, batch enrolment, and payment reconciliation.

## Run Locally

```powershell
python app.py --port 8765
```

Then open:

```text
http://127.0.0.1:8765/
```

## Notes

- The app currently uses SQLite for local data storage.
- Real workbook/database files are intentionally excluded from git because they may contain student, parent, and payment information.
- Production deployment should use a hosted database such as Supabase Postgres and real authentication before public use.

## Planned Production Stack

- Authentication: Supabase Auth, Clerk, or another low-cost provider.
- Payments: Stripe with Google Pay enabled.
- Database: Supabase Postgres or another managed PostgreSQL service.
- Domain: `app.afterschools.org` is recommended for the application.

## Student attendance and barcode scanning

Student Attendance supports USB/Bluetooth keyboard scanners and phone/tablet/webcam scanning over HTTPS. Barcodes are assigned once at enrolment. Run `python run_attendance_uat.py` for an isolated synthetic database and printable student tags. See [the UAT guide](docs/STUDENT_ATTENDANCE_UAT.md) for the checklist and production validation requirements.

## Schedule Builder — local UAT

The separate staff scheduling pilot covers week copying, recurring availability, colleague/manager-approved swaps, and an in-app notification inbox. On Windows double-click **Start Staff Scheduling UAT.bat**, or run `python run_staff_scheduling_uat.py`. See [the staff scheduling UAT guide](docs/STAFF_SCHEDULING_UAT.md). Production scheduling is unchanged; the new API is disabled by default.
