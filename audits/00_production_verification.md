# Production Verification of Audit Findings (01–06)

**Date:** 2026-06-12
**Method:** Read-only queries (SELECT/COUNT only) run against production via Render `manage.py dbshell`, each batch wrapped in `BEGIN; SET TRANSACTION READ ONLY; … ROLLBACK;`. No mutations. Production = full ~6-year dataset, all distributors (dev is a ~3-distributor / ~4-year subset).

**Why this exists:** The six audits were authored against dev data. Structure/code/schema findings are environment-independent; data findings (counts, sizes, index usage, dead-field population) required production verification before acting. This file records what production showed and, where relevant, how it changes an audit's conclusion. The six audit files are left as-written; this is the verification layer over them.

## Platform & scale — CONFIRMED (Audit 04, 06)
- Production PostgreSQL **16.13** (current, supported). Audit 06 currency item closed.
- Largest table `sales_salesrecord`: **110,169 rows / 22 MB**. Entire dataset is small — no scale problem anywhere, no partitioning needed, any schema restructuring is cheap to migrate.
- `stats_reset` is NULL → index-usage counts are lifetime-cumulative and trustworthy.

## Index reality on sales_salesrecord — CONFIRMED (Audit 04)
Seven indexes (~11 MB, ≈ half the table). Three `Meta.indexes` composites, each shadowed by a Django auto FK index:
- `(account_id, sale_date)` [822k scans] KEEP; `account_id` single [627k] — **defensible drop** (leftmost-prefix covered; trades a marginally larger read scan for one fewer write-path index).
- `(company_id, sale_date)` [3,553] KEEP; `company_id` single [4] — **clear drop** (near-unused, fully covered).
- `(item_id, sale_date)` [24] vs `item_id` single [222] — **review/consolidate**, low stakes.
- `import_batch_id` [320] and `pkey` — KEEP.
- Execution wrinkle: Django auto-creates FK indexes; a naive RemoveIndex may be recreated on `migrate`. How to make the drops stick is an execution-phase detail.

## Missing distributor linkage on sales_salesrecord — CONFIRMED (Audit 04)
Sales rows carry only `distributor_wholesale_price` (numeric); **no distributor FK / column**. Distributor-level sales analytics and the distributor-as-tenant model both need this captured. Roadmap item.

## Account.distributor NOT NULL — CLOSED (Audit 01)
`accounts_account.distributor_id` is `is_nullable = NO` in production. The hardening migration landed; zero nulls structurally possible. Item closed.

## SalesRecord grain — QUANTIFIED / REFRAMED (Audit 01)
- 110,018 distinct `(company, account, item, sale_date)` groups; only **149** have >1 row; max **3**. 99.86% naturally unique.
- Adding `import_batch_id` to the key leaves **149 unchanged** → the duplicates are **intra-batch**, so `(grain + batch)` is NOT a clean natural key.
- Net: "no natural key" is real but bounded to 149 edge cases, not chaos. Small, cheap decision either way: confirm whether the 149 are legitimate same-day multi-line sales → add a line discriminator or accept a documented surrogate-only grain; or import artifacts → dedupe the 149 and add a uniqueness constraint. Recommend eyeballing a sample of the 149 first. Before-COGS, not a blocker.

## AccountItem.date_first_associated — DOWNGRADED (Audit 01 / backlog)
- Of 14,053 AccountItems with sales, **14,053 (100%)** have `date_first_associated` exactly equal to the earliest actual sale date. Zero stale, zero earlier.
- The "never recalculated" risk is real in code but has produced **no data corruption** — correct everywhere today (imports have carried full history).
- Net: downgrade from "fix" to **accepted-debt**, optional cheap guard only. No cleanup needed. (6 AccountItems have no sales — negligible.)

## AccountItemPriceHistory — CONFIRMED DEAD + BROKEN (Audit 01 / backlog)
- 1,672 rows; all three indexes at 0 scans (nothing reads it).
- Sample shows rows written within seconds of each other (logged on every import touch, not on price change), prices oscillating (e.g. 15.99→16.99→15.99→17.99), and the latest history entry rarely matching `current_price` (10.99 vs 13.99; 17.99 vs 22.99; 8.99 vs 9.99).
- All timestamps Apr–Jun 2026 → a ~2-month-old feature never wired to a reader; not 6 years of history.
- Net: stronger than "dead" — it is actively writing misleading data. **Stop the write path** (removes silent junk + minor import overhead); drop the table later. Relevant before COGS pricing work.

## Net effect on prioritization
Changed by production (must flow into the roadmap so stale dev conclusions aren't carried forward):
- `date_first_associated` → accepted-debt (was a fix candidate).
- SalesRecord grain → bounded small decision (was open-ended).
- AccountItemPriceHistory → stop-writing now (confirmed and strengthened).

Confirmed by production: scale is a non-issue; index redundancy is real (one clear drop, one judgment drop); missing distributor FK is real; Account.distributor is closed.
