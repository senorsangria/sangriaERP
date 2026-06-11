# Audit 4/6 — Query Efficiency, Indexes & Scalability

**Date:** 2026-06-11
**Scope:** N+1/query efficiency, index coverage vs real query patterns, the 3.5MB events listing, import/bulk scalability, and the growth trajectory to 1-5 tenants.
**Method:** Read-only, **measured not guessed**: every heavy view exercised through Django's test client against dev data with `connection.queries` counting and response-size capture; key SQL shapes run through `EXPLAIN ANALYZE` on the dev Postgres; `pg_stat_user_indexes` scan counts collected; all list/report/import code paths read.
**Builds on:** audit 01 §4b (real indexes), §4c (table sizes: SalesRecord 93,175 rows / 21MB dominates), D11/D12/D16/D25; audit 02 §6 (tenancy index shape).

**Headline:** The codebase is **query-disciplined — no significant N+1 anywhere** (heaviest pages run 8-26 queries; select_related/prefetch_related used correctly throughout; aggregations are DB-side). The problems are different: (1) the events listing ships **3.54MB of HTML** because it renders *every* event, in *both* tabs, in *two* layouts each, with no pagination — reproduced exactly in dev; (2) **production is served by `manage.py runserver`** (run.sh → `.replit [deployment]`), Django's single-threaded dev server, so every one of those 3.5MB renders blocks every other user; (3) the known structural gaps (distributor-via-account join, EXTRACT month-bucketing, redundant indexes) are now confirmed at the query-plan level with real timings — fine today at 93k rows, linear pain as data multiplies.

### Measured baseline (dev data: 1,377 events, 3,483 accounts, 93,175 sales rows)

| View | Queries | Response size | Verdict |
|---|---|---|---|
| **event_list (?tab=active)** | 14 | **3,540,807 B** | The 3.5MB page — §3a |
| **event_list (?tab=past)** | 14 | **3,540,903 B** | Same size — both tabs always rendered |
| report: account sales (top distributor) | 23 | **1,636,389 B** | Unpaginated full-table report — §3c |
| account_list | 11 | 251,196 B | Paginated (100/page) ✓ |
| forecast tab, computed (dist 10 = 38k sales rows) | 22 | 150,597 B | Healthy |
| report: item sales / distribution | 23 / 26 | ~139 KB | Healthy |
| distributor_list (all 4 tabs) | 13 | ~117 KB | Healthy |
| production_home | 22 | 109 KB | Healthy (lines prefetched) |
| batch_list | 8 | 79 KB | Healthy |
| account_detail_combined (?tab=sales, busiest account) | 19 | 75 KB | Healthy |
| report: account detail | 20 | 57 KB | Healthy |
| account-sales CSV export | 13 | 35 KB | Healthy |
| PO modal / suggest / production PO modal | 8-14 | 0.4-1.7 KB | Healthy |
| events CSV export | 10 | 2 KB | Healthy |

---

## SECTION 1 — N+1 AND QUERY-EFFICIENCY AUDIT

### 1a/1b. N+1 findings: effectively none

Systematic check of every heavy view (loops + template FK access vs select_related/prefetch_related):

| View | Related-object handling | Result |
|---|---|---|
| event_list | `_get_visible_events` does `select_related('account','ambassador','event_manager','created_by','account__distributor')` (events/views.py:145-148) — covers every FK the 697-line template touches | ✅ 14 queries for 1,377 rendered events |
| account_list | `select_related('distributor')` (views.py:247); template touches only `account.distributor.name` | ✅ 11 queries |
| distributor_list (all tabs) | Distributors + snapshots + POs fetched as scoped querysets; forecast computed only for the selected distributor/group | ✅ 13-22 queries |
| production_home | `ProductionPO…prefetch_related('lines__item')` (production/views.py:93); snapshots via single scoped queryset | ✅ 22 queries |
| account_detail_combined | account_items `select_related('item__brand')`, recent events via select_related'd visible queryset, sales aggregated DB-side | ✅ 19 queries |
| reports (all five) | Pure `values().annotate(Sum)` aggregation + one `pk__in` account fetch | ✅ 20-26 queries |
| batch_list / mapping_list | `select_related('distributor','brand','mapped_item__brand')` | ✅ 8 queries |
| import preview/history | In-memory dict lookups; counts via aggregate queries | ✅ |

Two *query-fan-out* (not N+1) notes inside otherwise-clean views:

- **event_list sidebar recomputation:** `available_cities`/`available_counties` each evaluate a re-filtered UNION (`(qs_no_city | paid_qs_no_city).distinct()`) — 4 extra filtered evaluations of the full visible-events set per page load (events/views.py:467-496), plus `dates()`, creators, and two distributor-pk scans. All cheap at 1.4k events; linear growth with event count. (Bundled into F1's fix — these lists only matter for the filter sidebar.)
- **`_detect_overlap` (imports/views.py:666-677):** 2 COUNT queries per overlapping (distributor, month) for the preview, plus one big OR'd total query. Monthly-cadence imports touch 1-2 months → fine. A multi-year initial backfill over existing data could hit hundreds of EXTRACT-filtered counts — noted in §4a.

### 1c. Aggregation placement: DB-side everywhere it matters ✅

- Reports: `values('account_id','sale_date__year').annotate(Sum('quantity'))` — DB-side (reports/views.py:383-399 and equivalents in all five reports).
- Forecast: `values('item_id','sale_date__year','sale_date__month').annotate(units=Sum('quantity'))` — DB-side (forecast.py:270-272, 479-481); the forward inventory walk then iterates *month buckets*, not rows — correct design.
- Account detail sales tab: per-month sums DB-side.
- **No large-table Python-side aggregation exists.** The only Python-side loops over query results are over already-aggregated buckets or page-sized row sets. The one Python sort of a full table: `_sort_events` materializes all visible events and sorts in Python (events/views.py:265-292) — necessary for its 7-group custom order, and the real cost there is rendering, not sorting (1,377 objects sort in ~ms).

---

## SECTION 2 — INDEX COVERAGE vs QUERY PATTERNS

### 2a. Actual indexes vs actual filters, high-volume tables

Measured with `EXPLAIN ANALYZE` on dev (93k sales rows). Three canonical shapes:

**Shape C — company + date range (reports' last-12-months, dashboards):**
```
Index Scan using sales_sales_company_de19d4_idx  (company_id=3 AND sale_date BETWEEN …)
Execution Time: 3.0 ms  (13,053 rows)
```
✅ The `(company, sale_date)` composite does exactly its job.

**Shape A — distributor-scoped month rollup (the forecast, every distributor report):**
```
HashAggregate ← Nested Loop:
  Index Scan accounts_account_distributor_id (719 accounts)
  → 719× Index Scan sales_salesrecord_account_id  (53 rows avg each)
Execution Time: 21.3 ms  (38,046 rows aggregated)
```
⚠️ Works, but it's 719 inner index probes — cost is linear in (accounts × rows/account). No index can serve "this distributor's sales by month" directly because **distributor isn't on the table** (audit 01 D12). At 10× data: ~200ms; at distributor-tenant import volumes (millions of rows): seconds, on every forecast/report load.

**Shape B — year-IN filter (account-sales report):**
```
…719× Index Scan by account_id, then Filter: EXTRACT(year FROM sale_date) = ANY(…)
  Rows Removed by Filter: 21 (of ~52 per account)
Execution Time: 20.7 ms  (22,646 rows kept, ~40% fetched-then-discarded)
```
⚠️ The EXTRACT filter is unindexable as written (audit 01 D11 confirmed at plan level): rows are fetched and 40% thrown away post-read. Same pattern in `sale_date__year/__month` filters across reports, replace-on-import deletes, and `_detect_overlap`.

Other tables: Account (3.5k), Event (1.4k), DistributorPO (18), InventorySnapshot (33), OwnInventorySnapshot (14), AccountItem (10k) — all current query patterns are pk/FK/unique-index served; nothing slow is possible at these sizes, and the unique composites (audit 01 §4b) serve the upsert paths.

### 2b. Missing indexes / structures

| Missing | Query it serves | Benefit | Note |
|---|---|---|---|
| `distributor` FK on SalesRecord + `(distributor, sale_date)` index | Forecast rollups, all 5 reports, replace-on-import deletes, overlap detection — everything currently joining through accounts | Turns Shape A/B nested loops (719 probes) into one index range scan; removes the join from the hottest read path | This is audit 01 **D12** — a schema change (denormalization), not a pure index add; the single highest-leverage structural fix for read scaling |
| Month-serving structure: either a `sale_month` DateField (first-of-month) with `(company, sale_month)` / `(distributor, sale_month)` indexes, or rewriting `__year/__month` filters as date *ranges* (`sale_date__gte/lt`) which the existing composites already serve | All month-bucket aggregation and month-grain deletes | Eliminates fetch-then-discard (Shape B) and enables index range scans for month windows | Audit 01 **D11**. The *code-only* variant (date ranges instead of EXTRACT) is an S-cost quick win for filters; GROUP BY month still needs EXTRACT but that's cheap once rows are range-scanned |
| (Nothing else) | — | — | At current and 10× sizes no other filter pattern lacks support; Event/Account/PO tables are config-sized or already covered |

### 2c. Redundant/unused indexes — confirmed with live scan counts

`pg_stat_user_indexes` after this audit's instrumented page loads (representative of real access patterns):

| Index | Scans | Verdict |
|---|---|---|
| sales `(account_id, sale_date)` composite | 11,516 | **The workhorse** (every distributor-scoped query rides it) |
| sales `account_id` single | 7,277 | **Redundant** — same leading column as the composite; planner uses it interchangeably; drop |
| sales `company_id` single | 0 | **Redundant** (composite `(company, sale_date)` leads on it); drop — audit 01 D16 confirmed |
| sales `(item_id, sale_date)` composite | 0 | Kept by doubt: item-scoped queries all go through the account join today; the item-sales report aggregates per item *within* distributor rows. Candidate to drop **after** D12 lands (a distributor column changes the plan space); until then harmless-ish but unused |
| sales `item_id` single | 0 | **Redundant** with the composite regardless; drop |
| accounts `company_id` | 0 | Low value at table size but correct to keep for tenant-leading scans as tenants multiply |
| accountitem `account_id` single | 3 | Redundant with unique `(account, item)`; drop |
| events single-col FK indexes ×6 | ≤34 | Fine; table is small; revisit only at scale |

Net: **4 confirmed droppable on SalesRecord + 1 on AccountItem** — pure write-amplification savings on the two biggest tables (8 indexes currently maintained per SalesRecord insert; imports write 1,000-row chunks × 8 index updates each).

### 2d. Tenancy index dimension (audit 02 §6 deep check)

Confirmed: every high-volume table is company-leading or company-owned-object-leading — `(company_id, sale_date)` on sales; `company_id` on account/event; distributor/account/po-leading composites on the rest, all reached through company-scoped parents. **No index shape forces tenant A's queries to scan tenant B's rows.** The genuine cross-tenant performance coupling is at the *process* level, not the index level: one `runserver` process (§3/F2) and one shared Postgres buffer pool. With ≤5 tenants the index picture needs nothing tenant-specific.

---

## SECTION 3 — THE HEAVY-PAYLOAD PROBLEM

### 3a. The events listing 3.5MB — diagnosed precisely

Reproduced in dev: **3,540,807 bytes, 14 queries** — so it is not a query problem; it is a rendering-volume problem with four multipliers:

1. **No pagination:** all 1,377 visible events are materialized (`_sort_events` lists the active set; `paid_events = list(paid_qs…)` lists all 1,358 paid events — events/views.py:535-537).
2. **Both tabs always rendered:** the template emits the *Active* and *Past* tab-panes in every response regardless of `?tab=` (Bootstrap tab-panes at event_list.html:113 and :251 — the 'past' request returns the same 3.54MB).
3. **Every event rendered twice per tab:** a mobile card block *and* a desktop table row (`d-lg-none` cards + `d-none d-lg-block` table — :130/:198 and :267/:334), with the unused layout merely CSS-hidden.
4. **~1.3KB of markup per rendering** (card divs, badge classes, per-row URLs).

Arithmetic: 1,377 events × 2 layouts ≈ 2,754 rendered blocks × ~1.3KB ≈ 3.5MB. ✔ matches observed.

The dominant content is the **1,358 PAID events (98.6% of rows)** — historical imports nobody scrolls through on the Active tab. Fix directions (recommendation only): render only the requested tab server-side (immediately halves it); paginate or lazy-load the Past tab (cuts the remaining 97%); the mobile/desktop double-render is the polish item after those. Note the sidebar fan-out queries (§1a) shrink naturally with the same change.

### 3b. Pagination audit — every list view

| View | Paginated? | Current rows | Trajectory |
|---|---|---|---|
| event_list (active+past) | ❌ | 1,377 | **Grows with every tenant's event history — the 3.5MB page is day-one reality for any tenant importing history** |
| account_list | ✅ 100/page | 3,483 | Safe |
| distributor POs tab | ✅ 50/page | 18 | Safe |
| report: account sales | ❌ (deliberate full-table) | 2,190 rows → 1.6MB | Grows with accounts-with-sales per distributor; at 5-10k accounts → 4-8MB. Client-side sortable table is the feature; needs a ceiling eventually (§3c) |
| reports: item sales / distribution | ❌ | item-count-bounded (8 items) | Safe by shape (rows = items/buckets, not accounts) |
| batch_list | ❌ | 74 | Slow growth (per import); fine for years |
| mapping_list | ❌ | 51 | Bounded by catalog × distributors; fine |
| user_list / distributor_list / group list | ❌ | ≤10 | Config-sized; fine |
| inventory snapshots tab | ❌ | 33 | Grows ~items×distributors×months; revisit at a few thousand |

### 3c. Other large payloads

- **report: account sales — 1,636,389 B** measured. Same family as event_list but legitimately a full-table working report; it degrades linearly with account count. CSV export of the same data is 35KB — the HTML table markup is ~45× the data. Medium-term: server-side pagination/virtualization or a row ceiling with "export for full set".
- All modal-data JSON endpoints: 0.4-1.7KB ✅. CSV exports stream-build in memory but at KB sizes ✅. No other view exceeded ~250KB in measurement.

---

## SECTION 4 — IMPORT & BULK-OPERATION SCALABILITY

### 4a. The sales import path (imports/views.py:758-940)

Well-engineered core: mappings and all existing accounts pre-loaded into dicts (3 queries regardless of size), new accounts `bulk_create` in 500s, SalesRecords `bulk_create` in 1,000s, batch stats updated once. Scaling assessment per stage:

| Stage | Cost shape | Strain point |
|---|---|---|
| CSV parse + normalize | O(rows) in memory, whole file in RAM (temp file re-parse at confirm) | ~500k+ rows → memory + request-timeout risk (sync request, no background job) |
| Account dict pre-load | 2 queries, O(accounts) memory | Fine to 100k accounts |
| SalesRecord bulk_create | rows/1000 queries × 8 index updates/row (→ 4 after §2c drops) | Fine to ~1M rows per import if run async; the 8-index write amplification is the multiplier |
| **AccountItem `get_or_create` loop (:917-925)** | **1-2 queries per unique (account,item) pair, inside the open transaction** | **The bottleneck.** Dev already has 10,207 pairs; a new tenant's initial history import with ~1,500 accounts × 5 items ≈ 7,500 pairs ≈ **10-15k sequential queries** → minutes of wall-time *inside one transaction* (locks held, replace-delete uncommitted). Fix shape (S): one `filter(account__in, item__in).values_list` to find existing pairs + one `bulk_create(ignore_conflicts=True)` |
| `_detect_overlap` preview | 1 big DISTINCT scan + 2 EXTRACT-filtered COUNTs per overlapping month | Monthly imports: trivial. Re-import of multi-year history over existing data: hundreds of counts, each fetch-and-discard (Shape B) — tens of seconds of preview. Bounded by month-count, not rows; acceptable with a warning, better after D11 |
| Replace-delete (`_replace_overlapping_months`) | One DELETE per (distributor, month), each via account-join + EXTRACT | Same Shape B cost; deletes also pay 8-index maintenance. Fine at monthly grain |
| **The whole flow is a synchronous web request** | — | At distributor-tenant volumes (audit 01 §6c: "multiply sales volume"), any of the above exceeding ~30-60s hits server/proxy timeouts. The structural fix is a background job, not query tuning — flag for audit 06 |

### 4b. Other bulk operations

- **batch_delete** (imports/views.py:1180-1239): per-auto-created-account existence checks — 1 query per account, **twice** (preview + inside transaction). A batch that auto-created 1,000 accounts → ~2,000 queries. Same fix shape as the AccountItem loop (one annotated query). Medium-low: deletes are rare/admin.
- **Account merge:** admin-only field edit; no bulk path exists ✅ (nothing to scale).
- **account_bulk_delete:** association counts per selected account (3 queries each via `get_account_associations`) — bounded by selection size; fine.
- **Forecast/projection:** DB-aggregated then O(items × months) Python walk — bounded by catalog size (8 items), not data volume ✅. Group forecast iterates member distributors' profiles — bounded by group size ✅.
- **`save()`-loop patterns:** distribution's sort_position renumbering and safety-stock saves are per-item/per-PO loops on config-sized sets ✅.

---

## SECTION 5 — SCALABILITY TRAJECTORY (what breaks first)

Ranked by (impact × proximity), grounded in audit 01 §4c sizes and the measurements above:

1. **Already broken at current scale: event_list payload (F1) × runserver (F2).** 3.5MB per hit today, single-threaded server: two users on the events page = visible queueing; a tenant on mobile data gets a 3.5MB page for 19 active events. This degrades *user-perceived* performance now, before any growth.
2. **First tenant onboarding: the import AccountItem loop (F5).** Initial history import is exactly the worst case (every pair is new). Minutes-long transaction during the highest-stakes demo moment of onboarding. Cheap to fix; do it before tenant #1's first big import.
3. **Months 3-12, data 2-5×: account-sales report payload (F4)** crosses multi-MB as accounts-with-sales grow, and **Shape A/B query times (F3/F6)** grow linearly — from 20ms toward 100-300ms per aggregation, several per report page. Annoying before alarming.
4. **Distributor-as-tenant era (data 10-50×): Shape A/B become seconds** — the account-join + EXTRACT pattern is the structural ceiling; D12 (distributor on SalesRecord) + D11 (month structure) are the fixes, and doing them *before* the data multiplies is 10× cheaper than after. Import-as-web-request also breaks here (timeout), needing background jobs.
5. **Steady-state write tax:** 8 indexes on SalesRecord (3-4 droppable) — invisible until import sizes grow, then a free 30-40% write saving left on the table.
6. **Not a near-term risk:** tenant-count itself. 5 tenants ≈ 5× config-sized tables (still tiny) + 5× sales volume (handled by the same fixes); index shapes are tenant-isolated (§2d). The shared-fate concern is the single process/DB, which F2 (real app server, multiple workers) largely addresses at this scale.

---

## SECTION 6 — PRIORITIZED FINDINGS SUMMARY

Severity: HIGH = degrades UX at current/near-term scale; MEDIUM = growth risk; LOW = micro-opt. **QW** = quick win. Near-term risks are F1-F5/F7; F3/F6's *full* fixes (D11/D12) are deliberately staged for the pre-distributor-tenant window.

| ID | Sev | QW | Finding | Why it matters | Cost | X-ref |
|---|---|---|---|---|---|---|
| **F1** | HIGH | QW-ish | event_list: 3.54MB measured; no pagination; both tabs rendered every request; every event rendered in 2 layouts; 98.6% of rows are PAID/imported history | Worst page in the app, on the most-used screen, at *current* scale; mobile users eat 3.5MB per visit; fix is view/template-local (render requested tab only → paginate past tab) | S-M | §3a |
| **F2** | HIGH | **QW** | Production deployment runs `manage.py runserver` (run.sh:21 via `.replit [deployment]`) — single-threaded dev server; gunicorn is already in requirements.txt, unused | Every 3.5MB render blocks all other requests; dev server is also unhardened; switching the run command to gunicorn w/ workers is the single cheapest concurrency fix available | **S** | audit 06 (ops) |
| **F3** | MEDIUM→HIGH-at-scale | — | Distributor-scoped sales reads nest-loop through accounts (719 probes, 21ms @ 38k rows, measured); linear in volume; hits forecast + all reports + replace-delete | The structural read ceiling; becomes seconds at distributor-tenant volumes; fix = audit 01 D12 (distributor FK + (distributor, sale_date) index), best done before data multiplies | M | 01-D12 |
| **F4** | MEDIUM | — | Account-sales report: 1.64MB measured, unpaginated by design; HTML is ~45× the CSV of the same data | Grows linearly with accounts-with-sales; multi-MB within the year; needs a ceiling/virtualization eventually, not urgently | M | §3c |
| **F5** | MEDIUM | **QW** | Import: `AccountItem.get_or_create` per unique pair inside the open transaction (imports/views.py:917) — ~10-15k sequential queries for a typical initial-history import | Minutes-long locked transaction exactly at tenant-onboarding's first big import; fix is one existing-pairs query + bulk_create(ignore_conflicts) | **S** | §4a, 02 §5 |
| **F6** | MEDIUM | partial QW | EXTRACT month-bucket pattern confirmed at plan level: ~40% of fetched rows discarded post-read in year-IN shape; unindexable as written | Taxes every report/forecast/overlap/delete; code-only mitigation (rewrite `__year/__month` filters as date ranges — existing composites then serve them) is S; full fix is 01-D11's month column | S (ranges) / M (D11) | 01-D11 |
| **F7** | MEDIUM | **QW** | Redundant indexes confirmed by live scan counts: sales `company_id`(0), `account_id`(redundant twin of workhorse), `item_id`(0) singles + `(item,sale_date)`(0); accountitem `account_id` | ~30-40% of SalesRecord write amplification for zero read benefit; drops are one no-downtime migration each (review `(item,sale_date)` again after D12) | **S** | 01-D16 |
| **F8** | LOW | — | event_list sidebar recomputes city/county lists via 4 extra full-set UNION evaluations per load | Disappears inside F1's rework; harmless at current size | S | §1a |
| **F9** | LOW | — | `_detect_overlap`/replace-delete: per-month EXTRACT-filtered COUNTs/DELETEs; batch_delete: 2 queries per auto-created account | Only bites on multi-year re-imports / huge batch deletes; bounded by months not rows; improves automatically with F6 | S | §4a/4b |
| **F10** | LOW | — | Whole import flow is a synchronous web request (parse→validate→replace→insert in one request/transaction) | Fine at monthly cadence; breaks (timeouts) at distributor-tenant volumes — background-job question for audit 06 | L (when needed) | §4a, 02 §7 |
| **F11** | LOW | **QW** | Dev PG never ANALYZEd (audit 01 D25): planner row estimates off 2-50× in measured plans (e.g. est 389 vs actual 22,646 in Shape B) | Dev query plans don't predict prod behavior; one `ANALYZE` fixes; verify prod autovacuum (01 §4e-7) | **S** | 01-D25 |
| **F12** | INFO | — | Positive findings worth preserving: zero N+1 across all heavy views; all aggregation DB-side; imports bulk-chunked; forecasts aggregate-then-walk; modal endpoints sub-2KB; pagination correct where present | The discipline exists — F1 is a template/UX decision, not a pattern failure; new code should keep this bar | — | — |

**Counts: 2 HIGH · 5 MEDIUM · 4 LOW · 1 INFO — 12 findings.**

**Quick-win package (one short PR each, no behavior change):** F2 (gunicorn run command), F7 (drop 4-5 redundant indexes), F5 (bulk AccountItem creation), F6-partial (date-range filters), F11 (ANALYZE + prod check). Combined: S×5, and they remove the concurrency choke, the onboarding-import stall, and a third of big-table write cost before tenant #1.

**Deliberately deferred (not premature):** D12/D11 schema changes (do in the pre-distributor-tenant window, sized M, with the grain decision from audit 01 D1), report virtualization (F4), background imports (F10).

### Cross-reference
01-D11 → F6 (plan-level confirmation + interim range-filter mitigation). 01-D12 → F3 (timed evidence). 01-D16 → F7 (scan-count confirmation, +1 new droppable). 01-D25 → F11 (estimate skew measured). 02 §6 → §2d (tenant-leading shape confirmed; coupling is process-level). 02 §5 / first-tenant onboarding → F5. Audit 06 intake: F2 (deployment), F10 (background jobs), shared-DB noisy-neighbor monitoring.
