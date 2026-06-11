# Audit 5/6 — Code Structure & Maintainability

**Date:** 2026-06-11
**Scope:** Duplication, view complexity & logic placement, template/frontend organization, app boundaries, dead code & consistency, conventions/docs/tests.
**Method:** Read-only. Helper definitions diffed across apps for drift; statement-level similarity measured (difflib) on suspected twin functions; per-function line counts computed for all view files; inline JS/CSS quantified per template; cross-app import graph extracted; unreferenced-template scan; test-suite census.
**Builds on:** audits 01-04. Where the audits-02/03 structural recommendation (one permission+tenancy decorator + scoped managers + two-company test harness) also resolves a finding here, it is cross-referenced rather than re-proposed.

**Headline:** The codebase is **disciplined but duplicated**. Patterns are followed consistently (the filter pattern, the JSON-endpoint validation pattern, DB-side aggregation — audits 2-4 all found the conventions held); the problem is that the patterns propagate by **copy-paste, and the copies have started to diverge**. Measured: the three report views and their CSV twins are 60-66% identical (~600 duplicated statements — the report and its export can silently disagree); `distributor_po_save` vs `distributor_group_po_save` are **91% identical**; `_strip_excel_zip` exists twice and the copies **have already drifted** (one is None-safe, one crashes). On the frontend, ~4,900 lines of JS live inline in templates with **zero application JS files in static/**, and the forecast-grid CSS exists in two diverged dialects. Views are fat (top: 458 lines) but logic *extraction precedent* is good where it exists (forecast.py, order_generation.py). No commented-out code, no TODOs, almost no dead templates — the hygiene is real; the debt is structural duplication.

---

## SECTION 1 — DUPLICATION INVENTORY

### 1a. Duplicated Python

| # | What | Copies | Diverged? | Cost / recommendation |
|---|---|---|---|---|
| Py-1 | **Report view ↔ CSV-twin pairs** — filter parsing, route handling, distributor resolution, aggregation duplicated per pair: `account_sales_by_year`(287 stmts)↔`_csv`(197) **66% identical**; `report_item_sales_by_year`(279)↔`_csv`(191) **66%**; `report_account_distribution`(303)↔`_csv`(204) **60%** | 3 pairs ≈ **~600 duplicated statements** in reports/views.py (2,375 lines, 14 functions) | Structurally yes (the 34-40% delta is render-vs-write plus small behavioral differences — e.g. the CSV redirects where HTML renders no-data) | **Highest-value consolidation in the codebase**: extract a `build_<report>_data(user, filters) → rows/totals` function per report; HTML view renders it, CSV view writes it. Kills the "report says X, export says Y" bug class and makes the COGS-era reports build on functions, not view bodies |
| Py-2 | **`distributor_po_save` (151 stmts) ↔ `distributor_group_po_save` (164)** — **91% identical** (payload validation, status guards, SO assignment, line rewrite). The *template* side of this pair was already consolidated (recent "Unify PO modal" commits); the Python twin remains. Sibling: `po_modal_data`↔`group_orders_modal_data` 52% (less urgent) | 2 | Not yet — which is exactly the moment to merge | Extract `_save_po_set(request, company, target_distributor, payload, …)`; both endpoints become thin wrappers. A validation fix applied to one copy but not the other is a **financial-data** bug waiting to happen (these write POs/SO numbers) |
| Py-3 | **`_strip_excel_zip`** — imports/views.py:172 vs account_import_views.py:50 | 2 | **YES — already diverged**: the account-import copy is None-safe (`(value or '').strip()`), the sales-import copy throws on None | The proof-case for the drift class. Move to `utils/normalize.py` (which already exists for exactly this kind of helper) |
| Py-4 | `_format_quantity_cases` — distribution/views.py:92 vs production/views.py:38 | 2 | Identical today | Known from prompt; same `utils/` move |
| Py-5 | Temp-file trio: `_temp_import_dir/_save_temp_file/_cleanup_temp_file` (imports) vs `_inv_temp_dir/_inv_save_temp_file/_inv_cleanup_temp_file` (distribution) | 2×3 | Cosmetic naming divergence already | One `utils/uploads.py` |
| Py-6 | `_require_permission` (imports:60 vs event_import:40, different deny behaviors: redirect-to-access-denied vs message+redirect-dashboard) + the 6-variant gate-helper family (audit 03 P2/P13) | ~8 | Yes — deny UX differs per app | Resolved wholesale by the audits-02/03 **shared decorator** — this is the duplication that recommendation deletes |
| Py-7 | `_month_label`, `_parse_date`-family, CSV header parsing — small parse helpers re-rolled per import app | 2-3 each | Minor | Sweep into utils/ during the Py-3/4/5 move |
| Py-8 | Test fixture builders — `_make_company`, `_make_distributor`, `_make_supplier_admin`, `_make_item`, `_make_brand`, `_make_mapping` etc. redefined per test file (2-3 copies each, 23 files with hand-rolled setUp) | many | Each file's builders set slightly different defaults | A single `apps/core/test_utils.py` (or factories) — prerequisite for the audit-02 two-company harness anyway, which needs exactly these builders parameterized by company |

### 1b. Duplicated templates / fragments

| # | What | Evidence | Diverged? |
|---|---|---|---|
| T-1 | **Forecast grid CSS+markup** — distribution's `.forecast-table` system (distributor_list.html:7-20) vs production's `.forecast-grid` system (production_home.html:8-12) | Same visual language (status-colored cells, snapshot highlight, sticky layout) | **YES — diverged class vocabularies** (`forecast-green/yellow/red/no_data` vs `status-snapshot…`), padding 0.2 vs 0.25rem, min-width 42 vs 48px. Each future tweak must be made twice and already isn't |
| T-2 | **Filter modal markup** — 7 templates each carry a bespoke copy (account_list, event_list, distributor_list, production_home, 3 reports) | ARCHITECTURE.md explicitly designates account_list.html as the "reference template" — i.e., **copy-paste is the documented propagation mechanism**. Server side is properly shared (core/filters.py) | Per-page drift in markup/JS wiring is the known result; the bootstrap-modal JS is re-wired per page |
| T-3 | **Mobile-card + desktop-table dual rendering** — every list page renders each row twice (audit 04 F1 measured the cost on event_list) | event_list ×2 tabs ×2 layouts; account_list, reports same pattern | A row-partial (`{% include %}` with the two layouts in one place) halves the surface; full fix is audit 04 F1's rework |
| T-4 | `.po-table th.vertical-header` block and other per-page `<style>` blocks repeated in distributor_list (2 style blocks) and production_home | — | Minor |
| T-5 | Partials are underused: only 6 `_*.html` exist (one of them dead — §5a) against 18,173 template lines | — | — |

### 1c. Duplicated JS

| # | What | Evidence |
|---|---|---|
| J-1 | **CSRF/getCookie + fetch boilerplate in 13 templates** — each page that POSTs JSON re-declares cookie parsing/`X-CSRFToken` header wiring | 13 templates matched |
| J-2 | **The IIFE-per-template pattern at scale**: ~4,900 lines of inline `<script>` across 12 templates (dashboard 1,083; distributor_list 1,015; production_home 630; user_edit 425; event_form 368; account_sales_by_year 298; _contacts_modal 263; _notes_modal 239 (dead); event_detail 198; item_sales 167; account_distribution 118; resolve_mappings 105). **static/ contains zero application JS** (only vendored bootstrap + 69-line filters.css) | The save-PO modal logic, projection-tool logic, notes/contacts modal logic, debounce/tooltip wiring each live (and repeat) inline |
| J-3 | Modal open/close + tab-state-persistence wiring re-implemented per page | 5 templates instantiate `bootstrap.Modal` by hand; 10 hang work on DOMContentLoaded |

**Cost:** beyond drift, this is the home of the **bootstrap-load-order bug class** the team has hit repeatedly — inline scripts racing the vendored bundle, per-page re-wiring each getting the ordering subtly wrong, and *no test coverage able to see any of it* (§6c). Recommendation (no implementation): adopt per-page JS files in static/ (`static/js/<page>.js`) loaded via the existing `{% block extra_js %}`, one shared `app.js` for CSRF/fetch/debounce/modal helpers; this is mechanical extraction, not a rewrite.

---

## SECTION 2 — VIEW COMPLEXITY & BUSINESS-LOGIC PLACEMENT

### 2a. The fat views (measured, top of 25 ranked)

| View | Lines | Bundled responsibilities | Decompose? |
|---|---|---|---|
| distribution.**distributor_list** | **458** | FIVE tabs in one function (distributor table, forecast incl. group-mode + PO-additions simulation, inventory snapshots, PO projection tool w/ pagination, group panel) + session filter handling | Yes — one function per tab behind a dispatcher; the tabs already have disjoint context |
| reports.report_account_distribution / account_sales_by_year / item_sales_by_year (+3 CSV twins) | 363/353/336 (+253/249/235) | Filter parse + session + route resolution + aggregation + totals + render/CSV | Yes — via the Py-1 data-builder extraction (one move fixes both fatness and duplication) |
| accounts.**account_detail_combined** | 354 | Account header + items + recent events + full sales-tab analytics (yearly/monthly/eventcount matrices) | Sales-tab analytics → a builder shared with reports.account_detail_sales (317 — which is the *same analytics re-implemented*; cross-app near-twin of this view's sales tab) |
| production.production_home | 242 | Dashboard + forecast + snapshots + PO list (4 tabs) | Same shape as distributor_list, milder |
| imports.import_upload / _execute_import / import_preview | 221/183/114 | Multi-file parse, validation, session staging / the entire import engine / confirm+replace orchestration | `_execute_import` and `_replace_overlapping_months` are an **importer service** living in a views file — extract to `apps/imports/engine.py`; the audits-04 F5 fix lands cleaner there |
| events.event_list / event_export_csv | 198/168 | List + 9-filter handling + sidebar option computation / export re-deriving the same filters | Filter/sidebar machinery shared between the two; audit 04 F1 rework is the moment |

### 2b. Where the meaningful logic lives

**Properly extracted (the good precedent):**
- `distribution/forecast.py` (523) — forecast math, pure functions over querysets ✓
- `distribution/order_generation.py` (462) — PO generation algorithm ✓
- `imports/matching.py` + `utils/normalize.py` — account matching/normalization ✓
- `accounts/utils.py` (205) — coverage/visibility ✓; `core/filters.py` — session filters ✓; `events/storage.py` — photo storage ✓

**Embedded in views (should be extracted; COGS-relevant flagged):**
- **The sales-import engine** (`_execute_import`, `_replace_overlapping_months`, `_detect_overlap` — ~400 lines in imports/views.py). COGS/QuickBooks reconciliation will be built **on top of import semantics** — this is the highest-priority extraction for the costing work.
- **PO save/validation logic** (the 91% twins, ~350 lines, distribution/views.py) — POs are the COGS revenue-side anchor; `assign_so_number` is in models.py but its transactional usage lives in the view twins.
- **Report aggregation** (reports/views.py, ~1,400 lines of build logic) — COGS reporting will want these as composable data functions, not view bodies.
- **Recap→price propagation** (`_apply_price_updates` in events/views.py) — imported *from a views module* by event_import; it's a domain function in the wrong layer (goes moot if it moves when event_import retires, but the function itself should live in events/services or accounts).
- Inventory CSV validation (`parse_inventory_csv`/`validate_inventory_import` in distribution/views.py — already imported by tests as if it were a module API).

### 2c. Is there a pattern?

**Ad hoc by app generation.** Distribution (newest, most complex) extracts its hard logic (forecast, order_generation) and keeps endpoint plumbing in views — the right shape. Reports (older) keeps 100% of logic in views. Imports keeps the engine in views but extracted matching. The convention to write down (§6a): *"algorithms and multi-step engines live in a module; views parse, gate, call, render"* — the codebase already half-follows it.

---

## SECTION 3 — TEMPLATE & FRONTEND ORGANIZATION

### 3a. Template sizes (top, of 18,173 total lines)

| Template | Lines | Inline JS | Inline CSS | Verdict |
|---|---|---|---|---|
| distribution/distributor_list.html | **2,125** | 1,015 (48%) | 2 style blocks (~60) | The giant: 5 tabs + 2 modals + projection tool. Split per tab into partials + extract JS; pairs with the 2a view split |
| core/dashboard.html | 1,579 | **1,083 (69%)** | — | Mostly the account-search/notes/contacts SPA-ish panel; prime JS-file extraction |
| production/production_home.html | 1,289 | 630 | ~22 | Same shape as distributor_list, milder |
| events/event_detail.html | 1,074 | 198 | — | Recap forms; acceptable after the two above |
| reports/account_sales_by_year.html | 932 | 298 | — | Sort/filter JS shareable across the 3 report pages |
| core/user_edit.html | 765 | 425 | — | Coverage-area panel JS |
| events/event_list.html | 697 | 110 | — | Audit 04 F1 rework target |

**Partial extraction priority:** distributor_list (per-tab), dashboard (panels), the shared filter-modal fragment (T-2), a list-row partial for the dual-layout pattern (T-3).

### 3b. Inline JS — quantified

~4,900 lines inline across 12 templates vs **0 lines** of first-party JS in static/. At current scale it has already produced: the repeated bootstrap-load-order bugs (scripts racing the bundle, per-page re-wiring), 13 copies of CSRF boilerplate, and zero testability (no JS can be unit-tested from inside a Django template). The IIFE-in-template pattern was fine at 3 pages; at 12 pages and 5k lines it is the single largest unmanaged surface in the codebase. Extraction is mechanical (move to `static/js/<page>.js`, keep the `{% block extra_js %}` hook, pass server data via `json_script`), and unlocks linting at minimum.

### 3c. CSS

`static/css/filters.css` (69 lines) is the only shared app CSS — and it works (7 pages use it). Everything else is per-template `<style>` blocks, with the forecast grid duplicated-and-diverged (T-1). Recommendation: `app.css` for the forecast/status-cell/vertical-header vocabularies; keep page-specific oddities inline.

### 3d. Repeated UI patterns — DRY status

| Pattern | Server side | Markup/JS side |
|---|---|---|
| Filter modal (7 pages) | ✅ shared (core/filters.py, documented) | ❌ copy-paste by design ("reference template") |
| PO modal (single + group) | ❌ 91% duplicated views (Py-2) | ✅ recently consolidated (the part that was done) |
| Notes/contacts modals | ✅ shared partial (_contacts_modal included ×2) | ⚠️ _notes_modal partial is dead; live notes JS sits in dashboard.html |
| Mobile/desktop dual render | n/a | ❌ duplicated per page (T-3) |
| Status badges/colors | model property (Event.status_badge_class) ✓ | forecast colors duplicated (T-1) |

---

## SECTION 4 — APP STRUCTURE & BOUNDARIES

### 4a. The app split — sensible, two notes

The 10-app split maps cleanly to domains and audit 01's model groups. Observations:

- **distribution is the giant** (2,242-line views + 985 lines of algorithm modules + 18 migrations) but *cohesive* — it's the product's center of gravity, not a junk drawer. The §2a/3a splits address its bulk without moving boundaries.
- **sales** is a models-only app (no views/urls) — fine; it exists so SalesRecord doesn't live inside imports. **reports** is a views-only read-layer app — fine.
- **event_import** is a whole app for a retiring tool — its removal (audit 02 T9) deletes an app cleanly; the app boundary did its job.
- One misplacement inherited from audit 01: CoPacker lives in catalog for migration-ordering reasons (documented in its docstring) — cosmetic; revisit only if a production-domain restructuring happens for COGS.

### 4b. Cross-app imports

Import graph is hub-and-spoke around core/catalog/accounts/distribution (the kernel models) — healthy, no module-level cycles. The smells are **three view-layer cross-imports** (functions used as library code from another app's views module):

1. `core/views.py:25 ← apps.accounts.views._build_enhanced_coverage_areas`
2. `accounts/views.py:398 ← apps.events.views._get_visible_events` (deferred import to dodge the cycle — the cycle exists conceptually)
3. `event_import/views.py:31 ← apps.events.views._apply_price_updates`

Plus ~12 function-local deferred imports (accounts.utils ↔ events/sales, etc.) — each one is a marker of logic living in the wrong module rather than a true architectural cycle. All three named functions are domain/visibility logic that belongs in `utils.py`/service modules of their owning app; moving them dissolves the view-to-view dependencies without touching app boundaries.

### 4c. The core app — coherent kernel ✓

Company/User/RBAC models (184+51), auth+user-management views (419), forms (266), nav (210), session-filter utils (79), context processor, templatetags, two management commands. Nothing misfiled; nothing that belongs elsewhere lives here; the junk-drawer failure mode has not happened. (The only debatable resident is `filters.py` — shared UI machinery rather than kernel — harmless.)

---

## SECTION 5 — DEAD CODE & CONSISTENCY

### 5a. Dead code (beyond audits 01/03)

| Item | Evidence | Cost |
|---|---|---|
| `distributor == 'none'` filter branch + "(No distributor assigned)" dropdown option | accounts/views.py:132-133 + account_list.html:265 — impossible since Account.distributor went non-null (01 backlog #3b) | A UI option that can never match — confusing to users; S |
| `ImportBatch.Status.FAILED` and `HAS_UNMAPPED_ITEMS` | Never assigned anywhere (only PENDING→COMPLETE written); dev data: 74/74 complete (01 §4d) | Dead enum states imply error handling that doesn't exist; S |
| `templates/accounts/_notes_modal.html` (302 lines incl. 239 JS) | Zero references from any template or view (live notes UI is inline in dashboard.html) | Dead partial that *looks* like the canonical notes implementation; S |
| (Recap of already-found, for the cleanup PR): 13 dead permissions + dead authz branches (03 P4/P5), ImportBatch.brand (01 D17), DB_REVIEW.md stale (01 D30), event_import app (02 T9) | — | One consolidated cleanup pass |

No commented-out code blocks and zero TODO/FIXME/HACK markers in apps/ or templates/ — genuinely clean in that dimension.

### 5b. Naming/convention dialects

| Thing | Dialects found |
|---|---|
| Test files | `tests.py` vs `tests_*.py` (distribution/production) vs `test_*.py` (also distribution) vs `tests/` package (imports only) — **3.5 conventions** |
| Gate helpers | `_require_supplier_admin` (×3, two of which actually check permissions — 03 P13), `_require_permission` (×2, different deny UX), `_require_can_import`, `_require_inventory_permission`, `_require_production_permission`, `_require_account_access` |
| JSON success key | `{'success': True}` ×23 vs `{'ok': True}` ×8 |
| JSON error strings | 'Permission denied' / 'Permission denied.' / 'Forbidden' for the same 403 |
| Method guards | `if request.method != 'POST'` inline (majority) vs `@require_POST` (core) |
| Temp-file helpers | bare names vs `_inv_`-prefixed copies |

Each alone is cosmetic; together they mean **every app is its own dialect** for plumbing concerns — exactly the layer the audits-02/03 decorator + a tiny `json_error()/json_ok()` helper pair would standardize for free.

### 5c. Error handling & validation

- JSON endpoints: consistent *shape* (`{'error': msg}` + proper status codes) with cosmetic drift (above); payload validation is excellent and consistent in distribution/production (audit 02 §2), hand-rolled per endpoint elsewhere.
- HTML flows: forms for CRUD ✓; import flows use messages+redirect consistently ✓.
- Broad `except Exception` appears only at import-flow boundaries (with cleanup + user message) — defensible.
- No global JSON-error middleware/handler; a crash inside a JSON endpoint returns an HTML 500 page to a fetch() caller — minor, worth noting for the decorator work.

---

## SECTION 6 — CONVENTIONS & DOCUMENTATION

### 6a. Implicit conventions that should be written down

The codebase runs on ~8 strong implicit conventions (audits 2-5 verified they actually hold). One page each in ARCHITECTURE.md would make them transferable to future devs/sessions:

1. **Tenancy:** every queryset anchors on `request.user.company` / a scoped helper / a scoped parent (02 §1.0) — to be superseded by the decorator+managers.
2. **Gating:** per-app `_require_*` helper, first lines of every view (03 §2a) — ditto.
3. **JSON mutation endpoints:** parse → validate every payload id against company → `transaction.atomic` → `{'success': True}` (distribution is the reference implementation — 02 §2).
4. **Filter pattern** — already documented ✓ (the proof that documenting a pattern works here).
5. **Logic placement:** algorithms/engines in modules, views as plumbing (§2c — half-followed, worth making official).
6. **Decimal cases convention:** quantities stored as Decimal(10,6) cases everywhere; pallets derived at render (01 §1.3).
7. **Year/month integer-pair convention** for snapshots/POs (01 §6a) and the months-are-the-grain reality (01 D11).
8. **Import staging:** temp file + session pointer + re-validate-at-confirm (02 §1.1 imports) — the security-relevant part especially.

### 6b. Existing docs — currency check

| Doc | State |
|---|---|
| ARCHITECTURE.md (103 lines) | Current but covers exactly **one** pattern (filters). The natural home for §6a; today it under-sells how much convention exists |
| PRODUCT_DECISIONS.md (4,726 lines, 59 sections) | The de-facto architecture doc, session-log style — high value, hard to navigate; needs an index/التOC discipline more than a rewrite |
| REFACTORING_BACKLOG.md | Current and well-maintained ✓ (audit 01 verified all 7 items); the audits/ series now feeds it |
| DB_REVIEW.md | **Stale** (23 vs 33 models) — superseded by audits/01; mark or delete (01 D30) |
| DEPLOYMENT.md / README / replit.md | Audit 06's scope (note: run.sh contradiction found in 04 F2 suggests staleness) |
| audits/ 01-05 | Current by construction; §7 tables are the roadmap input |

### 6c. Tests — 1,337 tests / 22,867 lines

- **Strengths:** serious volume, endpoint-level coverage of the PO/forecast/import machinery (the 1,973-line tests_po_endpoints.py is why the JSON endpoints audit so cleanly), behavior-focused naming.
- **Gaps:**
  1. **No shared fixtures/factories** — `_make_company/_make_distributor/…` redefined per file (§1a Py-8). Every new test file pays setup tax; the two-company harness (02 T2) needs the shared version anyway.
  2. **Three-and-a-half file-naming conventions** (§5b) — trivial to standardize on `tests/test_<topic>.py` packages (imports/ already shows the pattern).
  3. **Single-company blindness** — effectively all tests build one company; zero cross-tenant assertions (02 T2's premise).
  4. **Zero JS/E2E coverage** — the ~4,900 inline-JS lines (incl. the PO modal, projection tool, recap flows) are untested; this is where the bootstrap-load-order regressions live and why the suite never catches them. Pragmatic recommendation at this scale: extract JS to files (§3b) + a *thin* Playwright smoke layer (login, each major page renders, one PO save round-trip, one recap save) — ~10 scenarios, not a pyramid.
  5. Growth hygiene: at 23k lines, adopt the targeted-run convention formally (per-app in dev, full suite in CI) and keep per-file runtime visible — the suite's value decays fast if it gets slow enough to skip.

---

## SECTION 7 — PRIORITIZED FINDINGS SUMMARY

Severity: HIGH = actively slowing development / bug-risking now; MEDIUM = maintainability drag; LOW = polish. **EFV** = enables-feature-velocity (makes COGS / sharing / tenant work materially easier).

| ID | Sev | EFV | Finding | Why it matters | Cost | X-ref |
|---|---|---|---|---|---|---|
| **C1** | HIGH | **EFV** | Report↔CSV twins: 3 pairs, 60-66% identical, ~600 duplicated statements (Py-1); same analytics re-implemented a third time in account_detail_combined's sales tab | Report-vs-export divergence is a silent-wrong-numbers bug class; COGS reporting needs these as data functions; also fixes 6 of the 8 fattest views | M | 04-F4, §2a |
| **C2** | HIGH | **EFV** | PO save twins 91% identical (Py-2) — financial-data writes maintained in two copies | Validation/SO-number fixes must be applied twice; the template half was already unified — finish the job before COGS builds on POs | S-M | 02 §2, 01-D5/D6 |
| **C3** | HIGH | **EFV** | ~4,900 lines inline JS, zero app JS files, CSRF boilerplate ×13, bootstrap-load-order bug class recurring and untestable (J-1/2/3, §3b, §6c-4) | The largest unmanaged surface; mechanical extraction unlocks linting, sharing, and the thin E2E layer | M | §6c |
| **C4** | HIGH | **EFV** | No shared test fixtures (Py-8) + single-company test blindness + 3.5 test-naming dialects | Every future test pays setup tax; blocks the 02-T2 two-company harness (the audit series' #1 structural safeguard) | S (fixtures) | 02-T2 |
| **C5** | MEDIUM | **EFV** | Import engine (~400 lines) lives in imports/views.py (§2b) | COGS reconciliation builds on import semantics; extraction also hosts the 04-F5 fix and future background-job move (04-F10) | S-M | 04-F5/F10 |
| **C6** | MEDIUM | EFV | Already-diverged small-helper copies: `_strip_excel_zip` (None-safety drift — Py-3), temp-file trio, `_format_quantity_cases`, `_require_permission` deny-UX drift | Proven drift, trivial consolidation into existing utils/ | S | — |
| **C7** | MEDIUM | EFV | Forecast CSS duplicated & diverged (`forecast-table` vs `forecast-grid` — T-1); filter-modal markup copy-paste ×7 by documented convention (T-2); dual-layout row markup per page (T-3) | Every UI tweak ×2-7; T-3 is half of 04-F1's payload | S-M | 04-F1 |
| **C8** | MEDIUM | — | Fat tab-dispatcher views: distributor_list 458 (5 tabs), production_home 242, account_detail_combined 354 | Change amplification + merge-conflict magnets; split per tab alongside template partials | M | §2a |
| **C9** | MEDIUM | — | Three view-layer cross-imports (`_get_visible_events`, `_build_enhanced_coverage_areas`, `_apply_price_updates`) + ~12 deferred-import cycle dodges | Visibility/domain logic trapped in view modules; moving to owning apps' utils dissolves the conceptual cycles | S | §4b |
| **C10** | MEDIUM | — | Plumbing dialects: 6 gate-helper names (2 lying), success/ok JSON split, 3 flavors of 403 strings, mixed method guards | Cognitive tax per app; standardized for free by the 02/03 decorator + a json_ok/json_error pair | S (with decorator) | 03-P2/P13 |
| **C11** | LOW | — | Dead code sweep: `distributor='none'` branch + UI option, FAILED/HAS_UNMAPPED_ITEMS never set, _notes_modal.html (302 lines), + audits' prior items batched | One half-day cleanup PR; the 'none' option is user-visible confusion | S | 01-D17/D30, 03-P4/P5 |
| **C12** | LOW | EFV | Implicit conventions undocumented (8 listed §6a); ARCHITECTURE.md covers 1 of them; PRODUCT_DECISIONS.md unnavigable at 4,726 lines | The conventions held because one author held them; tenants-era contributors (and future sessions) need them written | S | all audits |
| **C13** | INFO | — | Positives to preserve: zero commented-out/TODO debt; coherent core app; clean app boundaries (incl. models-only sales, read-only reports); algorithm extraction precedent (forecast/order_generation); filter pattern proves documentation works here | The refactor should consolidate around these, not restructure them | — | — |

**Counts: 4 HIGH · 6 MEDIUM · 2 LOW · 1 INFO — 13 findings.**

**Suggested sequencing note for the Phase-2 synthesis:** C4 (shared fixtures) → unblocks 02-T2 harness; C2+C5 (PO/import extraction) → before COGS foundations; C1 (report builders) → with or before COGS reporting; C3+C7 (frontend extraction) → pairs naturally with 04-F1's event-list rework; C10-C12 ride along with the decorator work already recommended by audits 02/03.

### Cross-reference
02-T2/§3c & 03-P2/§4c (decorator+managers+harness) ← resolves C4-enablement, C10, parts of C6; 04-F1 ← T-3/C7; 04-F4 ← C1; 04-F5/F10 ← C5; 01-D5/D6 (SO-number race + clean()-bypass) ← lands inside C2's extraction; 01-D17/D30, 03-P4/P5 ← batched in C11; 02 §2's "distribution is the reference implementation" ← C13.
