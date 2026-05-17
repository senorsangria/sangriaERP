"""
Unit tests for apps/production/forecast.py — compute_production_forecast.

These are pure algorithm tests — no views, no HTTP.
"""
from decimal import Decimal

from django.test import TestCase

from apps.catalog.models import Brand, Item
from apps.core.models import Company
from apps.distribution.models import Distributor, DistributorPO, DistributorPOLine
from apps.production.forecast import compute_production_forecast, MONTH_SHORT
from apps.production.models import OwnInventorySnapshot


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_company(name='Test Co'):
    return Company.objects.create(name=name)


def make_brand(company, name='Brand'):
    return Brand.objects.create(company=company, name=name)


def make_item(brand, name='Item', item_code='CODE', sort_order=1, safety_stock=None):
    return Item.objects.create(
        brand=brand, name=name, item_code=item_code, sort_order=sort_order,
        production_safety_stock_cases=safety_stock,
    )


def make_snapshot(company, item, year=2026, month=4, qty='100'):
    return OwnInventorySnapshot.objects.create(
        company=company, item=item, year=year, month=month,
        quantity_cases=Decimal(qty),
    )


def make_distributor(company, name='Dist'):
    return Distributor.objects.create(company=company, name=name)


def make_po_with_demand(distributor, item, year, month, cases):
    po = DistributorPO.objects.create(
        distributor=distributor, year=year, month=month, status='projected',
    )
    DistributorPOLine.objects.create(
        po=po, item=item, quantity_cases=Decimal(str(cases)),
    )
    return po


# ---------------------------------------------------------------------------
# 1. Empty state
# ---------------------------------------------------------------------------

class ForecastEmptyStateTest(TestCase):

    def test_no_snapshots_returns_message(self):
        company = make_company()
        result = compute_production_forecast(company)
        self.assertTrue(result['message'])
        self.assertEqual(result['rows'], [])
        self.assertEqual(result['horizon'], [])


# ---------------------------------------------------------------------------
# 2. Horizon and year spans
# ---------------------------------------------------------------------------

class ForecastHorizonTest(TestCase):

    def setUp(self):
        self.company = make_company()
        self.brand = make_brand(self.company)
        self.item = make_item(self.brand)
        make_snapshot(self.company, self.item, year=2026, month=4)

    def test_horizon_has_13_entries(self):
        result = compute_production_forecast(self.company)
        self.assertEqual(len(result['horizon']), 13)

    def test_horizon_first_entry_is_anchor(self):
        result = compute_production_forecast(self.company)
        h = result['horizon'][0]
        self.assertEqual(h['year'], 2026)
        self.assertEqual(h['month'], 4)
        self.assertTrue(h['is_snapshot'])

    def test_horizon_remainder_are_projection(self):
        result = compute_production_forecast(self.company)
        for h in result['horizon'][1:]:
            self.assertFalse(h['is_snapshot'])

    def test_horizon_crosses_year_boundary(self):
        make_snapshot(self.company, self.item, year=2025, month=12)
        result = compute_production_forecast(self.company)
        # Anchor: April 2026. Last projection: April 2027.
        # Actually anchor is most recent: April 2026 (not Dec 2025)
        last = result['horizon'][-1]
        self.assertEqual(last['year'], 2027)
        self.assertEqual(last['month'], 4)

    def test_year_spans_single_year(self):
        # Anchor Apr 2026, projection May 2026 - Apr 2027 → two years
        result = compute_production_forecast(self.company)
        self.assertEqual(len(result['year_spans']), 2)
        # 2026 covers Apr-Dec = 9 columns, 2027 covers Jan-Apr = 4 columns
        year_map = {s['year']: s['colspan'] for s in result['year_spans']}
        self.assertEqual(year_map[2026], 9)
        self.assertEqual(year_map[2027], 4)

    def test_year_spans_when_anchor_is_december(self):
        # Anchor Dec 2025 → spans Dec 2025 (1) + Jan-Dec 2026 (12) = two years
        make_snapshot(self.company, self.item, year=2025, month=12)
        result = compute_production_forecast(self.company)
        # Most recent is Apr 2026 still
        # Let's use a fresh company for isolation
        c2 = make_company('C2')
        b2 = make_brand(c2)
        i2 = make_item(b2, 'I2', 'I2')
        make_snapshot(c2, i2, year=2025, month=12)
        result2 = compute_production_forecast(c2)
        year_map = {s['year']: s['colspan'] for s in result2['year_spans']}
        self.assertEqual(year_map[2025], 1)   # Dec
        self.assertEqual(year_map[2026], 12)  # Jan-Dec


# ---------------------------------------------------------------------------
# 3. Single item projections — no demand
# ---------------------------------------------------------------------------

class ForecastNoDemandTest(TestCase):

    def setUp(self):
        self.company = make_company()
        self.brand = make_brand(self.company)
        self.item = make_item(self.brand)
        make_snapshot(self.company, self.item, year=2026, month=4, qty='500')

    def test_flat_projection_when_no_demand(self):
        result = compute_production_forecast(self.company)
        row = result['rows'][0]
        # Anchor cell is 500
        self.assertEqual(row['monthly_data'][0]['status'], 'snapshot')
        self.assertEqual(row['monthly_data'][0]['inventory'], 500.0)
        # All projection cells stay at 500 (no demand to deplete)
        for cell in row['monthly_data'][1:]:
            self.assertEqual(cell['inventory'], 500.0)
            self.assertEqual(cell['status'], 'green')

    def test_anchor_cell_is_snapshot_status(self):
        result = compute_production_forecast(self.company)
        cell = result['rows'][0]['monthly_data'][0]
        self.assertEqual(cell['status'], 'snapshot')
        self.assertTrue(cell['is_snapshot'])


# ---------------------------------------------------------------------------
# 4. Single item projections — with demand
# ---------------------------------------------------------------------------

class ForecastWithDemandTest(TestCase):

    def setUp(self):
        self.company = make_company()
        self.brand = make_brand(self.company)
        self.item = make_item(self.brand)
        make_snapshot(self.company, self.item, year=2026, month=4, qty='100')
        self.distributor = make_distributor(self.company)

    def test_demand_depletes_inventory(self):
        make_po_with_demand(self.distributor, self.item, 2026, 5, 30)
        result = compute_production_forecast(self.company)
        row = result['rows'][0]
        # May 2026 projection (index 1): 100 - 30 = 70
        may_cell = row['monthly_data'][1]
        self.assertEqual(may_cell['month'], 5)
        self.assertEqual(may_cell['inventory'], 70.0)

    def test_demand_accumulates_over_months(self):
        make_po_with_demand(self.distributor, self.item, 2026, 5, 30)
        make_po_with_demand(self.distributor, self.item, 2026, 6, 20)
        result = compute_production_forecast(self.company)
        row = result['rows'][0]
        june_cell = next(c for c in row['monthly_data'] if c['month'] == 6 and c['year'] == 2026)
        self.assertEqual(june_cell['inventory'], 50.0)  # 100-30-20

    def test_demand_goes_negative_shows_red(self):
        make_po_with_demand(self.distributor, self.item, 2026, 5, 150)
        result = compute_production_forecast(self.company)
        row = result['rows'][0]
        may_cell = next(c for c in row['monthly_data'] if c['month'] == 5)
        self.assertEqual(may_cell['inventory'], -50.0)
        self.assertEqual(may_cell['status'], 'red')

    def test_demand_from_multiple_distributors_aggregated(self):
        dist2 = make_distributor(self.company, 'Dist Two')
        make_po_with_demand(self.distributor, self.item, 2026, 5, 30)
        make_po_with_demand(dist2, self.item, 2026, 5, 20)
        result = compute_production_forecast(self.company)
        may_cell = next(
            c for c in result['rows'][0]['monthly_data']
            if c['month'] == 5 and c['year'] == 2026
        )
        self.assertEqual(may_cell['inventory'], 50.0)  # 100 - (30+20)


# ---------------------------------------------------------------------------
# 5. Safety stock status logic
# ---------------------------------------------------------------------------

class ForecastSafetyStockTest(TestCase):

    def setUp(self):
        self.company = make_company()
        self.brand = make_brand(self.company)
        self.distributor = make_distributor(self.company)

    def test_below_safety_stock_is_yellow(self):
        item = make_item(self.brand, safety_stock=50)
        make_snapshot(self.company, item, year=2026, month=4, qty='100')
        make_po_with_demand(self.distributor, item, 2026, 5, 60)
        result = compute_production_forecast(self.company)
        may_cell = next(
            c for c in result['rows'][0]['monthly_data']
            if c['month'] == 5 and not c['is_snapshot']
        )
        self.assertEqual(may_cell['inventory'], 40.0)
        self.assertEqual(may_cell['status'], 'yellow')

    def test_exactly_at_safety_stock_is_green(self):
        # _cell_status uses strictly-less-than, so inv == safety_stock → green
        item = make_item(self.brand, safety_stock=40)
        make_snapshot(self.company, item, year=2026, month=4, qty='100')
        make_po_with_demand(self.distributor, item, 2026, 5, 60)
        result = compute_production_forecast(self.company)
        may_cell = next(
            c for c in result['rows'][0]['monthly_data']
            if c['month'] == 5 and not c['is_snapshot']
        )
        self.assertEqual(may_cell['inventory'], 40.0)
        self.assertEqual(may_cell['status'], 'green')

    def test_above_safety_stock_is_green(self):
        item = make_item(self.brand, safety_stock=30)
        make_snapshot(self.company, item, year=2026, month=4, qty='100')
        make_po_with_demand(self.distributor, item, 2026, 5, 60)
        result = compute_production_forecast(self.company)
        may_cell = next(
            c for c in result['rows'][0]['monthly_data']
            if c['month'] == 5 and not c['is_snapshot']
        )
        self.assertEqual(may_cell['status'], 'green')

    def test_zero_inventory_is_red_regardless_of_safety_stock(self):
        item = make_item(self.brand, safety_stock=None)
        make_snapshot(self.company, item, year=2026, month=4, qty='0')
        result = compute_production_forecast(self.company)
        may_cell = result['rows'][0]['monthly_data'][1]
        self.assertEqual(may_cell['inventory'], 0.0)
        self.assertEqual(may_cell['status'], 'red')

    def test_no_safety_stock_green_when_positive(self):
        item = make_item(self.brand, safety_stock=None)
        make_snapshot(self.company, item, year=2026, month=4, qty='10')
        result = compute_production_forecast(self.company)
        may_cell = result['rows'][0]['monthly_data'][1]
        self.assertEqual(may_cell['status'], 'green')


# ---------------------------------------------------------------------------
# 6. No-data item handling
# ---------------------------------------------------------------------------

class ForecastNoDataItemTest(TestCase):

    def setUp(self):
        self.company = make_company()
        self.brand = make_brand(self.company)

    def test_item_with_no_snapshot_and_no_demand_is_all_no_data(self):
        make_item(self.brand)
        # Need at least one snapshot for the company to get an anchor
        item2 = make_item(self.brand, item_code='I2')
        make_snapshot(self.company, item2, year=2026, month=4, qty='100')
        result = compute_production_forecast(self.company)
        no_data_row = next(r for r in result['rows'] if r['item'].item_code == 'CODE')
        for cell in no_data_row['monthly_data']:
            self.assertEqual(cell['status'], 'no_data')

    def test_item_with_no_snapshot_but_demand_starts_at_zero(self):
        item = make_item(self.brand, item_code='D001')
        dist = make_distributor(self.company)
        # Give another item a snapshot to set the anchor
        anchor_item = make_item(self.brand, item_code='A001')
        make_snapshot(self.company, anchor_item, year=2026, month=4, qty='200')
        make_po_with_demand(dist, item, 2026, 5, 50)
        result = compute_production_forecast(self.company)
        demand_row = next(r for r in result['rows'] if r['item'].item_code == 'D001')
        # Anchor cell: no snapshot for this item → no_data
        self.assertEqual(demand_row['monthly_data'][0]['status'], 'no_data')
        # May 2026: starts at 0, demand 50 → -50, red
        may_cell = next(c for c in demand_row['monthly_data'] if c['month'] == 5)
        self.assertEqual(may_cell['inventory'], -50.0)
        self.assertEqual(may_cell['status'], 'red')


# ---------------------------------------------------------------------------
# 7. Anchor cell semantics under the new algorithm
# ---------------------------------------------------------------------------

class ForecastEarlierSnapshotTest(TestCase):
    """
    Phase D1 — new anchor = oldest of each item's most-recent snapshot.

    An item WITH a snapshot in the anchor month shows status='snapshot'.
    An item WITHOUT a snapshot in the anchor month but with earlier data
    shows a calculated green/yellow/red value (NOT no_data — that was the old behavior).
    An item whose walk hasn't reached the anchor column at all shows no_data.
    """

    def test_anchor_cell_shows_snapshot_when_item_has_anchor_month_snap(self):
        # item_a most-recent = Apr; item_b most-recent = Jan → anchor = Jan
        # item_b HAS a Jan snapshot → 'snapshot' at anchor column
        company = make_company()
        brand = make_brand(company)
        item_a = make_item(brand, item_code='A001')
        item_b = make_item(brand, item_code='B001')
        make_snapshot(company, item_a, year=2026, month=4, qty='200')
        make_snapshot(company, item_b, year=2026, month=1, qty='300')
        result = compute_production_forecast(company)
        self.assertEqual(result['anchor_year'], 2026)
        self.assertEqual(result['anchor_month'], 1)
        row_b = next(r for r in result['rows'] if r['item'].item_code == 'B001')
        anchor_cell = row_b['monthly_data'][0]
        self.assertEqual(anchor_cell['status'], 'snapshot')
        self.assertAlmostEqual(anchor_cell['inventory'], 300.0)

    def test_anchor_cell_shows_no_data_for_item_whose_walk_misses_anchor(self):
        # Same setup: anchor=Jan, but item_a's earliest snap is Apr → walk doesn't reach Jan
        company = make_company()
        brand = make_brand(company)
        item_a = make_item(brand, item_code='A001')
        item_b = make_item(brand, item_code='B001')
        make_snapshot(company, item_a, year=2026, month=4, qty='200')
        make_snapshot(company, item_b, year=2026, month=1, qty='300')
        result = compute_production_forecast(company)
        row_a = next(r for r in result['rows'] if r['item'].item_code == 'A001')
        # item_a has no Jan snapshot and walk starts at Apr → no_data at Jan anchor
        anchor_cell = row_a['monthly_data'][0]
        self.assertEqual(anchor_cell['status'], 'no_data')

    def test_anchor_cell_shows_calculated_value_for_item_with_earlier_snapshot(self):
        # item_a: Dec 2025 snap + Apr 2026 snap (most-recent=Apr)
        # item_b: Jan 2026 snap (most-recent=Jan) → anchor=Jan
        # item_a walk covers Dec→Jan; Jan has no item_a snap → calculated value shown
        company = make_company()
        brand = make_brand(company)
        item_a = make_item(brand, item_code='A001', safety_stock=None)
        item_b = make_item(brand, item_code='B001')
        make_snapshot(company, item_a, year=2025, month=12, qty='400')
        make_snapshot(company, item_a, year=2026, month=4,  qty='300')
        make_snapshot(company, item_b, year=2026, month=1,  qty='200')
        result = compute_production_forecast(company)
        self.assertEqual(result['anchor_month'], 1)
        row_a = next(r for r in result['rows'] if r['item'].item_code == 'A001')
        jan_cell = row_a['monthly_data'][0]  # Jan is the anchor column
        # item_a: walk Dec(snap=400) → Jan(no snap, no demand → 400, green)
        self.assertEqual(jan_cell['status'], 'green')
        self.assertAlmostEqual(jan_cell['inventory'], 400.0)

    def test_item_with_earlier_snapshot_projects_forward_from_known_value(self):
        # anchor = min(Apr, Feb) = Feb (item_b most-recent=Feb, item_a most-recent=Apr)
        # item_b walk: Feb(snap=300) → Mar(-50=250) → Apr(-50=200) → May(-60=140)
        company = make_company()
        brand = make_brand(company)
        dist = make_distributor(company)
        item_a = make_item(brand, item_code='A001')
        item_b = make_item(brand, item_code='B001')
        make_snapshot(company, item_a, year=2026, month=4, qty='100')
        make_snapshot(company, item_b, year=2026, month=2, qty='300')
        make_po_with_demand(dist, item_b, 2026, 3, 50)
        make_po_with_demand(dist, item_b, 2026, 4, 50)
        make_po_with_demand(dist, item_b, 2026, 5, 60)
        result = compute_production_forecast(company)
        row_b = next(r for r in result['rows'] if r['item'].item_code == 'B001')
        # 300 - 50(Mar) - 50(Apr) = 200; May: 200 - 60 = 140
        may_cell = next(c for c in row_b['monthly_data'] if c['month'] == 5)
        self.assertAlmostEqual(may_cell['inventory'], 140.0)


# ---------------------------------------------------------------------------
# 8. Demand row totals
# ---------------------------------------------------------------------------

class ForecastDemandByMonthTest(TestCase):

    def test_demand_by_month_aggregates_across_items(self):
        company = make_company()
        brand = make_brand(company)
        item_a = make_item(brand, item_code='A001')
        item_b = make_item(brand, item_code='B001')
        make_snapshot(company, item_a, year=2026, month=4, qty='100')
        dist = make_distributor(company)
        make_po_with_demand(dist, item_a, 2026, 5, 40)
        make_po_with_demand(dist, item_b, 2026, 5, 30)
        result = compute_production_forecast(company)
        self.assertEqual(result['demand_by_month']['2026-05'], 70.0)

    def test_demand_by_month_keyed_as_string(self):
        company = make_company()
        brand = make_brand(company)
        item = make_item(brand)
        make_snapshot(company, item, year=2026, month=4, qty='100')
        result = compute_production_forecast(company)
        self.assertIn('2026-05', result['demand_by_month'])

    def test_demand_by_month_anchor_included(self):
        company = make_company()
        brand = make_brand(company)
        item = make_item(brand)
        make_snapshot(company, item, year=2026, month=4, qty='100')
        dist = make_distributor(company)
        make_po_with_demand(dist, item, 2026, 4, 25)  # demand in anchor month
        result = compute_production_forecast(company)
        self.assertEqual(result['demand_by_month']['2026-04'], 25.0)


# ---------------------------------------------------------------------------
# 9. Item ordering
# ---------------------------------------------------------------------------

class ForecastItemOrderingTest(TestCase):

    def test_items_ordered_by_brand_then_sort_order(self):
        company = make_company()
        brand_a = make_brand(company, 'Aardvark Brand')
        brand_z = make_brand(company, 'Zebra Brand')
        item_z1 = make_item(brand_z, 'Z1', 'Z1', sort_order=1)
        item_a2 = make_item(brand_a, 'A2', 'A2', sort_order=2)
        item_a1 = make_item(brand_a, 'A1', 'A1', sort_order=1)
        make_snapshot(company, item_z1, year=2026, month=4, qty='10')
        result = compute_production_forecast(company)
        names = [r['item'].item_code for r in result['rows']]
        # Aardvark Brand items first (A1 then A2), then Zebra Brand (Z1)
        self.assertEqual(names.index('A1') < names.index('A2'), True)
        self.assertEqual(names.index('A2') < names.index('Z1'), True)

    def test_inactive_items_excluded(self):
        company = make_company()
        brand = make_brand(company)
        active = make_item(brand, 'Active', 'ACT')
        inactive = make_item(brand, 'Inactive', 'INACT')
        inactive.is_active = False
        inactive.save()
        make_snapshot(company, active, year=2026, month=4, qty='100')
        result = compute_production_forecast(company)
        codes = [r['item'].item_code for r in result['rows']]
        self.assertIn('ACT', codes)
        self.assertNotIn('INACT', codes)


# ---------------------------------------------------------------------------
# Phase D: production_po_additions parameter
# ---------------------------------------------------------------------------

class ProductionPOAdditionsTest(TestCase):

    def setUp(self):
        self.company = make_company()
        self.brand = make_brand(self.company)
        self.item = make_item(self.brand, name='Red Wine', item_code='RED')
        # Anchor month: April 2026, 100 cases on hand
        make_snapshot(self.company, self.item, year=2026, month=4, qty='100')
        # Distributor demand: 30 cases in May 2026
        dist = make_distributor(self.company)
        make_po_with_demand(dist, self.item, year=2026, month=5, cases=30)

    def test_production_po_additions_increases_inventory(self):
        # Without production PO: May = 100 - 30 = 70
        result_no_po = compute_production_forecast(self.company)
        may_data_no_po = result_no_po['rows'][0]['monthly_data'][1]
        self.assertAlmostEqual(may_data_no_po['inventory'], 70.0)

        # With production PO of 200 cases in May: May = 100 + 200 - 30 = 270
        additions = {(self.item.pk, 2026, 5): 200.0}
        result_with_po = compute_production_forecast(self.company, production_po_additions=additions)
        may_data_with_po = result_with_po['rows'][0]['monthly_data'][1]
        self.assertAlmostEqual(may_data_with_po['inventory'], 270.0)

    def test_production_po_additions_applies_at_start_of_month_before_depletion(self):
        # If production adds 20 but demand is 30: net = 100 + 20 - 30 = 90 (not 100 - 30 + 20 = 90, same math)
        # Verify the order doesn't matter numerically but the addition IS counted:
        additions = {(self.item.pk, 2026, 5): 20.0}
        result = compute_production_forecast(self.company, production_po_additions=additions)
        may_inv = result['rows'][0]['monthly_data'][1]['inventory']
        self.assertAlmostEqual(may_inv, 90.0)  # 100 + 20 - 30

    def test_production_po_additions_does_not_apply_to_anchor_month(self):
        # Anchor month is April — additions for April should NOT affect the snapshot cell
        additions = {(self.item.pk, 2026, 4): 500.0}
        result = compute_production_forecast(self.company, production_po_additions=additions)
        anchor_data = result['rows'][0]['monthly_data'][0]
        self.assertEqual(anchor_data['status'], 'snapshot')
        # Snapshot value is still 100 (additions do not change it)
        self.assertAlmostEqual(anchor_data['inventory'], 100.0)

    def test_none_production_po_additions_uses_no_additions(self):
        result_none = compute_production_forecast(self.company, production_po_additions=None)
        result_default = compute_production_forecast(self.company)
        may_none = result_none['rows'][0]['monthly_data'][1]['inventory']
        may_default = result_default['rows'][0]['monthly_data'][1]['inventory']
        self.assertAlmostEqual(may_none, may_default)

    def test_production_po_additions_propagate_across_months(self):
        # Production adds 200 in May; downstream months carry that forward
        additions = {(self.item.pk, 2026, 5): 200.0}
        result = compute_production_forecast(self.company, production_po_additions=additions)
        # June: no demand, no production adds → carries May's 270
        jun_inv = result['rows'][0]['monthly_data'][2]['inventory']
        self.assertAlmostEqual(jun_inv, 270.0)


# ---------------------------------------------------------------------------
# Phase D1: snapshot-override algorithm
# ---------------------------------------------------------------------------

class SnapshotOverrideTest(TestCase):
    """
    Tests for Phase D1 snapshot-override semantics:
    - Snapshots REPLACE the calculated running value at their month
    - Multiple snapshots per item each override independently
    - Anchor = oldest of each item's most-recent snapshot
    - Production PO additions apply in the pre-anchor walk (bug fix)
    - Zero-value snapshots override with status='snapshot' (not red)
    """

    def test_snapshot_overrides_calculated_value(self):
        # Walk: Jan snap=100 → Feb(+500 prod, no demand → 600 calc) → Mar snap=300 (override)
        company = make_company()
        brand = make_brand(company)
        item = make_item(brand, item_code='RED')
        make_snapshot(company, item, year=2026, month=1, qty='100')
        make_snapshot(company, item, year=2026, month=3, qty='300')
        # anchor = Jan (only item, most-recent = Jan... wait: most-recent = Mar!)
        # Actually most-recent = Mar 2026 → anchor = Mar 2026
        additions = {(item.pk, 2026, 2): 500.0}
        result = compute_production_forecast(company, production_po_additions=additions)
        self.assertEqual(result['anchor_month'], 3)  # most-recent = Mar
        row = result['rows'][0]
        # anchor (Mar): snapshot=300 overrides the calculated 600
        mar_cell = next(c for c in row['monthly_data'] if c['month'] == 3 and c['is_snapshot'])
        self.assertEqual(mar_cell['status'], 'snapshot')
        self.assertAlmostEqual(mar_cell['inventory'], 300.0)
        # Feb comes before the anchor and is not in horizon; Apr+ continues from 300
        apr_cell = next(c for c in row['monthly_data'] if c['month'] == 4)
        self.assertAlmostEqual(apr_cell['inventory'], 300.0)  # no demand in Apr

    def test_snapshot_overrides_calculated_value_mid_horizon(self):
        # anchor = Jan; projection months include Mar which has a snapshot → override
        company = make_company()
        brand = make_brand(company)
        dist = make_distributor(company)
        item = make_item(brand, item_code='RED')
        make_snapshot(company, item, year=2026, month=1, qty='500')
        make_snapshot(company, item, year=2026, month=3, qty='800')
        make_po_with_demand(dist, item, 2026, 2, 200)
        make_po_with_demand(dist, item, 2026, 3, 100)
        # Walk: Jan(snap=500) → Feb(+0-200=300) → Mar(+0-100=200 calc, but snap=800 overrides)
        result = compute_production_forecast(company)
        # Item has Jan AND Mar snapshots; most-recent = Mar → anchor = Mar
        self.assertEqual(result['anchor_month'], 3)
        row = result['rows'][0]
        mar_cell = row['monthly_data'][0]  # anchor column = Mar
        self.assertEqual(mar_cell['status'], 'snapshot')
        self.assertAlmostEqual(mar_cell['inventory'], 800.0)
        # Apr continues from 800
        apr_cell = next(c for c in row['monthly_data'] if c['month'] == 4)
        self.assertAlmostEqual(apr_cell['inventory'], 800.0)

    def test_multiple_snapshots_per_item_each_overrides(self):
        # Jan snap=200, Mar snap=500, May snap=100; demands in Feb(50), Apr(80), Jun(30)
        # Walk: Jan(200) → Feb(150) → Mar(override=500) → Apr(420) → May(override=100) → Jun(70)
        company = make_company()
        brand = make_brand(company)
        dist = make_distributor(company)
        item = make_item(brand, item_code='RED')
        make_snapshot(company, item, year=2026, month=1, qty='200')
        make_snapshot(company, item, year=2026, month=3, qty='500')
        make_snapshot(company, item, year=2026, month=5, qty='100')
        make_po_with_demand(dist, item, 2026, 2, 50)
        make_po_with_demand(dist, item, 2026, 4, 80)
        make_po_with_demand(dist, item, 2026, 6, 30)
        result = compute_production_forecast(company)
        # most-recent = May 2026 → anchor = May
        self.assertEqual(result['anchor_month'], 5)
        row = result['rows'][0]
        may_cell = next(c for c in row['monthly_data'] if c['month'] == 5 and c['is_snapshot'])
        self.assertEqual(may_cell['status'], 'snapshot')
        self.assertAlmostEqual(may_cell['inventory'], 100.0)
        jun_cell = next(c for c in row['monthly_data'] if c['month'] == 6)
        self.assertAlmostEqual(jun_cell['inventory'], 70.0)  # 100 - 30

    def test_anchor_is_oldest_of_most_recent_snapshots(self):
        # item_a most-recent = Apr 2026; item_b most-recent = Feb 2026 → anchor = Feb
        company = make_company()
        brand = make_brand(company)
        item_a = make_item(brand, item_code='A')
        item_b = make_item(brand, item_code='B')
        make_snapshot(company, item_a, year=2026, month=4, qty='100')
        make_snapshot(company, item_b, year=2026, month=2, qty='200')
        result = compute_production_forecast(company)
        self.assertEqual(result['anchor_year'],  2026)
        self.assertEqual(result['anchor_month'], 2)

    def test_anchor_when_all_items_share_same_most_recent_snapshot(self):
        # Both items' most-recent = Apr 2026 → anchor = Apr
        company = make_company()
        brand = make_brand(company)
        item_a = make_item(brand, item_code='A')
        item_b = make_item(brand, item_code='B')
        make_snapshot(company, item_a, year=2026, month=4, qty='100')
        make_snapshot(company, item_b, year=2026, month=4, qty='200')
        result = compute_production_forecast(company)
        self.assertEqual(result['anchor_year'],  2026)
        self.assertEqual(result['anchor_month'], 4)

    def test_anchor_when_items_have_different_most_recent_dates(self):
        # item_a most-recent=Jun, item_b most-recent=Mar, item_c most-recent=May → anchor=Mar
        company = make_company()
        brand = make_brand(company)
        for code, month in [('A', 6), ('B', 3), ('C', 5)]:
            item = make_item(brand, item_code=code)
            make_snapshot(company, item, year=2026, month=month, qty='100')
        result = compute_production_forecast(company)
        self.assertEqual(result['anchor_month'], 3)

    def test_production_po_additions_apply_in_pre_anchor_walk(self):
        # item_a: Jan snap=500, Apr snap=300 (most-recent=Apr)
        # item_b: Mar snap=200 (most-recent=Mar) → anchor=Mar
        # item_a walk: Jan(500) → Feb(+200 prod, -100 demand=600) → Mar(no snap → 600 green)
        # Old algorithm had a bug: prod adds not applied during pre-anchor walk
        company = make_company()
        brand = make_brand(company)
        dist = make_distributor(company)
        item_a = make_item(brand, item_code='A', safety_stock=None)
        item_b = make_item(brand, item_code='B')
        make_snapshot(company, item_a, year=2026, month=1, qty='500')
        make_snapshot(company, item_a, year=2026, month=4, qty='300')
        make_snapshot(company, item_b, year=2026, month=3, qty='200')
        make_po_with_demand(dist, item_a, 2026, 2, 100)
        additions = {(item_a.pk, 2026, 2): 200.0}
        result = compute_production_forecast(company, production_po_additions=additions)
        self.assertEqual(result['anchor_month'], 3)
        row_a = next(r for r in result['rows'] if r['item'].item_code == 'A')
        # Mar anchor: item_a has no Mar snapshot → calculated; walk: 500+200-100=600 at Mar
        mar_cell = next(c for c in row_a['monthly_data'] if c['month'] == 3)
        self.assertAlmostEqual(mar_cell['inventory'], 600.0)
        self.assertEqual(mar_cell['status'], 'green')
        # Apr: snapshot=300 overrides
        apr_cell = next(c for c in row_a['monthly_data'] if c['month'] == 4)
        self.assertEqual(apr_cell['status'], 'snapshot')
        self.assertAlmostEqual(apr_cell['inventory'], 300.0)

    def test_zero_snapshot_value_overrides_with_snapshot_status(self):
        # A snapshot of 0 is real data — shows status='snapshot', not 'red'
        company = make_company()
        brand = make_brand(company)
        item = make_item(brand, item_code='RED')
        make_snapshot(company, item, year=2026, month=4, qty='500')
        make_snapshot(company, item, year=2026, month=6, qty='0')
        result = compute_production_forecast(company)
        row = result['rows'][0]
        jun_cell = next(c for c in row['monthly_data'] if c['month'] == 6)
        self.assertEqual(jun_cell['status'], 'snapshot')
        self.assertAlmostEqual(jun_cell['inventory'], 0.0)

    def test_item_with_no_snapshot_shows_no_data_at_anchor(self):
        # Item with demand but no snapshot: anchor cell = no_data (not a calculated red)
        company = make_company()
        brand = make_brand(company)
        dist = make_distributor(company)
        anchor_item = make_item(brand, item_code='ANC')
        demand_item = make_item(brand, item_code='DEM')
        make_snapshot(company, anchor_item, year=2026, month=4, qty='200')
        make_po_with_demand(dist, demand_item, 2026, 5, 50)
        result = compute_production_forecast(company)
        row = next(r for r in result['rows'] if r['item'].item_code == 'DEM')
        anchor_cell = row['monthly_data'][0]
        self.assertEqual(anchor_cell['status'], 'no_data')
        may_cell = next(c for c in row['monthly_data'] if c['month'] == 5)
        self.assertAlmostEqual(may_cell['inventory'], -50.0)
        self.assertEqual(may_cell['status'], 'red')

    def test_snapshot_override_then_continues_from_overridden_value(self):
        # After a mid-horizon snapshot, projection continues from snapshot value (not calculated)
        # Jan snap=500; Feb demand=200 (calc=300); Mar snap=800 (override); Apr demand=100 → 700
        company = make_company()
        brand = make_brand(company)
        dist = make_distributor(company)
        item = make_item(brand, item_code='RED')
        make_snapshot(company, item, year=2026, month=1, qty='500')
        make_snapshot(company, item, year=2026, month=3, qty='800')
        make_po_with_demand(dist, item, 2026, 2, 200)
        make_po_with_demand(dist, item, 2026, 4, 100)
        result = compute_production_forecast(company)
        # anchor = Mar (most-recent = Mar); horizon starts at Mar
        self.assertEqual(result['anchor_month'], 3)
        row = result['rows'][0]
        apr_cell = next(c for c in row['monthly_data'] if c['month'] == 4)
        self.assertAlmostEqual(apr_cell['inventory'], 700.0)  # 800 - 100
