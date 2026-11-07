"""Branch identity rules and fail-closed boundaries for isolated deployments.

This release deliberately does not support multiple branches in one database.
Country/airport pairs are reviewed registry entries, never guessed from a city.
"""
import re
import threading
import time
from collections import OrderedDict, deque
from urllib.parse import urlparse, parse_qs


AIRPORTS = {("CA", "YYC"): "Calgary", ("CA", "YWG"): "Winnipeg"}
CODE = re.compile(r"([A-Z]{2})-([A-Z]{3})-KMN-([0-9]{4})\Z")


def branch_code(country, airport, sequence):
    country, airport = country.strip().upper(), airport.strip().upper()
    if (country, airport) not in AIRPORTS:
        raise ValueError("Country/airport pair is not in the approved branch registry")
    if isinstance(sequence, bool) or not isinstance(sequence, int) or not 1 <= sequence <= 9999:
        raise ValueError("Branch sequence must be between 0001 and 9999")
    return f"{country}-{airport}-KMN-{sequence:04d}"


def validate_branch_code(value):
    match = CODE.fullmatch(str(value))
    if not match or branch_code(match[1], match[2], int(match[3])) != value:
        raise ValueError("Use an approved COUNTRY-AIRPORT-KMN-0001 branch code")
    return value


def isolated_branch(conn, pg=False, expected_code=None, expected_org=None):
    """Check the entire DB, not just the selected organization.

    A dedicated DB prevents legacy queries without branch predicates from seeing
    another branch. Refuse ambiguous data rather than silently taking its first row.
    No writes or schema changes are performed here.
    """
    prefix = "public." if pg else ""
    organizations = conn.execute(f"SELECT id FROM {prefix}organizations LIMIT 2").fetchall()
    branches = conn.execute(f"SELECT id, organization_id, code FROM {prefix}branches LIMIT 2").fetchall()
    if len(organizations) != 1 or len(branches) != 1:
        raise RuntimeError("An isolated deployment requires exactly one organization and one branch per database")
    org, branch = organizations[0], branches[0]
    if str(branch["organization_id"]) != str(org["id"]):
        raise RuntimeError("Branch organization does not match the isolated database")
    if expected_org and str(org["id"]) != str(expected_org):
        raise RuntimeError("Configured organization does not match the isolated database")
    if expected_code:
        validate_branch_code(expected_code)
        if branch["code"] != expected_code:
            raise RuntimeError("Configured branch code does not match the database; run the reviewed migration")
    return branch


def validate_production_config(settings):
    required = ("DATABASE_URL", "SMP_ORGANIZATION_ID", "SMP_BRANCH_CODE", "SUPABASE_URL", "SUPABASE_ANON_KEY", "JWT_SECRET")
    missing = [name for name in required if not settings.get(name)]
    if missing:
        raise RuntimeError("Missing production settings: " + ", ".join(missing))
    if not settings.get("SUPABASE_REQUIRE_AUTH"):
        raise RuntimeError("Production authentication must be enabled")
    if len(settings["JWT_SECRET"].encode()) < 32 or settings["JWT_SECRET"].startswith("dev-only-"):
        raise RuntimeError("JWT_SECRET must be a securely generated secret of at least 32 bytes")
    if not settings["SUPABASE_URL"].startswith("https://"):
        raise RuntimeError("Production authentication requires HTTPS")
    options = parse_qs(urlparse(settings["DATABASE_URL"]).query)
    if options.get("sslmode") != ["verify-full"]:
        raise RuntimeError("Production PostgreSQL requires sslmode=verify-full and a trusted server CA")
    validate_branch_code(settings["SMP_BRANCH_CODE"])


class LoginLimiter:
    """Bounded per-process protection; trusted edge limits are also required."""
    def __init__(self, attempts=30, seconds=60, clock=time.monotonic):
        self.attempts, self.seconds, self.clock = attempts, seconds, clock
        self.buckets = OrderedDict()
        self.lock = threading.Lock()

    def allow(self, client):
        with self.lock:
            now = self.clock()
            bucket = self.buckets.pop(client, deque())
            while bucket and bucket[0] <= now - self.seconds:
                bucket.popleft()
            allowed = len(bucket) < self.attempts
            if allowed:
                bucket.append(now)
            self.buckets[client] = bucket
            if len(self.buckets) > 2048:
                self.buckets.popitem(last=False)
            return allowed
