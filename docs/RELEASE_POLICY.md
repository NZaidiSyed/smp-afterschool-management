# Change, UAT approval and production promotion policy

## Authority and prerequisites

The user requested this policy on 2026-11-07. All future agents/operators read
`AGENTS.md`, `APP_MASTER.md`, this policy, and the active release record before
editing. User instructions take priority; this policy implements the user's
requirement that UAT approval precedes production promotion. A plan, general
"sure", test pass, approval for an older release, or request to prepare promotion
is not approval for a new exact release candidate.

The application master describes the app and stable rules. The JSON register
stores structured releases and append-only events; the master displays those
same events. Never place credentials or student/payment data in either file.
Do not backfill fictional approvals into historical releases.

## Credentials and environment settings

Reuse existing authorized **management credentials** (Render API key/Supabase
account token) through secure bindings when available. This does not mean
copying production application credentials into test. Management credentials
stay in operator/CI secret storage and must never be shipped to the browser or
application container. Select the intended Render service and Supabase project
by verified identity before every action; stop on ambiguity.

Test uses a separate project/database, auth users/project keys, JWT signing key,
private storage, backups and sandbox mail/payment endpoints. Shared non-secret
settings such as runtime versions, schema, feature flags and naming rules should
match production where applicable. Never run test seeding or a test suite against
production, and never extract values from production configuration files to
populate test. Missing connected access is a blocker, not permission to guess
keys, identities or URLs. Do not rotate current production keys without a need.

## Change workflow

1. **Read and register.** Inspect master facts, release history, open risks and
   current Git state. Work on a feature/test branch. Create a uniquely identified
   release entry with requested scope, data impact, risks, rollback plan, required
   checks, candidate revision, and a real requester/reference. Keep past entries.
2. **Implement and test.** Use synthetic fixtures. Record exact commands, source
   revision, passed/failed/skipped counts, browser evidence and migration/restore
   evidence. Changes affecting app behavior, infrastructure, dependencies, schema,
   security or these rules require current tests/review. Documentation-only work
   needs the documentation guard and consistency checks, not unrelated app tests.
3. **Freeze candidate.** Commit the candidate first. Record its full Git SHA and
   results in the register. Only register updates and the generated history part
   of APP_MASTER may differ after freezing. Any other change invalidates approval;
   commit a new candidate, repeat relevant checks, and obtain fresh approval.
4. **Hosted UAT.** Deploy that exact candidate to TEST, verify the separate target
   identities, and execute relevant component UAT checklists with actual users.
   For this security release, hosted login/MFA, ownership/authorization review,
   migration rehearsal, backup/restore and live-target configuration are required.
   Local tests alone cannot satisfy those requirements.
5. **Explicit approval.** Ask only when there is a concrete, reviewable tested
   candidate. Record the actual approver, date/time in UTC, source conversation
   or ticket reference, release ID, exact candidate SHA, scope, and explicit
   decision `approved_for_production`. Keep the user's original approval wording
   in the controlled ticket/conversation; record its reference without private
   content. Never invent a person, statement, reference or approval timestamp.
6. **Preflight.** Run `release_guard.py check-production`. Confirm all required
   checks/evidence and approvals, clean approved source, actual production service,
   database, organization and branch, a verified backup/restore and a rollback
   plan that preserves newer writes. Pause conflicting jobs/writes for migrations.
   Missing prerequisites block deployment even if generic approval exists.
7. **Promote.** Only after the above checks may an authorized operator merge/push
   the approved candidate to production or trigger its deployment/migration. Do
   not push directly to main as a workaround for review. Record the start event
   with target/service and deployment reference before the action. Recheck source
   identity immediately before promotion and apply only reviewed migrations.
8. **Verify and record.** After actual deployment success, confirm live revision,
   authentication, expected functionality, branch isolation, record counts/links
   and financial totals. Append `deployed` only with provider deployment reference,
   deployed candidate SHA and live verification evidence. The tool updates the
   master history. A Git push, green build, or open port alone is not deployment
   verification. Record failures/rollback as events; do not erase earlier entries.

Record UAT approval before deployment; record the deployment outcome afterwards.
Approval does not mark a release deployed. Deployed records must not be written
in advance. Rejected UAT returns the release to testing; changed candidates need
new approval. A rollback must follow an approved rollback plan or specific user
authorization, record its target revision and verification, and preserve new data.

## Machine checks and audit commands

```sh
python scripts/release_guard.py show
python scripts/release_guard.py validate --require-current
python scripts/release_guard.py check-production --release RELEASE_ID
python scripts/release_guard.py record --release RELEASE_ID --event-file /secure/path/event.json
```

`validate` checks the schema, references, state transitions, evidence and master
history. `--require-current` also verifies the active candidate's content against
the current tracked and untracked files. `check-production` additionally requires
explicit recorded UAT approval and all required passed gates. These are local
checks: **they do not authenticate the claimed human approver**. `record` validates
and appends an event, then regenerates only the master history. Register writes
happen first; if interrupted, `sync` repairs the generated history and `validate`
must pass before work continues. Evidence references must be real and retained.

Event JSON requires `state`, `actor`, `reference`, and `summary`. The tool adds its
UTC recording timestamp and candidate SHA; a recording timestamp is not evidence
of when an earlier event actually occurred. Optional `checks` updates contain
`status` and `evidence` for existing required checks. An approval event additionally
requires `approval` with `approver`, `approved_at`, `reference`, `decision`,
`release_id`, and `candidate_sha`. It must be the user's actual release-specific
approval, not a tool-generated substitute. A deployed event requires `deployment`
with `environment: production`, `service`, `reference`, `candidate_sha`, and
`verification`. Do not put secret-bearing URLs in events.

States: `planned -> testing -> awaiting_uat -> approved -> deployment_started ->
deployed`. `blocked` may interrupt preparation; `deployment_failed` and
`rolled_back` preserve unsuccessful outcomes. `legacy_unverified` is a historical
import and never grants future deployment permission. Previous approvals remain
in history, but only an approval of the current candidate can authorize it.

## Required platform enforcement (not yet configured here)

Repository docs and JSON checks alone cannot stop someone with direct hosting
access or permission to rewrite Git history. Before using this as a production
control, an administrator must:

- Protect main: require PRs, the security/regression job, release-documentation
  guard and production-approval guard, with restricted direct-push/bypass rights.
- Require independent approval from the application owner/authorized UAT approver
  through protected production deployment environments or an equivalent approval
  system; keep approval records outside an untrusted PR's editable contents.
- Turn off uncontrolled Render main-branch auto-deploy or restrict it to the
  approved/protected promotion path; use separate TEST and PRODUCTION services.
- Restrict production secrets to the approved deployment operator/workflow and
  maintain provider audit logs, restricted backups and recovery access.

The CI production-approval check checks **recorded** approval on main-bound PRs.
It cannot authenticate a user or configure provider protection. A direct main
push can only be detected after the push unless branch protection is enforced.
Do not claim those platform controls are active until verified through the
provider. Access was unavailable when this policy was introduced.
