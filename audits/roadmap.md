# productERP — Phase 2 Synthesis Roadmap

**Date:** 2026-06-12
**Revised:** 2026-06-13 — review decisions marked (bucket moves, sequence update, scope notes Q1/Q2/Q3/Q6 resolved, two items added).
**Purpose:** One prioritized, de-duplicated list of everything the foundation-audit series found, bucketed by when it matters, so execution (Phase 3) can proceed in small shippable slices without re-reading six audits.
**Inputs:** audits/00_production_verification.md (production-verified numbers — where it revises an audit, the revision wins), audits 01–06, REFACTORING_BACKLOG.md (pre-audit deferred items, folded in), PRODUCT_DECISIONS.md (decisions already made, respected — notably: the historical event-import tool is **retired**, not fixed; QuickBooks is **exports only**, no API integration).

**Overall verdict of the audit series:** The foundation is solid. No rewrite is needed anywhere — schema, query discipline, app boundaries, and configuration patterns are all fundamentally right (01-D27 aside, which is absence not breakage; 04-F12, 05-C13, 06-O18 all record the positives). What the audits found is *hardening in specific known places*: tenancy and permissions are enforced by convention rather than structure (one confirmed breach in the tool already slated for retirement), operations lack the safety net a multi-tenant financial product needs (backups, CI, error visibility, an EOL Django), and the code's main debt is copy-paste duplication, not bad design. Production verification (00) confirmed scale is a non-issue (largest table 110k rows / 22 MB), closed two items outright, and downgraded two more — the roadmap below reflects the revised conclusions, not the original dev-based ones.

---

## THE CENTERPIECE — one structural mechanism, three audits

Audits 02 (tenancy), 03 (permissions), and 05 (code structure) independently converged on the same fix. It is one mechanism with three parts:

1. **A `@require_permission('can_x')` view decorator** — in one line per view it handles: authentication, 403 on missing permission, and resolution of `request.company` (including the saas_admin null-company branch, decided once instead of inconsistently per helper). It replaces the six hand-rolled gate styles audit 03 catalogued (03 §2a) and the per-app `_require_*` helper copies audit 05 found drifting (05 C10, Py-6).
2. **Scoped model managers** — `Model.objects.for_company(c)` / `.visible_to(u)` on the tenant-bearing models, replacing ~150 hand-written `company=` filters. The codebase already half-thinks this way (the three existing scoped helpers); this makes it the standing pattern (02 §3c option 2).
3. **A two-company test harness** — a pytest fixture with two fully-populated companies, parametrized tests that hit every pk-addressed endpoint with the *other* company's pks and assert 404, plus 403 assertions per the permission map (03 §2a is the spec) and CI invariant checks on the cross-FK company pairs (02 T16). Audit 02's 142-endpoint census is the test list. Prerequisite: shared test fixtures (05 C4) — today every test file hand-rolls its own builders.

**What it resolves or backstops:** 02-T2 (tenancy is 100% convention — the core structural gap), 03-P2 (six enforcement styles, template/view gate mismatches), 05-C4 (no shared fixtures, single-company test blindness), 05-C10 (plumbing dialects), parts of 05-C6 (gate-helper drift); it is the enforcement vehicle for 03-P1 (unenforced account permissions) and the single place 02-T5/03-P6 (saas_admin inconsistency) gets decided. It also *backstops* everything else: any future missed filter or gate (the event-import tool proved the failure mode is real) gets caught by the harness instead of shipping.

**Why it is likely the first major execution block:** it converts the single biggest risk class (silent cross-tenant leaks and permission over-grants) from "hope review catches it" into a tested property *before* external tenants exist, and every code change after it lands is safer. And it is the choke point the future **distributor-as-tenant sharing layer** needs: audit 02 §7's strongest finding is that `company=user.company` is today the *only* visibility concept — a sharing feature needs a second axis (grants/links), and `.visible_to(user)` is the one seam it can plug into. Every raw `company=` filter written between now and then is one more line that fights that future.

**Deliberately not adopted** (02 §3c): ambient/thread-local tenant managers and Postgres RLS — both assessed as overkill or risky at 1–5 cooperating tenants. Recorded under Accepted Debt with revisit triggers.

---

## Item key

- **Sev / Cost** are as stated by the source audit ("est." where the audit gave none). Cost: S < half a day, M = days, L = week+.
- **Prod status:** *verified* (00 confirmed it in production) · *revised-by-00* (00 changed the conclusion — the revised form is what appears here) · *env-independent* (code/structure finding, environment doesn't matter) · *dev-measured* (numbers came from dev; mechanism is structural).

### Closed / already done (not roadmap items)

- **Account.distributor non-null + PROTECT** — verified structurally enforced in production (00); the backlog #3b deploy gate is satisfied. Closed.
- **Production PostgreSQL currency** — 16.13, supported (00). Closed.
- **GitHub PAT rotation (06-O5)** — **DONE.**
- **"Production runs the dev server" (04-F2)** — reframed by audit 06: production runs gunicorn on Render; run.sh is Replit-dev-only. The real remnants are R18 (one worker) and R46 (stale platform files).

---

## BUCKET 1 — BEFORE-FIRST-TENANT (external tenants ~3 months out)

| ID | Item | Source | Sev | Cost | Prod status | Notes / dependencies |
|---|---|---|---|---|---|---|
| **R1** | **The centerpiece**: @require_permission decorator + scoped managers + two-company harness (see section above) | 02 T2/§3c, 03 P2/§4c, 05 C4/C10 | HIGH | S-M (harness) + M (managers, incremental) | env-independent | Fixtures (05 C4) first; harness runs in R17's CI; managers adopt app-by-app |
| **R2** | DECIDED 2026-06-13 — Delete the event_import app OUTRIGHT: the app directory, all endpoints, all migrations, and the `can_run_historical_event_import` permission. Decision recorded in PRODUCT_DECISIONS.md. This **is** the fix for the one confirmed cross-tenant read+write breach (02 T1, CRITICAL), the single-tenant-hardwired tool surface (02 T9), and the permission cleanup (03 P12). The event data it imported (Event rows with `is_imported=True`) is retained. A future tenant-facing importer is a fresh build, not a revival of this tool. | 02 T1/T9, 03 P12 | CRITICAL (T1) | S | env-independent | Must land before any external tenant credential is issued. (Contingency only, if retirement somehow slips: the S-cost stopgap is validating posted pks against the candidate set.) |
| **R3** | Add company terms to the invariant-reliant event queries (`Event.objects.filter(account=…)` in accounts/reports/utils) + CI invariant checks on company-pair consistency | 02 T3/T16 | HIGH | S + S | env-independent | Invariant checks ride in R1's harness |
| **R4** | Validate the reports `?items=` pks against the company at parse time (foreign item names currently leak by pk probing) | 02 T7 | MEDIUM | S | env-independent | Audit 02 did not BFT-flag this, but the leak class only becomes real once tenant #2 exists — placed here for that reason |
| **R5** | Enforce the granular account-mutation permissions (create/edit/toggle/delete currently gated only by `can_view_accounts`); resolve the bulk-delete inconsistency | 03 P1 (+P9) | HIGH | S | env-independent | Cleanest via R1's decorator but can ship standalone |
| **R6** | Operator policy package: written rule that no tenant user ever gets `is_staff`; check prod for stray superusers; declare cross-company user moves unsupported | 02 T4/T13/T14, 03 P13 | HIGH (T4) | S | env-independent | Policy + one query, no code |
| **R7** | Decide the saas_admin access model (recommendation on the table: saas_admin = Django-admin operator only; remove the app-side all-tenant account search branch) | 02 T5, 03 P6 | MEDIUM | S | env-independent | DECIDED 2026-06-13: saas_admin operates via Django admin only; the app-side all-tenant account search branch is removed. Implemented inside R1's decorator. |
| **R8** | CoPacker CRUD UI (mirror the Brand pattern) — currently tenants cannot manage co-packers at all; hard onboarding blocker for production features | 02 T11 | MEDIUM (blocker) | S | env-independent | — |
| **R9** | `provision_tenant` management command + ONBOARDING.md runbook (company + so_sequence_start prompt + first admin; import order; smoke test) | 02 T12/§5, 06 §6b | LOW (T12) / MEDIUM (gap) | S | env-independent | Removes the so_sequence_start=2006 trap |
| **R10** | Seeded-data hygiene: guard `catalog/0007` against fresh-environment contamination, `seed_data` refuses to run unless DEBUG/--force, stop shipping seed data as migrations | 01 D3, 02 T10, 06 O16 | HIGH (D3) | S | env-independent | — |
| **R11** | Django 5.2 LTS upgrade (5.1.6 is EOL and missing nine security releases incl. a SQL-injection fix) + Pillow bump + enable Dependabot — ✅ IMPLEMENTED 2026-06-13 (on develop, pending deploy). | 06 O1/O14 | HIGH | S-M | env-independent | Do early — all later code changes should land on a supported framework; the 1,337-test suite verifies it |
| **R12** | The SECURE_* settings block: proxy SSL header, SSL redirect, Secure cookies, HSTS (+ gate the Replit ALLOWED_HOSTS wildcards out of prod) — ✅ IMPLEMENTED 2026-06-13 (on develop, pending deploy). | 06 O2 (+O11) | HIGH | S | env-independent | Six lines of settings; cookies currently lack the Secure flag in production |
| **R13** | Error visibility: LOGGING-to-stdout config + Sentry (tagged per company) + uptime check. Production 500s are currently discarded entirely — ✅ IMPLEMENTED 2026-06-13 (on develop, pending deploy). | 06 O3 | HIGH | S | env-independent | Cheapest high-value fix in the whole series |
| **R14** | Backup posture: verify the Render plan's snapshot/PITR reality, automate nightly `pg_dump` to R2, automate/checklist the pre-deploy backup, run one restore drill, turn on R2 versioning (receipts are financial records) | 06 O4, 01 D2 (recovery side) | HIGH | S (verify+dump) / M (full) | env-independent | The only current recovery for any destructive op is "restore everything, lose the day" |
| **R15** | ~~Rotate the GitHub PAT~~ — **COMPLETED** — ✅ DONE 2026-06-13. | 06 O5 | HIGH | — | done | Listed for completeness |
| **R16** | `/healthz` + `/ops/status` endpoints — on-demand verification of prod config (DEBUG, ALLOWED_HOSTS, secure flags, migrations pending, deployed commit) | 06 O6 | MED-HIGH | S | env-independent | Settles the two unverifiable-from-repo items (prod DEBUG / ALLOWED_HOSTS) — ✅ IMPLEMENTED 2026-06-13 (on develop, pending deploy) |
| **R17** | CI (GitHub Actions: full test suite + `check --deploy` + `makemigrations --check`) + one-page deploy runbook; staging Render service as the M-half — S-half (CI + deploy runbook) ✅ IMPLEMENTED 2026-06-13 (on develop, pending deploy); M-half (staging) still pending. | 06 O7, 05 §6c | MEDIUM (HIGH-leaning) | S (CI+runbook) / M (staging) | env-independent | CI should precede the heavy refactoring slices — it protects everything after it. R1's harness runs here |
| **R18** | gunicorn worker config committed as `gunicorn.conf.py` (WEB_CONCURRENCY=1 today = one request at a time platform-wide) | 06 O8, 04 F2-remnant | MEDIUM | S | env-independent | — |
| **R19** | DEPLOYMENT.md truth-up (remove EMAIL_* fiction, add CSRF_TRUSTED_ORIGINS + USE_OBJECT_STORAGE) + add `.env.example` | 06 O10 | MEDIUM | S | env-independent | The env-var contract doubles as the DR document |
| **R20** | Import AccountItem loop fix: replace the per-pair `get_or_create` (~10–15k sequential queries inside one transaction on a typical initial-history import) with one lookup + `bulk_create(ignore_conflicts)` | 04 F5 | MEDIUM | S | dev-measured | Fix before tenant #1's first big import — it is exactly the onboarding worst case |

---

## BUCKET 2 — BEFORE-NEXT-FEATURE (gates for the COGS / QuickBooks-export work)

Note on framing: per PRODUCT_DECISIONS, no QuickBooks API integration will be built — the bookkeeper enters data manually from productERP exports. Where the audits say "QuickBooks sync/reconciliation," the actual requirement is **export correctness and a stable reconcile key**, which is what these items provide.

| ID | Item | Source | Sev | Cost | Prod status | Notes / dependencies |
|---|---|---|---|---|---|---|
| **R23** | SalesRecord grain decision — production bounded this to **149 groups (max 3 rows) out of 110,018**, all intra-batch, 99.86% naturally unique. DECIDED 2026-06-13: SalesRecord keeps its surrogate-id grain — no natural key, no uniqueness constraint; every source line is ingested as its own row. Production + product-owner review confirmed same-day duplicate (account, item, date) rows and negative quantities are legitimate business events (multiple same-day orders, same-day corrections that back out an earlier order, returns, sample draws against house accounts), not artifacts; the source has no line identifier. A pre-COGS diagnostic confirmed the data layer preserves signed quantities and same-grain multiples faithfully end-to-end (ingest, bulk_create, and replace-on-import round-trip). The export reconcile key is the surrogate id plus the grain. Negative quantities must net correctly in COGS; sample rows are commingled (see R59). | 01 D1, backlog #1, 00 | HIGH → bounded | S-M (was open-ended M) | **revised-by-00** | Before COGS, not a blocker. The reconcile key for exports depends on it |
| **R24** | Stop the AccountItemPriceHistory write path now; drop the table later. Production showed it is not just dead but actively writing misleading data (logged per import touch, not per price change; never matches current_price). Do **not** copy this pattern for the planned ending-inventory feature — OwnInventorySnapshot is the right precedent | 01 D9, backlog #7, 00 | MEDIUM, strengthened | S | **revised-by-00** | Write-path stop is NOT COGS-gated — sequenced early in the before-first-tenant list (see proposed sequence) as a cheap standalone: stops active misleading-data generation now. Only the table drop stays deferred in Bucket 2. — write-path stop ✅ IMPLEMENTED 2026-06-13 (on develop, pending deploy); table drop still deferred |
| **R25** | Soft-delete / mutation audit trail for financial data: two live hard-delete paths into SalesRecord (batch CASCADE, replace-on-import) plus untrailed inventory-snapshot and PO deletes; only trail today is free-text notes. Design it tenant-agnostic (actor + timestamp + scope), per the sharing-future guardrails (R52) | 01 D2, backlog #4 | HIGH | M-L | env-independent | Corrections become financially material with COGS; R14 is the recovery-side complement |
| **R26** | Denormalize `distributor` onto SalesRecord + `(distributor, sale_date)` index — removes the 719-probe account join from every forecast/report/replace-delete (21 ms now, linear growth; seconds at distributor-tenant volumes). Production confirmed no distributor column exists. Also a prerequisite for distributor-level analytics and the distributor-as-tenant model | 01 D12, 04 F3, backlog #3a, 00 | MEDIUM → HIGH at scale | M | **verified** | Cheapest done before data multiplies; re-review the (item, sale_date) index after it lands (R21) |
| **R27** | Month-serving structure for sales queries. First slice (S, code-only): rewrite `__year/__month` filters as date ranges — existing composites then serve them, eliminating the measured 40% fetch-and-discard. Full fix (M): a month column or equivalent, decided with R23/R26 | 01 D11, 04 F6 (+F9), backlog #2 | MEDIUM | S (ranges) / M (full) | dev-measured | Every consumer aggregates monthly; data verified genuinely daily |
| **R28** | Extract the PO save logic (the 91%-identical save twins) into one function + fix the SO-number race (MAX+1 with no lock, no unique constraint) + enforce the status-conditional PO#/SO# rules at the DB/save layer (clean() is bypassed by update_fields saves) | 05 C2, 01 D5/D6 | HIGH (C2) / MED-HIGH (D5) / MEDIUM (D6) | S-M | env-independent | POs are the COGS revenue-side anchor; finish before COGS builds on them. The template half was already unified |
| **R29** | Extract the sales-import engine (~400 lines) out of imports/views.py into a module | 05 C5 | MEDIUM | S-M | env-independent | COGS reconciliation builds on import semantics; also the natural host for the future background-job move (R56) and where R54 would be revisited |
| **R30** | Report data-builder extraction: the three report↔CSV twin pairs (60–66% identical, ~600 duplicated statements) plus the third re-implementation in the account sales tab become `build_<report>_data()` functions; HTML renders, CSV writes. Pre-audit PRODUCT_DECISIONS already marks this "urgent" | 05 C1, 04 F4 (partial), PRODUCT_DECISIONS "Future Cleanup" | HIGH | M | env-independent | Kills the report-says-X-export-says-Y bug class; COGS reporting builds on functions, not view bodies |
| **R31** | COGS build-surface design decisions (no code yet): Material/BOM/BOMLine as new tables, costs at PO/run level + effective-dated standards (never a mutable scalar on Item), UoM model, extend CoPacker rather than parallel it. The single biggest fork — components as Item-with-a-kind vs a separate Material model — is Scope Notes Q5 | 01 D27/§2d | INFO (defines the work) | — (design) | env-independent | Output: exports clean enough for the bookkeeper (per the no-QB-integration decision) |
| **R59** | Sample / house-account identification mechanism, before COGS treats sample cost. Today samples are distinguishable only by Account.name convention (e.g. 'MBD SAMPLE'); account_type exists but is never populated by the importer and there is no dedicated flag. Decide and implement: backfill account_type with a controlled vocabulary, or add an is_sample/is_house_account boolean and backfill. COGS margin math must exclude samples (cost, no revenue), so this gates correct COGS. Sibling design decision to R31/Scope-Note Q5. | pre-COGS diagnostic 2026-06-13, 01 | MEDIUM | S-M | env-independent | — |

---

## BUCKET 3 — OPPORTUNISTIC (do when touching the area anyway)

| ID | Item | Source | Sev | Cost | Prod status | Notes |
|---|---|---|---|---|---|---|
| **R32** | Account dedupe: merge the 4 live duplicate groups (the merge tool exists, unused) and decide an account uniqueness posture | 01 D7, 02 §3d | MEDIUM | M | dev-measured | Per-tenant data quality, not a cross-tenant risk |
| **R33** | Distributor `(company, name)` / `(company, code)` uniqueness | 01 D8, 02 §3d | MEDIUM | S | env-independent | — |
| **R34** | `AccountItem.item` CASCADE → PROTECT (the lone Item-FK outlier) | 01 D10 | MEDIUM | S | env-independent | — |
| **R35** | Consolidated cleanup pass: fossil role gate on the sales import (03 P3), 13 dead permissions + lying names (03 P4), dead role-ladder branches (03 P5), dead `ImportBatch.brand` column (01 D17), stale DB_REVIEW.md (01 D30), dead enum states + dead `_notes_modal.html` + impossible `distributor='none'` filter (05 C11) | 03 P3/P4/P5, 01 D17/D30, 05 C11 | MEDIUM/LOW | S-M | env-independent | Audit 03 notes this batches naturally with R2's retirement |
| **R36** | Replace the 10-migration RBAC grant chain with an idempotent `sync_rbac` command; squash migration chains after prod converges | 03 P7, 01 D29, 02 T8 (mechanism half) | MEDIUM | S-M | env-independent | — |
| **R37** | Document `distributor_contact` as a reserved stub (1 permission, no viable UI path) | 03 P8 | LOW | S | env-independent | Whoever builds it first defines the distributor-side access pattern — build it on R1's helpers |
| **R38** | Gate the login-only AJAX endpoints (geo-footprint lists, staff roster) behind view permissions | 03 P10 | LOW | S | env-independent | — |
| **R39** | Inline-JS extraction: ~4,900 lines across 12 templates → `static/js/<page>.js` + shared `app.js` (CSRF/fetch/modal helpers); add a thin ~10-scenario Playwright smoke layer | 05 C3, §6c-4 | HIGH (EFV) | M | env-independent | Mechanical, not a rewrite; pairs naturally with R22; home of the recurring bootstrap-load-order bug class |
| **R40** | Frontend fragment consolidation: diverged forecast-grid CSS dialects, the ×7 copy-pasted filter-modal markup, a shared list-row partial for the dual mobile/desktop render | 05 C7 (T-1/T-2/T-3/T-4) | MEDIUM | S-M | env-independent | T-3 is half of R22's payload |
| **R41** | Split the fat tab-dispatcher views (distributor_list 458 lines / 5 tabs; production_home; account_detail_combined) | 05 C8 | MEDIUM | M | env-independent | With R40's per-tab partials |
| **R42** | Move the three cross-imported view functions (`_get_visible_events`, `_build_enhanced_coverage_areas`, `_apply_price_updates`) into their owning apps' utils/service modules | 05 C9 | MEDIUM | S | env-independent | `_apply_price_updates`'s worst consumer disappears with R2 |
| **R43** | Consolidate the drifted small helpers (`_strip_excel_zip` — already diverged, one copy crashes on None — temp-file trio, `_format_quantity_cases`, parse helpers) into utils/ | 05 C6 (Py-3/4/5/7) | MEDIUM | S | env-independent | — |
| **R44** | Write down the 8 implicit conventions in ARCHITECTURE.md (tenancy, gating, JSON-endpoint pattern, logic placement, decimal-cases, year/month pairs, import staging); index PRODUCT_DECISIONS.md | 05 C12 | LOW (EFV) | S | env-independent | The conventions held because one author held them; tenant-era contributors need them written |
| **R45** | Account-sales report ceiling/virtualization (1.64 MB measured, grows linearly; HTML is ~45× the CSV of the same data) | 04 F4 | MEDIUM | M | dev-measured | "Not urgently" per audit 04; R30 is the prerequisite refactor |
| **R46** | Deploy config in code: render.yaml (or documented equivalent), fix/remove the `.replit [deployment]` runserver footgun and stale runtime.txt | 06 O9 | MEDIUM | S | env-independent | gunicorn.conf.py ships in R18 |
| **R47** | Untrack `.claude/settings.local.json` and gitignore it | 06 O12 | MEDIUM | S | env-independent | — |
| **R48** | Media hardening: collapse the dual storage switch to one derived flag (06 O13), then authenticated/signed media serving (02 T6 — receipts are financial documents protected only by URL secrecy today) | 06 O13, 02 T6 | MEDIUM | S (switch) + M (auth) | env-independent | Neither audit gated this on a milestone; raise priority if receipts become externally referenced |
| **R49** | Document the dev env-var dual-source behavior (ambient Replit secrets silently override `.env`) | 06 O15 | LOW | S | env-independent | — |
| **R50** | ANALYZE the dev database (planner estimates measured off 2–50×) | 01 D25, 04 F11 | LOW | S | dev-only (prod side closed by 00) | — |
| **R51** | Minor model hygiene batch: Company on_delete inconsistency (01 D18), NULL-account tastings / NULL-date event (D19), ItemMapping SET_NULL not resetting status (D20), W342 OneToOneField (D21), null=True CharField + 'Unknown' sentinels (D22), stale InventoryImportBatch counters (D23), ProductionPOLine stored-derived drift (D24), missing timestamps on recap/route tables (D26), duplicate coverage-area rows possible (01 §3a-6) | 01 D18–D24, D26, §3a | LOW | S each | env-independent | Fold individual fixes into whatever PR touches the model |
| **R52** | Standing design guardrails for the distributor-as-tenant future (no code now): don't add more required supplier-side semantics to Account (01 D4/F1-friction); keep sales reads/mutations flowing through narrow choke points; don't deepen the notes-append audit pattern; per-context values go on junction tables, not Item scalars (01 D14/F2/F3/F5/F6-friction) | 01 D4/D14/§6c, 02 §7 | HIGH-awareness / MEDIUM | — (discipline) | env-independent | R1's `.visible_to()` and R25's tenant-agnostic audit design are the concrete embodiments |
| **R21** | Redundant index drops on sales_salesrecord: company_id single index = clear drop (do when next touching the sales models). account_id single index = KEEP — decided 2026-06-13: negligible benefit at current scale (110k rows), and R26 will reshape the index plan space; revisit after R26 lands. (item, sale_date) pair = review after R26. Execution wrinkle: Django auto-recreates FK indexes on a naive RemoveIndex — making drops stick is an execution detail | 01 D16, 04 F7, 00 | LOW (01) / MEDIUM-QW (04) | S | **revised-by-00** | One no-downtime migration per drop (Moved from before-first-tenant 2026-06-13 — does not gate tenant onboarding.) |
| **R22** | Event list payload rework: render only the requested tab, paginate/lazy-load the Past tab (3.54 MB measured today; 98.6% of rows are imported history). Absorbs the sidebar fan-out (04 F8); the mobile/desktop double-render is the follow-up polish (pairs with R39/R40) | 04 F1 (+F8), 05 T-3 | HIGH | S-M | dev-measured | Day-one reality for any tenant importing history — the 3.5 MB events page is 98.6% imported history from the retiring event_import tool; a new tenant starts with an empty events page. (Moved from before-first-tenant 2026-06-13 — does not gate tenant onboarding.) |

---

## BUCKET 4 — ACCEPTED-DEBT (documented, deliberately not fixed)

| ID | Item | Source | Why accepted | Revisit trigger |
|---|---|---|---|---|
| **R53** | `AccountItem.date_first_associated` never recalculated after replace-imports | 01 D28, backlog #6, **00 (revised)** | Production showed **100% accuracy** — all 14,053 AccountItems-with-sales exactly match the earliest real sale date; the code risk is real but has produced zero corruption. Downgraded from fix to accepted-debt per 00. Optional cheap guard only | If a replace-import ever drops the earliest month for a pair, or if first-seen reporting becomes a feature |
| **R54** | ImportBatch grain = upload, not month (partially-gutted batches after replace-on-import) | 01 D15/D23, backlog #5 | The appended audit-note workaround is built, surfaced in the UI, and accepted as the compensating record (per PRODUCT_DECISIONS' replace-on-import design) | Revisit toward per-(distributor, month) batches when R29 reworks the import area |
| **R55** | Global Role/Permission vocabulary — per-tenant custom roles impossible; a role edit affects every tenant | 01 D13, 02 T8, 03 §3d | Safe-but-rigid today (no role-editing UI exists); acceptable for 1–5 cooperating tenants. R36 fixes the *delivery mechanism*; the schema stays global | The first tenant-specific access request ("tenant X's admins shouldn't see reports") |
| **R56** | Sales import runs as a synchronous web request | 04 F10 | Fine at monthly cadence and current volumes; R20 removes the worst in-transaction cost; R18's timeout setting buys headroom. Explicitly "deliberately deferred, not premature" per audit 04 | Distributor-as-tenant import volumes (timeouts); build on R29's extracted engine |
| **R57** | Route orphan duplicates permitted (nullable created_by in the unique key) | 01 §3a-7 | Documented and accepted in a model comment; low stakes | Standalone route management feature, if built |
| **R58** | Ambient-tenant managers and Postgres RLS not adopted | 02 §3c options 4/5 | L-cost and risky at this scale; R1 covers the need | Tenant count past single digits (ambient); distributor-as-tenant sharing design (RLS re-evaluation) |
| **R60** | Silent zero-quantity coercion on import — a non-numeric quantity cell becomes quantity=0 and is ingested rather than skipped or flagged. | pre-COGS diagnostic 2026-06-13 | Harmless at current data quality (zero nets to nothing in sums); no known occurrences. | When the importer is extended for a new tenant's file format, add a 'rows coerced to zero' count to the import summary. |

---

## PROPOSED SEQUENCE — BEFORE-FIRST-TENANT bucket

> **PROPOSED — execution order is decided by the product owner, not this document.** Slices are small and independently shippable per the Phase 3 model. Sequencing honors: the PAT rotation (R15/O5) is already done; CI precedes the Django upgrade so the framework change lands through a gate; error visibility is the cheapest high-value fix; and the R24 write-stop is pulled forward as a cheap standalone to stop active misleading-data generation.

0. ~~PAT rotation (R15)~~ — **completed.**
1. ~~**R13 — Error visibility** (logging + Sentry + uptime). Cheapest high-value fix.~~
2. ~~**R12 — SECURE_* settings block.** Closes the live cookie-security gap.~~
3. ~~**R17 (S-half) — CI + deploy runbook.** Makes the 1,337 tests a deploy gate before the framework upgrade and everything after.~~
4. ~~**R11 — Django 5.2 LTS + Pillow + Dependabot.** Lands through the CI gate; the test suite verifies it.~~
5. ~~**R16 — /healthz + /ops/status.** Verifies in production that slices 2–4 took effect.~~
6. ~~**R24 (write-path stop only) — Stop the AccountItemPriceHistory write path.** Pulled forward from Bucket 2: cheap, standalone, stops active misleading-data generation now. Table drop stays deferred.~~
7. **R18 — gunicorn workers.**
8. **R14 — Backups**: verify plan, nightly dump to R2, restore drill, R2 versioning. Must precede any data-rewriting migration.
9. **R2 — Retire event_import (delete outright).** Closes the only CRITICAL finding; gates issuing any external credential.
10. **R3 + R4 — small scoping patches.**
11. **R1 (slice 1) — shared fixtures → two-company harness + lint**, wired into CI.
12. **R1 (slice 2) + R5 + R6 + R7 — decorator rollout begins**; enforce account permissions; operator-policy and saas_admin decisions land here.
13. **R20 — Import AccountItem bulk fix.**
14. **R8 — CoPacker UI.** Unblocks production features for tenants.
15. **R9 + R10 — provision_tenant + onboarding runbook + seed-data guards.**
16. **R19 — DEPLOYMENT.md truth-up + .env.example.**
17. **R17 (M-half) — Staging service**; rehearse the Bucket-2 data-rewriting migrations before tenant #1.

*(R21 and R22 are no longer in this sequence — now Opportunistic.)*

---

## SCOPE NOTES — decisions the roadmap needs from the product owner

### RESOLVED (2026-06-13)

1. **SalesRecord grain (R23):** DECIDED — SalesRecord keeps its surrogate-id grain; no natural key, no uniqueness constraint; every source line is ingested as its own row. Same-day duplicate (account, item, date) rows and negative quantities are legitimate business events (multiple same-day orders, same-day corrections, returns, sample draws against house accounts); the source has no line identifier. Pre-COGS diagnostic confirmed signed quantities and same-grain multiples are preserved faithfully end-to-end. See R23 and PRODUCT_DECISIONS.md.
2. **`account_id` single-index drop (R21):** DECIDED — drop company_id index only; keep the account_id single index pending R26. Negligible benefit at current scale (110k rows); R26 will reshape the index plan space; revisit after R26 lands. See R21.
3. **saas_admin operating model (R7):** DECIDED — saas_admin operates via Django admin only; the app-side all-tenant account search branch is removed. Implemented inside R1's decorator. See R7 and PRODUCT_DECISIONS.md.
6. **event_import retirement scope (R2):** DECIDED — event_import deleted outright: app, endpoints, migrations, and the can_run_historical_event_import permission. Event data is retained. Any future tenant-facing importer is a fresh build, not a revival. See R2 and PRODUCT_DECISIONS.md.

### Open

4. **`can_view_all_accounts` / `can_view_all_events` (R35/R1):** complete the half-done migration (visibility helpers consult these permissions) or delete them and accept roles-as-visibility-tiers? Either is coherent; pick one before the decorator rollout cements it.
5. **COGS components fork (R31):** are raw materials/packaging modeled as Item rows with a `kind` flag, or as a separate Material model? Audit 01 calls this "the single biggest fork in the road" — it should be decided before any COGS table is created.
7. **Operational spend (R14/R17):** approve the Render backup-plan verification/upgrade if the tier lacks daily snapshots, the ~$1–7/mo cron job for nightly dumps, and the starter-tier staging service.
8. **The 4 duplicate account groups (R32):** merge them now using the existing (unused) merge tool, or leave until an account-data pass?

---

*This document supersedes nothing — the six audits hold the detail and 00 holds the production evidence; this file holds the decisions and the order. Update it as items complete or decisions land.*
