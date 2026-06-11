# Audit 6/6 — Operations, Configuration & Deployment Readiness

**Date:** 2026-06-11
**Scope:** Settings hygiene, secrets, backups & recovery, the deploy pipeline, monitoring/observability, tenant-onboarding operations, dependency currency — the production-environment lens.
**Method:** Read-only (this document is the only write). settings.py/urls.py/run.sh/.replit/runtime.txt/requirements.txt read in full; `python manage.py check --deploy` executed; resolved settings introspected via a Django shell; git history probed for ever-committed secrets (`git log -S`, `git log --all -- .env`); dependency currency verified against djangoproject.com release/EOL announcements (June 2026).
**Production context (stated, partially unverifiable from the repo):** Production runs on **Render** behind Render's edge (TLS terminated there), started with **gunicorn** — *not* the `run.sh` → `runserver` path audit 04 found, which is the **Replit dev environment only**. Deploys: develop → merge to main → push to GitHub → Render auto-deploys and auto-migrates. `WEB_CONCURRENCY=1` (per audit 04 intake). External tenants onboard in ~3 months.
**Builds on:** audit 01 (D2 hard-delete paths, D25, §4e prod checks), audit 02 (T1/T6/T10/T12, §5 onboarding), audit 03 (gating map), audit 04 (F2/F5/F10/F11), audit 05 (C12 docs, §6c tests).

**Headline:** The configuration *pattern* is right — single env-driven settings.py, `SECRET_KEY` hard-required from the environment, `DEBUG` defaulting to False, no secret ever committed (verified across history). What's missing is everything *around* the pattern: **zero `SECURE_*` hardening** (4 `check --deploy` warnings — session/CSRF cookies are sent without the `Secure` flag in production today), **zero error visibility** (no LOGGING config + empty `ADMINS` means production 500 tracebacks are silently discarded), **no automated backup posture** beyond Render's managed snapshots and the user remembering to take manual pre-deploy backups, **no CI** (1,337 tests exist and nothing runs them before a deploy), **no staging**, and **no way to verify any of production's actual configuration on demand** — the exact gap the requested status endpoint closes. One live credential issue: a **GitHub PAT embedded in the dev clone's git remote URL** — and because pushing to main auto-deploys, that token is effectively a production-deploy credential. Dependency-wise, **Django 5.1.6 is past end-of-life** (5.1 security support ended 2025-12-31) and missing nine 5.1.x security releases, including the 5.1.12 SQL-injection fix.

---

## SECTION 1 — SETTINGS HYGIENE

### 1a. Structure

- **Single `producterp/settings.py`** (284 lines) — no dev/prod split, no `settings/` package. Every dev↔prod difference is carried by **environment variables** (`SECRET_KEY`, `DEBUG`, `ALLOWED_HOSTS`, `CSRF_TRUSTED_ORIGINS`, `DATABASE_URL`, the five `CLOUDFLARE_R2_*` vars, `USE_OBJECT_STORAGE` read in urls.py).
- `load_dotenv(BASE_DIR / '.env')` (settings.py:15) loads a local `.env` **without override** — ambient environment wins. On Replit, secrets are *also* injected as ambient env vars, so dev currently runs with two sources where the ambient value silently beats the file (observed live: `.env` says `DEBUG=True`, resolved `DEBUG` is `False` because the ambient var is set). Dev-only confusion, but it means "what's in `.env`" is not "what the app runs with" (O15).
- For a single-app, two-environment setup this shape is **appropriate** — a settings split is not needed; what's needed is the missing production block (1b/1c) and a way to *see* the resolved values in prod (§5c).

### 1b. The critical flags

| Flag | How set | Assessment |
|---|---|---|
| **DEBUG** | `os.getenv('DEBUG', 'False')` truthy-string parse (settings.py:21) | ✅ **Fails safe**: unset → False. Production is correct *iff* Render's env doesn't set `DEBUG=true` — there is no guard preventing it and **no way to verify from the repo** (closed by §5c status endpoint). Not flagged CRITICAL because the default is safe; flagged as a verification gap (O6). |
| **ALLOWED_HOSTS** | `ALLOWED_HOSTS` env, comma-split (settings.py:23-24) — dev value is `*` | ⚠️ Two issues. (1) Prod value lives only in Render's dashboard — unverifiable; if anyone copies the dev `*` there, host-header protection is off (verify once via §5c). (2) **Five `*.{riker,picard,janeway,sisko,kirk}.replit.dev` wildcards are appended unconditionally** (settings.py:62-75) — they pollute production's ALLOWED_HOSTS and CSRF_TRUSTED_ORIGINS on Render. Practical risk is low (attacker must control a replit.dev subdomain *and* point it at Render) but it's dev config leaking into prod; gate it on `REPL_SLUG` like the other Replit blocks (O11). |
| **SECRET_KEY** | `os.environ['SECRET_KEY']` (settings.py:20) — **hard-required, no default** | ✅ The strongest possible pattern (app refuses to boot without it). Dev key is 50 chars. Never committed: `git log -S "django-insecure"` and `git log -S "SECRET_KEY = '"` across all history return nothing; `.env` has zero commits (§2b). |
| **SECURE_\*** | **None exist.** No `SECURE_SSL_REDIRECT`, no `SECURE_HSTS_SECONDS`, no `SESSION_COOKIE_SECURE`, no `CSRF_COOKIE_SECURE`, no `SECURE_PROXY_SSL_HEADER` | ❌ The main settings gap — full analysis in 1c. Mitigating facts: Render's edge forces HTTP→HTTPS at the platform level (covers the redirect), and `SESSION_COOKIE_HTTPONLY=True` + `X_FRAME_OPTIONS=DENY` are Django defaults already in effect. Not mitigated: cookie `Secure` flags and HSTS. |
| **DATABASES** | `DATABASE_URL` via dj-database-url, `conn_max_age=600`, `conn_health_checks=True` (settings.py:140-147); discrete `DB_*` vars as fallback | ✅ Clean env-driven config, no credentials in the repo, sensible pooling for a small worker count. The localhost/postgres fallback defaults are dev conveniences and harmless (prod always has DATABASE_URL). |

### 1c. `python manage.py check --deploy` — full output and meaning

Run 2026-06-11 (dev env; **all five findings are environment-independent** — they reflect settings code, so production raises the identical list):

| Warning | Meaning | Does it matter here? |
|---|---|---|
| **security.W004** — `SECURE_HSTS_SECONDS` not set | Browsers aren't told to refuse plain-HTTP connections to the domain in future visits | **Yes, moderately.** Render redirects HTTP→HTTPS, but without HSTS every fresh visit's first request can go out over HTTP (cookie exposure window, SSL-strip vector). Set `SECURE_HSTS_SECONDS` (start small, e.g. 3600, then raise; only add `includeSubDomains`/`preload` deliberately). |
| **security.W008** — `SECURE_SSL_REDIRECT` not True | Django itself won't redirect HTTP→HTTPS | **Mitigated by platform** — Render's edge already 301s HTTP to HTTPS for its domains. Setting it anyway is free *but* requires `SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')` first, or every request looks insecure to Django and it redirect-loops. Add both together. |
| **security.W012** — `SESSION_COOKIE_SECURE` not True | **The session cookie is sent without the `Secure` attribute** — a browser will attach it to any plain-HTTP request to the host (e.g. an `http://` link clicked before the edge redirect completes) | **Yes — the most real of the four.** This is a financial app; a sniffed session cookie is full account takeover within the tenant. One line to fix. |
| **security.W016** — `CSRF_COOKIE_SECURE` not True | Same exposure for the CSRF token | **Yes** — lower impact than the session cookie but the same one-line class of fix. |
| **fields.W342** — `DistributorGroup.primary_distributor` FK(unique=True) | Should be OneToOneField | Already catalogued as audit 01 **D21** (cosmetic). Listed here for completeness — it appears in `--deploy` output and will distract future runs; fix or note it. |

Equally informative is what **didn't** fire: no W018 (`DEBUG=True in deployment` — DEBUG resolved False in this run), no W009 (SECRET_KEY weak/`django-insecure-` prefixed), no W020/W021 omissions beyond those above. **Recommended production block** (gate on an env var or on `not DEBUG`):

```python
if not DEBUG:
    SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')  # Render sets X-Forwarded-Proto
    SECURE_SSL_REDIRECT = True
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_HSTS_SECONDS = 3600  # raise after verifying
```

`SECURE_PROXY_SSL_HEADER` also fixes a latent correctness issue independent of the warnings: without it, `request.is_secure()` is False behind Render's proxy and `request.build_absolute_uri()` generates `http://` URLs (will bite the first time emails/links are generated server-side).

---

## SECTION 2 — SECRETS & CREDENTIALS

### 2a. Codebase scan

- **No hardcoded secrets** in any tracked file. Greps across `apps/`, `producterp/`, `utils/`, scripts and docs for key/password/token assignment patterns and `postgres://user:pass@` connection strings return only the **placeholder examples** in README.md:45 and DEPLOYMENT.md:18. ✅
- `settings.py` contains zero literals — every sensitive value is `os.environ`/`os.getenv`. ✅
- `.env` exists in the working tree with real dev values, is **gitignored** (`.gitignore:3`) and **has never been committed** (`git log --all -- .env` is empty). ✅
- The only "secret-shaped" strings in tracked files are `SECRET_KEY=test-key-for-check` invocations inside `.claude/settings.local.json` permission entries — test values, not credentials.

### 2b. Git-history awareness

- `git log -S "django-insecure"` and `git log -S "SECRET_KEY = '"` / `= "` across all branches: **no hit** — settings.py never contained a hardcoded key in any revision. ✅
- **⚠️ O5 — a real credential exists in the dev environment (not in tracked files):** the `origin` remote URL in this clone's `.git/config` embeds a **GitHub Personal Access Token in plaintext** (`https://senorsangria:ghp_…@github.com/senorsangria/sangriaERP.git`). It is not committed and never will be (`.git/` isn't tracked), but: anyone with access to this Replit workspace can read it, and because **pushing main auto-deploys to Render**, this token is transitively a *production deploy credential*. **Rotate the token** and switch the remote to use Replit's git credential integration or a fine-grained PAT stored as a Replit secret. (Value deliberately not reproduced here.)

### 2c. Production secret provisioning

Production secrets (SECRET_KEY, DATABASE_URL, ALLOWED_HOSTS, CSRF_TRUSTED_ORIGINS, R2 keys, USE_OBJECT_STORAGE) live as **Render environment variables** — the right pattern; nothing sensitive in the repo. Two hygiene gaps:

- **DEPLOYMENT.md is drifted** (last updated March 2026): it documents `EMAIL_*` variables that **settings.py never reads** (no `EMAIL_HOST` etc. anywhere in settings — configuring them on Render would silently do nothing), and it **omits two variables production actually requires**: `CSRF_TRUSTED_ORIGINS` (default is localhost-only — prod POSTs fail without it, so it must already be set on Render, undocumented) and `USE_OBJECT_STORAGE` (read in urls.py:28, documented only inside PRODUCT_DECISIONS.md). DEPLOYMENT.md should be the single accurate contract for the Render env panel (O10).
- README references copying an example env file, but **no `.env.example` exists** — add one (placeholder values only) so the documented setup path works.

### 2d. Gitignore status

- `.env`, `media/`, `staticfiles/`, `db.sqlite3`, `*.log` all correctly ignored and verified untracked. ✅
- **`.claude/settings.local.json` is tracked** (in `git ls-files`, with multi-commit history, and currently sitting modified in `git status`). It is per-machine assistant config — it leaks internal tooling/workflow details (incl. dev usernames) into the repo every tenant-era contributor will clone, and it generates permanent diff noise. `git rm --cached` it and add `.claude/settings.local.json` to `.gitignore` (keep shared `.claude/settings.json` tracked if one is ever added) (O12).

---

## SECTION 3 — BACKUPS & DATA SAFETY

### 3a. Current backup story

Searched the repo for any backup tooling: **none exists** — no pg_dump script, no scheduled job, no backup documentation. The entire posture is:

1. **Render managed-Postgres snapshots** — capability depends on the instance plan (free instances: none/limited; paid: daily snapshots with ~7-day retention; point-in-time recovery only on higher tiers). **Which of these production actually has is not verifiable from the repo and must be confirmed in the Render dashboard.**
2. **Manual pre-deploy backups the user remembers to take** — unautomated, undocumented (no runbook states how they're taken, where they're stored, or how restore works).
3. Media (event photos, expense receipts) in Cloudflare R2 — **no versioning/backup configuration is recorded anywhere**; receipt images are part of the financial record.
4. Incidental: a `gitsafe-backup` git remote (code only, not data).

### 3b. Recovery story for the destructive operations (audits 01-02)

The app's known destructive paths — **replace-on-import** (hard-deletes overlapping months of SalesRecord), **batch delete** (CASCADE hard-delete of sales rows + auto-created accounts), **inventory snapshot bulk delete** (no trail at all), **PO delete**, **account merge** (admin-only, cross-company-capable per 02-T4), and the **planned schema changes** (audit 01 D1/D11/D12 are data-rewriting migrations) — share one recovery answer today: **restore the entire database to the last snapshot, losing every tenant's writes since that snapshot.** Specifically:

- **No point-in-time recovery confirmed** — without PITR, an import that wipes the wrong months at 4 p.m. costs the whole day's writes across *all* tenants to undo.
- **No per-tenant restore** — single shared DB; recovering tenant A's deleted sales means surgery on a restored copy, a procedure that exists nowhere in writing.
- **No restore has ever been rehearsed** (nothing in any doc) — an untested backup is a hope, not a backup.
- The in-app compensations are thin by design (audit 01 D2): free-text `ImportBatch.notes` is the only trail on the sales path.

### 3c. Recommended posture for multi-tenant financial data

In priority order, all before first tenant:

1. **Verify & document the Render plan's backup reality** (one dashboard visit): snapshot cadence, retention, PITR availability. If the plan lacks daily snapshots + ≥7-day retention, upgrade — this is the cheapest insurance in the stack.
2. **Automated nightly logical dumps** — a scheduled job (Render cron job, ~$1-7/mo) running `pg_dump -Fc` to the R2 bucket (separate prefix, lifecycle rule: keep 30 daily + 12 monthly). Logical dumps are what enable *selective/per-tenant* restore (`pg_restore -t`), which platform snapshots never give you.
3. **Automate the pre-deploy backup** — remove the "user remembers" dependency: either a Render pre-deploy hook that runs a dump before `migrate`, or fold it into the deploy runbook as a checklist gate (§4d). A bad auto-migration with no fresh dump is the single most plausible catastrophic-loss scenario in the current setup.
4. **One restore drill now, then quarterly**: restore the latest dump to a scratch Postgres, run `manage.py check` + row-count spot-checks (audit 01 §4e queries) against it, write down the steps and timing. Target: documented restore < 1 hour.
5. **R2 versioning** on the media bucket (receipts are financial documents; deletion/overwrite should be recoverable).
6. Longer-term (with COGS): per-tenant data export (CSV/dump per company) — both an offboarding obligation and the ultimate per-tenant recovery tool.

---

## SECTION 4 — DEPLOYMENT PIPELINE

### 4a. The actual flow (documented here as it really is)

```
Replit workspace (dev) ── work on `develop` branch
        │  manual: merge develop → main  ("Merge develop: …" commits; no PRs)
        ▼
GitHub senorsangria/sangriaERP ── push main
        │  Render auto-deploy on push to main
        ▼
Render web service ── build (pip install, collectstatic presumed)
        │  auto-migrate (python manage.py migrate) on deploy
        ▼
gunicorn (WEB_CONCURRENCY=1) + WhiteNoise static + Render Postgres + Cloudflare R2 media
```

Facts about this flow that live **nowhere in the repo**: the Render build command, start command, pre-deploy command, env vars, Postgres plan/version, and domain config are all dashboard-only. The repo's only deployment artifacts are **stale or dev-only**: `run.sh` + `.replit [deployment]` still specify `runserver` (the audit 04 F2 finding — now understood as Replit-dev-only, but the `.replit` deployment block is a loaded footgun if anyone ever clicks Replit's Deploy button), and `runtime.txt` pins `python-3.12.0` while dev runs 3.13.4 (whether Render even reads runtime.txt vs `PYTHON_VERSION` is itself unverified).

### 4b. Risk assessment

| Risk | Reality | Severity |
|---|---|---|
| **Auto-migrate on deploy, no safety net** | A bad migration (and the audit-01 roadmap explicitly plans data-rewriting ones: D1 grain fix, D11 month column, D12 backfill) halts the deploy or corrupts data with no automated pre-migrate backup, no rollback procedure documented, and no maintenance-mode story. Django migrations aren't transactional across data backfills; a partial failure leaves prod mid-state. | **HIGH** combined with §3 (the mitigation *is* the backup automation) |
| **No CI gate of any kind** | 1,337 tests (audit 05 §6c) and `check --deploy` exist, and **nothing runs them between "merge to main" and "production"**. A failing test suite deploys as readily as a passing one. | HIGH-leaning-MEDIUM (discipline currently compensates) |
| **No staging environment** | dev (Replit, ambient quirks, 1-company data) → prod, nothing in between. Migrations meet production-shaped data for the first time *in production*. | MEDIUM today → HIGH the day tenant #1 onboards |
| **Manual-merge, manual-backup process** | Single operator, no PR review, backup-before-deploy is memory-dependent. Fine solo; not tenant-grade. | MEDIUM |
| **Config not in code** | No render.yaml/IaC — the service can't be reproduced from the repo; dashboard drift is invisible and unreviewable. | MEDIUM |
| **WEB_CONCURRENCY=1** | gunicorn's default sync worker × 1 = **one request at a time** — operationally the same concurrency as the dev server audit 04 flagged, just hardened. One slow page (the 3.5MB event list, a big import) blocks every user of every tenant. | MEDIUM (S to fix) |

### 4c. Run command / build / static specifics

- **Static files:** WhiteNoise correctly wired (middleware second, `CompressedStaticFilesStorage` when R2 is on, `STATIC_ROOT=staticfiles/`). ✅ Note: when R2 is *not* configured the `STORAGES` block is skipped entirely, so static serving silently degrades to the non-compressed default — harmless but another instance of the dual-switch issue below.
- **Media:** storage backend switches on "all four R2 vars set" (settings.py:217-222) while the **unauthenticated local-media URL route** switches on a *different* flag, `USE_OBJECT_STORAGE` (urls.py:28). Two independent switches for one concern: if Render ever has the R2 vars but not `USE_OBJECT_STORAGE=true`, prod serves `media/` from local disk **unauthenticated** (and uploads land on Render's ephemeral disk, lost on redeploy). Collapse to one derived flag (`USE_OBJECT_STORAGE = _r2_configured`) (O13; ties to 02-T6).
- **gunicorn config:** nothing in the repo (no gunicorn.conf.py, no Procfile). With `WEB_CONCURRENCY=1`, recommend for this app at 1-5 tenants: `gunicorn producterp.wsgi --workers 2-3 --threads 4 --worker-class gthread --timeout 120 --max-requests 1000 --max-requests-jitter 100` (gthread because the workload is DB-and-template-bound with occasional long imports; `--timeout 120` acknowledges the synchronous import flow (04-F10) until it moves to a background job; CONN_MAX_AGE=600 × (workers×threads) connections stays well within Postgres limits). Commit this as `gunicorn.conf.py` so it's versioned.

### 4d. Should there be staging? — Yes, the light version

Before external tenants: **yes**, and it's cheap. The lightest stack that adds real safety, in increasing order (do 1-2 now, 3 before tenant #1):

1. **CI on GitHub Actions (free)** — on every PR/push to main: run the test suite + `manage.py check --deploy --fail-level WARNING` + `makemigrations --check`. ~30 lines of YAML; converts the 23k-line test suite from documentation into a deploy gate. (Also the natural home for audit 02's two-company harness when it lands.)
2. **Deploy runbook** (one page, versioned in the repo): backup → merge → push → watch Render deploy logs → hit the status endpoint (§5c) → spot-check. Turns the implicit process into a checklist a future hire can follow.
3. **A staging Render service** — same repo, `develop` branch auto-deploy, starter-tier Postgres restored weekly (or pre-release) from the production dump (this *is* the restore drill from §3c-4, two birds). Migrations and big imports rehearse against production-shaped data; total cost ≈ one more starter instance. A persistent dev DB on Replit is **not** a substitute — staging's value is *prod-like config* (gunicorn, R2, DEBUG=False, real env vars).

Not recommended at this scale: full preview-environments-per-PR, blue/green, or container orchestration — all overkill for 1-5 tenants.

---

## SECTION 5 — MONITORING & OBSERVABILITY

### 5a. What exists today

- **Render's built-in service logs and metrics** (CPU/memory graphs, log stream) — and that is the complete list.
- **No error tracking** (no Sentry/Rollbar in requirements or settings).
- **No LOGGING config at all** in settings.py — and this is worse than "default logging": with `DEBUG=False`, Django's default config routes unhandled-exception reports to the `mail_admins` handler, **`ADMINS` is empty, and the console handler is filtered to DEBUG=True only — so production 500 tracebacks are discarded entirely.** Render's log stream shows gunicorn's access line (`500 -`) and nothing else. Production errors are currently invisible (O3).
- **No uptime monitoring**, no health-check endpoint configured for Render's own health checks (no `/healthz` route exists — §1.1 census in audit 02 confirms the full URL space).
- No per-tenant visibility of any kind.

### 5b. Minimum viable observability for 1-5 tenants with financial data

All S-cost, in order:

1. **LOGGING config to stdout** (console handler, no DEBUG filter, `django.request` at ERROR, app loggers at INFO) — Render captures stdout; this alone makes 500s visible in the log stream. ~15 lines of settings.
2. **Sentry** (free tier covers this scale): `sentry-sdk[django]`, one `init()` gated on env var. Tag every event with `company` (one `before_send` or middleware line) → **per-tenant issue visibility** — when tenant #2 reports "imports are broken," you filter by their tag. Captures the synchronous-import timeouts (04-F10) before users report them.
3. **Uptime check** — UptimeRobot/Better Uptime (free) against `/healthz`, plus set the same path as Render's health-check URL so deploys gate on it.
4. That's it for now. Defer: APM/tracing, log aggregation, dashboards — Render metrics + Sentry performance sampling (free) cover the 1-5-tenant era.

### 5c. The on-demand production-config verification (the user's ask)

Two endpoints plus one command, all S-cost:

**1. `/healthz` — unauthenticated, for machines.** Returns 200 + `{"status": "ok", "db": true}` (one `SELECT 1`). Used by Render health checks and the uptime monitor. No version info (unauthenticated).

**2. `/ops/status` — the operator status page** (gate: `saas_admin` role / `is_staff`, the audit-02 T4 policy). Reports exactly what's needed to verify production config on demand:

```json
{
  "deployed_commit": "c12d9e7…",          // os.environ['RENDER_GIT_COMMIT'] — Render injects this
  "render_service": "producterp-web",      // RENDER_SERVICE_NAME; absent ⇒ not on Render
  "server": "gunicorn 23.0.0",             // detect: 'gunicorn' in sys.modules / SERVER_SOFTWARE
  "workers_env": "WEB_CONCURRENCY=1",
  "python": "3.13.4", "django": "5.1.6",
  "debug": false,                           // THE flag — red banner if true
  "allowed_hosts": ["erp.example.com"],     // verifies no '*', shows the replit-wildcard pollution
  "secure": {"session_cookie_secure": true, "csrf_cookie_secure": true,
              "hsts_seconds": 3600, "proxy_ssl_header": true},
  "db": {"connected": true, "engine": "postgresql", "server_version": "16.10",
          "migrations_pending": 0},          // MigrationExecutor().migration_plan(targets) length
  "storage": {"object_storage": true, "bucket": "producterp-media"},  // R2 on, names only — no keys
  "deploy_checks": {"silenced": 0, "warnings": 4},  // run check --deploy in-process, count by level
  "tenants": {"companies": 1, "active_users": 8},
  "time": "2026-06-11T14:00:00Z"
}
```

Render template variant: a simple table with green/red rows. Implementation notes: read settings + `os.environ` (Render injects `RENDER_GIT_COMMIT`, `RENDER_SERVICE_NAME`); pending migrations via `MigrationExecutor(connection).migration_plan(executor.loader.graph.leaf_nodes())`; `check --deploy` programmatically via `django.core.checks.run_checks(include_deployment_checks=True)` and count warnings; **never include values of secrets** — booleans and names only. After the §1c settings block ships, this page should read: server=gunicorn, debug=false, all four secure flags true, migrations_pending=0, warnings≤1 (the W342 cosmetic) — one glance, verified.

**3. `manage.py check --deploy` in the pipeline** — run it in CI (§4d-1) with `--fail-level WARNING` once §1c lands, so config regressions can't merge; the status endpoint then verifies the *running* environment matches.

---

## SECTION 6 — TENANT-ONBOARDING OPERATIONS

(Builds on audit 02 §5, which covered the in-app mechanics; this is the operational wrapper.)

### 6a. End-to-end, operationally, today

| Phase | What it takes | Operational gaps |
|---|---|---|
| **Pre-flight (platform)** | Nothing exists | No checklist; the audit-02 BFT security list (T1/T3/T9 fixes, T4 policy) is the *real* gate before any external tenant credentials are issued |
| **Provisioning** | Django admin: create Company (remember `so_sequence_start` — default 2006 is Señor Sangria's, 02-T12); create first admin user + roles, or saas_admin via `/users/create` | Two manual admin-UI operations with a known trap; only 2 management commands exist (`create_saas_admin`, `seed_data`) — **no `provision_tenant`** |
| **Config** | Co-packers: **impossible without a developer** (no UI, no admin registration — 02-T11). Brands/items/distributors: tenant self-service ✓ | T11 is the onboarding blocker |
| **Data setup** | Account import + sales-history import UIs (supplier_admin-gated ✓) | First big import hits the 04-F5 minutes-long-transaction bug — fix before the highest-stakes demo moment; replace-on-import semantics need explaining (runbook) |
| **Users/coverage** | `/users` UI + coverage tab ✓ (distributors must exist first) | Self-service ✓ |
| **Infra per tenant** | **None** — shared app, DB, R2 bucket; nothing to provision | ✓ by design at this scale. Note: nothing per-tenant to monitor either until Sentry tagging (§5b-2) |
| **Verification** | Nothing | No "tenant smoke test" — log in as the new tenant's admin, confirm empty-state pages render, run one tiny import |

### 6b. What to automate/script for 1-5 tenants

1. **`provision_tenant` management command** (audit 02's recommendation, scoped here): args `--name --slug --so-sequence-start --admin-email`; creates Company, first supplier_admin (random password printed once or emailed), prints the onboarding checklist with URLs. Idempotency guard on slug. **S.**
2. **`ONBOARDING.md` runbook**, versioned: pre-flight gate (BFT list status), provision command, co-packer setup, import order (accounts → mappings → sales → inventory), the smoke test, and the "verify on `/ops/status`" step. **S.**
3. **CoPacker CRUD UI** (02-T11) — prerequisite, not automation. **S.**
4. Guard `seed_data` against production habit-runs (02-T10): refuse unless `DEBUG=True` or `--force` is passed. **S.**
5. Skip for now: self-serve signup, tenant-scoped env config, billing automation — wrong scale.

---

## SECTION 7 — DEPENDENCY & PLATFORM CURRENCY

### 7a. Python dependencies (requirements.txt, fully pinned ✓)

| Package | Pinned | Status (June 2026) |
|---|---|---|
| **Django 5.1.6** | Feb 2025 | ❌ **EOL + unpatched.** 5.1 is a non-LTS release whose security support ended **2025-12-31** (final release 5.1.15, 2025-12-02). 5.1.6 additionally predates **nine** 5.1-series security releases (5.1.7→5.1.15), including **5.1.12 (2025-09-03), which fixed CVE-2025-57833 — SQL injection** — in a financial app. Currently supported lines: **5.2 LTS (supported to Apr 2028)** and 6.0. **Upgrade to 5.2 LTS** — the 5.1→5.2 delta is small for a vanilla Django app like this (no removed-API usage patterns spotted); pin 5.2.latest and re-run the test suite. **HIGH, before first tenant.** |
| Pillow 11.1.0 | Jan 2025 | ⚠️ Behind: 11.3+ (Jul 2025) fixed CVE-2025-48379 (heap overflow on image write). Pillow parses **user-uploaded photos** here — keep it current as a policy. Upgrade with the Django bump. |
| psycopg2-binary 2.9.10 | current 2.x | OK. (Note for later: psycopg2 is maintenance-mode; psycopg 3 is Django's forward path — move opportunistically, not now.) |
| gunicorn 23.0.0 | current | ✅ |
| whitenoise 6.9.0 | current-ish | ✅ |
| boto3 1.42.85 / django-storages 1.14.6 / dj-database-url 2.3.0 / python-dotenv 1.0.1 / rapidfuzz 3.14.3 | — | ✅ fine; routine bumps with the upgrade PR |
| **Process gap** | — | No Dependabot/pip-audit/safety anywhere. Enable **GitHub Dependabot** (one yaml) — at minimum it would have flagged the Django EOL months ago. |

### 7b. Platform

| Layer | Dev (verified) | Prod (needs dashboard verification) |
|---|---|---|
| Python | 3.13.4 | **Unknown.** `runtime.txt` says `python-3.12.0` — a 2023-era patch release with known CVEs *if* Render honors it (Render's native runtime reads `PYTHON_VERSION`/`.python-version`; runtime.txt may be ignored entirely). Either way the file is wrong: align everything on 3.13.x (set `PYTHON_VERSION` on Render, fix or delete runtime.txt) |
| PostgreSQL | 16.10 (supported until Nov 2028 ✓) | **Unknown** — confirm version + plan tier (ties to §3c-1 backup verification) in the Render dashboard; add to `/ops/status` output |
| OS/platform | Replit Nix (dev-only) | Render managed — nothing to do |

---

## SECTION 8 — PRIORITIZED FINDINGS SUMMARY

Severity: CRITICAL = confirmed prod-exposed security misconfig (none found — but two items are *unverifiable* from the repo and must be confirmed via the status endpoint); HIGH = backup/recovery gaps, deploy risks, EOL-security exposure; MEDIUM/LOW otherwise. Cost: S (<½ day), M (days), L (week+). **BFT** = before first tenant.

| ID | Sev | BFT | Finding | Why it matters | Cost | X-ref |
|---|---|---|---|---|---|---|
| **O1** | HIGH | **BFT** | Django 5.1.6: EOL line (5.1 security support ended 2025-12-31) **and** missing 5.1.7-5.1.15 security fixes incl. the 5.1.12 SQL-injection patch (CVE-2025-57833) | Unpatched framework under multi-tenant financial data; gap widens monthly | S-M (→5.2 LTS; test suite exists to verify) | §7a |
| **O2** | HIGH | **BFT** | Zero `SECURE_*` settings: session/CSRF cookies lack `Secure` flag in prod today; no HSTS; no `SECURE_PROXY_SSL_HEADER` (so `is_secure()`/absolute URLs are wrong behind Render); `check --deploy` W004/W008/W012/W016 | Session-cookie theft = tenant account takeover; the fix is the 6-line settings block in §1c | **S** | §1b/1c |
| **O3** | HIGH | **BFT** | No error visibility: no LOGGING config + empty ADMINS ⇒ production 500 tracebacks discarded (Django default routes them to mail_admins only when DEBUG=False); no Sentry; no uptime check | Errors during tenant onboarding would be literally invisible; cheapest fix in the audit | **S** | §5a/5b |
| **O4** | HIGH | **BFT** | Backup posture unverified and unautomated: Render snapshot/PITR capability unconfirmed, no scheduled logical dumps, pre-deploy backup is memory-dependent, restore never rehearsed, R2 unversioned | Destructive ops (replace-on-import, batch delete, planned migrations — 01-D2) have "restore everything, lose the day" as the only recovery; catastrophic for multi-tenant financial data | S (verify+automate dump) / M (full posture §3c) | §3, 01-D2 |
| **O5** | HIGH | **BFT** | GitHub PAT in plaintext in the dev clone's git remote URL; push-to-main auto-deploys ⇒ the token is a production-deploy credential | Workspace access = prod code execution; rotate + switch credential mechanism | **S** | §2b |
| **O6** | MED-HIGH | **BFT** | No way to verify production config on demand: DEBUG/ALLOWED_HOSTS/secure flags/migration state in prod are dashboard-only knowledge; no /healthz, no status endpoint | The user's stated need; closes the "is prod actually configured right?" loop permanently; spec in §5c | **S** | §5c |
| **O7** | MEDIUM | BFT | Deploy pipeline has no gates: no CI (1,337 tests never run pre-deploy), auto-migrate with no backup hook/rollback runbook, no staging | A bad migration or failing build reaches prod unchecked; mitigations are cheap (§4d: CI yaml + runbook + staging service) | S (CI+runbook) / M (staging) | §4b/4d, 05 §6c |
| **O8** | MEDIUM | BFT | `WEB_CONCURRENCY=1` — single sync gunicorn worker: one request at a time platform-wide | One slow page/import blocks all tenants; §4c config string fixes it; commit gunicorn.conf.py | **S** | §4c, 04-F1/F2/F10 |
| **O9** | MEDIUM | — | Deployment config not in code: no render.yaml/IaC; `.replit [deployment]` still runs runserver (footgun); runtime.txt pins python-3.12.0 (stale/possibly ignored) | Prod unreproducible from repo; dashboard drift invisible; conflicting platform files mislead | S | §4a, 04-F2 |
| **O10** | MEDIUM | BFT | DEPLOYMENT.md drift: documents EMAIL_* vars settings.py never reads; omits required CSRF_TRUSTED_ORIGINS and USE_OBJECT_STORAGE; no .env.example despite README referencing one | The env-var contract is the onboarding/DR document; today it's wrong in both directions | S | §2c |
| **O11** | MEDIUM | — | Replit ALLOWED_HOSTS + CSRF_TRUSTED_ORIGINS wildcards (5 `*.{cluster}.replit.dev` entries) appended unconditionally — active in production | Dev trust surface in prod host/CSRF lists; gate on REPL_SLUG | S | §1b |
| **O12** | MEDIUM | — | `.claude/settings.local.json` tracked in git (with history; currently modified) | Per-machine config leaking internal tooling detail; permanent diff noise; should be gitignored | S | §2d |
| **O13** | MEDIUM | — | Media storage dual-switch: backend keys off R2 vars, unauthenticated serving route keys off USE_OBJECT_STORAGE — can drift to "R2 configured but local media served/written on ephemeral disk" | Silent media loss on redeploy + unauthenticated serving in prod under one mis-set env var; collapse to one derived flag | S | §4c, 02-T6 |
| **O14** | LOW | — | Pillow 11.1.0 behind known-CVE fixes (parses user uploads); no Dependabot/pip-audit | Routine currency; automate the awareness | S | §7a |
| **O15** | LOW | — | Dev env-var dual-source: ambient Replit secrets silently override `.env` (observed: DEBUG file=True, resolved=False) | "What's in .env" ≠ what runs; confuses debugging; document or set override policy | S | §1a |
| **O16** | LOW | BFT | `seed_data` runs unguarded in any environment (creates a real-looking tenant) | Habit-run in prod creates junk tenant data; add DEBUG/--force guard | S | 02-T10, §6b |
| **O17** | LOW | — | Prod Postgres hygiene unverified: version, plan, autovacuum/ANALYZE state (dev stats are cold) | Audit 01 §4e-7 / 04-F11 prod check still outstanding; fold into /ops/status + runbook | S | 01-D25, 04-F11 |
| **O18** | INFO | — | Positives to preserve: SECRET_KEY hard-required from env (boot-fails without); DEBUG fails safe; `.env` never committed; no secret in any tracked revision; DATABASE_URL pattern with pooling+health checks; WhiteNoise correctly wired; deps fully pinned; portable no-platform-API codebase | The foundation is right — this audit's work is additive hardening, not rework | — | — |

**Counts: 0 CRITICAL (two unverifiable-from-repo items — prod DEBUG and prod ALLOWED_HOSTS — would be CRITICAL if found wrong; O6's endpoint settles both) · 5 HIGH · 8 MEDIUM · 4 LOW · 1 INFO — 18 findings.**

### The BEFORE-FIRST-TENANT operations package (one sprint, mostly S-cost)

1. **O5** rotate the PAT (today). 2. **O2** the §1c settings block. 3. **O3** LOGGING + Sentry + uptime check. 4. **O1** Django 5.2 LTS (+Pillow). 5. **O4** verify Render backups + nightly pg_dump-to-R2 + one restore drill. 6. **O6** `/healthz` + `/ops/status`. 7. **O7** CI yaml + deploy runbook. 8. **O8** gunicorn.conf.py with real worker count. 9. **O10/O16** DEPLOYMENT.md truth-up + seed_data guard. Staging (O7's M-half) lands when the audit-02 BFT code fixes do — rehearse those migrations on it.

### Cross-reference to audits 01-05

- 01-D2 (hard-delete paths) → the recovery story they lack is §3b/O4. 01-D25/§4e-7 → O17. 01-D21 (W342) appears in `check --deploy` output (§1c).
- 02-T1/T3/T9/T4 (security BFTs) → §6a pre-flight gate; 02-T6 (media) → O13; 02-T10 → O16; 02-T11/T12/§5 → §6; 02-T2 harness → runs in O7's CI.
- 03 (permissions) → `/ops/status` gating policy (saas_admin/is_staff only, per 02-T4).
- 04-F2 → resolved as "Replit-dev-only" but its artifacts remain (O9); 04-F5/F10 → §6a import risks + O8's timeout setting; 04-F11 → O17.
- 05-C12 (docs) → O10 and the §4d runbook; 05 §6c (1,337 tests) → O7's CI is what makes them load-bearing.
