"""
Tests for apps.distribution.order_generation — Phase 4-step-2a.

Covers generate_projected_orders() logic and view integration.
"""
from datetime import date

from django.test import Client, TestCase
from django.urls import reverse

from apps.distribution.forecast import compute_distributor_forecast
from apps.distribution.models import Distributor, DistributorItemProfile, InventorySnapshot
from apps.distribution.order_generation import generate_projected_orders, suggest_po_for_month
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

    # -----------------------------------------------------------------------
    # 13. Consecutive triggers — one order per prior month, no pile-up
    # -----------------------------------------------------------------------
    def test_each_month_gets_own_order_when_consecutive_triggers(self):
        # Item depletes 100/month from 500-case starting inventory.
        # Triggers begin in Jul 2026 (500 - 100*6 = -100).
        # Order capacity = 100 cases = exactly one month's depletion.
        # Each trigger should generate ONE order in its prior month.
        dist = _make_distributor_with_profile(
            self.company, order_value=100, order_unit='cases'
        )
        item = _make_item(self.brand, 'Item A', 'A001')
        self._snap(dist, item, 2026, 1, qty=500)
        for m in range(2, 14):
            y, mo = (2025, m) if m <= 12 else (2026, m - 12)
            self._sale(dist, item, y, mo, 100)
        result, _ = self._run(dist)

        slots = {(s['year'], s['month']): s for s in result['orders_per_horizon']}
        # Jun, Jul, Aug each placed orders one month prior
        self.assertEqual(slots[(2026, 6)]['order_count'], 1)  # for Jul trigger
        self.assertEqual(slots[(2026, 7)]['order_count'], 1)  # for Aug trigger
        self.assertEqual(slots[(2026, 8)]['order_count'], 1)  # for Sep trigger
        # No month should have more than one order (capacity exactly covers one month)
        for slot in result['orders_per_horizon']:
            self.assertLessEqual(slot['order_count'], 1)

    # -----------------------------------------------------------------------
    # 14. Order sized to cover depletion + safety stock gap exactly
    # -----------------------------------------------------------------------
    def test_order_sized_to_cover_one_month_plus_safety(self):
        # Item: starting_inventory=0, depletion=100, safety=50.
        # Feb trigger: virtual_inv = -100. required_cases = 50 - (-100) = 150.
        # order_qty=150 exactly exhausts capacity in Step 1 (no filler).
        dist = _make_distributor_with_profile(
            self.company, order_value=150, order_unit='cases'
        )
        item = _make_item(self.brand, 'Item A', 'A001')
        DistributorItemProfile.objects.create(
            distributor=dist, item=item, safety_stock_cases=50
        )
        self._snap(dist, item, 2026, 1, qty=0)
        for m in range(2, 14):
            y, mo = (2025, m) if m <= 12 else (2026, m - 12)
            self._sale(dist, item, y, mo, 100)
        result, _ = self._run(dist)
        all_orders = [o for slot in result['orders_per_horizon'] for o in slot['orders']]
        self.assertGreater(len(all_orders), 0)
        first_line = all_orders[0]['lines'][0]
        self.assertEqual(first_line['item'].pk, item.pk)
        self.assertEqual(first_line['cases'], 150.0)

    # -----------------------------------------------------------------------
    # 15. Zero safety stock — order sized to end at zero, no extra buffer
    # -----------------------------------------------------------------------
    def test_zero_safety_stock_zero_buffer(self):
        # No safety stock profile → ss treated as 0.
        # Start=5, dep=20/month → Feb: -15. required_cases = 0-(-15) = 15.
        # order_qty=15 exactly exhausts capacity in Step 1 (no filler).
        dist = _make_distributor_with_profile(
            self.company, order_value=15, order_unit='cases'
        )
        item = _make_item(self.brand, 'Item A', 'A001')
        # No DistributorItemProfile → safety_stock effectively 0
        self._snap(dist, item, 2026, 1, qty=5)
        for m in range(2, 14):
            y, mo = (2025, m) if m <= 12 else (2026, m - 12)
            self._sale(dist, item, y, mo, 20)
        result, _ = self._run(dist)
        all_orders = [o for slot in result['orders_per_horizon'] for o in slot['orders']]
        self.assertGreater(len(all_orders), 0)
        # First order for first trigger should allocate exactly 15 cases
        first_line = all_orders[0]['lines'][0]
        self.assertEqual(first_line['cases'], 15.0)

    # -----------------------------------------------------------------------
    # 16. Filler items sized with same formula as triggering items
    # -----------------------------------------------------------------------
    def test_filler_items_sized_with_same_formula(self):
        # Item A triggers in Feb (required 10 cases), leaving 90 capacity.
        # Item B is safe in Feb but triggers in Mar (required 20 cases).
        # B should appear as filler using required_cases = ss - virtual_inv[Mar].
        dist = _make_distributor_with_profile(
            self.company, order_value=100, order_unit='cases'
        )
        item_a = _make_item(self.brand, 'Item A', 'A001', sort_order=1)
        item_b = _make_item(self.brand, 'Item B', 'B001', sort_order=2)
        # A: snap=10, dep=20 → Feb=-10 (trigger)
        self._snap(dist, item_a, 2026, 1, qty=10)
        for m in range(2, 14):
            y, mo = (2025, m) if m <= 12 else (2026, m - 12)
            self._sale(dist, item_a, y, mo, 20)
        # B: snap=30, dep=25 → Feb=5 (safe), Mar=-20 (filler trigger)
        self._snap(dist, item_b, 2026, 1, qty=30)
        for m in range(2, 14):
            y, mo = (2025, m) if m <= 12 else (2026, m - 12)
            self._sale(dist, item_b, y, mo, 25)
        result, _ = self._run(dist)
        all_orders = [o for slot in result['orders_per_horizon'] for o in slot['orders']]
        self.assertGreater(len(all_orders), 0)
        # B must appear in some order as filler
        b_lines = [
            l for o in all_orders for l in o['lines'] if l['item'].pk == item_b.pk
        ]
        self.assertTrue(b_lines, 'Item B should appear as a filler in at least one order')
        # B's allocation in the first order containing it: sized by formula
        first_b_line = b_lines[0]
        # required_cases for B = max(0, 0 - (-20)) = 20 (B's Mar virtual_inv = -20)
        self.assertEqual(first_b_line['cases'], 20.0)

    # -----------------------------------------------------------------------
    # 17. Multiple orders when single item's need exceeds order capacity
    # -----------------------------------------------------------------------
    def test_multiple_orders_per_month_when_single_item_exceeds_capacity(self):
        # Item needs 50 pallets, distributor orders 20 pallets at a time → 3 orders.
        # cpp=12: 50 pallets = 600 cases. Snap=5, dep=600/month.
        # Feb trigger: virtual_inv=-595. pallets_needed=ceil(595/12)=50.
        # Orders: 20 pallet (240 cases), 20 pallet (240 cases), 10 pallet (120 cases).
        dist = _make_distributor_with_profile(
            self.company, order_value=20, order_unit='pallets'
        )
        item = _make_item(self.brand, 'Item A', 'A001')
        item.cases_per_pallet = 12
        item.save()
        self._snap(dist, item, 2026, 1, qty=5)
        for m in range(2, 14):
            y, mo = (2025, m) if m <= 12 else (2026, m - 12)
            self._sale(dist, item, y, mo, 600)
        result, _ = self._run(dist)
        # Jan slot (prior of Feb) should have 3 orders (20+20+remaining pallets)
        jan_slot = next(
            s for s in result['orders_per_horizon']
            if s['year'] == 2026 and s['month'] == 1
        )
        self.assertEqual(jan_slot['order_count'], 3)
        # Each order is capped at 20 pallets = 240 cases; item A must appear in all
        for o in jan_slot['orders']:
            line_items = {l['item'].pk for l in o['lines']}
            self.assertIn(item.pk, line_items)
            self.assertLessEqual(o['total_cases'], 240.0)

    # -----------------------------------------------------------------------
    # 18. Orders distribute across months, no pile-up in a single month
    # -----------------------------------------------------------------------
    def test_orders_distribute_across_months_no_pile_up(self):
        # Steady 100/month depletion, 500-case start, order_qty=100 cases.
        # Triggers begin in Jul 2026. Each subsequent month should get its own
        # order (one per prior month), never multiple orders for the same month.
        dist = _make_distributor_with_profile(
            self.company, order_value=100, order_unit='cases'
        )
        item = _make_item(self.brand, 'Item A', 'A001')
        self._snap(dist, item, 2026, 1, qty=500)
        for m in range(2, 14):
            y, mo = (2025, m) if m <= 12 else (2026, m - 12)
            self._sale(dist, item, y, mo, 100)
        result, _ = self._run(dist)
        # No prior month should accumulate more than one order
        for slot in result['orders_per_horizon']:
            self.assertLessEqual(slot['order_count'], 1)
        # At least three orders generated (one each for several trigger months)
        self.assertGreaterEqual(result['total_orders_count'], 3)


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
    # 13. Orders row renders; total_count equals saved_count (not inflated by algorithm)
    # -----------------------------------------------------------------------
    def test_orders_row_renders_with_counts(self):
        dist, item = self._make_setup()
        resp = self.client.get(
            self.url + f'?tab=forecast&forecast_distributor={dist.pk}'
        )
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'forecast-orders-row')
        # No saved POs → total_count = 0 for all slots (not inflated by algorithm)
        orders_result = resp.context['orders_result']
        for slot in orders_result['orders_per_horizon']:
            if not slot['is_snapshot']:
                self.assertEqual(slot['total_count'], slot['saved_count'],
                                 f'total_count should equal saved_count for {slot["year"]}-{slot["month"]}')

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
    # 17. Month with no saved POs shows dash (no badge)
    # -----------------------------------------------------------------------
    def test_orders_row_no_orders_shows_dash(self):
        # Item stays safe through whole horizon and no saved POs → all months show dash
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
        self.assertContains(resp, 'forecast-order-btn')  # buttons always render
        # No saved POs → all total_count values are 0
        orders_result = resp.context['orders_result']
        for slot in orders_result['orders_per_horizon']:
            if not slot['is_snapshot']:
                self.assertEqual(slot['total_count'], 0)


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


# ---------------------------------------------------------------------------
# suggest_po_for_month unit tests
# ---------------------------------------------------------------------------

class SuggestPoForMonthTest(TestCase):

    def setUp(self):
        self.company = _make_company('Suggest Co')
        self.brand = _make_brand(self.company)

    def _dist_cases(self, order_value=10):
        return Distributor.objects.create(
            company=self.company, name='Cases Dist',
            order_quantity_value=order_value, order_quantity_unit='cases',
        )

    def _dist_pallets(self, order_value=2):
        dist = Distributor.objects.create(
            company=self.company, name='Pallet Dist',
            order_quantity_value=order_value, order_quantity_unit='pallets',
        )
        return dist

    def _item(self, name='Item A', code='A001', cpp=None):
        item = _make_item(self.brand, name=name, item_code=code)
        if cpp is not None:
            item.cases_per_pallet = cpp
            item.save()
        return item

    def _forecast(self, item, cells, safety_stock=None):
        """Build a minimal forecast_result with one item and given (year, month, inventory) cells."""
        ss_map = {}
        if safety_stock is not None:
            ss_map[item.pk] = safety_stock
        monthly_data = [
            {
                'year': y, 'month': m,
                'inventory': inv,
                'inventory_display': '' if inv is None else str(inv),
                'depletion': None, 'status': 'green', 'is_snapshot': False,
            }
            for y, m, inv in cells
        ]
        return {'safety_stock_map': ss_map, 'rows': [{'item': item, 'monthly_data': monthly_data}]}

    # 1. Item below safety stock → suggestion returned
    def test_suggests_for_item_below_safety_stock(self):
        dist = self._dist_cases(order_value=10)
        item = self._item()
        # Modal month=May 2026 → lookahead=June 2026; inv=15, ss=50 → shortage=35
        # Capacity=10 cases total; allocate min(ceil(35), 10) = 10 cases
        fr = self._forecast(item, [(2026, 6, 15.0)], safety_stock=50)
        result = suggest_po_for_month(dist, 2026, 5, fr)
        self.assertEqual(len(result['lines']), 1)
        self.assertEqual(result['lines'][0]['item_id'], item.pk)
        self.assertEqual(result['lines'][0]['cases'], 10.0)
        self.assertIsNone(result['lines'][0]['pallets'])

    # 2. Item exactly at safety stock → no suggestion
    def test_does_not_suggest_for_item_at_safety_stock(self):
        dist = self._dist_cases()
        item = self._item()
        fr = self._forecast(item, [(2026, 6, 50.0)], safety_stock=50)
        result = suggest_po_for_month(dist, 2026, 5, fr)
        self.assertEqual(result['lines'], [])

    # 3. Item above safety stock → no suggestion
    def test_does_not_suggest_for_item_above_safety_stock(self):
        dist = self._dist_cases()
        item = self._item()
        fr = self._forecast(item, [(2026, 6, 80.0)], safety_stock=50)
        result = suggest_po_for_month(dist, 2026, 5, fr)
        self.assertEqual(result['lines'], [])

    # 4. No items below safety stock → empty lines
    def test_empty_lines_when_no_shortages(self):
        dist = self._dist_cases()
        item_a = self._item('Item A', 'A001')
        item_b = self._item('Item B', 'B001')
        fr = {
            'safety_stock_map': {item_a.pk: 20, item_b.pk: 30},
            'rows': [
                {'item': item_a, 'monthly_data': [{'year': 2026, 'month': 6, 'inventory': 100.0, 'inventory_display': '100', 'depletion': None, 'status': 'green', 'is_snapshot': False}]},
                {'item': item_b, 'monthly_data': [{'year': 2026, 'month': 6, 'inventory': 100.0, 'inventory_display': '100', 'depletion': None, 'status': 'green', 'is_snapshot': False}]},
            ],
        }
        result = suggest_po_for_month(dist, 2026, 5, fr)
        self.assertEqual(result['lines'], [])

    # 5. Saved POs in forecast_result reduce projected inv → fewer/no suggestions
    def test_considers_saved_pos_in_lookahead(self):
        dist = self._dist_cases(order_value=100)
        item = self._item('Item PO', 'PO01')
        acc = _make_account(self.company, dist)
        batch = _make_batch(self.company, dist)
        _make_snapshot(dist, item, 2026, 4, quantity=50)
        _make_sale(self.company, batch, acc, item, 2025, 5, 100)

        # Without saved PO: May inv = 50 - 100 = -50 → shortage below ss=0 by 50
        fr_no_po = compute_distributor_forecast(dist, today=date(2026, 4, 20))
        result_no_po = suggest_po_for_month(dist, 2026, 4, fr_no_po)
        self.assertGreater(len(result_no_po['lines']), 0)

        # With saved PO of 200 cases in May → May inv = 50 + 200 - 100 = 150 ≥ 0 → no shortage
        po_additions = {(item.pk, 2026, 5): 200.0}
        fr_with_po = compute_distributor_forecast(dist, today=date(2026, 4, 20), po_additions=po_additions)
        result_with_po = suggest_po_for_month(dist, 2026, 4, fr_with_po)
        self.assertEqual(result_with_po['lines'], [])

    # 6. Cases mode: shortage=35, total_capacity=10 → allocate min(35,10)=10 cases
    def test_cases_mode_rounding(self):
        dist = self._dist_cases(order_value=10)
        item = self._item()
        fr = self._forecast(item, [(2026, 6, 15.0)], safety_stock=50)
        result = suggest_po_for_month(dist, 2026, 5, fr)
        self.assertEqual(result['lines'][0]['cases'], 10.0)

    # 7. Pallets mode: shortage=35, total_capacity=2 pallets, cpp=12
    #    pallets_needed=ceil(35/12)=3, allocate min(3,2)=2 pallets = 24 cases
    def test_pallets_mode_rounding(self):
        dist = self._dist_pallets(order_value=2)
        item = self._item(cpp=12)
        fr = self._forecast(item, [(2026, 6, 15.0)], safety_stock=50)
        result = suggest_po_for_month(dist, 2026, 5, fr)
        self.assertEqual(len(result['lines']), 1)
        self.assertEqual(result['lines'][0]['cases'], 24.0)
        self.assertEqual(result['lines'][0]['pallets'], 2)

    # 8. December → January rollover; shortage=45, capacity=10 → allocate min(45,10)=10
    def test_december_january_rollover(self):
        dist = self._dist_cases(order_value=10)
        item = self._item()
        # modal month=Dec 2026 → lookahead=Jan 2027; inv=5, ss=50, shortage=45
        fr = self._forecast(item, [(2027, 1, 5.0)], safety_stock=50)
        result = suggest_po_for_month(dist, 2026, 12, fr)
        self.assertEqual(len(result['lines']), 1)
        self.assertEqual(result['lines'][0]['cases'], 10.0)

    # 9. Null safety stock treated as zero
    def test_null_safety_stock_treated_as_zero(self):
        dist = self._dist_cases(order_value=10)
        item = self._item()
        # No safety stock → ss=0; inv=-10 → shortage=10
        fr = self._forecast(item, [(2026, 6, -10.0)], safety_stock=None)
        result = suggest_po_for_month(dist, 2026, 5, fr)
        self.assertEqual(len(result['lines']), 1)
        self.assertEqual(result['lines'][0]['cases'], 10.0)

    # 10. Pallet mode item missing cases_per_pallet → skipped
    def test_skips_pallet_mode_item_missing_cases_per_pallet(self):
        dist = self._dist_pallets()
        item = self._item(cpp=None)  # no cpp
        fr = self._forecast(item, [(2026, 6, 5.0)], safety_stock=50)
        result = suggest_po_for_month(dist, 2026, 5, fr)
        self.assertEqual(result['lines'], [])

    # 11. No order profile → empty lines
    def test_empty_lines_when_no_order_profile(self):
        dist = _make_distributor(self.company, 'No Profile')
        item = self._item()
        fr = self._forecast(item, [(2026, 6, 5.0)], safety_stock=50)
        result = suggest_po_for_month(dist, 2026, 5, fr)
        self.assertEqual(result['lines'], [])

    # 12. Lookahead month outside horizon → skip item (empty lines)
    def test_empty_lines_when_lookahead_outside_horizon(self):
        dist = self._dist_cases()
        item = self._item()
        # Forecast horizon ends at May 2026; modal month=May, lookahead=June not in horizon
        fr = self._forecast(item, [(2026, 5, 5.0)], safety_stock=50)
        result = suggest_po_for_month(dist, 2026, 5, fr)
        self.assertEqual(result['lines'], [])

    # -----------------------------------------------------------------------
    # New capacity-allocation tests
    # -----------------------------------------------------------------------

    def _multi_item_fr(self, items_and_cells, safety_stock_map):
        """Build a minimal forecast_result with multiple items."""
        rows = []
        for item, cells in items_and_cells:
            monthly_data = [
                {
                    'year': y, 'month': m, 'inventory': inv,
                    'inventory_display': '' if inv is None else str(inv),
                    'depletion': None, 'status': 'yellow', 'is_snapshot': False,
                }
                for y, m, inv in cells
            ]
            rows.append({'item': item, 'monthly_data': monthly_data})
        return {'safety_stock_map': safety_stock_map, 'rows': rows}

    # 13. Total pallet capacity respected across multiple items
    def test_total_capacity_respected_across_multiple_items(self):
        # 3 items each needing 10 pallets; capacity=20 pallets → only top 2 allocated
        dist = self._dist_pallets(order_value=20)
        item_a = self._item('Item A', 'TC_A', cpp=10)  # shortage=100, pallets_needed=10
        item_b = self._item('Item B', 'TC_B', cpp=8)   # shortage=80,  pallets_needed=10
        item_c = self._item('Item C', 'TC_C', cpp=5)   # shortage=50,  pallets_needed=10

        fr = self._multi_item_fr(
            [
                (item_a, [(2026, 6, 0.0)]),
                (item_b, [(2026, 6, 20.0)]),
                (item_c, [(2026, 6, 50.0)]),
            ],
            {item_a.pk: 100, item_b.pk: 100, item_c.pk: 100},
        )
        result = suggest_po_for_month(dist, 2026, 5, fr)
        item_ids = [l['item_id'] for l in result['lines']]
        self.assertEqual(len(result['lines']), 2)
        self.assertIn(item_a.pk, item_ids)
        self.assertIn(item_b.pk, item_ids)
        self.assertNotIn(item_c.pk, item_ids)
        line_a = next(l for l in result['lines'] if l['item_id'] == item_a.pk)
        line_b = next(l for l in result['lines'] if l['item_id'] == item_b.pk)
        self.assertEqual(line_a['pallets'], 10)
        self.assertEqual(line_b['pallets'], 10)

    # 14. Items sorted by largest absolute deficit first
    def test_sorted_by_largest_deficit_first(self):
        dist = self._dist_cases(order_value=1200)  # large enough for all
        item_a = self._item('Item A', 'SD_A')   # shortage=200
        item_b = self._item('Item B', 'SD_B')   # shortage=800 — largest
        item_c = self._item('Item C', 'SD_C')   # shortage=100

        fr = self._multi_item_fr(
            [
                (item_a, [(2026, 6, 0.0)]),
                (item_b, [(2026, 6, 0.0)]),
                (item_c, [(2026, 6, 0.0)]),
            ],
            {item_a.pk: 200, item_b.pk: 800, item_c.pk: 100},
        )
        result = suggest_po_for_month(dist, 2026, 5, fr)
        self.assertEqual(len(result['lines']), 3)
        # B has the largest deficit → must appear first
        self.assertEqual(result['lines'][0]['item_id'], item_b.pk)

    # 15. Partial allocation when capacity runs out mid-item
    def test_partial_allocation_when_capacity_runs_out(self):
        # Item A needs 16 pallets, Item B needs 8 pallets, capacity=20
        # A gets 16, B gets remaining 4 (partial)
        dist = self._dist_pallets(order_value=20)
        item_a = self._item('Item A', 'PA_A', cpp=10)  # shortage=155 → ceil(155/10)=16 pallets
        item_b = self._item('Item B', 'PA_B', cpp=10)  # shortage=75  → ceil(75/10)=8  pallets

        fr = self._multi_item_fr(
            [
                (item_a, [(2026, 6, 45.0)]),
                (item_b, [(2026, 6, 125.0)]),
            ],
            {item_a.pk: 200, item_b.pk: 200},
        )
        result = suggest_po_for_month(dist, 2026, 5, fr)
        line_by_id = {l['item_id']: l for l in result['lines']}
        self.assertIn(item_a.pk, line_by_id)
        self.assertIn(item_b.pk, line_by_id)
        self.assertEqual(line_by_id[item_a.pk]['pallets'], 16)
        self.assertEqual(line_by_id[item_a.pk]['cases'], 160.0)
        # B only gets 4 pallets (remaining capacity), not the 8 it needs
        self.assertEqual(line_by_id[item_b.pk]['pallets'], 4)
        self.assertEqual(line_by_id[item_b.pk]['cases'], 40.0)

    # 16. Capacity=0 returns empty lines
    def test_capacity_zero_returns_empty(self):
        dist = Distributor.objects.create(
            company=self.company, name='Zero Cap Dist',
            order_quantity_value=0, order_quantity_unit='cases',
        )
        item = self._item('Item Z', 'ZZ01')
        fr = self._forecast(item, [(2026, 6, 5.0)], safety_stock=50)
        result = suggest_po_for_month(dist, 2026, 5, fr)
        self.assertEqual(result['lines'], [])

    # 17. Cases mode: 3 items each short by 50, capacity=100 → only 2 fit
    def test_cases_mode_total_capacity(self):
        dist = self._dist_cases(order_value=100)
        item_a = self._item('Item A', 'CM_A')
        item_b = self._item('Item B', 'CM_B')
        item_c = self._item('Item C', 'CM_C')

        fr = self._multi_item_fr(
            [
                (item_a, [(2026, 6, 50.0)]),
                (item_b, [(2026, 6, 50.0)]),
                (item_c, [(2026, 6, 50.0)]),
            ],
            {item_a.pk: 100, item_b.pk: 100, item_c.pk: 100},
        )
        result = suggest_po_for_month(dist, 2026, 5, fr)
        self.assertEqual(len(result['lines']), 2)
        item_ids = [l['item_id'] for l in result['lines']]
        self.assertIn(item_a.pk, item_ids)
        self.assertIn(item_b.pk, item_ids)
        self.assertNotIn(item_c.pk, item_ids)
        self.assertEqual(result['lines'][0]['cases'], 50.0)
        self.assertEqual(result['lines'][1]['cases'], 50.0)

    # 18. Second suggest call after first PO saved picks up previously uncovered items
    def test_subsequent_call_after_first_po_saved(self):
        from apps.distribution.forecast import compute_distributor_forecast
        from datetime import date as _date

        dist = self._dist_cases(order_value=80)
        item_a = self._item('Item A', 'SQ_A')
        item_b = self._item('Item B', 'SQ_B')
        item_c = self._item('Item C', 'SQ_C')
        item_d = self._item('Item D', 'SQ_D')

        acc   = _make_account(self.company, dist)
        batch = _make_batch(self.company, dist)
        today = _date(2026, 4, 20)

        # All items: snap=10 April 2026; deficits in May 2026: A=80, B=70, C=60, D=40
        for it, depletion in [(item_a, 90), (item_b, 80), (item_c, 70), (item_d, 50)]:
            _make_snapshot(dist, it, 2026, 4, quantity=10)
            _make_sale(self.company, batch, acc, it, 2025, 5, depletion)

        # First suggest: April modal → lookahead May 2026
        fr1 = compute_distributor_forecast(dist, today=today)
        result1 = suggest_po_for_month(dist, 2026, 4, fr1)

        # A has the largest deficit (80 = full capacity) → only A allocated
        self.assertEqual(len(result1['lines']), 1)
        self.assertEqual(result1['lines'][0]['item_id'], item_a.pk)
        self.assertEqual(result1['lines'][0]['cases'], 80.0)

        # Simulate saving the first PO for item A (80 cases in May 2026)
        po_additions = {(item_a.pk, 2026, 5): 80.0}
        fr2 = compute_distributor_forecast(dist, today=today, po_additions=po_additions)
        result2 = suggest_po_for_month(dist, 2026, 4, fr2)

        # A is now at safety stock; B has the next largest deficit
        second_ids = {l['item_id'] for l in result2['lines']}
        self.assertNotIn(item_a.pk, second_ids)
        self.assertIn(item_b.pk, second_ids)
        # B (deficit 70) should be first in the second suggestion
        self.assertEqual(result2['lines'][0]['item_id'], item_b.pk)
