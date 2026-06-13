# Deploy Runbook

Deploys go via a merge from `develop` → `main`. Render auto-deploys `main`
and auto-runs `python manage.py migrate` on each deploy. This runbook
documents the existing process; it does not change it.

---

## Pre-deploy checklist

**1. Branch and tree clean**

```bash
git branch --show-current   # must be on develop
git status                  # must be clean (no uncommitted changes)
```

**2. Review the commit range**

```bash
git log main..develop --oneline
```

Read every commit. Confirm nothing is half-finished or accidentally included.

**3. Confirm migrations are additive and non-destructive**

```bash
python manage.py showmigrations | grep "\[ \]"   # list unapplied migrations
```

For each unapplied migration: open the file and confirm it is safe to run
against live data — additive columns must have defaults or `null=True`;
no `DROP COLUMN`, no `ALTER` that rewrites rows; no `RunPython` that
mutates financial records without a rollback path.

**4. CI must be green**

Check the GitHub Actions run on the tip of `develop`:
- `makemigrations --check` — no uncommitted model changes
- `check --deploy` — no new security errors
- Full test suite — all 1341+ tests pass

Do not deploy if CI is red.

---

## ⛔ STOP — human confirmation gate

Before proceeding:

- [ ] Pre-deploy checklist above is fully complete
- [ ] A production database backup is confirmed in place

**Take a manual backup now if the automated backup has not run today:**

```bash
# On Render console or via a one-off job:
pg_dump $DATABASE_URL > producterp_$(date +%Y%m%d_%H%M%S).sql
```

Upload or verify the dump is stored in Cloudflare R2 (or another durable
location outside Render). Do not proceed without a confirmed backup — Render
auto-migrate runs immediately on deploy and cannot be aborted mid-flight.

---

## Deploy

**Merge develop → main (no fast-forward, so the merge commit is visible):**

```bash
git checkout main
git merge develop --no-ff -m "Deploy: merge develop into main (YYYY-MM-DD)"
git push origin main
```

Render detects the push and starts the deploy pipeline automatically:
1. Builds the new image
2. Runs `python manage.py migrate`
3. Swaps to the new instance

---

## Post-deploy monitoring

Watch the Render dashboard for 2–3 minutes after the deploy completes:

- Build log: confirm `migrate` output shows only the expected migrations applied
- Instance log: no `500 Internal Server Error` lines (these now appear in stdout via R13 logging)
- Spot-check the app: log in, load the dashboard, open one report

If anything looks wrong, **roll back immediately** via Render's "Rollback" button
(reverts to the previous deploy without touching the database — safe if the
migration was additive).

---

## Notes

- `main` is production. Never push feature work directly to `main`.
- `develop` is the integration branch. All feature branches merge here first.
- The M-half of R17 (a staging Render service for rehearsing migrations before
  tenant #1) is still pending — until it exists, the pre-deploy migration review
  above is the only gate between `develop` and production data.
