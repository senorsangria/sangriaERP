# productERP — Refactoring Backlog

This is a living document. It is the list of known structural / technical-debt
items that we have **deliberately deferred** — things that work correctly today
but that should be improved for long-term scale, correctness, or upcoming
features (especially the planned **COGS / QuickBooks sync** and expanded
**reporting** work).

Each entry records:

- **What it is** — the structural issue, described against the current code.
- **Why it matters** — the cost it imposes or the future work it complicates.
- **Recommendation** — the direction we'd take when we promote it.
- **Status** — a `Deferred — not scheduled` marker, unless promoted.

**Adding an item here does not mean something is broken.** Everything listed
works as built. It means we have consciously identified an improvement and
decided to revisit it later rather than now. This file exists so those
decisions are not lost in conversation history as the system grows.

Promoting an item to real work means moving or re-marking it here (see the
closing note).

---

## Sales / Import schema (assessed June 2026)

Assessment captured from the replace-on-import diagnostic. Items are
prioritized; verify against the current models when promoting any of them.

Relevant models at time of writing:
`apps/sales/models.py` (`SalesRecord`), `apps/imports/models.py`
(`ImportBatch`), `apps/accounts/models.py` (`Account`, `AccountItem`).

### 1. No uniqueness constraint on `SalesRecord` — **Priority: HIGH (foundational)**

- **What it is:** `SalesRecord` (`apps/sales/models.py`) defines no
  `unique_together` and no `UniqueConstraint`. Its `Meta` declares only
  ordering and three non-unique indexes (`(company, sale_date)`,
  `(account, sale_date)`, `(item, sale_date)`). Duplicate sales rows —
  identical `(company, account, item, sale_date, quantity)` — are therefore
  structurally permitted.
- **Why it matters:** Today the only thing preventing duplicates is the import
  hard-stop (soon to become replace-on-import). Nothing at the database level
  guarantees one row per real-world sale. Any future **upsert or reconciliation
  path — exactly what COGS / QuickBooks sync needs** — requires a stable key to
  match incoming rows against existing ones. Without a uniqueness constraint
  there is no key to reconcile on, and a re-import bug could silently double
  data.
- **Recommendation:** Once it is confirmed whether multiple rows per
  `(account, item, day)` are ever legitimate (e.g. multiple distributor line
  items on the same day), add a `UniqueConstraint` at the true grain. Likely
  `(company, account, item, sale_date)`, possibly including `import_batch`.
- **Status:** `Deferred — not scheduled.`

### 2. Daily dating when the domain operates monthly — **Priority: MEDIUM**

- **What it is:** `SalesRecord.sale_date` is a `DateField` (daily grain). But
  every consumer operates by month: forecasts (`apps/distribution/forecast.py`
  groups by `sale_date__year` / `sale_date__month`), reports
  (`apps/reports/views.py`, same pattern), the replace-on-import feature
  (overlap detected per distributor + month), and the planned COGS work.
- **Why it matters:** The daily grain is finer than the domain actually uses, so
  every analytic query repeatedly derives month buckets with
  `ExtractYear` / `ExtractMonth`. There is no index that serves a
  month-bucketed distributor query directly, so month rollups can't be served
  cleanly as data scales.
- **Recommendation:** Decide daily-vs-monthly deliberately. If monthly is the
  true domain grain, consider a denormalized month/period field (or an index
  that supports month-bucketed queries) so rollups don't re-extract on every
  read.
- **Status:** `Deferred — not scheduled.`

### 3. Dual distributor source of truth — **Priority: HIGH**

- **What it is:** A sales record's distributor is resolved two different ways.
  All queries read it via `SalesRecord.account → Account.distributor`
  (`apps/accounts/models.py`: `distributor` is `on_delete=SET_NULL`,
  `null=True`). Separately, `ImportBatch` stores its own `distributor` FK
  (`apps/imports/models.py`: `on_delete=PROTECT`, non-null). These are two
  parallel sources that are not constrained to agree, and the one every query
  actually uses (`account.distributor`) is nullable.
- **Why it matters:** A null `Account.distributor` silently drops that account's
  rows from **every distributor-scoped query** — forecasts, reports, and the
  overlap/replace logic — with no error. For financial sync (COGS / QuickBooks),
  silently missing rows is a correctness and reconciliation hazard, and the join
  through `Account` adds cost to every distributor-scoped read.
- **Recommendation:** Either (a) denormalize `distributor` directly onto
  `SalesRecord` — removes the account join, enables a clean
  `(distributor, sale_date)` index, and eliminates the nullable risk — or
  (b) make `Account.distributor` non-null with `on_delete=PROTECT`. Option (b)
  is now **done** (see note below); the broader denormalize-onto-`SalesRecord`
  idea (a) remains deferred.
- **Status:** `Deferred — not scheduled` (broader denormalization onto
  `SalesRecord`). The `Account.distributor` hardening (option b) is **DONE**.

#### ✅ DONE — `Account.distributor` non-null + PROTECT

The change to make `Account.distributor` non-nullable with `on_delete=PROTECT`
(previously `SET_NULL` / `null=True`) is **complete** (migration
`accounts/0013_alter_account_distributor`). Dev was audited clean (0
null-distributor accounts), so it shipped as a simple `AlterField` with no data
migration. A companion fix hardened the account-import flow to reject rows with
a blank distributor cell (a clean upload error instead of an `IntegrityError`),
and tests now cover the constraint, the PROTECT-on-delete behavior, and the
blank-distributor rejection. It hardens the foundation that replace-on-import
depends on — that feature detects and deletes overlapping data by
`account__distributor`, so a reliable non-null distributor protects its
correctness.

> **Production deploy gate (still outstanding):** before the
> `0013_alter_account_distributor` migration is deployed to production,
> production must be confirmed to have **zero** null-distributor accounts. This
> was verified in dev only; the prod check is a deploy-time gate.

Only the broader idea (a) — denormalizing `distributor` directly onto
`SalesRecord` — remains in this backlog as deferred.

### 4. No soft-delete / mutation audit log for sales data — **Priority: MEDIUM-HIGH (rises with financial sync)**

- **What it is:** Sales deletions are hard deletes. `batch_delete`
  (`apps/imports/views.py`) calls `.delete()` on `SalesRecord` rows, and
  **replace-on-import is now built and does the same** for overlapping months
  (`_replace_overlapping_months`). There is no recovery path and no structured
  audit trail of what was removed beyond the free-text `ImportBatch.notes`
  annotation. **This is now a live, user-triggered hard-delete path** (not just
  the batch-delete admin action), which raises the importance of this item.
- **Why it matters:** Before COGS / QuickBooks make data corrections
  **financially material**, an accidental or mis-scoped delete is unrecoverable
  and untraceable. A correction that flows into financial reporting needs to be
  auditable — what changed, when, by whom.
- **Recommendation:** Introduce a soft-delete (e.g. a deleted/voided flag with a
  timestamp and actor) or a dedicated delete/replace audit log for sales
  mutations, so corrections are safe and traceable.
- **Status:** `Deferred — not scheduled.`

### 5. Batch grain ≠ analytic grain (the partial-batch problem) — **Priority: MEDIUM**

- **What it is:** `ImportBatch` is scoped **per upload range** — one upload of a
  distributor's Jan–May data creates a single batch whose `date_range_start` /
  `date_range_end` span Jan–May and whose `records_imported` counts all of it.
  Analytic and replace operations, by contrast, work **per month**.
- **Why it matters:** A month-grain replace deletes only part of an existing
  batch's rows, leaving that batch **partially gutted**: `records_imported` and
  `date_range_*` no longer match the rows that remain, and the Import History
  views then show overlapping batch rows for a replaced month (the stale
  original plus the new partial-month batch). This is the root cause of the
  partial-batch staleness that replace-on-import has to work around. For
  replace-on-import we have **chosen to leave the original batch numbers as the
  historical record** and explain the change via an appended `ImportBatch.notes`
  audit note, rather than recompute the batch statistics.
- **Workaround in place (built):** replace-on-import is now live and uses exactly
  this workaround — affected batches keep their original `records_imported` /
  `date_range`, and each gets an appended `notes` line listing the months replaced
  (with date + user). So the partial-batch staleness is real and accepted today;
  the audit note is the compensating record. Import History may therefore show a
  stale original batch alongside the new partial-month batch for a replaced month.
- **Recommendation:** Move toward per-`(distributor, month)` batch granularity
  (or an "import event" + per-month rollup), so replace, history, and audit all
  share one grain and partial-batch staleness disappears.
- **Status:** `Deferred — not scheduled` (workaround in place via the audit note).

### 6. `AccountItem.date_first_associated` is never recalculated — **Priority: LOW**

- **What it is:** During import, `AccountItem` rows are created via
  `get_or_create` with `date_first_associated` set to the earliest sale date
  seen for that `(account, item)` pair, and never updated thereafter
  (`apps/accounts/models.py` documents it as "set on creation, never updated";
  the import in `apps/imports/views.py` only sets it on create).
- **Why it matters:** After a delete + reimport (replace-on-import), if the
  replaced month held the earliest sale for an `(account, item)` pair, the
  existing `AccountItem.date_first_associated` is not updated and can become
  inaccurate relative to the surviving data. Minor today, but real for any
  first-sale / first-seen reporting.
- **Recommendation:** Recalculate `date_first_associated` on delete/replace if
  first-sale accuracy matters for the report consuming it.
- **Status:** `Deferred — not scheduled.`

---

## Accounts / pricing schema (assessed June 2026)

### 7. Shelf price history (`AccountItemPriceHistory`) is write-only dead data with incomplete semantics — **Priority: MEDIUM (rises when account-item dated-history work begins)**

- **What it is:** Shelf price is modeled as two pieces. `AccountItem.current_price`
  (`apps/accounts/models.py`) is a single, overwritten value — the only price ever
  *displayed* (account detail, account-item views, recap). `AccountItemPriceHistory`
  is a separate dated table that is **populated but never read or displayed anywhere**
  (the only references in `apps/` and `templates/` are the write site in
  `apps/events/views.py` `_apply_price_updates`). Worse, its semantics are off: a
  history row stores the **superseded (old)** price (`price=account_item.current_price`,
  the previous value) dated `recorded_at = now` at the moment it is replaced — not
  one-row-per-capture. The **first** price writes no history row, and the **current**
  price is never a history row either.
- **Why it matters:** "What was the shelf price at this account on date X" cannot be
  reconstructed from this table — the dates mark *supersession*, not *capture*, and the
  endpoints (first + current) are missing. So the history is misleading if ever surfaced,
  and any feature that "follows the shelf-price pattern" would inherit a half-built shape
  whose display side was never written. This becomes a real blocker when the planned
  **ending-inventory capture at the account-item level** (a value captured during an
  event but belonging to the account-item relationship, with a dated history and a
  "most recent" surfaced on account detail) is built — it is conceptually the same
  pattern and would be tempted to copy this broken one.
- **Recommendation:** When a genuine dated-history-at-account-item feature is built
  (e.g. ending inventory), establish clean **one-capture = one dated row** semantics
  (each captured value is its own row dated at capture time, "most recent" derived by
  max date) **with an actual display**, and consider refactoring shelf price to match.
  `OwnInventorySnapshot` (`apps/production/models.py`) — one row per
  `(company, item, year, month)`, period stored as data, latest derived by `max(year, month)`,
  and actually consumed by the forecast — is the **better precedent** to follow than the
  shelf-price archive-on-change model.
- **Status:** `Deferred — not scheduled` (priority rises when ending-inventory /
  account-item history work begins).

---

## Keeping this document current

Update this document whenever we identify a structural improvement that we
choose to defer rather than do now. When an item is **promoted to real work**,
move it or re-mark it accordingly (as was done for the `Account.distributor`
non-null + PROTECT change above) so the backlog always reflects what is truly
deferred versus what is in flight.
