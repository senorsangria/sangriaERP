# Audit 3/6 — Permissions & Access Control

**Date:** 2026-06-11
**Scope:** The permission model as-built, role-system archaeology, the per-endpoint enforcement map, the permission×tenancy intersection, and a cleanup recommendation.
**Method:** Read-only. Live Role/Permission tables dumped from dev DB; every `has_permission`/`has_role`/`is_<role>` call site in apps and templates enumerated (48 live role-check sites, ~80 permission-check sites); every gate helper read; enforcement cross-checked against audit 02's 142-endpoint census.
**Builds on:** audit 01 (D13 global RBAC), audit 02 (T1/T4/T5/T9, endpoint census §1.1). The **event_import tool is slated for retirement** — its permission (`can_run_historical_event_import`) is marked "to be removed" throughout and not analyzed further.

**Headline:** The permission system is a custom, well-built RBAC core (User→roles→permissions, cached lookups) wearing a role-era skin. **14 of 43 permissions (33%) are dead or template-only** — most shadowed by role checks or coarser permissions that took their place. The most consequential cluster: **all four account-mutation endpoints are gated only by `can_view_accounts`** while the granular `can_create/edit/toggle/delete_accounts` permissions sit unchecked. No live exploit exists today because every current role bundle that has *view* also has the mutation permissions — the gap is **latent over-grant** that fires the day any role bundle diverges (e.g., a read-only analyst role, or a tenant asking for one). Zero live permission×tenancy intersection failures remain once event_import retires.

---

## SECTION 1 — THE PERMISSION MODEL AS-BUILT

### 1a. Mechanism

Fully custom — Django's built-in auth permissions, Groups, and `user.has_perm()` are **not used** (auth_group: 0 rows; `core_user_user_permissions`: 0 rows; the only Django-auth flag in play is `is_staff` on the single operator account).

```
core.Permission  (codename unique, description)          — 43 rows, global
core.Role        (name/codename unique, permissions M2M) — 8 rows, global
core.User.roles  (M2M)                                   — assignment point
```

- Checks: `user.has_permission(codename)` / `user.has_role(codename)` — both **instance-cached** (one query per request per type; core/models.py:111-147). Template side: `{{ user|has_perm:'…' }}` filter (core/templatetags/rbac.py) plus the 8 `is_<role>` properties.
- Source of truth for *definitions*: **data migrations** (core/0004 + nine follow-ups). No admin/UI for editing roles or permission bundles. Permission/Role are registered models but role-bundle edits happen only via migration (audit 01 D29 recommends replacing this with an idempotent sync command).
- Tenancy dimension: **none**. Permission and Role have no company FK (audit 01 §1.1); `has_permission` is a global boolean. "Within your company" is supplied entirely by the separate queryset-scoping conventions (audit 02 §3).

### 1b. Role archaeology

The original system was pure roles (pre-Phase-10.5); the RBAC migration kept the role API as a compatibility layer. What remains, classified:

| Vestige | Where | Used? | Verdict |
|---|---|---|---|
| **8 `is_<role>` properties on User** (models.py:154-184) | 62 Python call sites + 14 template sites | Heavily | **The fossil API.** Docstring admits it: "implemented via has_role() so existing template and view checks continue to work unchanged." Every one is a role check wearing a property; they make role-based gating frictionless and permission-based gating optional |
| `ROLE_CHOICES` hardcoded list (core/forms.py:16-25) | user_list filter dropdown | Yes | Parallel to the Role table — a role added in data drifts out of the filter UI silently |
| `imports/views.py:55 _require_supplier_admin` → `is_supplier_admin` | Gates the **main sales import flow** (upload/preview/success) | Yes | **Pure role gate where `can_import_sales_data` exists** and gates the sibling account-import flow. Fossil gate (P3) |
| `_get_visible_users` role ladder (core/views.py:61-87) | user_list | Partially | sales/territory/ambassador_manager branches are **unreachable dead code** — user_list is gated by `can_manage_users`, which only supplier_admin & saas_admin hold |
| `_can_manage_user` role ladder (core/views.py:35-58) | user_edit/deactivate/password_reset | Partially | Live for supplier/saas everywhere; live for **sales_manager only via password-reset** (gated by `can_reset_user_password`, which sales_manager holds); territory/ambassador_manager branches unreachable (they lack both gate permissions) |
| Role branches in visibility helpers (`_get_visible_events` 6 branches; `get_accounts_for_user`/`get_distributors_for_user` saas/supplier branches; reports' 8× `has_role('supplier_admin')` coverage-bypass) | Everywhere rows are filtered | **Load-bearing** | Not fossils — this is the *visibility tier* system. But note: `can_view_all_accounts`/`can_view_all_events` permissions exist to express exactly this, and the helpers **don't consult them** (events/accounts visibility = role names; `can_view_all_accounts` is checked only in account_list's inactive-filter branch and event-create's search-disable). Half-migrated |
| `_can_delete_note` role list, dashboard `search_roles` list, events AJAX eligibility role lists, `coverage_area_add/remove` `is_supplier_admin` gates | Various | Load-bearing | Role-as-domain-concept (who can be an ambassador) is legitimate; role-as-permission-check (coverage gates) is the fossil pattern |
| Template role badges (user_list colors etc.) | Cosmetic | Yes | Harmless |
| Misnamed helpers: catalog/distribution `_require_supplier_admin` | — | Yes | **Actually check permissions** (`can_manage_brands` / `can_manage_distributors`) — name is the fossil, behavior already migrated |

**Role→permission migration completeness: ~70%.** Gating of feature areas is mostly on permissions; row visibility, user management, recap access, coverage management, and the main sales import are still role-driven. No pre-RBAC `User.role` field or role-named auth Groups survive (clean removal — migrations 0003-0005 era).

### 1c. The 43 permissions — what each gates, where checked

**ALIVE — 29** (check sites verified in Python):

| Permission | Gates | Checked at |
|---|---|---|
| can_view_events | Event list/detail/export + nav | events/views.py:397,613,763; nav |
| can_create_events / can_edit_events | Event create/edit | :868 / :962 |
| can_release_event | Release | :1029 |
| can_request_revision | Request revision | :1073 |
| can_approve_event | Approve + unrelease + revert-complete/-recap-submitted/-revision-requested | :1120,1139,1413,1540,1575 |
| can_delete_event | Draft delete | :1593 |
| can_mark_ok_to_pay | Mark/revert OK-to-pay | :1440,1468 |
| can_view_accounts | Account list/detail **and all account CRUD** (see P1) + note_list + nav | accounts/views.py:34 via `_require_account_access` |
| can_delete_accounts | **Bulk** delete only, AND-ed with supplier_admin role | :848 |
| can_view_all_accounts | account_list privileged branches; event-create search behavior | :217,250; events:855 |
| can_manage_contacts | Contact CRUD | :1208,1239,1269 |
| can_manage_account_notes | Note create/update (+dashboard tile) | :1326,1350 |
| can_manage_brands | **All of catalog** — brands AND items AND reorder | catalog/views.py:20 helper |
| can_manage_distributors | Distributor CRUD/list/detail/profiles | distribution/views.py:46 helper |
| can_manage_distributor_groups | Group CRUD | :268-334 |
| can_manage_distributor_inventory | Inventory upload/delete **and every PO/forecast/projection endpoint** | :55 helper + 15 inline sites |
| can_manage_production | All 9 production endpoints | production/views.py:48 helper |
| can_import_sales_data | Account-import flow + mapping resolve/bulk-save + nav (NOT the main sales import — P3) | account_import_views.py:38; imports:1284,1362 |
| can_manage_item_mapping | Mapping list/create/edit + resolve/bulk-save | imports:971-1039,1285,1363 |
| can_view_import_history | Batch list/detail/delete | imports:1067,1143,1164 |
| can_view_report_account_sales | Account-sales report + CSV + account sales tabs **+ the entire routes app** | reports ×6; accounts:373; routes:23-157 |
| can_view_report_item_sales | Item report + CSV + sort | reports ×3 |
| can_view_report_account_distribution | Distribution report + CSV | reports ×2 |
| can_manage_users | user_list/create/edit/deactivate | core:211,243,263,313 |
| can_create_users | user_create (second gate) | :245 |
| can_reset_user_password | user_password_reset | :338 |
| can_redirect_to_events_on_login | Login/dashboard redirect | :112,139 |
| can_run_historical_event_import | event_import app — **to be retired with the tool** | event_import ×8 |

**DEAD — 13** (zero Python check sites; verified by exhaustive sweep + indirection check):

| Permission | Why dead | Shadowed by |
|---|---|---|
| can_create_accounts | account_create checks only can_view_accounts | coarse gate |
| can_edit_accounts | account_edit ditto | coarse gate |
| can_toggle_account_status | account_toggle ditto | coarse gate |
| can_manage_items | item CRUD gated by can_manage_brands | sibling permission |
| can_reorder_items | move-up/down gated by can_manage_brands | sibling permission |
| can_manage_user | `_can_manage_user()` *function* does role-ladder logic; the permission is never read (name-shadow trap) | role logic |
| can_fill_recap | `_can_recap()` uses assignment+coverage object logic (good logic, ignores the permission) | object logic |
| can_view_all_events | visibility is role-branched in `_get_visible_events` | role logic |
| can_view_draft_events | `_can_view_drafts` returns True unconditionally (events/views.py:222-229); draft rules live in role branches | role logic |
| can_view_coverage_areas_tab | view uses `is_supplier_admin` (core:282) | role check |
| can_assign_coverage_areas | coverage add/remove use `is_supplier_admin` (accounts:909,1021) | role check |
| can_access_dashboard | dashboard is login-only; never checked anywhere | nothing |
| can_view_saas_admin_ui | no such UI exists (audit 02 §4c) | nothing |

**TEMPLATE-ONLY — 1:**

| Permission | Situation |
|---|---|
| can_export_events_csv | Button hidden by `{% if user\|has_perm:'can_export_events_csv' %}` (event_list.html:68); the endpoint `event_export_csv` checks **can_view_events** instead (events/views.py:613). Today every role with view also has export, so no live gap — classic mismatch (P2/2d) |

---

## SECTION 2 — ENFORCEMENT MAP

### 2a. Per-endpoint enforcement (against audit 02's census)

Enforcement styles in use — **six** different hand-rolled mechanisms, no decorators/mixins/middleware:

1. `denied = _require_<x>(request); if denied: return denied` helper (5 variants across apps, two of them misnamed)
2. Inline `if not request.user.has_permission('…'): return render(403)/JsonResponse(403)/redirect(...)`
3. Inline role checks (`is_supplier_admin`)
4. Object-capability functions (`_can_recap`, `_can_manage_user`, `_can_delete_note`, `_can_unrelease` logic)
5. `@login_required` only
6. Visibility-queryset-as-gate (fetching from `_get_visible_events` — a 404, not a 403)

| Endpoint family (count) | Gate | Style |
|---|---|---|
| Auth/profile/access-denied/UI-state (8) | login (or none for login/logout) | 5 — appropriate |
| dashboard (1) | login + role list for search | 3/5 — `can_access_dashboard` never used |
| user_list/create (2) | can_manage_users (+can_create_users) | 2 |
| user_edit/deactivate (2) | can_manage_users + `_can_manage_user` ladder | 2+4 |
| user_password_reset (1) | can_reset_user_password + ladder | 2+4 |
| brands/items ×10 | can_manage_brands (helper misnamed "supplier_admin") | 1 |
| distributor groups ×4 | can_manage_distributor_groups | 2 |
| distributor CRUD/profiles ×7 | can_manage_distributors (misnamed helper) | 1 |
| inventory upload/preview/confirm/delete ×4 | can_manage_distributor_inventory | 1/2 |
| PO modal/suggest/save/delete, group PO ×7, projection/toggle/move ×4 | can_manage_distributor_inventory | 2 |
| production ×9 | can_manage_production | 1 |
| sales import upload/preview/success ×3 | **is_supplier_admin role** | 3 — P3 |
| account import ×3 | can_import_sales_data | 1 |
| mappings ×3 + resolve/bulk ×2 | can_manage_item_mapping (+can_import_sales_data on the shared two) | 1/2 |
| batch list/detail/delete ×3 | can_view_import_history | 1 |
| account list/detail ×3 | can_view_accounts | 1 |
| **account create/edit/toggle/delete ×4** | **can_view_accounts only** | 1 — **P1** |
| account bulk-delete (1) | can_delete_accounts AND supplier_admin role | 2+3 — P9 |
| coverage add/remove ×2 | **is_supplier_admin role** (dead permissions exist for exactly this) | 3 |
| notes ×4 | view: can_view_accounts; create/update: can_manage_account_notes; delete: `_can_delete_note` | 2+4 |
| contacts ×4 | can_view_accounts→list; can_manage_contacts→CUD | 2 |
| accounts AJAX ×4 | login only (+coverage scoping on search) | 5 — P10 |
| event list/detail (2) | can_view_events | 2 |
| event create/edit (2) | can_create_events / can_edit_events | 2 |
| event workflow ×10 | per-action permissions (release/approve/ok-to-pay/delete) + unrelease combo | 2/4 ✓ consistent |
| recap save/submit/unlock, photo-delete, expenses ×6 | `_can_recap` object logic + status checks | 4+6 ✓ consistent |
| event export CSV (1) | can_view_events (template shows by can_export_events_csv) | 2 — mismatch |
| events AJAX ×3 | login only (+company scope) | 5 — P10 |
| reports ×11 | per-report view permissions ✓ uniform | 2 |
| routes ×4 | can_view_report_account_sales (semantic misuse) + created_by ownership | 2+4 |
| event_import ×8 | can_run_historical_event_import | 1 — to be retired |

### 2b. Template-only gating (endpoint reachable directly)

Exhaustive cross-check of template `has_perm`/role conditions vs view gates found **one true template-only permission** (can_export_events_csv, above) and **one template-stronger-than-view cluster**:

- **Account mutation buttons vs endpoints (the P1 cluster):** `account_list`/`account_detail` compute context flags from `can_delete_accounts` + supplier_admin (views.py:303-306, 848) and templates show/hide accordingly — but the underlying `account_create/edit/toggle/delete` endpoints accept any `can_view_accounts` holder. The *buttons* are stricter than the *endpoints*. Not exploitable under current bundles (every view-holder also holds the mutation perms — verified against the live matrix in §1c); it becomes real the day a bundle diverges.

No mutation endpoint was found with **zero** gate. Nothing financial/exporting is reachable without at least a feature-area permission. → **No CRITICAL.**

### 2c. Login-only endpoints — sensitivity review

| Endpoint | Sensitive? |
|---|---|
| dashboard | No (search internally role-gated + coverage-scoped) |
| profile/profile_edit/password_change | Self-only ✓ |
| save_admin_tools_state | No |
| ajax_states/counties/cities (accounts) | Mild: leaks the company's geographic account footprint (distinct states/counties/cities) to any authenticated company user — incl. stub roles like distributor_contact and payroll_reviewer who can't see accounts at all |
| ajax_accounts_search / ajax_event_accounts | Coverage-scoped via `get_accounts_for_user` ✓ (returns none for users without coverage) |
| ajax_ambassadors / ajax_event_managers | Mild: returns the company's staff roster (names+ids of ambassadors/managers/admins) to any authenticated company user, regardless of event permissions |

All read-only; none mutate; none financial. → P10 (LOW).

### 2d. Mismatch class (template gates ≠ view gates)

1. `can_export_events_csv` (template) vs `can_view_events` (view) — events export.
2. Account mutation buttons (`can_delete_accounts`+role) vs endpoints (`can_view_accounts`) — see 2b.
3. **Nav vs view:** nav shows "Import Sales Data" on `can_import_sales_data` (nav.py:107,123) but the flow enforces the supplier_admin *role* (imports/views.py:55) — a user granted the permission through any future custom bundle sees the menu item and gets access-denied. Fails closed (deny), but proves nav/view can disagree.
4. Name-shadow trap: `can_manage_user` (permission, dead) vs `_can_manage_user` (function, live role ladder) — a future developer will reasonably believe the permission is enforced.

---

## SECTION 3 — PERMISSION × TENANCY INTERSECTION

### 3a. Does "has permission X" ever skip company scoping?

Cross-referenced against audit 02's census: **the only endpoints where a permission check coexisted with missing tenant scoping were the event_import three (T1)** — permission-gated (`can_run_historical_event_import`) yet unscoped. With that tool retiring, **zero live intersection failures remain**: every kept permission-gated endpoint also carries correct company scoping (audit 02 §1.1, 132/135 SCOPED).

The structural observation stands, though: permission and scoping are **two independent hand-written steps** at every endpoint — `denied = _require_X(request)` then `get_object_or_404(Model, pk=…, company=request.user.company)`. Nothing ties them; event_import is the proof that one can ship without the other. Permissions themselves are tenant-blind globals: `has_permission('can_manage_brands')` means "anywhere", and only the queryset line makes it "in my company". This is acceptable *if* the two-step is made structural (see 4c).

### 3b. Privileged tiers

| Tier | Cross-tenant reach | Deliberate? | Could a tenant get it? |
|---|---|---|---|
| `is_superuser` | Total (Django admin + all `user.has_perm`) — **not used by the custom RBAC at all** (`has_permission` ignores it); relevant only to Django admin | No superuser is created by any code path (create_saas_admin sets only is_staff); one may exist from manual `createsuperuser` — **check prod** | No code path grants it ✓ |
| `is_staff` | Full unscoped Django admin: all 11 registered models, cross-company FK dropdowns (audit 02 T4) | Yes — operator-only by construction (only create_saas_admin sets it; no form exposes it — verified UserCreateForm/UserEditForm field lists) | Only via shell/admin by an existing staff user. Policy gap, not code gap (T4) |
| `saas_admin` role (company=NULL) | App-side: ALL companies' accounts via `get_accounts_for_user` (dashboard search + 2 AJAX endpoints — audit 02 T5); all users via `_get_visible_users`; `_can_manage_user` always-True (can edit/deactivate/reset-password **any user in any tenant** — these views *are* company-blind for saas_admin, deliberately); company picker on user-create. Everything else: NULL company → 404/empty | Half-deliberate (audit 02 T5: inconsistent) | **Role assignment is properly fenced**: only saas_admins see the saas_admin role in the role queryset (forms.py:70-74,153-157); user_create/edit require can_manage_users (supplier/saas only). A supplier_admin cannot self-escalate ✓ |
| `supplier_admin` role | None cross-tenant (all checks scoped) — but it is the **top intra-tenant tier**: 41/43 permissions, including the to-be-retired import tool | Yes | This IS the tenant-admin role |

Notable curiosity: **saas_admin holds fewer permissions (31) than supplier_admin (41)** — it lacks production, distributor-inventory/groups, item-mapping, import-history, reports, notes, contacts. Combined with the NULL-company 404s, the operator role's app-side surface is mostly vestigial; its real power is `is_staff`. This supports audit 02 T5's recommendation: declare saas_admin = Django-admin-operator, remove its app-side all-tenant account branch.

### 3c. Assignment & tenant onboarding

- **Roles→users:** via user_create/user_edit UI (supplier_admin & saas_admin only, by permission gates). Role queryset excludes saas_admin for non-saas requesters ✓. Multi-role assignment is possible (M2M with checkboxes) — permission union applies (has_permission scans all roles).
- **Permissions→roles:** migrations only. No UI. A tenant cannot alter bundles (safe), and neither can the operator without a deploy (rigid — audit 02 T8).
- **Tenant onboarding fit:** clean in mechanism — create tenant admin, assign supplier_admin, done; the bundle is global so nothing per-tenant to configure. Two wrinkles: (1) the bundle currently includes `can_run_historical_event_import` → nav shows the to-be-retired tool to every new tenant admin (resolves with retirement; until then it's the T1 hole-holder); (2) because bundles are global, any future "tenant X's admins shouldn't see reports" request forces either a new global role or the per-tenant role schema change (T8). For 1-5 cooperating tenants, acceptable.

---

## SECTION 4 — ROLE/PERMISSION CLEANUP OPPORTUNITY

### 4a. Safe to remove / load-bearing / ambiguous

**Safe to remove (dead, nothing reads them):**
- 13 dead permissions (§1c table) — delete rows + their migration-grant lines via one cleanup migration, OR wire them up (see 4b for which).
- `_get_visible_users` branches for sales/territory/ambassador_manager; `_can_manage_user` branches for territory/ambassador_manager (unreachable through gates). ~40 lines.
- `can_run_historical_event_import` + its 8 check sites — goes with the tool retirement.
- `_can_view_drafts` (returns True; inline it away).

**Load-bearing role logic (keep, but it's the visibility tier system — rename the concept, don't delete):** `_get_visible_events` branches, `get_accounts_for_user`/`get_distributors_for_user` admin branches, reports' supplier_admin bypass, `_can_recap`, `_can_delete_note`, dashboard/AJAX role eligibility lists, the `_can_manage_user` supplier/saas/sales_manager-password paths.

**Ambiguous (decide deliberately):**
- `is_<role>` properties: 62+14 call sites. They can't be deleted until the load-bearing logic above is re-expressed; they *should* stop being used for **gating** (coverage endpoints, sales import) immediately.
- `can_view_all_accounts`/`can_view_all_events`: half-wired. Either complete the migration (visibility helpers consult them instead of role names — the cleaner end-state) or delete them and accept roles-as-visibility-tiers.
- `distributor_contact` role: 1 permission, no coherent access path (can create notes via direct POST but cannot view any page that lists them — note_list requires can_view_accounts). Stub for the distributor-as-tenant future (audit 02 §7). Document as unfinished; don't grant it to anyone.
- `payroll_reviewer`: coherent niche bundle (5 perms) — fine as-is.

### 4b. Coherence assessment & recommended cleaned-up model (NOT implemented)

**Current incoherences (beyond dead perms):**
- Granularity is inconsistent: events get per-action verbs (release/approve/ok-to-pay — good, the workflow needs them); accounts have per-action verbs that aren't enforced; catalog/distribution collapse everything into one `can_manage_*`; PO management hides inside `can_manage_distributor_inventory`; routes borrow a *reports* permission.
- Naming lies: `can_manage_brands` actually means "manage catalog"; `can_manage_distributor_inventory` actually means "distributor inventory + POs + forecasting"; `can_view_report_account_sales` actually also means "use routes"; `can_manage_user` means nothing.
- Two gates AND-ed only once in the codebase (bulk-delete: permission AND role) — a pattern that exists nowhere else and contradicts single-delete having neither.

**Recommended canonical set (~28), two patterns only — `can_view_<area>` / `can_manage_<area>` plus workflow verbs where a real state machine exists:**

| Area | Permissions |
|---|---|
| Accounts | can_view_accounts, **can_manage_accounts** (absorbs create/edit/toggle/delete — enforce it!), can_view_all_accounts (wire into visibility), can_manage_contacts, can_manage_account_notes |
| Catalog | can_manage_catalog (rename from can_manage_brands; drop items/reorder) |
| Distribution | can_manage_distributors, can_manage_distributor_groups, can_manage_distributor_inventory, **can_manage_distributor_pos** (split out — POs are financially material; COGS raises the stakes) |
| Production | can_manage_production |
| Imports | can_import_sales_data (enforce on the main flow too), can_manage_item_mapping, can_view_import_history |
| Events | can_view_events, can_view_all_events (wire or drop), can_create_events, can_edit_events, can_release_event, can_request_revision, can_approve_event, can_delete_event, can_mark_ok_to_pay, can_export_events_csv (enforce in view), can_fill_recap → **drop** (object logic `_can_recap` is correct here; delete the permission rather than pretend) |
| Reports | the existing 3 + **can_manage_routes** (stop borrowing) |
| Users | can_manage_users, can_create_users, can_reset_user_password, can_assign_coverage_areas (wire — replaces is_supplier_admin gates; absorbs the tab-view perm) |
| Login | can_redirect_to_events_on_login |
| **Delete** | can_access_dashboard, can_view_saas_admin_ui, can_manage_user, can_manage_items, can_reorder_items, can_view_coverage_areas_tab, can_view_draft_events, can_create/edit/toggle_accounts (absorbed), can_run_historical_event_import (with tool) — **11 deletions** |

**Roles stay as bundles** (they're the right UX for assignment); their definitions move from migrations to an idempotent `sync_rbac` management command (single source file, runs on deploy — also resolves audit 01 D29's 10-migration chain pattern). Draft/visibility tiers remain role-derived *inside the visibility helpers* unless can_view_all_* is wired — either is coherent; pick one.

**The one enforcement pattern all endpoints should use:** a single decorator per view —

```
@require_permission('can_manage_distributor_pos')   # auth + 403 + (audit-02) company resolution
```

— replacing all six styles. Object-capability functions (`_can_recap`, `_can_delete_note`) stay as a *second* layer where genuinely object-conditional; template flags must come from the same context the view checked (pass `perms` dict from the decorator), eliminating the 2d mismatch class mechanically.

### 4c. Interaction with audit 02's structural recommendation

They should be **one mechanism, two layers**:

- **Decorator** (`@require_permission('can_x')`): authentication → 403-on-missing-permission → resolves `request.company` (handling the saas_admin NULL branch in exactly one place — closes audit 02 T5's inconsistency by policy) → optionally asserts non-null company for tenant views. This merges this audit's six gate styles with audit 02's option-3 mixin.
- **Scoped managers** (audit 02 option 2: `.for_company(c)`/`.visible_to(u)`): row scoping inside the view body, fed by the `request.company` the decorator resolved.

One imports the other's output; neither can be forgotten independently without the two-company test harness (audit 02 T2) catching it — the harness should assert both 404-on-foreign-pk **and** 403-on-missing-permission per endpoint, using §2a's table as the spec (it lists the expected permission per endpoint family). The same decorator is also the future hook for distributor-as-tenant visibility (audit 02 §7): `visible_to(user)` replaces `company=…` in exactly one layer.

---

## SECTION 5 — PRIORITIZED FINDINGS SUMMARY

No CRITICAL: every mutation/export endpoint has at least a feature-area gate; the template-only and dead-permission gaps are latent (current role bundles mask them — verified against the live matrix). Cost: S (<½ day), M (days), L (week+).

| ID | Sev | BFT | Finding | Why it matters | Cost | X-ref |
|---|---|---|---|---|---|---|
| **P1** | HIGH | **BFT** | Account mutations (create/edit/toggle/single-delete) gated only by `can_view_accounts`; the 4 granular permissions exist but are never checked; templates gate buttons more strictly than endpoints | Latent over-grant: the first diverging role bundle (read-only analyst, tenant request) silently grants full account CRUD; fix is cheap now, an incident later | S | 02-census; §2b |
| **P2** | HIGH | BFT | Six enforcement styles, no shared decorator/mixin; template/nav/view gates can disagree (4 concrete mismatches: export-CSV, account buttons, import nav, can_manage_user name-shadow) | Same failure mode as tenancy-by-convention (02-T2): the next mismatch ships silently; the fix is the shared mechanism in §4c | M | 02-T2, 02 §3c |
| **P3** | MEDIUM | — | Main sales-import flow gated by `is_supplier_admin` role while `can_import_sales_data` gates the sibling flows and the nav | Fails closed today, but it's the clearest live fossil gate and a nav/view mismatch; one-line fix | S | §1b |
| **P4** | MEDIUM | — | 13 dead permissions (33% incl. template-only) + misleading names (`can_manage_brands`=catalog, `can_manage_distributor_inventory`=also POs, reports perm gates routes, `can_manage_user` shadow) | The permission vocabulary lies — future developers will trust names and dead entries; cleanup is mostly deletions | S-M | §1c, §4b |
| **P5** | MEDIUM | — | Dead role-ladder branches (`_get_visible_users`/`_can_manage_user` for territory/AM; sales reachable only via password-reset) + `_can_view_drafts` no-op + ROLE_CHOICES drift risk | Dead authz code is where wrong assumptions breed; ~50 lines removable | S | §1b |
| **P6** | MEDIUM | BFT (decide) | saas_admin tier incoherent: 31<41 perms, app-side mostly 404s except the all-tenant account search; real power is is_staff; `_can_manage_user` gives it cross-tenant user admin (deliberate?) | Operator model must be decided before tenants arrive; recommendation: saas_admin = Django-admin operator, remove app-side all-tenant branch | S (policy) | 02-T4/T5 |
| **P7** | MEDIUM | — | Role bundles editable only via migrations; 10-migration grant chain; no per-tenant bundles possible | Every permission tweak is a deploy; replace with idempotent sync command when doing P4 | S-M | 01-D29, 02-T8 |
| **P8** | LOW | — | distributor_contact role is a stub: 1 permission, no viable UI path (can POST notes, can't view them) | Half-built role visible in the role dropdown; document as reserved for distributor-tenant future | S | 02 §7 |
| **P9** | LOW | — | bulk-delete uniquely requires permission AND role; single-delete requires neither granular gate | Inconsistent within one file; resolves inside P1 | S | §2a |
| **P10** | LOW | — | Login-only AJAX: geo-footprint lists (states/counties/cities) and staff roster (ambassadors/managers) exposed to any authenticated company user incl. stub roles | Mild intra-tenant info exposure; add can_view_accounts / can_view_events gates | S | §2c |
| **P11** | INFO | — | Object-capability layer (`_can_recap`, `_can_delete_note`, unrelease combo) is consistent and correct — keep it; delete the dead `can_fill_recap` rather than wiring it | The good pattern to preserve through cleanup | — | §4a |
| **P12** | INFO | — | `can_run_historical_event_import` + 8 check sites retire with the event_import tool; until then supplier_admin (= every tenant admin) holds the 02-T1 hole | Confirms retirement covers the permission side; sequence the retirement before tenant #1 | — | 02-T1/T9 |
| **P13** | INFO | — | No is_superuser is created by any code path and custom RBAC ignores it — but verify none exists in prod (`SELECT username FROM core_user WHERE is_superuser;`) | Unaudited standing superuser would bypass nothing in-app but owns the admin | S (check) | 02-T4 |

**Counts: 0 CRITICAL · 2 HIGH · 5 MEDIUM · 3 LOW · 3 INFO — 13 findings.**

**BEFORE-FIRST-TENANT:** P1 (enforce account granular perms — S), P2 (adopt the single decorator at least for new code + add permission assertions to the 02-T2 test harness — S for the harness hook), P6 (decide the operator model — S). P3/P4/P5 are safe to batch into one cleanup pass alongside the event_import retirement.

### Cross-reference

- 02-T1/T9 → P12 (permission side resolves with retirement). 02-T4 → P6/P13 (is_staff policy + superuser check). 02-T5 → P6 (saas_admin model). 02-T8 → P7. 02-T2 harness → P2 (add 403 assertions; §2a is the spec). 01-D13 → §3a (tenant-blind permissions are acceptable given structural two-step). 01-D29 → P7 (sync command).
- New here: P1, P2 (permission dimension), P3, P4, P5, P8, P9, P10, P11, P13.
