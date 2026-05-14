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
# 7. Anchor cell for items with earlier snapshots
# ---------------------------------------------------------------------------

class ForecastEarlierSnapshotTest(TestCase):

    def test_item_with_earlier_snapshot_shows_no_data_in_anchor_column(self):
        company = make_company()
        brand = make_brand(company)
        item_a = make_item(brand, item_code='A001')
        item_b = make_item(brand, item_code='B001')
        # item_a snapshot in April (anchor), item_b snapshot in January
        make_snapshot(company, item_a, year=2026, month=4, qty='200')
        make_snapshot(company, item_b, year=2026, month=1, qty='300')
        result = compute_production_forecast(company)
        row_b = next(r for r in result['rows'] if r['item'].item_code == 'B001')
        # Anchor column (April): item_b has no April snapshot → no_data
        self.assertEqual(row_b['monthly_data'][0]['status'], 'no_data')

    def test_item_with_earlier_snapshot_projects_forward_from_known_value(self):
        company = make_company()
        brand = make_brand(company)
        dist = make_distributor(company)
        item_a = make_item(brand, item_code='A001')
        item_b = make_item(brand, item_code='B001')
        make_snapshot(company, item_a, year=2026, month=4, qty='100')
        make_snapshot(company, item_b, year=2026, month=2, qty='300')
        # Demand for item_b in Mar, Apr (gap months) and then May (projection)
        make_po_with_demand(dist, item_b, 2026, 3, 50)
        make_po_with_demand(dist, item_b, 2026, 4, 50)
        make_po_with_demand(dist, item_b, 2026, 5, 60)
        result = compute_production_forecast(company)
        row_b = next(r for r in result['rows'] if r['item'].item_code == 'B001')
        # After gap walk: 300 - 50(Mar) - 50(Apr) = 200 at start of projection
        # May projection: 200 - 60 = 140
        may_cell = next(c for c in row_b['monthly_data'] if c['month'] == 5)
        self.assertEqual(may_cell['inventory'], 140.0)


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
