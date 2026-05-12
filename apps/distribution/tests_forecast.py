"""
Tests for apps.distribution.forecast — Phase 4-step-1.

Covers compute_distributor_forecast() calculation logic and view integration.
"""
from datetime import date

from django.test import Client, TestCase
from django.urls import reverse

from apps.accounts.models import Account
from apps.catalog.models import Brand, Item
from apps.core.models import Company, User
from apps.core.rbac import Permission, Role
from apps.distribution.forecast import compute_distributor_forecast
from apps.distribution.models import Distributor, DistributorItemProfile, InventorySnapshot
from apps.imports.models import ImportBatch
from apps.sales.models import SalesRecord


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_company(name='Forecast Co'):
    return Company.objects.create(name=name)


def _make_supplier_admin(company, username='fc_admin'):
    user = User.objects.create_user(username=username, password='testpass123', company=company)
    user.roles.set([Role.objects.get(codename='supplier_admin')])
    return user


def _make_distributor(company, name='Test Dist'):
    return Distributor.objects.create(company=company, name=name)


def _make_brand(company, name='Test Brand'):
    return Brand.objects.create(company=company, name=name)


def _make_item(brand, name='Item A', item_code='ITMA', sort_order=1):
    return Item.objects.create(brand=brand, name=name, item_code=item_code, sort_order=sort_order)


def _make_account(company, distributor, name='Test Retailer'):
    return Account.objects.create(
        company=company, distributor=distributor,
        name=name, state='NJ', state_normalized='NJ',
    )


def _make_batch(company, distributor):
    return ImportBatch.objects.create(
        company=company,
        distributor=distributor,
        import_type=ImportBatch.ImportType.SALES_DATA,
        status=ImportBatch.Status.COMPLETE,
    )


def _make_snapshot(distributor, item, year, month, quantity=100):
    return InventorySnapshot.objects.create(
        distributor=distributor, item=item,
        quantity_cases=quantity, year=year, month=month,
    )


def _make_sale(company, batch, account, item, year, month, quantity):
    return SalesRecord.objects.create(
        company=company, import_batch=batch, account=account,
        item=item, sale_date=date(year, month, 1), quantity=quantity,
    )


# ---------------------------------------------------------------------------
# Unit tests — compute_distributor_forecast()
# ---------------------------------------------------------------------------

class ForecastComputeTest(TestCase):

    def setUp(self):
        self.company = _make_company()
        self.distributor = _make_distributor(self.company)
        self.brand = _make_brand(self.company)
        self.item = _make_item(self.brand)
        self.account = _make_account(self.company, self.distributor)
        self.batch = _make_batch(self.company, self.distributor)

    def _sale(self, year, month, qty, item=None):
        return _make_sale(
            self.company, self.batch, self.account,
            item or self.item, year, month, qty,
        )

    def _snap(self, year, month, qty=100, item=None):
        return _make_snapshot(self.distributor, item or self.item, year, month, qty)

    # -----------------------------------------------------------------------
    # 1. No snapshots → empty result with message
    # -----------------------------------------------------------------------
    def test_forecast_no_snapshots_returns_empty_with_message(self):
        result = compute_distributor_forecast(self.distributor, today=date(2026, 5, 1))
        self.assertEqual(result['rows'], [])
        self.assertIn('No inventory snapshots', result['message'])

    # -----------------------------------------------------------------------
    # 2. horizon[0] IS the snapshot month; horizon[1] is first projection
    # -----------------------------------------------------------------------
    def test_forecast_horizon_starts_after_most_recent_snapshot(self):
        self._snap(2026, 3)
        result = compute_distributor_forecast(self.distributor, today=date(2026, 5, 1))
        # horizon[0] = snapshot anchor (Mar 2026)
        self.assertEqual(result['horizon'][0]['year'], 2026)
        self.assertEqual(result['horizon'][0]['month'], 3)
        self.assertTrue(result['horizon'][0]['is_snapshot'])
        # horizon[1] = first projection month (Apr 2026)
        self.assertEqual(result['horizon'][1]['year'], 2026)
        self.assertEqual(result['horizon'][1]['month'], 4)
        self.assertFalse(result['horizon'][1]['is_snapshot'])

    # -----------------------------------------------------------------------
    # 3. Horizon is 13 months (1 snapshot + 12 projection)
    # -----------------------------------------------------------------------
    def test_forecast_horizon_is_12_months(self):
        self._snap(2026, 1)
        result = compute_distributor_forecast(self.distributor, today=date(2026, 5, 1))
        self.assertEqual(len(result['horizon']), 13)
        for row in result['rows']:
            self.assertEqual(len(row['monthly_data']), 13)

    # -----------------------------------------------------------------------
    # 4. Year spans correct when crossing year boundary
    #    Snapshot Jun 2026 → anchor=Jun + projection Jul 2026..Jun 2027
    #    2026: Jun(1) + Jul-Dec(6) = 7; 2027: Jan-Jun = 6
    # -----------------------------------------------------------------------
    def test_forecast_year_spans_correct_when_crossing_year_boundary(self):
        self._snap(2026, 6)
        result = compute_distributor_forecast(self.distributor, today=date(2026, 1, 1))
        spans = result['year_spans']
        self.assertEqual(len(spans), 2)
        self.assertEqual(spans[0]['year'], 2026)
        self.assertEqual(spans[0]['colspan'], 7)   # Jun(anchor) + Jul–Dec
        self.assertEqual(spans[1]['year'], 2027)
        self.assertEqual(spans[1]['colspan'], 6)   # Jan–Jun

    # -----------------------------------------------------------------------
    # 5. Simple depletion: 100 starting, 10/month → 50 after 5 projection months
    #    monthly_data[0] = snapshot (100), [1..5] = projections, [5] = 50
    # -----------------------------------------------------------------------
    def test_forecast_simple_depletion(self):
        self._snap(2026, 1, qty=100)
        for m in range(2, 14):
            y = 2025 if m <= 12 else 2026
            mo = m if m <= 12 else m - 12
            self._sale(y, mo, 10)
        today = date(2026, 1, 20)
        result = compute_distributor_forecast(self.distributor, today=today)
        row = result['rows'][0]
        # After 5 projection months of depletion (10 each), inventory = 50
        self.assertEqual(row['monthly_data'][5]['inventory'], 50.0)

    # -----------------------------------------------------------------------
    # 6. Negative inventory shows red (projection month)
    # -----------------------------------------------------------------------
    def test_forecast_negative_inventory_shows_red(self):
        self._snap(2026, 1, qty=5)
        self._sale(2025, 2, 10)
        result = compute_distributor_forecast(self.distributor, today=date(2026, 1, 20))
        # [0] = snapshot, [1] = Feb 2026 projection (5 - 10 = -5)
        cell = result['rows'][0]['monthly_data'][1]
        self.assertEqual(cell['status'], 'red')
        self.assertEqual(cell['inventory'], -5.0)

    # -----------------------------------------------------------------------
    # 7. Below safety stock shows yellow
    # -----------------------------------------------------------------------
    def test_forecast_below_safety_stock_shows_yellow(self):
        self._snap(2026, 1, qty=100)
        DistributorItemProfile.objects.create(
            distributor=self.distributor, item=self.item, safety_stock_cases=80
        )
        self._sale(2025, 2, 30)
        result = compute_distributor_forecast(self.distributor, today=date(2026, 1, 20))
        cell = result['rows'][0]['monthly_data'][1]  # [0]=snapshot, [1]=Feb 2026
        self.assertEqual(cell['status'], 'yellow')
        self.assertEqual(cell['inventory'], 70.0)

    # -----------------------------------------------------------------------
    # 8. Above safety stock shows green
    # -----------------------------------------------------------------------
    def test_forecast_above_safety_stock_shows_green(self):
        self._snap(2026, 1, qty=100)
        DistributorItemProfile.objects.create(
            distributor=self.distributor, item=self.item, safety_stock_cases=50
        )
        self._sale(2025, 2, 10)
        result = compute_distributor_forecast(self.distributor, today=date(2026, 1, 20))
        cell = result['rows'][0]['monthly_data'][1]  # [0]=snapshot, [1]=Feb 2026
        self.assertEqual(cell['status'], 'green')

    # -----------------------------------------------------------------------
    # 9. No safety stock set: above 0 is green, 0 or below is red
    # -----------------------------------------------------------------------
    def test_forecast_no_safety_stock_set_shows_green_only_above_zero(self):
        self._snap(2026, 1, qty=5)
        self._sale(2025, 2, 3)   # → inv 2, green
        self._sale(2025, 3, 10)  # → inv -8, red
        result = compute_distributor_forecast(self.distributor, today=date(2026, 1, 20))
        row = result['rows'][0]
        self.assertEqual(row['monthly_data'][1]['status'], 'green')   # Feb 2026
        self.assertEqual(row['monthly_data'][2]['status'], 'red')     # Mar 2026

    # -----------------------------------------------------------------------
    # 10. Fully-ended months use actual sales; projection months use prior-year
    # -----------------------------------------------------------------------
    def test_forecast_uses_actual_sales_for_fully_ended_months(self):
        self._snap(2026, 1, qty=100)
        self._sale(2026, 2, 10)
        self._sale(2026, 3, 20)
        self._sale(2025, 5, 5)

        today = date(2026, 5, 15)
        result = compute_distributor_forecast(self.distributor, today=today)
        row = result['rows'][0]
        monthly = {(c['year'], c['month']): c for c in row['monthly_data']}

        self.assertEqual(monthly[(2026, 2)]['inventory'], 90.0)
        self.assertEqual(monthly[(2026, 3)]['inventory'], 70.0)
        self.assertEqual(monthly[(2026, 4)]['inventory'], 70.0)   # 0 actual depletion
        self.assertEqual(monthly[(2026, 5)]['inventory'], 65.0)   # prior-year

    # -----------------------------------------------------------------------
    # 11. Current month uses prior year even if actual sales exist
    # -----------------------------------------------------------------------
    def test_forecast_current_month_uses_prior_year_even_if_sales_exist(self):
        self._snap(2026, 1, qty=100)
        self._sale(2026, 5, 50)   # actual — should be ignored (current month)
        self._sale(2025, 5, 10)   # prior-year — should be used

        today = date(2026, 5, 15)
        result = compute_distributor_forecast(self.distributor, today=today)
        row = result['rows'][0]
        monthly = {(c['year'], c['month']): c for c in row['monthly_data']}
        # prior-year path: 100 - 0(Feb) - 0(Mar) - 0(Apr) - 10(May) = 90
        self.assertEqual(monthly[(2026, 5)]['inventory'], 90.0)

    # -----------------------------------------------------------------------
    # 12. Negative actual sales (returns) floored at 0 depletion
    # -----------------------------------------------------------------------
    def test_forecast_negative_actual_sales_floored_at_zero(self):
        self._snap(2026, 1, qty=50)
        self._sale(2026, 2, -5)

        today = date(2026, 5, 1)
        result = compute_distributor_forecast(self.distributor, today=today)
        row = result['rows'][0]
        monthly = {(c['year'], c['month']): c for c in row['monthly_data']}
        self.assertEqual(monthly[(2026, 2)]['inventory'], 50.0)

    # -----------------------------------------------------------------------
    # 13. Negative prior-year sales floored at 0 depletion
    # -----------------------------------------------------------------------
    def test_forecast_negative_prior_year_sales_floored_at_zero(self):
        self._snap(2026, 1, qty=50)
        self._sale(2025, 2, -5)

        today = date(2026, 1, 20)
        result = compute_distributor_forecast(self.distributor, today=today)
        row = result['rows'][0]
        # [0]=snapshot(50), [1]=Feb 2026 projection (prior-year=-5 → depletion=0 → inv=50)
        self.assertEqual(row['monthly_data'][1]['inventory'], 50.0)

    # -----------------------------------------------------------------------
    # 14. Missing prior-year shows no_data for projection months
    # -----------------------------------------------------------------------
    def test_forecast_missing_prior_year_shows_no_data_for_projection(self):
        self._snap(2026, 1, qty=100)
        today = date(2026, 1, 20)
        result = compute_distributor_forecast(self.distributor, today=today)
        row = result['rows'][0]
        # [0]=snapshot, [1]=Feb 2026 projection (no prior-year Feb 2025 → no_data)
        cell = row['monthly_data'][1]
        self.assertEqual(cell['status'], 'no_data')
        self.assertIn('No prior year data', cell['reason'])

    # -----------------------------------------------------------------------
    # 15. Missing snapshot but has prior-year → starts at 0, projects normally
    # -----------------------------------------------------------------------
    def test_forecast_missing_snapshot_but_has_prior_year_starts_at_zero(self):
        item2 = _make_item(self.brand, name='Item B', item_code='ITMB', sort_order=2)
        _make_snapshot(self.distributor, item2, 2026, 1, quantity=50)
        self._sale(2025, 2, 10)  # prior-year for self.item

        today = date(2026, 1, 20)
        result = compute_distributor_forecast(self.distributor, today=today)
        rows_by_item = {r['item'].pk: r for r in result['rows']}
        row = rows_by_item[self.item.pk]
        # [0]=anchor (no snapshot → no_data), [1]=Feb 2026 (0 - 10 = -10, red)
        self.assertEqual(row['monthly_data'][0]['status'], 'no_data')
        self.assertEqual(row['monthly_data'][1]['inventory'], -10.0)
        self.assertEqual(row['monthly_data'][1]['status'], 'red')

    # -----------------------------------------------------------------------
    # 16. Missing snapshot AND no prior-year → anchor=no_data, projections=no_data
    # -----------------------------------------------------------------------
    def test_forecast_missing_snapshot_and_no_prior_year_shows_no_data(self):
        item2 = _make_item(self.brand, name='Item B', item_code='ITMB', sort_order=2)
        _make_snapshot(self.distributor, item2, 2026, 1, quantity=50)

        today = date(2026, 1, 20)
        result = compute_distributor_forecast(self.distributor, today=today)
        rows_by_item = {r['item'].pk: r for r in result['rows']}
        row = rows_by_item[self.item.pk]
        # Anchor cell: no snapshot for this item
        self.assertEqual(row['monthly_data'][0]['status'], 'no_data')
        # All 12 projection cells: no data (no starting inventory, no prior-year sales)
        for cell in row['monthly_data'][1:]:
            self.assertEqual(cell['status'], 'no_data')
            self.assertIn('No starting inventory and no prior year data', cell['reason'])

    # -----------------------------------------------------------------------
    # 17. Inactive distributor item profiles are excluded from forecast
    # -----------------------------------------------------------------------
    def test_forecast_excludes_inactive_distributor_items(self):
        self._snap(2026, 1)
        DistributorItemProfile.objects.create(
            distributor=self.distributor, item=self.item, is_active=False
        )
        result = compute_distributor_forecast(self.distributor, today=date(2026, 5, 1))
        item_ids = [r['item'].pk for r in result['rows']]
        self.assertNotIn(self.item.pk, item_ids)

    # -----------------------------------------------------------------------
    # 18. Items with Item.is_active=False are excluded from forecast
    # -----------------------------------------------------------------------
    def test_forecast_excludes_inactive_items(self):
        self._snap(2026, 1)
        self.item.is_active = False
        self.item.save()
        result = compute_distributor_forecast(self.distributor, today=date(2026, 5, 1))
        item_ids = [r['item'].pk for r in result['rows']]
        self.assertNotIn(self.item.pk, item_ids)

    # -----------------------------------------------------------------------
    # 19. Items sorted by brand name, then sort_order, then item name
    # -----------------------------------------------------------------------
    def test_forecast_items_sorted_by_brand_sort_order_name(self):
        self._snap(2026, 1)
        brand_b = _make_brand(self.company, name='B Brand')
        item_b1 = _make_item(brand_b, name='Z Item', item_code='ZB1', sort_order=1)
        item_b2 = _make_item(brand_b, name='A Item', item_code='AB2', sort_order=2)
        _make_snapshot(self.distributor, item_b1, 2026, 1, quantity=10)
        _make_snapshot(self.distributor, item_b2, 2026, 1, quantity=10)
        result = compute_distributor_forecast(self.distributor, today=date(2026, 5, 1))
        item_names = [r['item'].name for r in result['rows']]
        self.assertEqual(item_names[0], 'Z Item')   # B Brand, sort_order=1
        self.assertEqual(item_names[1], 'A Item')   # B Brand, sort_order=2
        self.assertEqual(item_names[2], 'Item A')   # Test Brand

    # -----------------------------------------------------------------------
    # 20. Snapshot column: horizon has 13 entries; first is the snapshot month
    # -----------------------------------------------------------------------
    def test_forecast_includes_snapshot_month_as_first_column(self):
        self._snap(2026, 4, qty=100)
        result = compute_distributor_forecast(self.distributor, today=date(2026, 5, 1))
        self.assertEqual(len(result['horizon']), 13)
        self.assertEqual(result['horizon'][0]['year'], 2026)
        self.assertEqual(result['horizon'][0]['month'], 4)
        self.assertTrue(result['horizon'][0]['is_snapshot'])
        # All subsequent entries are projection months
        for h in result['horizon'][1:]:
            self.assertFalse(h['is_snapshot'])

    # -----------------------------------------------------------------------
    # 21. Snapshot cell has the actual snapshot quantity
    # -----------------------------------------------------------------------
    def test_forecast_snapshot_cell_has_actual_value(self):
        self._snap(2026, 4, qty=248)
        result = compute_distributor_forecast(self.distributor, today=date(2026, 5, 1))
        anchor = result['rows'][0]['monthly_data'][0]
        self.assertEqual(anchor['inventory'], 248.0)
        self.assertEqual(anchor['inventory_display'], '248')

    # -----------------------------------------------------------------------
    # 22. Snapshot cell has status='snapshot'
    # -----------------------------------------------------------------------
    def test_forecast_snapshot_cell_has_snapshot_status(self):
        self._snap(2026, 4, qty=100)
        result = compute_distributor_forecast(self.distributor, today=date(2026, 5, 1))
        anchor = result['rows'][0]['monthly_data'][0]
        self.assertEqual(anchor['status'], 'snapshot')
        self.assertTrue(anchor['is_snapshot'])

    # -----------------------------------------------------------------------
    # 23. Projection months unaffected by snapshot column (regression)
    # -----------------------------------------------------------------------
    def test_forecast_subsequent_months_unaffected(self):
        self._snap(2026, 1, qty=100)
        self._sale(2025, 2, 20)  # prior-year → Feb 2026 depletion 20

        today = date(2026, 1, 20)
        result = compute_distributor_forecast(self.distributor, today=today)
        row = result['rows'][0]
        # [0]=snapshot(100), [1]=Feb 2026 (100-20=80)
        self.assertEqual(row['monthly_data'][0]['status'], 'snapshot')
        self.assertEqual(row['monthly_data'][0]['inventory'], 100.0)
        self.assertEqual(row['monthly_data'][1]['inventory'], 80.0)
        self.assertEqual(row['monthly_data'][1]['status'], 'green')

    # -----------------------------------------------------------------------
    # 24. Year spans include snapshot month in its year
    # -----------------------------------------------------------------------
    def test_forecast_year_spans_include_snapshot_month(self):
        # Snapshot Apr 2026 → anchor=Apr, projection May 2026..Apr 2027
        # 2026: Apr(1)+May-Dec(8) = 9; 2027: Jan-Apr = 4
        self._snap(2026, 4)
        result = compute_distributor_forecast(self.distributor, today=date(2026, 1, 1))
        spans = result['year_spans']
        self.assertEqual(len(spans), 2)
        self.assertEqual(spans[0]['year'], 2026)
        self.assertEqual(spans[0]['colspan'], 9)   # Apr(anchor)+May–Dec
        self.assertEqual(spans[1]['year'], 2027)
        self.assertEqual(spans[1]['colspan'], 4)   # Jan–Apr

    # -----------------------------------------------------------------------
    # 25. Regression: negative inventory cell correctly has status='red'
    # -----------------------------------------------------------------------
    def test_forecast_negative_inventory_cell_status_red(self):
        self._snap(2026, 1, qty=10)
        self._sale(2025, 2, 50)  # prior-year depletion > starting → negative

        result = compute_distributor_forecast(self.distributor, today=date(2026, 1, 20))
        row = result['rows'][0]
        proj_cell = row['monthly_data'][1]  # Feb 2026 projection
        self.assertEqual(proj_cell['status'], 'red')
        self.assertLess(proj_cell['inventory'], 0)


# ---------------------------------------------------------------------------
# View integration tests
# ---------------------------------------------------------------------------

class ForecastViewTest(TestCase):

    def setUp(self):
        self.company = _make_company('View Co')
        self.admin = _make_supplier_admin(self.company, username='view_admin')
        self.distributor = _make_distributor(self.company)
        self.brand = _make_brand(self.company)
        self.item = _make_item(self.brand)
        self.client = Client()
        self.client.login(username='view_admin', password='testpass123')
        self.url = reverse('distributor_list')

    # -----------------------------------------------------------------------
    # 26. Forecast distributor dropdown renders with all distributors
    # -----------------------------------------------------------------------
    def test_forecast_distributor_dropdown_renders(self):
        _make_snapshot(self.distributor, self.item, 2026, 1)
        dist2 = _make_distributor(self.company, name='Second Dist')
        resp = self.client.get(self.url + '?tab=forecast')
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'forecast-distributor-select')
        self.assertContains(resp, self.distributor.name)
        self.assertContains(resp, dist2.name)

    # -----------------------------------------------------------------------
    # 27. Forecast distributor selection via GET param
    # -----------------------------------------------------------------------
    def test_forecast_distributor_selection_via_get_param(self):
        dist2 = _make_distributor(self.company, name='Second Dist')
        _make_snapshot(dist2, self.item, 2026, 1)
        resp = self.client.get(
            self.url + f'?tab=forecast&forecast_distributor={dist2.pk}'
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.context['forecast_distributor'].pk, dist2.pk)

    # -----------------------------------------------------------------------
    # 28. Forecast data loads eagerly even when active_tab != forecast
    # -----------------------------------------------------------------------
    def test_forecast_tab_data_loads_eagerly_with_permission(self):
        _make_snapshot(self.distributor, self.item, 2026, 1)
        resp = self.client.get(self.url + '?tab=distributors')
        self.assertEqual(resp.status_code, 200)
        self.assertIsNotNone(resp.context['forecast_result'])
        self.assertIsNotNone(resp.context['forecast_distributor'])

    # -----------------------------------------------------------------------
    # 29. Forecast tab hidden without can_manage_distributor_inventory
    # -----------------------------------------------------------------------
    def test_forecast_tab_hidden_without_permission(self):
        role, _ = Role.objects.get_or_create(
            codename='test_fc_no_inv', defaults={'name': 'FC No Inventory'}
        )
        perm = Permission.objects.get(codename='can_manage_distributors')
        role.permissions.set([perm])
        limited = User.objects.create_user(
            username='fc_no_inv', password='testpass123', company=self.company,
        )
        limited.roles.set([role])

        c = Client()
        c.login(username='fc_no_inv', password='testpass123')
        resp = c.get(self.url)
        self.assertEqual(resp.status_code, 200)
        self.assertNotContains(resp, 'pane-forecast')
