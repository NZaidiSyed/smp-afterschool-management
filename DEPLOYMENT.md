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
   - Environment variable: `SMP_DATA_DIR=/var/data`
   - Add a persistent disk mounted at `/var/data`
4. After Render creates the service, copy its hostname.

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

- Replace local SQLite with hosted Postgres before real multi-organization use.
- Add real authentication and roles.
- Add Stripe billing with Google Pay enabled.
- Split each organization by `organization_id`.
- Add automated database backups on the hosting provider.
