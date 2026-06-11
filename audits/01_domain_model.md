# Audit 1/6 — Domain Model & Database State

**Date:** 2026-06-11
**Scope:** Every model, table, constraint, index, relationship; actual dev-PostgreSQL state; migration-chain health; grain assessment.
**Method:** Read-only. All models.py files read in full; FK graph dumped programmatically from the Django app registry; live dev DB (`heliumdb`) inspected via information_schema / pg_indexes / read-only SQL; `makemigrations --check` run.
**Inputs for later audits:** Section 7 is the consolidated findings table. Tenancy-specific observations are flagged `→ Audit 2`, performance ones `→ Audit 4-equivalent`.

**Headline numbers:** 33 concrete models across 10 apps (+3 M2M through-tables: `core_user_roles`, `core_role_permissions`, `events_event_items`). 103 applied migrations, 0 unapplied, `makemigrations --check` clean — **no schema/model drift**. 93,175 SalesRecord rows (21 MB) dominate a ~30 MB database. Dev DB has live duplicate sales rows and duplicate accounts (details in §4d).

> Note: the existing `DB_REVIEW.md` (931 lines) describes **23** models and predates DistributorGroup, DistributorPO/Line, CoPacker, ProductionPO/Line, OwnInventorySnapshot, AccountContact, AccountNote, Route/RouteAccount. It is stale; this document supersedes it for current state.

---

## SECTION 1 — COMPLETE MODEL INVENTORY

All models inherit `created_at`/`updated_at` from `core.TimeStampedModel` **except**: `AccountItem`, `AccountItemPriceHistory`, `AccountContact` (has own timestamps), `AccountNote` (has own timestamps), `EventPhoto` (uploaded_at only), `EventItemRecap` (**no timestamps at all**), `Expense` (created_at only), `RouteAccount` (**no timestamps**), `Permission`, `Role` (**no timestamps**).

### 1.1 Tenancy / Users (`apps/core`)

| Model | Table | Purpose | Company FK |
|---|---|---|---|
| Company | core_company | Top-level tenant | — (is the tenant) |
| User | core_user | AbstractUser + tenant + RBAC roles | ✅ nullable (saas_admin only) |
| Role | core_role | RBAC role bundle | ❌ **GLOBAL** |
| Permission | core_permission | RBAC granular permission | ❌ **GLOBAL** (by design) |

- **Company**: `name` CharField(255), `slug` unique (auto from name), `is_active` bool, `so_sequence_start` int default 2006. Ordering `[name]`.
- **User**: `company` FK→Company PROTECT null/blank related_name=`users`; `roles` M2M→Role; `phone` CharField(50) blank; `created_by` FK→self SET_NULL null. Role checks via cached `has_role()`/`has_permission()`.
- **Role**: `name` unique, `codename` unique, `permissions` M2M→Permission. **`Role.name`/`codename` are globally unique — tenants cannot have custom roles; an edit to a role's permission set affects every tenant.** (INFO here; primary material for Audit 2.)
- **Permission**: `codename` unique, `description`.

### 1.2 Catalog (`apps/catalog`)

| Model | Table | Purpose | Company FK |
|---|---|---|---|
| CoPacker | catalog_copacker | Contract manufacturer | ✅ PROTECT |
| Brand | catalog_brand | Product brand | ✅ PROTECT |
| Item | catalog_item | SKU | via `brand.company` (property) |

- **CoPacker**: `name`, `notes`, `is_active`. `unique_together (company, name)` ✅. Lives in catalog (not production) to avoid a circular migration dependency — documented in docstring.
- **Brand**: `name`, `description`, `is_active`. `unique_together (company, name)` ✅.
- **Item**: `brand` FK PROTECT; `name`; `item_code` (unique within brand); `sku_number` blank; `description`; `is_active`; `sort_order` PosInt default 0; `cases_per_pallet` PosInt null; `co_packer` FK→CoPacker PROTECT null; `cases_per_batch` PosInt null; `production_safety_stock_cases` PosInt null; `forecast_current_inventory` Decimal(10,2) default 0 ("ad-hoc on-hand for PO projection, not tied to inventory imports"). `unique_together (brand, item_code)` ✅ (transitively company-scoped via brand). No direct company FK — tenancy filters must traverse `brand__company` (→ Audit 2).

### 1.3 Distribution (`apps/distribution`)

| Model | Table | Purpose | Company FK |
|---|---|---|---|
| DistributorGroup | distribution_distributorgroup | Named group of distributors | ✅ PROTECT |
| Distributor | distribution_distributor | Distribution company | ✅ PROTECT |
| DistributorItemProfile | distribution_distributoritemprofile | Per-(distributor,item) config | via distributor |
| InventoryImportBatch | distribution_inventoryimportbatch | One inventory CSV upload | ✅ PROTECT |
| InventorySnapshot | distribution_inventorysnapshot | (dist,item,year,month) on-hand | via distributor |
| DistributorPO | distribution_distributorpo | Projected/actual PO to a distributor | via distributor |
| DistributorPOLine | distribution_distributorpoline | PO line (item, qty) | via po→distributor |

- **DistributorGroup**: `name`, `primary_distributor` **FK with `unique=True`** (Django warning W342 — should be OneToOneField), `notes`. `unique_together (company, name)` ✅.
- **Distributor**: `name`, `code` CharField(10) blank db_index (auto-generated from name in `save()`), address/city/state/notes, `is_active`, `order_quantity_value` PosInt null, `order_quantity_unit` choices pallets/cases **null=True on a CharField** (Django anti-pattern: both NULL and '' represent "unset"), `group` FK→DistributorGroup SET_NULL null. **No uniqueness constraint at all — duplicate (company, name) or (company, code) are permitted** (0 dupes in dev today).
- **DistributorItemProfile**: `safety_stock_cases` PosInt null, `is_active` bool default True. `unique_together (distributor, item)` ✅. Sparse-by-design (absent row == active, no target).
- **InventoryImportBatch**: `year`, `month`, `uploaded_by` SET_NULL null, `filename`, `distributor_count`, `snapshots_created`. No uniqueness on (company, year, month) — multiple uploads per month exist (1 dup group in dev). When snapshots are manually deleted (`distribution/views.py:1387`), batch counters go stale (same pattern as ImportBatch partial-gutting, in miniature).
- **InventorySnapshot**: `quantity_cases` Decimal(10,6) ≥0, `year`, `month`, `created_by` SET_NULL, `import_batch` FK→InventoryImportBatch SET_NULL null. **`unique_together (distributor, item, year, month)` ✅ — grain enforced.**
- **DistributorPO**: `year`, `month`, `status` (projected/actual/submitted/in_transit/delivered/invoiced/cancelled), `external_po_number` blank, `so_number` int null db_index ("auto-assigned when Submitted"), `generated_by_algorithm`, `notes`, `selected_for_projection`, `sort_position`, `created_by` SET_NULL. Multiple POs per (distributor, year, month) allowed **by design**. `clean()` enforces external_po_number for ACTUAL and so_number for SUBMITTED — model-validation only, **no DB constraint**, and `clean()` is not called on the `save(update_fields=…)` paths in `distribution/views.py`. **No unique constraint on so_number per company** and `assign_so_number()` (models.py:413) computes MAX+1 with no `select_for_update` — concurrent submits can mint duplicate SO numbers.
- **DistributorPOLine**: `quantity_cases` Decimal(10,6). `unique_together (po, item)` ✅. CASCADE from PO.

### 1.4 Accounts (`apps/accounts`)

| Model | Table | Purpose | Company FK |
|---|---|---|---|
| Account | accounts_account | Retail location | ✅ PROTECT |
| UserCoverageArea | accounts_usercoveragearea | User's coverage scope | ✅ PROTECT |
| AccountItem | accounts_accountitem | (account, item) "sold here" | via account |
| AccountContact | accounts_accountcontact | Contact person at account | via account |
| AccountNote | accounts_accountnote | Free-text note on account | via account |
| AccountItemPriceHistory | accounts_accountitempricehistory | Shelf-price archive | via account_item |

- **Account**: `company` PROTECT, `distributor` **PROTECT non-null** (hardened June 2026, migration 0013 — backlog item 3b DONE), `merged_into` FK→self SET_NULL null + `merge_note`, `name`, street/city/state/zip/phone (originals), `address_normalized`/`city_normalized`/`state_normalized` (matching), `vip_outlet_id` ("reference only"), `county` default `'Unknown'`, `on_off_premise` default `'Unknown'`, `account_type` raw text, `third_party_id`, `distributor_route` raw text, `is_active`, `auto_created`. Two managers: `objects` (all) + `active_accounts` (is_active & not merged). **No uniqueness constraint of any kind** — dedup is heuristic at import time via normalized address; 4 duplicate (company, name, address_normalized) groups exist in dev.
- **UserCoverageArea**: `coverage_type` choices distributor/county/city/account; `distributor` FK PROTECT **non-null and required even for county/city/account coverage types** (coverage is always scoped within a distributor — intentional but worth knowing); `account` SET_NULL null; `state`/`county`/`city` blank text. No uniqueness — duplicate identical coverage rows are possible (INFO).
- **AccountItem**: `account` CASCADE, `item` **CASCADE** (see §2b), `date_first_associated` (set on create, never recalculated — backlog item 6), `current_price` Decimal(6,2) null (only written by event recap). `unique_together (account, item)` ✅. No timestamps.
- **AccountContact**: title choices, name, email, phone, note, `is_tasting_contact`. No uniqueness (duplicate same-name contacts possible — fine).
- **AccountNote**: body, `created_by` SET_NULL. Fine.
- **AccountItemPriceHistory**: `price`, `recorded_at` auto_now_add, `recorded_by` SET_NULL null. **Write-only dead table with inverted semantics** (stores the superseded price at supersession time; first and current price never present) — backlog item 7, confirmed still current: only write site is `apps/events/views.py` `_apply_price_updates`; no read site anywhere. 815 rows across 379 account-items in dev.

### 1.5 Events (`apps/events`, `apps/event_import`)

| Model | Table | Purpose | Company FK |
|---|---|---|---|
| Event | events_event | Tasting / special event / admin hours | ✅ PROTECT |
| EventPhoto | events_eventphoto | Recap photo | via event |
| EventItemRecap | events_eventitemrecap | Per-(event,item) recap numbers | via event |
| Expense | events_expense | Recap expense + receipt | via event |
| HistoricalImportBatch | event_import_historicalimportbatch | One historical-event import run | ✅ **CASCADE** |

- **Event**: `event_type` (tasting/special_event/admin), `status` (8-state workflow), `account` SET_NULL null (required for tastings **by view logic only** — 2 tasting rows with NULL account exist in dev), `date` null (1 NULL in dev), `start_time` null, duration h/m, `ambassador`/`event_manager`/`created_by` all FK→User SET_NULL, `items` M2M→Item, `notes`, `revision_note`, `historical_batch` SET_NULL, `is_imported`, `legacy_ambassador_name`, recap fields (`recap_samples_poured`, `recap_qr_codes_scanned`, `recap_notes`, `recap_comment`). **No Meta.ordering and no indexes beyond FK defaults** — event list queries sort/filter in Python-specified order per view; fine at 1.4k rows.
- **EventPhoto**: `file_url` CharField(500) (storage-backend path), `account` SET_NULL (denormalized from event), `uploaded_by` SET_NULL.
- **EventItemRecap**: `shelf_price`, `bottles_sold`, `bottles_used_for_samples` all null. `unique_together (event, item)` ✅. No timestamps.
- **Expense**: `amount` Decimal(8,2), `description`, `receipt_photo_url` required.
- **HistoricalImportBatch**: `imported_by` SET_NULL, `imported_at`, `event_count`, `csv_filename`, `notes`. **company FK is CASCADE** (vs PROTECT everywhere else — see §2b).

### 1.6 Sales / Imports (`apps/sales`, `apps/imports`)

| Model | Table | Purpose | Company FK |
|---|---|---|---|
| SalesRecord | sales_salesrecord | One distributor sales line | ✅ PROTECT |
| ImportBatch | imports_importbatch | One sales/inventory file import | ✅ PROTECT |
| ItemMapping | imports_itemmapping | Raw import code → catalog Item | ✅ PROTECT |

- **SalesRecord**: `company` PROTECT, `import_batch` **CASCADE** (batch delete ⇒ hard-delete of sales rows), `account` PROTECT, `item` PROTECT, `sale_date` DateField, `quantity` int (may be negative — returns), `distributor_wholesale_price` Decimal(10,2) null (fully populated in dev: 0 nulls). **No uniqueness constraint** (backlog item 1, confirmed). Three composite indexes: (company, sale_date), (account, sale_date), (item, sale_date). **No distributor column** — distributor derived via `account.distributor` (backlog item 3a, confirmed still open).
- **ImportBatch**: `brand` FK PROTECT **null — and never populated: 74/74 rows NULL in dev; no write site sets it** (dead field), `distributor` PROTECT non-null, `import_type` (sales_data/inventory_data), `import_date` auto_now_add, `status` (pending/complete/has_unmapped_items/failed), `filename` (now stores a JSON list — `filename_display` parses it, legacy plain strings still supported), `notes` (free-text **audit channel for replace-on-import**), `date_range_start/end` null, counters (`records_imported`, `accounts_created`, `accounts_reactivated`, `records_skipped`, `account_items_created`). Batch is per **upload**, not per month — backlog item 5, confirmed (partial-gutting workaround live).
- **ItemMapping**: `distributor` PROTECT, `brand` PROTECT null (19/51 NULL — partially adopted), `raw_item_name` CharField(500), `mapped_item` SET_NULL null, `status` (unmapped/mapped/ignored). `unique_together (company, distributor, raw_item_name)` ✅.

### 1.7 Production (`apps/production`)

| Model | Table | Purpose | Company FK |
|---|---|---|---|
| ProductionPO | production_productionpo | PO to a co-packer for a month | ✅ PROTECT |
| ProductionPOLine | production_productionpoline | Line: item, batches, cases | via po |
| OwnInventorySnapshot | production_owninventorysnapshot | Company's own monthly on-hand | ✅ PROTECT |

- **ProductionPO**: `co_packer` PROTECT, `year`, `month`, `status` (projected/actual/complete), `external_po_number` (required for ACTUAL/COMPLETE via `clean()` only), `generated_by_algorithm`, `notes`, `created_by` SET_NULL. Multiple POs per (co_packer, year, month) allowed. Mirrors DistributorPO deliberately.
- **ProductionPOLine**: `batch_count` PosInt, `quantity_cases` Decimal(10,6) (= batch_count × item.cases_per_batch, **stored, not derived — can drift if cases_per_batch changes after creation**). `unique_together (po, item)` ✅.
- **OwnInventorySnapshot**: `quantity_cases` Decimal(10,6) ≥0, `year`, `month`, `created_by`/`updated_by` SET_NULL. **`unique_together (company, item, year, month)` ✅.** The backlog explicitly names this the *good precedent* for dated histories.

### 1.8 Routes (`apps/routes`)

| Model | Table | Purpose | Company FK |
|---|---|---|---|
| Route | routes_route | Named visiting route | ✅ **CASCADE** |
| RouteAccount | routes_routeaccount | Ordered account on route | via route |

- **Route**: `distributor` PROTECT, `created_by` SET_NULL null, `name`. `unique_together (created_by, distributor, name)` — **created_by is nullable so PostgreSQL permits unlimited duplicate orphan routes** (documented in a model comment as accepted). Company FK is **CASCADE** (inconsistent — see §2b).
- **RouteAccount**: `position` PosInt. `unique_together (route, account)` ✅. No timestamps.

---

## SECTION 2 — RELATIONSHIP MAP & CONSISTENCY

### 2a. FK graph — hubs

(Full programmatic dump verified against the live app registry; M2M: User↔Role, Role↔Permission, Event↔Item.)

**Company ← (16 inbound):** User, Brand, CoPacker, Distributor, DistributorGroup, InventoryImportBatch, Account, UserCoverageArea, Event, HistoricalImportBatch, ImportBatch, ItemMapping, SalesRecord, ProductionPO, OwnInventorySnapshot, Route.

**Item ← (9):** AccountItem, DistributorItemProfile, InventorySnapshot, DistributorPOLine, EventItemRecap, ItemMapping.mapped_item, SalesRecord, ProductionPOLine, OwnInventorySnapshot (+ Event M2M).

**Distributor ← (8):** DistributorGroup.primary_distributor, Distributor.group→DistributorGroup (outbound), DistributorItemProfile, InventorySnapshot, DistributorPO, Account, UserCoverageArea, ImportBatch, ItemMapping, Route. *(9 counting all)*

**Account ← (8):** AccountContact, AccountItem, AccountNote, Account.merged_into (self), UserCoverageArea, Event, EventPhoto, RouteAccount, SalesRecord.

**User ← (15):** created_by/uploaded_by/recorded_by-style audit FKs on most transactional models — all SET_NULL null=True (consistent ✅).

**Chains for tenancy resolution** (no direct company FK — relevant to Audit 2):
- Item → brand → company
- AccountItem → account → company (and → item → brand → company; verified consistent: 0 mismatches in dev)
- InventorySnapshot / DistributorPO / DistributorItemProfile → distributor → company
- DistributorPOLine → po → distributor → company
- EventItemRecap / EventPhoto / Expense → event → company
- AccountContact / AccountNote / AccountItemPriceHistory → account(_item) → company
- RouteAccount → route → company

### 2b. on_delete consistency per hub

**Company inbound — 14× PROTECT, 2× CASCADE:**

| FK | on_delete | Assessment |
|---|---|---|
| All others (14) | PROTECT | Consistent baseline |
| `routes.Route.company` | **CASCADE** | 🔶 Inconsistent. Migration `routes/0002_alter_route_fk_on_delete` deliberately changed it, but it diverges from the fleet. In practice unreachable (deleting a Company is blocked by 14 other PROTECTs first), so severity LOW — but it signals "company delete semantics" were never decided globally. |
| `event_import.HistoricalImportBatch.company` | **CASCADE** | 🔶 Same as above. |

**Distributor inbound — all PROTECT** ✅ (Account.distributor was the SET_NULL outlier; fixed in `accounts/0013`). Exception: `Distributor.group` SET_NULL (outbound, correct).

**Item inbound — 7× PROTECT, 1× CASCADE, 1× SET_NULL:**

| FK | on_delete | Assessment |
|---|---|---|
| DistributorItemProfile, InventorySnapshot, DistributorPOLine, EventItemRecap, SalesRecord, ProductionPOLine, OwnInventorySnapshot | PROTECT | Consistent |
| `accounts.AccountItem.item` | **CASCADE** | 🔶 Deleting an Item silently destroys its account-distribution records (and cascades to AccountItemPriceHistory). Usually masked by PROTECT from SalesRecord — but an item with account-items and *no* sales/snapshots/lines deletes clean. Should be PROTECT for consistency. |
| `imports.ItemMapping.mapped_item` | SET_NULL | ✅ Intentional (mapping reverts toward unmapped) — but **status is not reset to 'unmapped' by the FK**, leaving a `mapped` row with NULL item possible. MEDIUM-LOW. |

**Account inbound — mixed but mostly defensible:**

| FK | on_delete | Assessment |
|---|---|---|
| SalesRecord.account | PROTECT | ✅ sales anchor |
| AccountItem / AccountContact / AccountNote / RouteAccount | CASCADE | ✅ owned children |
| Event.account | SET_NULL | 🔶 Tasting events keep existing with NULL account — 2 such rows in dev. Event history survives account deletion, which is defensible, but "tasting requires account" is then only true at creation time. |
| EventPhoto.account, UserCoverageArea.account, Account.merged_into | SET_NULL | ✅ reasonable |

**User inbound — all SET_NULL** ✅ except `User.company` (PROTECT, correct) and `UserCoverageArea.user` (PROTECT, correct — coverage should not silently orphan). `admin.LogEntry.user` is Django's own CASCADE.

**Brand inbound:** Item PROTECT ✅, ImportBatch.brand PROTECT(null) ✅, ItemMapping.brand PROTECT(null) ✅ — consistent.

**Hard-delete cascade worth naming:** `SalesRecord.import_batch` is **CASCADE** — deleting an ImportBatch hard-deletes its sales rows. This is the designed batch-delete path, but combined with replace-on-import's direct `.delete()` it means there are **two** independent hard-delete paths into the largest financial table, with only `ImportBatch.notes` as the trail (backlog item 4, confirmed).

### 2c. The Item relationship web

```
Brand ──PROTECT── Item ──PROTECT── CoPacker        (production identity)
                   │
   ┌───────────────┼──────────────────────┐
   │ supplier side │ distributor side     │ field/event side
   │               │                      │
 OwnInventory    DistributorItemProfile  AccountItem ──── AccountItemPriceHistory
 Snapshot        InventorySnapshot       EventItemRecap
 ProductionPOLine DistributorPOLine      Event.items (M2M)
                 ItemMapping.mapped_item SalesRecord
```

Assessment: **coherent, not tangled.** Item is correctly the single product identity, and every per-context attribute lives on a junction model rather than on Item itself (DistributorItemProfile for distributor config, AccountItem for retail presence, ItemMapping for import vocabulary). Three soft spots:

1. **Item is accreting per-feature scalar config** — `cases_per_pallet`, `cases_per_batch`, `production_safety_stock_cases`, `forecast_current_inventory` are all "the one value for this item" fields added per feature. `forecast_current_inventory` in particular is a *mutable point-in-time quantity stored on a catalog entity* (a deliberate ad-hoc shortcut per its help_text). Each is fine alone; the trend will hurt when COGS needs per-co-packer or effective-dated values (MEDIUM, forward-looking).
2. **ItemMapping ↔ AccountItem asymmetry**: item *codes* are mapped per distributor (ItemMapping), but account *identity* is matched heuristically (normalized address, no mapping table). Not wrong, just two different resolution strategies in one import pipeline — relevant when distributor data sharing arrives (§6c).
3. **`ProductionPOLine.quantity_cases` stores a derived value** (batch_count × item.cases_per_batch at creation time) with no guard against later `cases_per_batch` edits — drift-by-design, undocumented outside the docstring (LOW).

### 2d. CoPacker deep-dive — COGS/BOM readiness

**Current shape:** CoPacker = `{company, name, notes, is_active}` — a name card. Referenced by `Item.co_packer` (PROTECT, null) and `ProductionPO.co_packer` (PROTECT). Production quantities live on `Item.cases_per_batch` and `ProductionPOLine.batch_count/quantity_cases`. Dev data: 2 co-packers, all 8 items have one assigned, 4 ProductionPOs / 6 lines.

**What exists that COGS can build on (genuinely useful):**
- Clean Item↔CoPacker assignment with PROTECT integrity.
- ProductionPO/Line with a status lifecycle and (po, item) uniqueness — a natural attachment point for run-level actual costs.
- OwnInventorySnapshot at an enforced monthly grain — the pattern (and possibly the data) for inventory valuation.
- Consistent Decimal(10,6) case quantities across PO lines and snapshots.

**What is structurally missing (everything below is *absent*, not broken):**

| Gap | Notes |
|---|---|
| No raw-material / component entity | Item models only finished goods. BOM needs ingredients, packaging, labels as first-class (probably non-sellable) materials. |
| No BOM table | Nothing links a finished Item to component quantities. Needs `BillOfMaterials` (+version/effective dates) and `BOMLine (bom, component, qty, uom, scrap%)`. |
| No cost fields anywhere | No standard/actual cost on Item, no unit cost on ProductionPOLine, no tolling fee on CoPacker or ProductionPO, no price on DistributorPOLine (revenue side). `SalesRecord.distributor_wholesale_price` is the only money column in the schema. |
| No unit-of-measure model | Cases/bottles/pallets are implicit integers on Item (`cases_per_pallet`, `cases_per_batch`). BOMs need explicit UoM + conversions (mL, g, units, case-pack). |
| No production-run entity | ProductionPO is a monthly *order*; costing wants per-run/lot actuals (yield, loss, dates). A run table can reference ProductionPOLine later. |
| No raw-material inventory/receipt | If COGS includes supplier-purchased materials consumed at the co-packer, there is no place to receive or hold them. |
| No effective-dated costing | Costs change per run/contract year; a single scalar would repeat the `Item.forecast_current_inventory` shortcut at much higher stakes. |

**Recommendations (no design, direction only):**
1. Treat CoPacker as the anchor it already is — extend it (contacts, terms, tolling structure) rather than create a parallel "Manufacturer".
2. Introduce Material/BOM/BOMLine as new tables; **do not overload Item** with component semantics, but do decide early whether components are `Item` rows with a `kind` flag or a separate model — this is the single biggest fork in the road.
3. Put actual costs at the ProductionPO(Line)-or-run level and standard costs in an effective-dated table; never as one mutable scalar on Item.
4. Before building, migrate `Item.cases_per_batch`-style production config toward a per-(item, co_packer) profile (mirroring DistributorItemProfile) if more than one co-packer can ever make the same item — the current scalar assumes exactly one.

---

## SECTION 3 — CONSTRAINTS & DATA-INTEGRITY GAPS

### 3a. Missing uniqueness where the domain implies one

| # | Table | Implied key | Status | Severity |
|---|---|---|---|---|
| 1 | **SalesRecord** | one row per distributor invoice line | **None** (backlog #1 confirmed). New evidence in §4d: legitimate multiple rows per (account, item, day) with different quantities exist, so the proposed `(company, account, item, sale_date)` key is **not valid** — the true grain needs a line discriminator or an import-source line id. | HIGH |
| 2 | **Account** | one row per physical location per company | None. Heuristic dedup only; 4 live dup groups in dev; merge tooling exists but unused (0 merged rows). | MEDIUM |
| 3 | **Distributor** | (company, name) and/or (company, code) | None. Code auto-generation can collide ("Burke Distributing" / "Best Beverage Dist" share initials). | MEDIUM |
| 4 | **DistributorPO.so_number** | unique per company once assigned | None + race-prone MAX+1 assignment (no lock). Financially-facing identifier. | MEDIUM-HIGH |
| 5 | **InventoryImportBatch** | arguably (company, year, month) | None — multiple batches per month exist (1 dup in dev); harmless today but batch counters silently go stale after manual snapshot deletes. | LOW |
| 6 | **UserCoverageArea** | (user, coverage_type, distributor, …) | None — exact-duplicate coverage rows possible. | LOW |
| 7 | **Route** | (created_by, distributor, name) exists but created_by nullable ⇒ dup orphans allowed | Documented & accepted in code comment. | INFO |

Enforced and correct ✅: InventorySnapshot, OwnInventorySnapshot, AccountItem, EventItemRecap, DistributorPOLine, ProductionPOLine, ItemMapping, Brand, CoPacker, DistributorGroup, Company.slug, Role/Permission codenames.

### 3b. Nullable fields that look wrong (Account.distributor pattern)

| Field | Why suspicious | Severity |
|---|---|---|
| `Event.account` (SET_NULL) | Required for tastings by view logic only; 2 NULL-account tasting rows exist in dev. A conditional CheckConstraint (`event_type='admin' OR account IS NOT NULL`) can't work with SET_NULL — decide whether history-preservation or integrity wins. | MEDIUM |
| `Item.cases_per_pallet` / `cases_per_batch` | Forecast/production math needs them; code must branch on NULL forever. Dev is fully populated (0 / 1 NULL) — could be required going forward. | LOW |
| `ImportBatch.brand` | Nullable AND never written (see 3c) — should be removed, not made non-null. | (see 3c) |
| `Distributor.order_quantity_unit` | `null=True` on CharField — two "unset" states (NULL and ''). Cosmetic. | LOW |
| `DistributorPO.external_po_number` / `so_number` | Conditionally required by status, enforced only in `clean()` which the JSON endpoints bypass via `save(update_fields=…)`. Dev currently has 0 violations, but nothing stops them. | MEDIUM |
| `User.company` | NULL only for saas_admin — intentional, documented. Watch in Audit 2 (every company-scoped query must special-case it). | INFO |

### 3c. Write-only / dead fields & tables

| Field/Table | Evidence | Severity |
|---|---|---|
| **AccountItemPriceHistory** (whole table) | 815 rows, 379 account-items; written only in `events/views.py::_apply_price_updates`; zero read sites; semantics inverted (stores superseded price at supersession time; first & current price absent). Backlog #7 confirmed verbatim. | MEDIUM |
| **ImportBatch.brand** | 74/74 rows NULL; no write site found in `apps/imports/views.py` or anywhere else. Dead column + dead FK index. | LOW (cleanup S) |
| **Account.merged_into / merge_note** | Merge support fully modeled (managers, FK, note) but 0 merged rows; merge_note has no UI write/read outside admin. Built-but-unused; keep (4 dup groups want it) but note it's unproven. | INFO |
| **Account.vip_outlet_id** | 3,416/3,483 populated at import; displayed only in Django admin; docstring says "reference only". Deliberate cold storage — fine. | INFO |
| **Event.recap_qr_codes_scanned** | Written via recap form & import; read in event_detail template — alive, but dev shows it's sparsely used. Not dead. | — |
| `Distributor.notes`, `DistributorGroup.notes`, `HistoricalImportBatch.notes` | Standard blank text; populated rarely; not dead, just sleepy. | — |

### 3d. Uniqueness scope vs company — correct on all counts, with two structural notes

- All business uniqueness is company-scoped directly (`(company, name)` on Brand/CoPacker/DistributorGroup, `(company, distributor, raw_item_name)` on ItemMapping) or transitively (`(brand, item_code)`, `(distributor, item, year, month)`, `(account, item)`, `(po, item)`, `(event, item)`, `(company, item, year, month)`). **No accidentally-global business uniqueness found.** ✅
- **Deliberately global:** `Role.name/codename`, `Permission.codename` — correct for a shared RBAC vocabulary, but it means tenant-custom roles are impossible without schema change (→ Audit 2), and `Company.slug` globally unique (correct for routing).
- **Transitive company scoping is the pattern to watch under sharing:** `(distributor, item, year, month)` on InventorySnapshot is company-scoped only because a Distributor belongs to one company today. If distributors are ever shared/linked across tenants, this constraint *still holds* (good), but ownership of the row becomes ambiguous (§6c).

### 3e. Soft-delete / audit-trail inventory

| Model | is_active | merged/voided | actor fields | Hard-delete paths with no trail |
|---|---|---|---|---|
| Company, Brand, Item, CoPacker, Distributor, DistributorItemProfile, Account | ✅ is_active | Account also merged_into | — | Item/Brand deletion mostly blocked by PROTECT ✅ |
| SalesRecord | ❌ | ❌ | created_at only | **batch_delete (cascade via ImportBatch), replace-on-import `_replace_overlapping_months` — both hard-delete; only trail is free-text `ImportBatch.notes` (appended line per batch). Backlog #4 confirmed, now live in two paths.** |
| InventorySnapshot | ❌ | ❌ | created_by | Manual multi-delete endpoint (`views.py:1387`) — no trail at all, and batch counters left stale. |
| DistributorPO / lines | status incl. CANCELLED ✅ | — | created_by | PO delete endpoint hard-deletes (with lines, CASCADE); no trail. Submitted POs with SO numbers can be deleted — SO sequence gaps. |
| Event | status workflow (no delete state) | — | created_by | Draft-delete permission exists (`can_delete_event`) — hard delete, acceptable for drafts. |
| AccountItem / PriceHistory | ❌ | — | — | CASCADE from account & item. |
| User | AbstractUser.is_active ✅ | — | created_by | — |
| Route/RouteAccount, contacts, notes | ❌ | — | created_by on some | CASCADE deletes, low stakes. |

**Picture:** master data is uniformly soft-deletable (`is_active`); **transactional/financial data is uniformly hard-deleted** with one free-text compensating note on the sales path and nothing on the inventory/PO paths. Django admin log (98 rows) only covers admin-site edits. This is the single biggest audit-trail gap given COGS is next (HIGH, confirmed+extended from backlog #4).

---

## SECTION 4 — ACTUAL DATABASE STATE (dev PostgreSQL `heliumdb`)

### 4a. Schema-vs-models drift

- `python manage.py makemigrations --check --dry-run` → **"No changes detected"** ✅ (one warning: W342 on `DistributorGroup.primary_distributor`, see §1.3).
- `showmigrations` → **0 unapplied** (103 rows in django_migrations) ✅.
- Live table list vs app registry: **exact match.** 45 tables = 33 model tables + 3 M2M + 6 Django (auth_*, django_*) + django_session/admin_log/content_type/migrations. **No orphaned tables, no leftover columns detected** (information_schema column sets match model fields on the tables spot-checked: salesrecord, account, item, distributorpo).
- One historical near-miss handled correctly: `AccountNotePhoto` (created in accounts/0009, deleted in 0011) left no residue.

### 4b. Real indexes vs Meta on the high-traffic tables

Verified via `pg_indexes`. **All Meta.indexes and unique constraints exist physically** ✅. Findings are about *redundancy* and *absence*:

| Table | Indexes present | Assessment |
|---|---|---|
| **sales_salesrecord** (93k rows) | pkey; composite (company,sale_date), (account,sale_date), (item,sale_date) ✅; **plus auto single-column FK indexes company_id, account_id, item_id — all three fully redundant** (each is the leading column of a composite); import_batch_id (needed) | 🔶 3 redundant indexes on the biggest, hottest-write table — pure write/disk overhead. **Missing:** any index serving distributor-scoped queries; every such query joins through accounts (backlog #3a) and month-bucketed queries re-derive EXTRACT(year/month) with no supporting expression index (backlog #2). |
| accounts_account (3.5k) | pkey, company_id, distributor_id, merged_into_id | OK at this size. No index on normalized-address matching columns — import matching scans (fine now; revisit at 10× → Audit 4). |
| distribution_distributorpo (18) | pkey, distributor_id, created_by_id, so_number | Fine. |
| events_event (1.4k) | pkey + 6 single-col FK indexes | Fine at this size; no composite (company, date)/(ambassador, status) — only matters at scale. |
| distribution_inventorysnapshot (33) | unique (distributor,item,year,month) ✅ + redundant distributor_id + item, batch, created_by | distributor_id redundant (leading col of unique). Trivial now. |
| production_owninventorysnapshot (14) | unique (company,item,year,month) ✅ + redundant company_id | Same pattern. |
| accounts_accountitem (10.2k) | unique (account,item) ✅ + redundant account_id + item_id | account_id redundant. |
| imports_itemmapping (51) | unique (company,distributor,raw_item_name) ✅ + company_id redundant + others | Same pattern. |

Pattern finding: **Django's auto FK index + a composite/unique starting with the same column = systematic redundancy** on ~6 tables. Only material on SalesRecord today (LOW overall, MEDIUM on SalesRecord at growth).

### 4c. Table sizes & growth

Top tables by rows (exact `COUNT(*)`; `pg_stat_user_tables.n_live_tup` reads 0 across the board — **dev has never been ANALYZEd / autovacuum stats are cold**, itself a finding for query-plan quality, LOW):

| Table | Rows | Total size |
|---|---|---|
| sales_salesrecord | **93,175** | **21 MB** |
| accounts_accountitem | 10,207 | 1.8 MB |
| events_event_items (M2M) | 5,439 | 1.3 MB |
| events_eventitemrecap | 5,378 | 1.3 MB |
| accounts_account | 3,483 | 1.8 MB |
| events_event | 1,377 | 944 kB |
| accounts_accountitempricehistory | 815 | 176 kB |
| imports_importbatch | 74 | 128 kB |
| routes_routeaccount / poline / itemmapping | ≤58 | <150 kB |
| everything else | ≤51 | noise |

**Growth-dominant:** SalesRecord (≈70% of all rows, ≈70% of disk incl. its 8 indexes) — and it is the table with no uniqueness, hard-delete churn, and 3 redundant indexes. AccountItem and the event tables grow linearly with activity; everything else is configuration-sized. Single company (1 row), 1 brand, 8 items, 10 distributors, 8 users — **all multi-tenant code paths are effectively untested by data** (→ Audit 2).

### 4d. Data-health spot checks (read-only)

| Check | Result | Reading |
|---|---|---|
| Exact duplicate SalesRecords (company,account,item,date,**qty**) | **10 groups** | True dupes at the strictest grain, all within a single import batch — import-side dup rows, not double-imports. |
| Day-grain duplicates (company,account,item,date) | **75 groups / 150 rows** | Mostly 2 rows with *different* quantities in the *same* batch ⇒ **legitimately multiple invoice lines per day exist** — critical input to the future UniqueConstraint design (invalidates the simple 4-column key). |
| sale_date day-of-month spread | All 31 values | Data is genuinely daily; the monthly-consumption mismatch (backlog #2) is a modeling choice, not a data artifact. |
| sale date range | 2020-01-02 → 2026-05-29 | 6.4 years of history. |
| `ImportBatch.distributor` vs `account.distributor` on sales rows | **0 mismatches** | The dual source of truth (backlog #3) is *currently consistent* in dev. |
| SalesRecord.company vs account.company / account.company vs distributor.company / event.company vs account.company | **0 / 0 / 0** | No cross-tenant leakage in dev (only 1 company, so weak evidence — re-run after tenant #2). |
| Sales rows on merged / inactive accounts | 0 / 0 | Clean. |
| Duplicate active accounts (company, name, address_normalized) | **4 groups** (incl. one ×3) | Live evidence for finding 3a-2; merge tool exists, unused. |
| Accounts: auto_created / inactive / merged | 3,479 / 3 / 0 | Account base is ~entirely import-born. |
| Tasting events with NULL account / events with NULL date | 2 / 1 | Minor warts (3b). |
| DistributorPO status-rule violations (ACTUAL w/o PO#, SUBMITTED w/o SO#) | 0 / 0 | Clean despite no DB enforcement. |
| Duplicate SO numbers per company | 0 | Clean today; race remains. |
| ItemMapping: 51 all `mapped`; ImportBatch: 74 all `complete` | — | No stuck imports. |
| InventorySnapshot with NULL import_batch | 0 | No orphaned snapshots yet. |
| AccountItem.current_price populated | 1,120 / 10,207 (11%) | Price capture is recap-driven and sparse, as designed. |
| Events: 1,358 of 1,377 are `paid` + `is_imported` | — | Event table is dominated by the historical import (one HistoricalImportBatch row, 1,358 events). |
| dead-field populations | ImportBatch.brand 0/74; ItemMapping.brand 32/51; price-history 815 rows | Confirms 3c. |

### 4e. Production-only checks (DO NOT run by the audit — run these against prod before/at deploy)

1. **Deploy gate for `accounts/0013` (already flagged in backlog):**
   `SELECT COUNT(*) FROM accounts_account WHERE distributor_id IS NULL;` — must be 0 before migrating prod.
2. **Real duplicate pressure at the strict grain:**
   `SELECT company_id, account_id, item_id, sale_date, quantity, COUNT(*) FROM sales_salesrecord GROUP BY 1,2,3,4,5 HAVING COUNT(*)>1;`
3. **Whether multi-line-per-day is real in prod too (decides the unique-key design):**
   `SELECT COUNT(*) FROM (SELECT company_id, account_id, item_id, sale_date FROM sales_salesrecord GROUP BY 1,2,3,4 HAVING COUNT(*)>1 AND COUNT(DISTINCT quantity)>1) x;`
4. **SO-number duplicates:**
   `SELECT d.company_id, p.so_number, COUNT(*) FROM distribution_distributorpo p JOIN distribution_distributor d ON d.id=p.distributor_id WHERE p.so_number IS NOT NULL GROUP BY 1,2 HAVING COUNT(*)>1;`
5. **Duplicate accounts:**
   `SELECT company_id, UPPER(name), address_normalized, COUNT(*) FROM accounts_account WHERE merged_into_id IS NULL AND is_active GROUP BY 1,2,3 HAVING COUNT(*)>1;`
6. **Batch-vs-account distributor consistency:**
   `SELECT COUNT(*) FROM sales_salesrecord s JOIN accounts_account a ON a.id=s.account_id JOIN imports_importbatch b ON b.id=s.import_batch_id WHERE a.distributor_id <> b.distributor_id;`
7. **Stats freshness / autovacuum:** `SELECT relname, n_live_tup, last_autoanalyze FROM pg_stat_user_tables ORDER BY n_live_tup DESC LIMIT 10;` — if n_live_tup is 0 like dev, planner stats are cold.

---

## SECTION 5 — MIGRATION CHAIN HEALTH

### 5a. Counts & squash candidates

| App | Files | Notes |
|---|---|---|
| core | 19 | **10 of 19 are RBAC permission/role data migrations** (0004, 0006–0015 minus 0016). Prime squash candidate — better: replace the migration-per-permission pattern with an idempotent `sync_permissions` management command / post-migrate hook, since permission vocabulary will keep growing. Contains one squash already (`0002_squashed` replaces 0002–0004). |
| distribution | 18 | Mixed schema + 3 data migrations; moderate squash candidate after prod converges. |
| events | 13 | Contains `0001_squashed` (replaces 0001–0003). Replaced originals still present (correct until all DBs converge; delete after). |
| accounts | 13 | Includes the add-then-remove churn of AccountNote fields (0009→0011→0012) — net-zero noise, squashable. |
| catalog | 8, imports 6, production 3, sales 2, routes 2, event_import 1 | Healthy. |

Duplicate-numbered files (core 0002/0003/0004 ×2, events 0001–0003 ×2) are squash artifacts with proper `replaces=` — **not** broken parallel chains. Both squashes exist specifically because the original migrations referenced the long-deleted `distribution.Account` model — historical FK rot that was correctly repaired.

### 5b. RunPython inventory & fresh-replay safety (20 files)

| Migration | Fresh-DB replay | Notes |
|---|---|---|
| core/0004_permission_role_data | ✅ safe | The authoritative RBAC seed; pure creates. |
| core/0006 redirect-permission | ⚠️ **fragile** | Bare `Role.objects.get(...)` / `Permission.objects.get(...)` with **no guard** — replays fine in-order today (0004 creates both), but breaks if a role codename is ever renamed/removed earlier in the chain, and breaks the squash story. The only one of the permission migrations with zero defensive handling. |
| core/0007–0015 permission tweaks (8 files) | ✅ mostly guarded | try/except DoesNotExist or get_or_create patterns; 0008/0012 fully defensive. (accounts/0008, accounts/0010 same family, guarded.) |
| catalog/0006_seed_co_packers | ✅ harmless | Filters for company named "drink up life"; silently no-ops otherwise. **Hardcodes a real customer's company name in the codebase.** |
| catalog/0007_seed_co_packers_all_companies | ⚠️ **tenant-onboarding wart** | Seeds 'Brotherhood Winery' & 'Nidra Packaging' (Señor Sangria's actual co-packers) into **every company that exists at migrate time**. On the current DB it already ran; but any *fresh* environment that creates companies before migrating, and any future squash that re-runs it, will inject one tenant's vendor names into other tenants. Irreversible by design (backwards=pass). |
| distribution/0014, 0015 (code backfills) | ✅ safe | Empty-table ⇒ no-op; idempotent re-derivation. 0015 duplicates the `_generate_code_from_name` algorithm (copy drift risk if model logic changes again — INFO). |
| distribution/0018_seed_sort_position | ✅ safe | Empty ⇒ no-op. |
| events/0004 festival→special_event | ✅ safe | Filter+update, reversible. |
| accounts/0006 remove state coverage | ✅ safe | Delete-by-filter, noop reverse. |

### 5c. Wart patterns

- **No `reverse_code=None`-without-reason cases**: every RunPython has an explicit reverse (noop or real). ✅
- **Irreversible-by-choice**: catalog/0007 backwards is an intentional pass (comment explains why) — acceptable.
- **Customer data baked into migrations**: catalog/0006 + 0007 (company name, vendor names). This is the main wart class; it converts a one-tenant convenience into a multi-tenant contamination risk and should be the **last** time seed data ships as a migration (use fixtures/admin instead).
- **Permission system delivered as 10 incremental data migrations**: works, but each new feature ships another one, several with copy-pasted role lookups of varying defensiveness. Structural recommendation: idempotent sync command + one squash. (M)
- No manually-edited/fake-default migrations detected; `--check` is clean; the two squashes are properly formed.

---

## SECTION 6 — GRAIN & DOMAIN-SHAPE ASSESSMENT

### 6a/6b. Grain per core table (known issues re-verified 2026-06-11)

| Table | One row means | Enforced? | Backlog cross-ref / status |
|---|---|---|---|
| **SalesRecord** | One distributor invoice line (account, item, day, qty, price) | ❌ **No** | Backlog #1 **confirmed open**. New nuance from §4d: same-day multi-line rows with differing quantities are real, so the assumed `(company, account, item, sale_date)` key is wrong — uniqueness work must first *define* the line grain (likely needs source line number, or accept (…, quantity)-inclusive keys with their false-dup risk). Backlog #2 (daily-vs-monthly) **confirmed**: dates span all 31 days; every consumer aggregates monthly; no month-serving index. |
| **InventorySnapshot** | (distributor, item) on-hand for a month | ✅ unique_together | Healthy. |
| **OwnInventorySnapshot** | (company, item) on-hand for a month | ✅ unique_together | Healthy; named best-precedent pattern in backlog #7. |
| **DistributorPO / Line** | One order (deliberately N per dist-month); line = (po, item) | Header: by-design no key ✅ intent, Line: ✅ | Status-conditional fields (PO#, SO#) enforced only in `clean()`, bypassed by `update_fields` save paths; SO sequence race (§3a-4). |
| **EventItemRecap** | (event, item) recap numbers | ✅ unique_together | Healthy. |
| **AccountItem** | "Item has been sold at account" | ✅ unique_together | Healthy except never-recalculated `date_first_associated` (backlog #6 **confirmed**, still LOW). |
| **ImportBatch** | One *upload* (possibly many months) | n/a | Backlog #5 **confirmed**: batch grain ≠ analytic grain (month); replace-on-import live with the notes-append workaround; partially-gutted batches are an accepted, visible artifact. Dual-distributor-truth (#3): account-side now non-null+PROTECT (**done**), denormalize-onto-SalesRecord half still open; dev shows 0 batch/account distributor mismatches today. |

### 6c. Forward-looking: distributor-as-tenant data sharing — friction map

Premise (not designing it): a Distributor could become a tenant that imports *its own* sales data and shares it with supplier tenants. Today every row has exactly one owner (company FK direct or transitive). Assessment of what is **neutral** vs what would **fight**:

**Neutral / already well-shaped:**
- **InventorySnapshot, DistributorItemProfile, DistributorPO/Line** — keyed by distributor, not company. The natural-key shapes survive a world where the distributor side owns the data; only *row ownership/visibility* needs a layer, not a reshape.
- **OwnInventorySnapshot, ProductionPO/Line, CoPacker, Brand/Item identity** — purely supplier-side; sharing never touches them.
- **ItemMapping's existence** — a per-(company, distributor) vocabulary-translation table is *exactly* the right concept for cross-tenant item resolution; its uniqueness key already anticipates "the same raw code means different things at different distributors."
- **Company-scoped uniqueness generally** — none of the business keys would *break* under sharing (no global business uniqueness exists to collide).

**Friction points (each would actively fight a sharing feature if further entrenched):**

| # | Structure | Why it fights | Sev |
|---|---|---|---|
| F1 | **SalesRecord.account → supplier-owned Account** (PROTECT, non-null) | The hardest one. A distributor-imported sales row must reference an account — but Account is a supplier-tenant CRM object (auto_created, merge state, coverage areas, events hang off it). Sharing requires either account-identity mapping across tenants or splitting "retail location identity" from "supplier CRM wrapper". Every feature that further entangles Account (notes, contacts, routes, events — all already CASCADE/SET_NULL off it) deepens this. **Don't add more required supplier-side semantics to Account.** | HIGH (awareness) |
| F2 | **SalesRecord.company = single owner** + import_batch CASCADE | Fine today; under sharing, "whose row is this" vs "who may read it" must separate. The CASCADE delete from a supplier-owned ImportBatch would be wrong for distributor-imported rows. Keep all new sales-adjacent features reading via a single query path (they already mostly do) so a visibility layer can be inserted in one place. | MEDIUM |
| F3 | **AccountItem auto-creation inside the sales import** | Import (data ingestion) writes CRM state (account_items, accounts_created, reactivation) as a side effect. A distributor-run import must not mutate a supplier's CRM. The coupling lives in `imports/views.py`, not the schema — but the longer import-side-effects accrete, the harder the split. | MEDIUM |
| F4 | **Distributor is company-owned with no notion of identity beyond the tenant** | Two suppliers using the same real-world distributor = two unrelated Distributor rows (model docstring already acknowledges "cross-tenant distributor sharing is a future feature"). Neutral *if* nothing new assumes distributor-uniqueness-per-real-entity; the auto-generated `code` is presentation-only today — keep it that way. | INFO |
| F5 | **ImportBatch.notes as the only mutation audit** | Free-text audit on a supplier-owned object cannot serve as the trail for another tenant's data corrections. The soft-delete/audit work (3e) should be designed tenant-agnostic (actor + timestamp + scope on the row or a dedicated log), which it would naturally be — just don't deepen the notes-append pattern. | MEDIUM |
| F6 | **`Item.forecast_current_inventory`-style "one scalar on a shared entity"** | Any new per-item operational value added as a scalar on Item implicitly assumes one owner-context per item. Under distributor-tenancy, per-context values belong on junction tables (DistributorItemProfile is the right precedent). | LOW (pattern guard) |

**Net:** nothing already built *blocks* the sharing future; the schema's junction-table discipline is genuinely good. The two things to protect going forward: (1) stop enriching `Account` with supplier-only required semantics, (2) route all sales reads/mutations through narrow choke-points so ownership/visibility can later be inserted once.

---

## SECTION 7 — PRIORITIZED FINDINGS SUMMARY

Cost: S (<½ day), M (days), L (week+). Severity ordered. "New" = not in REFACTORING_BACKLOG.md.

| ID | Sev | Finding | Why it matters | Cost | Backlog |
|---|---|---|---|---|---|
| D1 | HIGH | SalesRecord has no uniqueness; 10 exact-dup groups live in dev; legitimate same-day multi-line rows mean the assumed (company,account,item,date) key is **invalid** — true line grain undefined | No reconcile key for COGS/QB sync; silent doubling risk; the fix needs grain *definition* first, not just a constraint | M | #1 confirmed + **new evidence** |
| D2 | HIGH | No soft-delete/audit trail on financial mutations: two live hard-delete paths into SalesRecord (batch CASCADE, replace-on-import), plus untrailed InventorySnapshot & DistributorPO deletes | Corrections become financially material with COGS; unrecoverable + untraceable today | M-L | #4 confirmed + **extended (inventory/PO paths new)** |
| D3 | HIGH | Migration `catalog/0007` seeds one tenant's real co-packer vendors into every company; 0006 hardcodes a customer name | Direct tenant-data contamination pattern at the exact moment external tenants onboard; sets precedent | S (stop pattern; optional guard migration) | **New** |
| D4 | HIGH (awareness) | Sharing-future friction F1: SalesRecord→Account entanglement (supplier CRM object as the sales-row anchor) | Every new required supplier-side semantic on Account makes distributor-as-tenant harder; needs only discipline now | S (guard rail, no code) | **New** |
| D5 | MED-HIGH | SO number: MAX+1 assignment with no lock and no unique constraint per company | Duplicate financially-facing SO numbers under concurrency; cheap to fix (constraint + select_for_update or sequence) | S | **New** |
| D6 | MEDIUM | DistributorPO status-conditional rules (PO#/SO# required) enforced only in `clean()`, bypassed by `save(update_fields)` JSON paths | Invalid actual/submitted POs possible; dev clean today | S | **New** |
| D7 | MEDIUM | Account has no uniqueness; 4 live duplicate-account groups in dev; merge tool built but never used (0 merged) | Account is the join point for sales, events, routes; dups split history | M (dedup + decide key) | **New** (tooling exists) |
| D8 | MEDIUM | Distributor has no (company, name) or (company, code) uniqueness; code auto-gen can collide | Distributor is a hub (9 inbound FKs); a dup or code collision corrupts grouping/forecast UX | S | **New** |
| D9 | MEDIUM | AccountItemPriceHistory: write-only, semantics inverted (815 rows of unreadable history) | Misleading if ever surfaced; wrong template for the planned ending-inventory feature | S (kill) / M (fix semantics) | #7 confirmed |
| D10 | MEDIUM | `AccountItem.item` is CASCADE while all other Item FKs are PROTECT | Item deletion can silently destroy distribution history (+price history) | S | **New** |
| D11 | MEDIUM | Daily sale_date vs monthly consumption: all consumers aggregate by month via Extract; no month-serving index | Every report re-derives month buckets; rollups can't be indexed; worsens at scale | M | #2 confirmed |
| D12 | MEDIUM | Dual distributor truth, remaining half: distributor reached only via account join on the 93k-row table | Join cost on every distributor-scoped read; (account-side hardening DONE) | M | #3 part-open |
| D13 | MEDIUM | RBAC Role/Permission are global, delivered via 10 incremental data migrations of mixed defensiveness (core/0006 unguarded) | Tenant-custom roles impossible; migration chain brittle; full treatment in Audit 2 | M | **New** (structure) |
| D14 | MEDIUM | Sharing friction F2/F3/F5: single-owner sales rows w/ supplier-batch CASCADE; import writes CRM side effects; notes-append audit | None blocks today; each deepens the wall against distributor-tenant sharing | — (design guards) | **New** |
| D15 | MEDIUM | ImportBatch grain = upload, not month; partially-gutted batches accepted with notes workaround | Import history shows stale/overlapping batch rows; audit relies on free text | M-L | #5 confirmed |
| D16 | LOW | 3 fully-redundant single-column indexes on sales_salesrecord (+same pattern on 5 smaller tables) | Write amplification & disk on the growth-dominant table | S | **New** |
| D17 | LOW | `ImportBatch.brand` dead: nullable, never written (74/74 NULL) | Dead column + dead index; confuses readers | S | **New** |
| D18 | LOW | Company on_delete inconsistent: Route + HistoricalImportBatch CASCADE vs 14× PROTECT | Unreachable today (other PROTECTs fire first) but undecided delete semantics | S | **New** |
| D19 | LOW | `Event.account` SET_NULL leaves NULL-account tastings (2 in dev), 1 NULL-date event | "Tasting requires account" only true at creation; minor report edge cases | S | **New** |
| D20 | LOW | `ItemMapping.mapped_item` SET_NULL doesn't reset status — `mapped` row with NULL item possible | Import would treat it as mapped and fail late | S | **New** |
| D21 | LOW | DistributorGroup.primary_distributor: FK(unique=True) → W342, should be OneToOne | Cosmetic; silences a system check | S | **New** |
| D22 | LOW | `Distributor.order_quantity_unit` null=True on CharField; `'Unknown'` string sentinels on Account.county/on_off_premise | Two unset states; sentinel strings leak into reports/grouping | S | **New** |
| D23 | LOW | InventoryImportBatch counters go stale after manual snapshot deletes; no (company,year,month) key (1 dup) | Mini version of D15; cosmetic today | S | **New** |
| D24 | LOW | `ProductionPOLine.quantity_cases` stores derived batch_count×cases_per_batch; drifts if item config changes | Wrong case totals in projections after config edits | S | **New** |
| D25 | LOW | Dev PG statistics cold (n_live_tup=0 everywhere; never analyzed) | Dev query plans unrepresentative; check prod (§4e-7) | S | **New** |
| D26 | LOW | Timestamps missing on EventItemRecap, RouteAccount, AccountItem (PK-only audit) | Recap edit times unrecoverable — matters for payroll disputes | S | **New** |
| D27 | INFO | CoPacker structurally a name-card: no BOM, no materials, no costs, no UoM, no run entity (full gap list §2d) | Defines the COGS build surface; nothing to fix, everything to add | — | **New** (maps the build) |
| D28 | INFO | `AccountItem.date_first_associated` never recalculated after replace | First-seen reporting drift | S | #6 confirmed |
| D29 | INFO | Squash artifacts retained (core, events double-numbered files w/ replaces=); accounts 0009→0012 churn; core 19-file chain | Housekeeping; squash after prod converges; move permission seeding to a sync command | M | **New** |
| D30 | INFO | DB_REVIEW.md stale (23 vs 33 models) | Superseded by this audit; mark or remove | S | **New** |
| D31 | INFO | Single-tenant data reality: 1 company, all multi-tenant paths untested by data; dev cross-tenant checks all pass trivially | Audit 2 must rely on code inspection, not data evidence | — | **New** |

**Counts: 0 CRITICAL · 4 HIGH (D1–D4, one awareness-class) · 1 MED-HIGH (D5) · 10 MEDIUM · 11 LOW · 5 INFO — 31 findings.**
Nothing found that is actively corrupting data today; the HIGH items are all "the next feature wave (tenants, COGS) lands on this exact spot."

### Backlog cross-reference

| Backlog item | This audit |
|---|---|
| #1 SalesRecord uniqueness (HIGH) | **Confirmed + updated**: proposed key invalidated by live multi-line-per-day data (D1); prod query supplied (§4e-3) |
| #2 Daily-vs-monthly dating (MED) | **Confirmed** unchanged (D11); data verified genuinely daily |
| #3 Dual distributor truth (HIGH) | **Half done** (Account.distributor non-null+PROTECT verified in schema & 0 mismatches); denormalization half open (D12); **prod deploy gate still outstanding** (§4e-1) |
| #4 No soft-delete/audit for sales (MED-HIGH) | **Confirmed + extended** to inventory-snapshot and PO delete paths (D2) |
| #5 Batch grain ≠ analytic grain (MED) | **Confirmed**, workaround live as described (D15, D23 mini-variant) |
| #6 date_first_associated (LOW) | **Confirmed** unchanged (D28) |
| #7 Price history write-only (MED) | **Confirmed** verbatim, dev population quantified: 815 rows/379 items, 0 read sites (D9) |

**New findings not in the backlog:** D3–D8, D10, D13–D14, D16–D27, D29–D31.
