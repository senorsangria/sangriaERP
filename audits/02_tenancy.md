# Audit 2/6 — Multi-Tenancy: Isolation, Enforcement & Onboarding

**Date:** 2026-06-11
**Scope:** (A) Correctness — can data leak or be mutated across tenant boundaries today? (B) Structure — is tenancy enforced by construction or convention? (C) What does onboarding a tenant take?
**Method:** Read-only. Every URL pattern in every app enumerated (142 application endpoints + Django admin + media serving); every view's querysets traced to their company anchor; all `objects.get(pk=…)` / `get_object_or_404` call sites inspected; forms, admin registrations, management commands, templatetags, storage, and middleware reviewed. No raw SQL exists anywhere in `apps/` (verified — zero `.raw()` / `connection.cursor()` outside this audit's own queries).
**Builds on:** `audits/01_domain_model.md` — model inventory (§1), company-FK chains (§2a), seeded-migration finding D3, RBAC-global finding D13, sharing assessment (§6c). Not repeated here.

**Headline:** One **confirmed cross-tenant read+write chain** exists (the historical event import tool — CRITICAL, exploitable by the exact role external tenants will hold). Everything else in the 142-endpoint census is correctly scoped **today** — but scoped purely **by convention**: ~150 hand-written `company=`/helper filters with no structural mechanism, no cross-tenant test coverage, and a dev database with only one company, so nothing would catch the next missed filter. The Django admin and the media URL space sit outside the tenancy model entirely and are mitigated only by operational facts (one staff user; UUID filenames).

---

## SECTION 1 — THE SCOPING CENSUS

### 1.0 How scoping works (the three anchors)

Every correctly-scoped view in the codebase reaches tenant safety through one of three anchors:

1. **Direct filter:** `company=request.user.company` in the queryset or `get_object_or_404(Model, pk=…, company=request.user.company)`.
2. **Scoped helper:** `get_accounts_for_user(user)` / `get_distributors_for_user(user)` (accounts/utils.py — both company-filtered, coverage-aware) or `_get_visible_events(user)` (events/views.py:110 — starts from `Event.objects.filter(company=company)`, returns `none()` when company is NULL).
3. **Scoped parent:** child fetched via an already-verified parent (`get_object_or_404(AccountContact, pk=cpk, account=account)` where `account` was company-checked; `DistributorPO.objects.get(pk=po_id, distributor=distributor)` where `distributor` was company-checked).

Ownership-scoping (`created_by=request.user`) is used by the routes app — stronger than company-scoping.

### 1.1 Census tables

Classification: **SCOPED** (every queryset anchored as above) / **GATED** (auth/permission only, scoping indirect or unverifiable) / **UNSCOPED** (can read or write another company's rows) / **N/A** (no tenant data). Suspect lines quoted for anything not cleanly SCOPED.

#### core (14 endpoints)

| View | Class | Notes |
|---|---|---|
| login, logout, password_reset_stub, access_denied, save_admin_tools_state | N/A | Auth/session only |
| dashboard | SCOPED | Search via `get_accounts_for_user` (helper). saas_admin sees ALL companies' accounts here — see §4c |
| profile, profile_edit, password_change | SCOPED | Self only |
| user_list | SCOPED | `_get_visible_users` — `User.objects.filter(company=u.company)` per role; saas_admin all (by design) |
| user_create | SCOPED | Form forces `user.company = creator.company` for non-saas creators (core/forms.py:110); saas_admin picks company |
| user_edit | SCOPED* | Global pk fetch (views.py:266) **then** `_can_manage_user` company check. *One soft edge: the ambassador_manager rule (`target.created_by_id == manager.pk`, views.py:53-57) has **no company term** — only matters if a user is ever moved between companies (no UI for that; admin-only). Theoretical → finding T13 |
| user_deactivate, user_password_reset | SCOPED* | Same pattern + same soft edge |

#### catalog (10 endpoints)

| View | Class | Notes |
|---|---|---|
| brand_list/create/detail/edit/toggle | SCOPED | All `company=request.user.company` |
| item_create/edit/toggle/move_up/move_down | SCOPED | Brand company-checked, item fetched `brand=brand`; ItemForm scopes co_packer to `brand.company` (catalog/forms.py:91) |

#### distribution (26 endpoints) — all SCOPED

The highest-risk surface (pk-addressed JSON modal/save/suggest/delete endpoints) and the cleanest implementation in the codebase:

| View | Anchor |
|---|---|
| distributor_group_list/create/edit/delete | `company=` direct |
| distributor_list (+forecast tab) | `company=` direct on every branch incl. group forecast (`DistributorGroup.objects.filter(company=company, pk=group_pk)`, views.py:588) |
| distributor_create/edit/detail/toggle | `company=` direct |
| distributor_order_profile_save, distributor_safety_stock_save | Distributor company-checked; profiles via scoped parent; item list `brand__company=` |
| inventory_upload/preview/confirm | Confirm **re-parses the temp file and re-validates distributors/items against `request.user.company`** (views.py:1283-1290) — session contents can't smuggle foreign rows |
| inventory_bulk_delete | `filter(pk__in=ids, distributor__company=company)` — payload ids constrained in the same query (views.py:1385) |
| distributor_po_modal_data/suggest/save/delete | Distributor company-checked; **save validates every payload item id against `brand__company` and every PO id against the distributor+month** (views.py:1552-1570) — the model JSON-endpoint pattern |
| group PO modal/suggest/save | Group fetched `pk=group_pk, company=company`; POs via `distributor=primary` |
| save_forecast_inventory | Payload item ids checked against `company_item_ids` set before update (views.py:2092-2106) |
| toggle/bulk_toggle/move PO | All `filter(pk[__in]=…, distributor__company=company)` — scoping inside the UPDATE itself |

#### production (9 endpoints) — all SCOPED

Every fetch carries `company=company` (ProductionPO) or `brand__company`/`po__distributor__company`; `production_po_save` validates payload co_packer ids and item ids against the company (views.py:706, 715) like the distribution endpoints.

#### accounts (22 endpoints) — all SCOPED

Every one of the 13 pk-addressed views fetches `Account` with `company=request.user.company`; notes/contacts fetched via the scoped account (`pk=npk, account=account`); coverage endpoints check both the target user and distributor/account against the company (views.py:914-989, 1026-1029); the 4 AJAX endpoints filter `company=` or use `get_accounts_for_user`. `account_bulk_delete` constrains payload pks in-query (`filter(pk__in=pks, company=company)`, views.py:868). One *intra-company* note: `account_detail` checks company but not coverage — any company user with `can_view_accounts` can open any company account by pk. That is an authorization-design question for Audit 3, not a tenancy leak.

#### events (24 endpoints) — all SCOPED (one fragility noted)

All 19 pk-addressed views fetch from `_get_visible_events(request.user)` (company-filtered at source); 16 add a redundant explicit `company=company` — good defense-in-depth. save-recap/submit-recap/unlock-recap (views.py:1293, 1318, 1350) omit the redundant `company=` but the `visible` queryset already carries it. Child objects (photos, expenses, item recaps) fetched via the scoped event. Recap item rows iterate `event.items` (M2M set at creation from company-scoped EventForm choices). AJAX endpoints scope via `company=` + `get_accounts_for_user`.

**Fragility (finding T3):** `account_detail_combined`'s sales-tab event counts (accounts/views.py:524, 535) and reports' `account_detail_sales` (reports/views.py:2140, 2152) and `get_account_associations` (accounts/utils.py) query `Event.objects.filter(account=account)` with **no company term** — safe only while the invariant `event.company == account.company` holds. The event-import hole below is precisely what breaks that invariant.

#### imports (14 endpoints) — all SCOPED

Upload/preview/confirm re-resolve distributors from the file against `company` at execute time (views.py:580-582); `_replace_overlapping_months` deletes only `company=company` rows (views.py:733, 752); `batch_delete` derives all account ids from a `company=company` query before pk-addressing them (views.py:1174-1186); `bulk_save_mappings` validates each posted distributor and item against the company (views.py:1386-1387). Account-import execute checks `pk=row['existing_pk'], company=company` on updates (account_import_views.py:390-391).

#### reports (11 endpooints) — SCOPED except one input gap

All five report families anchor on `get_distributors_for_user` + `get_accounts_for_user` + `account__company=user.company` on SalesRecord, and route filters check `created_by=request.user`. Two exceptions:

- **T7 (gap):** `report_account_distribution` accepts `?items=<comma-ints>` and stores them in session **unvalidated** (reports/views.py:1657-1664); both the HTML view and the CSV export then run `Item.objects.filter(pk__in=selected_item_ids)` (views.py:1984) **without a company filter** to print item names in the report/CSV metadata (`# Selected items: …`). Foreign pks contribute nothing to the *numbers* (the sales base query is company-scoped, so foreign item ids match zero rows) — but the **names of another tenant's items (and their existence) leak** by pk probing.
- account_detail_sales / account_portfolio_json: company-checked + coverage-checked ✓; their event-count subqueries are invariant-reliant (see T3).

#### routes (4 endpoints) — all SCOPED

Ownership-scoped (`created_by=user`) for route fetch/list; account additions verified `company=user.company` (views.py:104); `remove_account_from_route` fetches RouteAccount by bare pk **but** then requires `route.created_by == request.user` (views.py:163-167) — owner check subsumes company check.

#### event_import (8 endpoints) — **2 UNSCOPED (confirmed chain)**

| View | Class | Detail |
|---|---|---|
| event_import_upload | SCOPED | Match candidates built from `company=request.user.company` accounts |
| event_import_review | SCOPED | Renders session data |
| **event_import_confirm** | **UNSCOPED (write of tainted data)** | For "review" items, accepts `match_<csv_key>=<int>` from POST and stores it as an account pk with **no validation of any kind** — not against the candidate list, not against the company (views.py:349-356) |
| **event_import_execute** | **UNSCOPED (cross-tenant write)** | `account = Account.objects.get(pk=account_pk)` — **no company filter** (views.py:461) — then `Event.objects.create(company=request.user.company, account=account, …)` + EventItemRecaps (views.py:521-553) |
| **event_import_export_csv** | **UNSCOPED (cross-tenant read)** | `Account.objects.filter(pk__in=confirmed_pks)` — **no company filter** (views.py:600) — then writes `account.name`, `account.street`, `account.city` into the downloaded CSV (views.py:622-626) |
| event_import_delete_all / delete_batch | SCOPED | `company=request.user.company` |
| event_import_validate_csv | SCOPED | `company=` on all account queries |

#### Out-of-band surfaces

| Surface | Class | Detail |
|---|---|---|
| **Django admin** (`/admin/`) | **UNSCOPED by design** | 11 ModelAdmins registered (Company, User, Brand, Item, Distributor, Account, UserCoverageArea, Event, ImportBatch, ItemMapping, SalesRecord) — standard ModelAdmins, **no company filtering on changelists or FK dropdowns**. Only mitigation: `is_staff` is set exclusively by the `create_saas_admin` command — no form or view in the app can grant it. See T4 |
| **Media serving** (`/media/…`) | **GATED by URL secrecy only** | `django.views.static.serve` mounted with **no authentication** whenever object storage is off (producterp/urls.py:29-36); in production, files go to Cloudflare R2 and are "served from R2 directly". Protection is solely the uuid4-hex filename (`events/{event_pk}/{uuid4hex}.jpg`, events/storage.py:18) — unguessable but unrevocable, unauthenticated, and tenant-blind. Receipts (expense photos) live here. See T6 |

### 1.2 Census totals

| Class | Count |
|---|---|
| SCOPED | 132 |
| N/A (auth/static/self-only) | 7 |
| UNSCOPED (event_import chain) | 3 |
| Input-gap (reports `?items=`) | 1 (counted in SCOPED for queryset shape; flagged T7) |
| Out-of-band (admin, media) | 2 surfaces |

### 1.3 Exploitability — confirmed vs theoretical

**CONFIRMED — the event-import chain (T1).** An authenticated user holding `can_run_historical_event_import` — granted to **supplier_admin** (core/migrations/0009), i.e. **every external tenant's admin** — can:

1. Upload a crafted CSV whose locations don't auto-match (trivially arranged), producing "review" items.
2. POST `match_<csv_key>=<foreign-account-pk>` at the confirm step (pks are sequential integers — dev runs ~14,000-17,000; enumeration is trivial).
3. **Read:** download the export CSV → another tenant's account **name, street address, and city** for every probed pk. Repeatable across the whole pk space.
4. **Write:** execute the import → `Event` rows created with `company=attacker` but `account=<victim's account>`, plus EventItemRecap children.

Victim-side blast: the victim **cannot see or delete** these events (event lists are company-filtered), but the invariant-reliant queries from T3 *do* count them — the victim's account-detail sales tab event counts become wrong, and `get_account_associations` will block/deflect the victim's account deletion because of phantom "associated events" they cannot inspect. Attacker-side, the foreign account's name renders in the attacker's own event list — a second read channel. Today, with one tenant, there are no victim rows — this is a **structural breach confirmed by code, not yet a data incident**. It must be closed before tenant #1.

**THEORETICAL (require operator action or future drift):**
- T13: `_can_manage_user`'s ambassador_manager rule lacks a company term — exploitable only if a user is moved across companies via the admin.
- T3: every invariant-reliant `Event.objects.filter(account=…)` query becomes a leak the moment any code path (T1 is one) writes a cross-company event.
- T4: any future grant of `is_staff` to a tenant user = total cross-tenant access in one step.

---

## SECTION 2 — MUTATION PATHS

Verdict per write path (does it verify the **target** belongs to the user's company before writing?):

| Mutation path | Verified? | Evidence |
|---|---|---|
| Distributor PO save (single + group) | ✅ | Distributor/group company-checked; payload item ids validated `brand__company`; payload PO ids validated against distributor+month; deletes restricted to PROJECTED status (distribution/views.py:1552-1576) |
| Distributor PO delete | ✅ | `get_object_or_404(DistributorPO, pk=po_pk, distributor=distributor)` after company-checked distributor |
| PO toggle/bulk-toggle/move | ✅ | Scoping inside the UPDATE queryset itself — strongest form |
| Production PO save/delete | ✅ | Same pattern incl. payload co_packer/item validation |
| Inventory snapshot create (confirm) | ✅ | Full re-validation against company at execute time |
| Inventory snapshot bulk delete | ✅ | `filter(pk__in=ids, distributor__company=company)` |
| Own-inventory upsert/bulk delete | ✅ | `update_or_create(company=company, …)`; delete filtered `company=company` |
| Sales import execute + replace | ✅ | Distributors re-resolved per company; replace deletes filter `company=company`; batch stats on own batch |
| Sales batch delete | ✅ | Batch company-checked; cascaded account deletes derived from company-scoped set |
| Account create/edit/toggle/delete/bulk-delete | ✅ | All company-anchored; bulk constrains pks in-query |
| Account merge | ✅* | **Admin-only** (no app UI; `merged_into`/`merge_note` editable solely in AccountAdmin). *Admin FK dropdown for `merged_into` is unscoped — an operator can merge across companies by mistake (T4 footgun) |
| Item forecast-inventory save | ✅ | Payload ids checked against company set |
| Safety-stock / item-profile save | ✅ | Scoped parent distributor |
| Account import execute | ✅ | Creates with `company=company`; updates check `pk, company=company` |
| Coverage area add/remove | ✅ | Target user, distributor, account all company-checked |
| Notes/contacts CRUD | ✅ | Via scoped account |
| Event status transitions (10 endpoints) | ✅ | Via `_get_visible_events` + `company=` |
| Recap save/submit + price updates | ✅ | Event scoped; `_apply_price_updates` writes via `event.account` |
| Expense add/delete, photo delete | ✅ | Via scoped event |
| User create/edit/deactivate/password | ✅* | `_can_manage_user`; T13 soft edge |
| Route save/remove | ✅ | Ownership + account company check |
| **Historical event import execute** | ❌ | **T1 — writes Events referencing unverified foreign Accounts** |
| Historical import delete-all/delete-batch | ✅ | `company=` filtered |

**One path fails. Every other mutation path independently verifies its target.** Notably, the *best* patterns (scoping inside the UPDATE, payload-id validation sets) already exist in-house — distribution/views.py is the internal reference implementation.

---

## SECTION 3 — STRUCTURAL ENFORCEMENT ASSESSMENT

### 3a. What exists today

| Mechanism | Status |
|---|---|
| Middleware setting a request company / ambient tenant | ❌ none (settings.py:103-112 — stock Django stack) |
| Company-scoped model managers | ❌ none (`Account.active_accounts` filters is_active/merged — **not** company) |
| Base view classes / mixins / decorators enforcing company | ❌ none (`_require_supplier_admin`-style helpers check **permissions**, not scoping) |
| Scoped query helpers (opt-in) | ✅ 3: `get_accounts_for_user`, `get_distributors_for_user`, `_get_visible_events` |
| Form-level scoping | ✅ consistent: every ModelForm takes `company=` and filters FK choices (verified: catalog, accounts, events, imports, distribution forms) |
| DB-level enforcement (RLS, constraints on company-pair consistency) | ❌ none |
| Cross-tenant tests | ❌ none found (and dev data has 1 company — audit 01 D31 — so even accidental coverage is impossible) |

**Scoping is 100% convention.** The convention is strong — 132/135 data endpoints comply, and the JSON endpoints show real care — but it is re-implemented by hand at every call site (~150 `company=`-bearing filters counted in views alone).

### 3b. The gap

How a future leak happens, concretely: a developer adds a modal endpoint and writes `Item.objects.get(pk=request.POST['item_id'])` instead of adding `brand__company=…`. Nothing fails. No test catches it (no two-company fixture exists). Dev data can't catch it (one company). Review must catch a *missing* term — the hardest kind of omission to see. The event_import app demonstrates this is not hypothetical: it is the newest standalone import tool, written against a single-tenant assumption, and it shipped the exact bug class. The codebase is 14.7k view-lines and growing; with COGS/BOM features next, the surface expands precisely where money data lives.

A second, subtler gap: **cross-FK company-pair invariants are enforced nowhere** (DB or model layer). `event.company == event.account.company`, `account.company == account.distributor.company`, `accountitem.account.company == accountitem.item.brand.company` are all true today (audit 01 §4d verified 0 mismatches) but only because every view happens to construct them correctly. T1 shows one constructor that doesn't.

### 3c. Recommendations (not implemented — assessed for THIS codebase)

| Option | What it is | Coverage | Migration cost | Verdict |
|---|---|---|---|---|
| **1. Two-company test harness** | Pytest fixture with company A + B fully populated; parametrized tests hit every pk-addressed endpoint (this audit's census is the endpoint list) with B's pks under A's session, asserting 404/403; plus an invariant checker (the audit-01 §4d company-pair queries) run in CI | All 142 endpoints, regression-proof | **S-M** (fixture + ~1 parametrized test per app; census table = test spec) | **Do first, BEFORE TENANT #1.** Converts convention into a tested property without touching production code |
| **2. Explicit scoped managers** (`Model.objects.for_company(c)` / `.for_user(u)`, with plain unscoped access lint-flagged) | Greppable, no magic, incremental adoption; the three existing helpers already are this pattern for Account/Distributor/Event — extend to Item, DistributorPO, SalesRecord, ImportBatch, ProductionPO | The 16 company-FK models + chain models from audit 01 §2a | **M** (~150 call sites, mechanical, can be done app-by-app; new code adopts immediately) | **Adopt as the standing convention.** The codebase's existing helper style shows the team already thinks this way |
| 3. View decorator (`@company_scoped` injecting `request.company`, asserting non-null) | Normalizes the `company = request.user.company` boilerplate; handles the saas_admin NULL-company branch in one place | All views | S | Worth doing opportunistically; does **not** prevent unscoped querysets, so it complements (not replaces) 1+2 |
| 4. Ambient tenant (thread-local/contextvar default manager filtering, set by middleware) | By-construction safety; unscoped query becomes impossible to write | Everything | **L and risky** — breaks Django admin, shell, management commands, cross-tenant saas_admin paths, and the import flows that legitimately construct objects for an explicit company; retrofitting onto 14.7k view-lines invites subtle breakage | **Not recommended now.** Re-evaluate if tenant count grows past the single-digit range or after the codebase consolidates on option 2 |
| 5. Postgres RLS | DB-enforced isolation keyed on a session GUC | All tables with a company chain | L (requires per-request SET, connection pooling care, and a company column or chain on every table — audit 01 §2a chains make several tables RLS-awkward) | Overkill for 1-5 cooperating tenants; revisit for distributor-as-tenant sharing where visibility ≠ ownership anyway |
| 6. Lint guard | CI grep: `objects.get(pk=` / `get_object_or_404(<TenantModel>, pk=` without a company/scoped-parent kwarg in the same call | pk-fetch class of bug only | S | Cheap tripwire; pairs with option 1 |

**Recommended package: 1 + 6 now (BEFORE-FIRST-TENANT), 2 + 3 as the standing pattern for new code with incremental backfill.** Skip 4 and 5 at this scale.

### 3d. Uniqueness scoping re-verified (audit 01 §3d cross-check)

Re-confirmed against current models — all business uniqueness is company-scoped directly or transitively; **no accidentally-global business constraint exists**, so two tenants picking the same brand/item-code/distributor-name cannot collide at the DB level. The day-one correctness items are the *missing* (not mis-scoped) constraints from audit 01: Distributor has **no** (company, name)/(company, code) uniqueness (D8) and Account has none (D7) — with multiple tenants importing data these become per-tenant data-quality bugs, not cross-tenant ones. Deliberately global and correct: Role/Permission codenames, Company.slug. The structural consequence of global Role/Permission (audit 01 D13) is sharper in tenant terms: **role definitions and permission grants are platform-wide** — an operator editing supplier_admin's permission set for one tenant's request changes every tenant simultaneously, and per-tenant custom roles are impossible without schema change. No role-editing UI exists (changes go through migrations/admin), which makes this safe-but-rigid today.

---

## SECTION 4 — SHARED-RESOURCE & SEEDED-DATA REVIEW

### 4a. Seeded/shared data — full inventory

| Source | What it creates | Blast radius | Verdict |
|---|---|---|---|
| `catalog/0007_seed_co_packers_all_companies` (audit 01 D3) | CoPackers 'Brotherhood Winery' + 'Nidra Packaging' | **Every company existing at migrate time.** Already ran in dev/prod (existing companies have them). Companies created *after* the migration (i.e., all future tenants on the live DB) are NOT seeded — so the live-DB risk is historical, not ongoing. The ongoing risk is fresh environments (CI, staging, DR-rebuild) where companies created before `migrate` (or a future squash re-run) get one tenant's vendors | Confirmed; risk profile refined: **process wart + fresh-env contamination**, not a live-DB onboarding bug |
| `catalog/0006_seed_co_packers` | Same vendors, only for a company named "drink up life" | No-op on any DB lacking that name | Hardcoded customer name in code (audit 01 D3) |
| `core/0004` + 9 follow-up RBAC migrations | Global Permissions/Roles | All tenants, by design | Correct (shared vocabulary), see 3d rigidity note |
| `seed_data` management command | Company "Drink Up Life, Inc" + brands "Señor Sangria"/"Backyard Barrel Co" + 9 SKUs | Only if an operator runs it; `get_or_create` keyed on slug — idempotent, creates exactly the dev tenant | Dev tool; harmless if never run in prod, **dangerous if run in prod by habit** (creates a real-looking tenant). Label it dev-only |
| `create_saas_admin` command | One `is_staff=True` saas_admin user (company=NULL) | Platform operator | Correct; this is the only is_staff path |
| **event_import app (NEW finding T10)** | — | The whole tool is **hardwired to one tenant's catalog**: CSV column map keys like `bottles sold bwred0750` (views.py:91-104) and `ITEM_CODES = ['BWRed0750', …]` (views.py:398) | Not a leak (item lookup is company-scoped at execute, foreign codes simply skip) — but a single-tenant tool surfaced in the nav of **every** tenant's supplier_admin (permission granted role-wide). For any other tenant it silently imports events with zero item recaps. Combined with T1 this app is the weakest code in the codebase: **recommend gating or retiring it before onboarding** |
| `Item.forecast_current_inventory` etc. defaults | — | Per-company data, no sharing | Fine |
| `DistributorItemProfile.get_or_create` patterns | Per-(distributor,item) rows | Distributor is company-owned → no cross-company creation | Fine |

### 4b. Global settings/flags that should be per-tenant

- **`Company.so_sequence_start` default 2006** — a Señor-Sangria-specific number baked into the schema default (core/models.py:37-40). Every new tenant silently starts SO numbers at 2006 unless the operator remembers to change it. Cosmetic but visibly "someone else's sequence" on day one. (LOW; onboarding checklist item.)
- **No feature-flag or per-tenant-settings table exists.** All behavior is uniform across tenants. For 1-5 cooperating tenants this is fine; note that the *first* tenant-specific request ("we date POs differently") currently has nowhere to land but code.
- Everything else in settings.py is infrastructure (DB, storage, static) — correctly global.

### 4c. User ↔ Company

- **One company per user, period** (single nullable FK). No membership table, no switching UI.
- **NULL company = saas_admin** (one user, created by command). The NULL-company behavior is **inconsistent across helpers** — this is the notable finding (T5):
  - `get_accounts_for_user`: saas_admin → `Account.active_accounts.all()` — **all tenants** (accounts/utils.py:59-61). Dashboard search and `ajax_accounts_search` therefore expose every tenant's account names/addresses to the saas_admin session — *intended* operator behavior, but the same saas_admin then 404s on `account_detail` (which filters `company=request.user.company` → NULL). 
  - `_get_visible_events`: NULL company → `Event.objects.none()` — saas_admin sees **no** events.
  - `get_distributors_for_user`: NULL company → `none()` — no distributors, so reports render empty.
  - Most detail/CRUD views: `company=NULL` filter → 404 on everything.
  - Net: the saas_admin role half-works by accident. Decide the operator-access model deliberately (probably: saas_admin operates via Django admin only, and the app-side "all companies" branch in `get_accounts_for_user` should be **removed** for consistency — it is the only app-layer cross-tenant read path that exists by design).
- **Moving a user between companies** (admin-only operation) dangles: UserCoverageArea rows keep the old `company` (they become silently inert — fail-closed, good); created events/notes/POs keep `created_by`/`ambassador` FKs to the moved user (old tenant's pages then render a now-foreign user's name — cosmetic cross-tenant name exposure); `_can_manage_user`'s T13 edge activates. No code supports this operation — recommend declaring it unsupported (deactivate + recreate instead).

---

## SECTION 5 — TENANT ONBOARDING MECHANICS

What "add a tenant" takes **today**, end to end (verified against code — no provisioning command exists):

| Step | How today | Sharp edges |
|---|---|---|
| 1. Create Company | Django admin only (no app UI, no API) | Must remember to set `so_sequence_start` (default 2006 = another tenant's sequence, T12). Slug auto-generates ✓ |
| 2. First admin user | saas_admin via `/users/create` (form shows company picker for saas_admin ✓) or Django admin | If via Django admin: roles M2M must be hand-assigned; password set flow manual |
| 3. Roles/permissions | Nothing to do — global, already seeded ✓ | Rigidity: tenant gets exactly the standard role set (3d) |
| 4. Brands + Items | Tenant admin via `/brands` UI ✓ | Item production fields (cases_per_pallet, cases_per_batch, safety stock) are per-item hand-entry |
| 5. **Co-packers** | **No UI exists and CoPacker is not registered in Django admin** — shell/seed-migration only (verified: zero CoPacker views/urls; not in any admin.py) | **Hard blocker for any tenant using production features.** ItemForm offers a co_packer dropdown that can never be populated by the tenant (T11) |
| 6. Distributors | Tenant admin via `/distributors` UI ✓; codes auto-generate ✓ | No (company,name) uniqueness — typo dupes possible (audit 01 D8) |
| 7. Item mappings | Created inline during first sales/inventory import ✓ | Smooth |
| 8. Accounts + sales history | Account import + sales import UIs ✓ | Import flows are supplier_admin-gated ✓; replace-on-import semantics need operator explanation |
| 9. Users/coverage | `/users` UI + coverage tab ✓ | Coverage requires distributors to exist first (FK non-null) |
| 10. Media/storage | Nothing per-tenant — shared bucket | Tenant files distinguishable only by event ownership (T6) |

**Assessment:** Steps 4, 6-9 are genuinely self-service. The gaps: company creation is operator-manual with a misleading default (1); **co-packer management is impossible without developer intervention (5)**; and there is no checklist/automation, so each of the 1-5 onboardings is an artisanal exercise. 

**Recommendations (S/M sizing):**
1. A `provision_tenant` management command (company + so_sequence_start prompt + first admin user + welcome email stub) — **S**, removes the two manual-admin steps and the T12 default trap.
2. CoPacker CRUD UI (mirror the Brand pattern — list/create/edit/toggle, supplier_admin-gated) or at minimum register CoPackerAdmin — **S**, unblocks production features for tenants. (Aligns with audit 01 §2d recommendation 1.)
3. Onboarding checklist doc per tenant (which imports, in what order, what to verify) — **S**.
4. Decide and document the saas_admin operating model (§4c) before granting anyone else operator access — **S** (policy).

---

## SECTION 6 — PERFORMANCE ISOLATION (light pass; deep analysis → audit 04)

From audit 01 §4b's real-index inventory, assessed for the tenancy dimension only — "does tenant A's volume sit in tenant B's query path?":

| Table | Tenant-query index shape | Verdict |
|---|---|---|
| sales_salesrecord (93k, growth-dominant) | `(company_id, sale_date)` composite — company-leading ✓; account/item composites lead on company-owned objects ✓ | Isolated. A tenant's report touches only its index range |
| accounts_account | `company_id` btree ✓ (+ per-company filters always present) | Isolated. Caveat: import matching scans normalized-address columns *within* a company filter — fine |
| events_event | `company_id` ✓ | Isolated |
| distribution_inventorysnapshot / distributorpo | Lead on `distributor_id` (company-owned) ✓ | Isolated |
| accounts_accountitem | Leads on `account_id` (company-owned) ✓ | Isolated |
| imports_itemmapping | Unique leads on `company_id` ✓ | Isolated |

**No table has an index shape that forces cross-tenant scans for tenant-scoped queries.** The shared-fate risks are infrastructure-level, not index-level: one Postgres instance (a tenant's 500k-row import competes for the same buffer pool/IO), one app process pool, and the redundant single-column indexes on SalesRecord (audit 01 D16) amplifying *everyone's* write cost. Quantification → audit 04 (performance) and audit 06 (ops).

---

## SECTION 7 — FORWARD FLAG: DISTRIBUTOR-AS-TENANT (ACCESS DIMENSION)

Audit 01 §6c covered the data-model friction (F1-F6). The access-pattern layer adds:

- **The company FK is the *only* visibility concept in the entire access layer.** All three scoped helpers, all ~150 hand-written filters, and every form queryset equate "may see" with "same company". Sharing requires a second axis (grants/links); every `company=request.user.company` call site written between now and then is one more line that will need to become `visible_to(user)`. **This is the strongest practical argument for Section 3c option 2** — a `.for_user(u)`/`.visible_to(u)` manager method is the single choke-point a future sharing layer plugs into; raw `company=` filters are not.
- **`distributor_contact` role exists but has no access model** — it appears in role choices and `_can_manage_user` reasoning, but no view branch grants distributor-keyed visibility. Whoever builds it first will define the distributor-side access pattern de facto; it should be built on the scoped-helper pattern, not ad-hoc filters.
- **Permissions are visibility-blind.** `can_view_accounts` etc. gate *features*; coverage areas gate *rows* — but only for accounts/events. A distributor-tenant would need row-gating on sales/inventory too, where today only `company=` exists.
- **The import pipeline assumes importer == owner == CRM-owner** (`company=request.user.company` stamped on batch, records, and auto-created accounts in one transaction — audit 01 F3). The access-layer corollary: there is no concept of importing *on behalf of* or *into a shared space*; replace-on-import deletes by `company + account__distributor` and would need a visibility-aware rewrite.
- **Coverage areas are supplier-side constructs** (distributor FK required on every row) — they would invert awkwardly for distributor-tenant users covering *suppliers*. Awareness only.

No action now beyond the §3c recommendation; everything else is design work for that feature's time.

---

## SECTION 8 — PRIORITIZED FINDINGS SUMMARY

Severity: CRITICAL = confirmed cross-tenant breach; HIGH = convention-only gap likely to leak under growth. Cost: S (<½ day), M (days), L (week+). **BFT** = must fix BEFORE-FIRST-TENANT.

| ID | Sev | BFT | Finding | Why it matters | Cost | X-ref |
|---|---|---|---|---|---|---|
| **T1** | **CRITICAL** | **BFT** | Historical event import: confirm step accepts arbitrary account pks (event_import/views.py:354), execute fetches them unscoped (:461) and writes cross-company Events; export CSV reads foreign account name/street/city unscoped (:600) | Confirmed read+write tenant breach, exploitable by pk enumeration, held by supplier_admin — the exact role every external tenant gets | **S** (validate posted pk ∈ company-scoped candidate set; add `company=` to the two queries) | Breaks invariant behind T3; audit 01 §4d |
| **T2** | HIGH | **BFT** | Tenancy is 100% convention: no manager/mixin/middleware/test enforces scoping across 142 endpoints; dev data (1 company) cannot reveal a miss | The next missed filter ships silently; event_import proves the failure mode is real | S-M (two-company test harness + lint, §3c opts 1+6) then M (scoped managers, opt 2) | 01-D31 |
| **T3** | HIGH | **BFT** | Invariant-reliant queries: `Event.objects.filter(account=…)` with no company term (accounts/views.py:524,535; reports/views.py:2140,2152; accounts/utils.py get_account_associations); invariant `event.company==account.company` enforced nowhere | Any cross-company event (T1 today, anything tomorrow) silently corrupts victim-side counts and blocks their account deletion | S (add company terms) + S (CI invariant check) | 01 §4d |
| **T4** | HIGH | **BFT** (policy) | Django admin: 11 unscoped ModelAdmins incl. cross-company FK dropdowns (account merge, distributor reassign); safe only because is_staff ≡ one operator | One is_staff grant away from total cross-tenant access; operator can cross-merge tenants by mis-click | S (policy: no tenant staff, documented) / M (scoped ModelAdmin if operators multiply) | 01-D7 (merge) |
| **T5** | MEDIUM | BFT (decide) | saas_admin NULL-company behavior inconsistent: all-tenant account search (accounts/utils.py:59) vs none-for-events vs 404-on-details | Undefined operator-access model; the all-tenant branch is the only by-design app-layer cross-tenant read — should be deliberate or deleted | S | — |
| **T6** | MEDIUM | — | Media (event photos, expense receipts) served unauthenticated, tenant-blind; protection = uuid4 URL secrecy; R2 in prod | Receipts are financial docs; links are unrevocable and shareable; no tenant boundary in storage | M (auth'd media view / signed URLs) | — |
| **T7** | MEDIUM | — | reports `?items=` pks stored in session unvalidated; `Item.objects.filter(pk__in=…)` name lookup unscoped (reports/views.py:1664,1984) | Foreign item names/existence leak into report+CSV metadata by pk probing | S (validate against `brand__company` at parse) | — |
| **T8** | MEDIUM | — | Global Role/Permission = platform-wide grants; an operator permission edit hits all tenants at once; no per-tenant roles possible | First tenant-specific access request has no home; blast radius of role edits is total | M-L (only when needed; document until then) | 01-D13 |
| **T9** | MEDIUM | BFT | event_import app is single-tenant-hardwired (BW item codes, views.py:91-104,398) yet exposed to every tenant's supplier_admin; same app carries T1 | Foreign tenants get a broken/misleading tool; the app is the codebase's weakest tenancy code | S (gate behind saas_admin or retire) | T1, 01-D3 family |
| **T10** | MEDIUM | — | Seeded-data inventory (§4a): catalog/0007 blast radius = companies existing at migrate time (fresh envs/squashes), seed_data creates a real-looking tenant if run in prod | Fresh-environment contamination + operator habit risk | S (guards + dev-only labels) | 01-D3 confirmed/refined |
| **T11** | MEDIUM | **BFT** | CoPacker has no UI and no admin registration — tenants cannot manage co-packers at all | Hard blocker for production/COGS features for any tenant; currently developer-only | S (CRUD UI mirroring Brand) | 01-D27, 01 §2d |
| **T12** | LOW | BFT (checklist) | `so_sequence_start` default 2006 = first tenant's sequence; no provisioning automation | Every new tenant silently inherits it; onboarding is artisanal (§5) | S (`provision_tenant` command) | — |
| **T13** | LOW | — | `_can_manage_user` ambassador_manager rule lacks company term (core/views.py:53-57) | Only live if users are moved cross-company (unsupported op — declare it so) | S | — |
| **T14** | LOW | — | User-company moves dangle coverage areas (inert, fail-closed) and leave cross-company created_by/ambassador FKs | Cosmetic name exposure + T13 activation; no supporting code exists | S (document as unsupported) | — |
| **T15** | INFO | — | Forms uniformly scope FK choices via `company=` kwarg; JSON endpoints validate payload ids; distribution app is the reference implementation | The house pattern is good — codify it (3c) rather than invent | — | — |
| **T16** | INFO | — | No cross-FK company-pair constraints at DB level (event↔account, account↔distributor, accountitem pairs) | The invariants T3 relies on are convention too; CI invariant checks (T2 harness) are the cheap mitigation | — | 01 §4d |

**Counts: 1 CRITICAL · 3 HIGH · 7 MEDIUM · 3 LOW · 2 INFO — 16 findings.**

**BEFORE-FIRST-TENANT list (in order):**
1. **T1** — close the event-import pk hole (S).
2. **T3** — add company terms to the invariant-reliant event queries (S).
3. **T2** — stand up the two-company test harness + lint; run it against the census table in §1.1 (S-M).
4. **T9** — gate or retire the event_import tool (S).
5. **T4** — written operator policy: no tenant user ever gets is_staff; admin is operator-only (S).
6. **T11** — CoPacker UI (S) — onboarding blocker, not a leak.
7. **T5** — decide the saas_admin access model (S).
8. **T12** — provisioning command + onboarding checklist (S).

### Cross-reference to audit 01

Confirmed/extended: D3 (blast radius refined → T10; plus new sibling T9), D7/D8 (uniqueness gaps re-framed as per-tenant data-quality risks, §3d), D13 (→ T8 with platform-wide-grant framing), D31 (single-company data → T2's core premise), §4d invariants (→ T3/T16), §6c F1-F6 (extended with the access dimension, §7). New here: T1, T2 (structural), T4, T5, T6, T7, T11, T12, T13, T14.
