"""
Tests for apps.distribution.order_generation — Phase 4-step-2a.

Covers generate_projected_orders() logic and view integration.
"""
from datetime import date

from django.test import Client, TestCase
from django.urls import reverse

from apps.distribution.forecast import compute_distributor_forecast
from apps.distribution.models import Distributor, DistributorItemProfile, InventorySnapshot
from apps.distribution.order_generation import generate_projected_orders
from apps.distribution.tests_forecast import (
    _make_company, _make_supplier_admin, _make_distributor,
    _make_brand, _make_item, _make_account, _make_batch,
    _make_snapshot, _make_sale,
)


# ---------------------------------------------------------------------------
# Extra helpers for order generation tests
# ---------------------------------------------------------------------------

def _make_distributor_with_profile(company, name='OG Dist',
                                    order_value=20, order_unit='pallets'):
    return Distributor.objects.create(
        company=company, name=name,
        order_quantity_value=order_value,
        order_quantity_unit=order_unit,
    )


def _forecast(distributor, today=None):
    """Convenience wrapper: compute forecast with a fixed today."""
    if today is None:
        today = date(2026, 1, 20)  # anchor = Jan 2026 if snapshot is Jan 2026
    return compute_distributor_forecast(distributor, today=today)


# ---------------------------------------------------------------------------
# Unit tests
# ---------------------------------------------------------------------------

class OrderGenerationTest(TestCase):

    def setUp(self):
        self.company = _make_company('OG Co')
        self.brand = _make_brand(self.company)
        self.account_cache = {}  # {distributor_id: account}
        self.batch_cache = {}

    def _account_and_batch(self, distributor):
        if distributor.pk not in self.account_cache:
            self.account_cache[distributor.pk] = _make_account(self.company, distributor)
            self.batch_cache[distributor.pk] = _make_batch(self.company, distributor)
        return self.account_cache[distributor.pk], self.batch_cache[distributor.pk]

    def _snap(self, distributor, item, year=2026, month=1, qty=100):
        return _make_snapshot(distributor, item, year, month, qty)

    def _sale(self, distributor, item, year, month, qty):
        acc, batch = self._account_and_batch(distributor)
        return _make_sale(self.company, batch, acc, item, year, month, qty)

    def _run(self, distributor, today=date(2026, 1, 20)):
        fr = compute_distributor_forecast(distributor, today=today)
        return generate_projected_orders(distributor, fr), fr

    # -----------------------------------------------------------------------
    # 1. No order profile → has_order_profile=False, no orders
    # -----------------------------------------------------------------------
    def test_generate_orders_no_profile_returns_empty_with_flag(self):
        dist = _make_distributor(self.company, 'No Profile Dist')
        item = _make_item(self.brand, 'Item A', 'A001')
        self._snap(dist, item, 2026, 1, qty=100)
        self._sale(dist, item, 2025, 2, 20)
        result, _ = self._run(dist)
        self.assertFalse(result['has_order_profile'])
        self.assertEqual(result['total_orders_count'], 0)

    # -----------------------------------------------------------------------
    # 2. All items safe through horizon → no orders
    # -----------------------------------------------------------------------
    def test_generate_orders_all_safe_no_orders(self):
        dist = _make_distributor_with_profile(self.company, order_value=100, order_unit='cases')
        item = _make_item(self.brand, 'Item A', 'A001')
        self._snap(dist, item, 2026, 1, qty=1000)
        # Depletion 10/month, safety 50, starting 1000 → never triggers
        for m in range(2, 14):
            y, mo = (2025, m) if m <= 12 else (2026, m - 12)
            self._sale(dist, item, y, mo, 10)
        DistributorItemProfile.objects.create(
            distributor=dist, item=item, safety_stock_cases=50
        )
        result, _ = self._run(dist)
        self.assertEqual(result['total_orders_count'], 0)
        for slot in result['orders_per_horizon']:
            self.assertEqual(slot['order_count'], 0)

    # -----------------------------------------------------------------------
    # 3. Simple trigger — pallet distributor, one item, one order
    # -----------------------------------------------------------------------
    def test_generate_orders_simple_trigger_pallet_distributor(self):
        dist = _make_distributor_with_profile(self.company, order_value=20, order_unit='pallets')
        item = _make_item(self.brand, 'Item A', 'A001')
        item.cases_per_pallet = 10
        item.save()
        # Starting 50 cases, depletion 20/month, safety stock 0
        # Month 1: 30, Month 2: 10, Month 3: -10 → triggers
        self._snap(dist, item, 2026, 1, qty=50)
        for m in range(2, 14):
            y, mo = (2025, m) if m <= 12 else (2026, m - 12)
            self._sale(dist, item, y, mo, 20)
        result, fr = self._run(dist)
        # Should generate at least one order
        self.assertGreater(result['total_orders_count'], 0)
        self.assertTrue(result['has_order_profile'])
        # Order lines should include item A
        all_orders = [o for slot in result['orders_per_horizon'] for o in slot['orders']]
        line_items = [l['item'].pk for o in all_orders for l in o['lines']]
        self.assertIn(item.pk, line_items)
        # Order unit is pallets
        for o in all_orders:
            self.assertEqual(o['order_unit'], 'pallets')

    # -----------------------------------------------------------------------
    # 4. Simple trigger — case distributor
    # -----------------------------------------------------------------------
    def test_generate_orders_simple_trigger_case_distributor(self):
        dist = _make_distributor_with_profile(self.company, order_value=100, order_unit='cases')
        item = _make_item(self.brand, 'Item A', 'A001')
        # Starting 30, depletion 20/month, safety 0
        # Month 1: 10, Month 2: -10 → triggers
        self._snap(dist, item, 2026, 1, qty=30)
        for m in range(2, 14):
            y, mo = (2025, m) if m <= 12 else (2026, m - 12)
            self._sale(dist, item, y, mo, 20)
        result, _ = self._run(dist)
        self.assertGreater(result['total_orders_count'], 0)
        all_orders = [o for slot in result['orders_per_horizon'] for o in slot['orders']]
        for o in all_orders:
            self.assertEqual(o['order_unit'], 'cases')
            # Each line's pallets is None for case distributors
            for l in o['lines']:
                self.assertIsNone(l['pallets'])

    # -----------------------------------------------------------------------
    # 5. Multiple triggering items — both allocated, more critical first
    # -----------------------------------------------------------------------
    def test_generate_orders_multiple_triggering_items(self):
        dist = _make_distributor_with_profile(self.company, order_value=200, order_unit='cases')
        item_a = _make_item(self.brand, 'Item A', 'A001', sort_order=1)
        item_b = _make_item(self.brand, 'Item B', 'B001', sort_order=2)
        # Item A: start 10, dep 20 → month 1: -10 (less critical)
        # Item B: start 5, dep 20 → month 1: -15 (more critical)
        self._snap(dist, item_a, 2026, 1, qty=10)
        self._snap(dist, item_b, 2026, 1, qty=5)
        for m in range(2, 14):
            y, mo = (2025, m) if m <= 12 else (2026, m - 12)
            self._sale(dist, item_a, y, mo, 20)
            self._sale(dist, item_b, y, mo, 20)
        result, _ = self._run(dist)
        self.assertGreater(result['total_orders_count'], 0)
        all_orders = [o for slot in result['orders_per_horizon'] for o in slot['orders']]
        first_order = all_orders[0]
        line_item_ids = {l['item'].pk for l in first_order['lines']}
        # Both items should be allocated in the first order
        self.assertIn(item_a.pk, line_item_ids)
        self.assertIn(item_b.pk, line_item_ids)
        # Item B (more critical, bigger deficit) should appear first
        self.assertEqual(first_order['lines'][0]['item'].pk, item_b.pk)

    # -----------------------------------------------------------------------
    # 6. Remaining capacity fills next-month nearest-to-safety items
    # -----------------------------------------------------------------------
    def test_generate_orders_capacity_remaining_looks_to_next_month(self):
        # Item A triggers in month 2, needs 11 cases from 100-case order
        # Remaining 89 cases should go to Item B in month 3 (closest to safety)
        dist = _make_distributor_with_profile(self.company, order_value=100, order_unit='cases')
        item_a = _make_item(self.brand, 'Item A', 'A001', sort_order=1)
        item_b = _make_item(self.brand, 'Item B', 'B001', sort_order=2)
        DistributorItemProfile.objects.create(
            distributor=dist, item=item_b, safety_stock_cases=50
        )
        # Item A: start=10, dep=20/month, ss=0 → month 1: -10 → triggers
        # Item B: start=200, dep=20/month, ss=50 → well safe for many months
        self._snap(dist, item_a, 2026, 1, qty=10)
        self._snap(dist, item_b, 2026, 1, qty=200)
        for m in range(2, 14):
            y, mo = (2025, m) if m <= 12 else (2026, m - 12)
            self._sale(dist, item_a, y, mo, 20)
            self._sale(dist, item_b, y, mo, 20)
        result, _ = self._run(dist)
        # At least one order should have both Item A and Item B in lines
        all_orders = [o for slot in result['orders_per_horizon'] for o in slot['orders']]
        found_b_in_order = any(
            item_b.pk in {l['item'].pk for l in o['lines']}
            for o in all_orders
        )
        self.assertTrue(found_b_in_order, 'Item B should get capacity from look-ahead')

    # -----------------------------------------------------------------------
    # 7. Item with large deficit → multiple orders same month
    # -----------------------------------------------------------------------
    def test_generate_orders_multiple_orders_same_month(self):
        # Item needs 3 orders of 10 cases each to fix a -25 deficit
        dist = _make_distributor_with_profile(self.company, order_value=10, order_unit='cases')
        item = _make_item(self.brand, 'Item A', 'A001')
        # Start=5, dep=30/month, ss=0 → month 1: 5-30=-25 → triggers
        self._snap(dist, item, 2026, 1, qty=5)
        for m in range(2, 14):
            y, mo = (2025, m) if m <= 12 else (2026, m - 12)
            self._sale(dist, item, y, mo, 30)
        result, _ = self._run(dist)
        # Should generate multiple orders for month 0 (first trigger)
        self.assertGreaterEqual(result['total_orders_count'], 2)

    # -----------------------------------------------------------------------
    # 8. Cap: max 5 orders per trigger month
    # -----------------------------------------------------------------------
    def test_generate_orders_max_5_orders_per_month_cap(self):
        # Item needs 10+ orders of 1 case each — cap at 5
        dist = _make_distributor_with_profile(self.company, order_value=1, order_unit='cases')
        item = _make_item(self.brand, 'Item A', 'A001')
        # Start=5, dep=100/month, ss=0 → month 1: -95 → needs 96 orders of 1 case to fix
        self._snap(dist, item, 2026, 1, qty=5)
        for m in range(2, 14):
            y, mo = (2025, m) if m <= 12 else (2026, m - 12)
            self._sale(dist, item, y, mo, 100)
        result, _ = self._run(dist)
        # Total orders should be capped, not infinite
        self.assertLessEqual(result['total_orders_count'], 60)  # generous upper bound
        # First trigger month (proj month 1) should have at most 5 orders
        all_slots = result['orders_per_horizon']
        for slot in all_slots:
            self.assertLessEqual(slot['order_count'], 5)

    # -----------------------------------------------------------------------
    # 9. Pallet distributor + item missing cases_per_pallet → skipped
    # -----------------------------------------------------------------------
    def test_generate_orders_skipped_items_no_cases_per_pallet(self):
        dist = _make_distributor_with_profile(self.company, order_value=20, order_unit='pallets')
        item = _make_item(self.brand, 'Item A', 'A001')
        # cases_per_pallet is None (default) — should be skipped
        self._snap(dist, item, 2026, 1, qty=100)
        self._sale(dist, item, 2025, 2, 20)
        result, _ = self._run(dist)
        skipped_reasons = {s['reason'] for s in result['skipped_items']}
        self.assertIn('no_cases_per_pallet', skipped_reasons)
        skipped_ids = {s['item'].pk for s in result['skipped_items']}
        self.assertIn(item.pk, skipped_ids)

    # -----------------------------------------------------------------------
    # 10. Item with all no_data projection cells → skipped (no_depletion_data)
    # -----------------------------------------------------------------------
    def test_generate_orders_skipped_items_no_depletion(self):
        dist = _make_distributor_with_profile(self.company, order_value=100, order_unit='cases')
        item = _make_item(self.brand, 'Item A', 'A001')
        # Snapshot exists but no sales data → all projection cells are no_data
        self._snap(dist, item, 2026, 1, qty=100)
        # No sales records → all projection months are no_data (no prior year data)
        result, _ = self._run(dist)
        skipped_reasons = {s['reason'] for s in result['skipped_items']}
        self.assertIn('no_depletion_data', skipped_reasons)

    # -----------------------------------------------------------------------
    # 11. No safety stock → triggers only when inventory < 0
    # -----------------------------------------------------------------------
    def test_generate_orders_safety_stock_zero_default(self):
        dist = _make_distributor_with_profile(self.company, order_value=50, order_unit='cases')
        item = _make_item(self.brand, 'Item A', 'A001')
        # No DistributorItemProfile → safety_stock treated as None → triggers when negative
        # Start=10, dep=20/month → month 1: -10 → triggers
        self._snap(dist, item, 2026, 1, qty=10)
        for m in range(2, 14):
            y, mo = (2025, m) if m <= 12 else (2026, m - 12)
            self._sale(dist, item, y, mo, 20)
        result, _ = self._run(dist)
        self.assertGreater(result['total_orders_count'], 0)

    # -----------------------------------------------------------------------
    # 12. Inactive distributor item excluded (already excluded from forecast)
    # -----------------------------------------------------------------------
    def test_generate_orders_excludes_inactive_items(self):
        dist = _make_distributor_with_profile(self.company, order_value=100, order_unit='cases')
        item_active = _make_item(self.brand, 'Item A', 'A001', sort_order=1)
        item_inactive = _make_item(self.brand, 'Item B', 'B001', sort_order=2)
        DistributorItemProfile.objects.create(
            distributor=dist, item=item_inactive, is_active=False
        )
        self._snap(dist, item_active, 2026, 1, qty=50)
        self._snap(dist, item_inactive, 2026, 1, qty=50)
        self._sale(dist, item_active, 2025, 2, 20)
        self._sale(dist, item_inactive, 2025, 2, 20)
        result, _ = self._run(dist)
        all_line_items = {
            l['item'].pk
            for slot in result['orders_per_horizon']
            for o in slot['orders']
            for l in o['lines']
        }
        self.assertNotIn(item_inactive.pk, all_line_items)


# ---------------------------------------------------------------------------
# View integration tests
# ---------------------------------------------------------------------------

class OrderGenerationViewTest(TestCase):

    def setUp(self):
        self.company = _make_company('View OG Co')
        self.admin = _make_supplier_admin(self.company, username='og_admin')
        self.brand = _make_brand(self.company)
        self.client = Client()
        self.client.login(username='og_admin', password='testpass123')
        self.url = reverse('distributor_list')

    def _make_setup(self, order_value=100, order_unit='cases'):
        dist = _make_distributor_with_profile(
            self.company, name='OG Dist', order_value=order_value, order_unit=order_unit
        )
        item = _make_item(self.brand, 'Item A', 'OGA1')
        acc = _make_account(self.company, dist)
        batch = _make_batch(self.company, dist)
        _make_snapshot(dist, item, 2026, 1, quantity=50)
        for m in range(2, 14):
            y, mo = (2025, m) if m <= 12 else (2026, m - 12)
            _make_sale(self.company, batch, acc, item, y, mo, 20)
        return dist, item

    # -----------------------------------------------------------------------
    # 13. Orders row renders with badge counts
    # -----------------------------------------------------------------------
    def test_orders_row_renders_with_counts(self):
        dist, item = self._make_setup()
        resp = self.client.get(
            self.url + f'?tab=forecast&forecast_distributor={dist.pk}'
        )
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'forecast-orders-row')
        # At least one badge should appear (item triggers in month 1)
        self.assertContains(resp, 'bg-info')

    # -----------------------------------------------------------------------
    # 14. Orders row warning when no profile
    # -----------------------------------------------------------------------
    def test_orders_row_warning_when_no_profile(self):
        dist = _make_distributor(self.company, name='No Profile Dist')
        item = _make_item(self.brand, 'Item NP', 'NP01')
        _make_snapshot(dist, item, 2026, 1, quantity=100)
        resp = self.client.get(
            self.url + f'?tab=forecast&forecast_distributor={dist.pk}'
        )
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'No order profile configured')

    # -----------------------------------------------------------------------
    # 15. Skipped items banner renders when applicable
    # -----------------------------------------------------------------------
    def test_skipped_items_banner_renders(self):
        # Pallet distributor, item without cases_per_pallet → skipped → banner shows
        dist = _make_distributor_with_profile(
            self.company, name='Pallet Dist', order_value=20, order_unit='pallets'
        )
        item = _make_item(self.brand, 'Item SB', 'SB01')
        # cases_per_pallet not set → skipped
        acc = _make_account(self.company, dist)
        batch = _make_batch(self.company, dist)
        _make_snapshot(dist, item, 2026, 1, quantity=50)
        _make_sale(self.company, batch, acc, item, 2025, 2, 20)
        resp = self.client.get(
            self.url + f'?tab=forecast&forecast_distributor={dist.pk}'
        )
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'forecast-skipped-banner')

    # -----------------------------------------------------------------------
    # 16. Skipped banner absent when no items skipped
    # -----------------------------------------------------------------------
    def test_skipped_items_banner_absent_when_no_skipped(self):
        dist, _ = self._make_setup(order_value=100, order_unit='cases')
        resp = self.client.get(
            self.url + f'?tab=forecast&forecast_distributor={dist.pk}'
        )
        self.assertEqual(resp.status_code, 200)
        self.assertNotContains(resp, 'forecast-skipped-banner')

    # -----------------------------------------------------------------------
    # 17. Month with no orders shows dash
    # -----------------------------------------------------------------------
    def test_orders_row_no_orders_shows_dash(self):
        # Item stays safe through whole horizon → all months show dash
        dist = _make_distributor_with_profile(
            self.company, name='Safe Dist', order_value=100, order_unit='cases'
        )
        item = _make_item(self.brand, 'Item SF', 'SF01')
        _make_snapshot(dist, item, 2026, 1, quantity=5000)
        acc = _make_account(self.company, dist)
        batch = _make_batch(self.company, dist)
        for m in range(2, 14):
            y, mo = (2025, m) if m <= 12 else (2026, m - 12)
            _make_sale(self.company, batch, acc, item, y, mo, 5)
        resp = self.client.get(
            self.url + f'?tab=forecast&forecast_distributor={dist.pk}'
        )
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'forecast-orders-row')
        self.assertNotContains(resp, 'bg-info')  # no badge = no orders


# ---------------------------------------------------------------------------
# Depletion exposure in forecast cells
# ---------------------------------------------------------------------------

class ForecastDepletionExposureTest(TestCase):

    def setUp(self):
        self.company = _make_company('Dep Co')
        self.distributor = _make_distributor(self.company)
        self.brand = _make_brand(self.company)
        self.item = _make_item(self.brand)
        self.account = _make_account(self.company, self.distributor)
        self.batch = _make_batch(self.company, self.distributor)

    def _sale(self, year, month, qty):
        return _make_sale(self.company, self.batch, self.account,
                          self.item, year, month, qty)

    def _snap(self, year, month, qty=100):
        return _make_snapshot(self.distributor, self.item, year, month, qty)

    def test_snapshot_cell_has_none_depletion(self):
        self._snap(2026, 1, qty=100)
        result = compute_distributor_forecast(self.distributor, today=date(2026, 5, 1))
        anchor = result['rows'][0]['monthly_data'][0]
        self.assertIsNone(anchor['depletion'])
        self.assertTrue(anchor['is_snapshot'])

    def test_past_month_cell_exposes_depletion(self):
        self._snap(2026, 1, qty=100)
        self._sale(2026, 2, 30)  # actual past sales
        result = compute_distributor_forecast(self.distributor, today=date(2026, 5, 1))
        row = result['rows'][0]
        monthly = {(c['year'], c['month']): c for c in row['monthly_data']}
        feb_cell = monthly[(2026, 2)]
        self.assertFalse(feb_cell['is_snapshot'])
        self.assertEqual(feb_cell['depletion'], 30.0)

    def test_projection_cell_exposes_depletion(self):
        self._snap(2026, 1, qty=100)
        self._sale(2025, 5, 25)  # prior-year for May 2026
        result = compute_distributor_forecast(self.distributor, today=date(2026, 1, 20))
        row = result['rows'][0]
        monthly = {(c['year'], c['month']): c for c in row['monthly_data']}
        may_cell = monthly[(2026, 5)]
        self.assertEqual(may_cell['depletion'], 25.0)

    def test_no_data_cell_has_none_depletion(self):
        self._snap(2026, 1, qty=100)
        # No prior-year data → no_data cells
        result = compute_distributor_forecast(self.distributor, today=date(2026, 1, 20))
        row = result['rows'][0]
        for cell in row['monthly_data'][1:]:  # skip anchor
            self.assertIsNone(cell['depletion'])
            self.assertEqual(cell['status'], 'no_data')

    def test_safety_stock_map_in_forecast_result(self):
        self._snap(2026, 1, qty=100)
        DistributorItemProfile.objects.create(
            distributor=self.distributor, item=self.item, safety_stock_cases=75
        )
        result = compute_distributor_forecast(self.distributor, today=date(2026, 5, 1))
        self.assertIn('safety_stock_map', result)
        self.assertEqual(result['safety_stock_map'].get(self.item.pk), 75)
