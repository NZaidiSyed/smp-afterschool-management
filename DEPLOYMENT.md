# Deployment Notes

## Recommended First Public Test

Use `app.afterschools.org` for the SMP application and keep `afterschools.org` available for the public marketing website.

The current app is a Python web server with SQLite storage. Cloudflare can manage DNS and security for the domain, but the Python server must run on a web host such as Render, Railway, Fly.io, or a VPS.

## Render Setup

1. Connect GitHub repository `NZaidiSyed/smp-afterschool-management`.
2. Create a new web service from `main`.
3. Use the included `render.yaml`, or configure manually:
   - Build command: `pip install -r requirements.txt`
   - Start command: `python app.py`
   - For local SQLite testing: `SMP_DATA_DIR=/var/data` and a persistent disk mounted at `/var/data`
   - For Supabase production: set `DATABASE_URL`, `SUPABASE_URL`, `SUPABASE_ANON_KEY`, and `SUPABASE_REQUIRE_AUTH=1`
4. After Render creates the service, copy its hostname.

## Supabase Production Environment

Set these on Render under **Environment**:

- `DATABASE_URL`: Supabase direct Postgres connection string. Use the project database password in the dashboard, not in chat.
- `SUPABASE_URL`: Supabase project API URL, for example `https://PROJECT_REF.supabase.co`.
- `SUPABASE_ANON_KEY`: Public anon key from Supabase API settings.
- `SUPABASE_REQUIRE_AUTH`: `1`
- `SMP_ORGANIZATION_ID`: optional. If omitted, SMP uses the first organization row or creates a starter one.

The current production bridge supports one selected organization for the first live test. Full self-service multi-organization onboarding should be the next production milestone.

## Cloudflare DNS

In Cloudflare for `afterschools.org`:

1. Create a DNS record:
   - Type: `CNAME`
   - Name: `app`
   - Target: the web host hostname
   - Proxy: enabled after the host verifies the custom domain
2. Add `app.afterschools.org` as a custom domain in the web host.
3. Wait for SSL certificate verification.

Known Cloudflare values supplied by owner:

- Zone ID: `604f7da7e380a55eb1f16234fe4c7c9b`
- Account ID: `b0068fcff500626d92173a4eb201516d`

## Important Production Work Still Needed

- Add invite-based organization membership management for multiple staff users.
- Add Stripe billing with Google Pay enabled.
- Add automated database backups on the hosting provider.
