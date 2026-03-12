"""
Tests for apps.reports — Account Sales by Year report.
"""
from datetime import date

from django.test import Client, TestCase
from django.urls import reverse

from apps.accounts.models import Account, UserCoverageArea
from apps.catalog.models import Brand, Item
from apps.core.models import Company, User
from apps.core.rbac import Role
from apps.distribution.models import Distributor
from apps.imports.models import ImportBatch
from apps.sales.models import SalesRecord


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_company(name='Test Co'):
    return Company.objects.create(name=name)


def make_distributor(company, name='Dist A'):
    return Distributor.objects.create(company=company, name=name)


def make_brand(company, name='Test Brand'):
    brand, _ = Brand.objects.get_or_create(company=company, name=name)
    return brand


def make_item(company, name='Test Item', item_code='TST001'):
    brand = make_brand(company)
    item, _ = Item.objects.get_or_create(brand=brand, item_code=item_code, defaults={'name': name})
    return item


def make_account(company, distributor, name='Test Liquors', city='Hoboken',
                 county='Hudson', on_off='OFF', account_type='', distributor_route=''):
    return Account.objects.create(
        company=company,
        distributor=distributor,
        name=name,
        city=city,
        state='NJ',
        state_normalized='NJ',
        county=county,
        on_off_premise=on_off,
        account_type=account_type,
        distributor_route=distributor_route,
        is_active=True,
    )


def make_batch(company, distributor):
    return ImportBatch.objects.create(
        company=company,
        distributor=distributor,
        import_type=ImportBatch.ImportType.SALES_DATA,
        status=ImportBatch.Status.COMPLETE,
    )


def make_sale(company, batch, account, item, sale_date, quantity=10):
    return SalesRecord.objects.create(
        company=company,
        import_batch=batch,
        account=account,
        item=item,
        sale_date=sale_date,
        quantity=quantity,
    )


def make_user(company, role_codename, username='testuser'):
    user = User.objects.create_user(
        username=username, password='testpass123', company=company,
    )
    role = Role.objects.get(codename=role_codename)
    user.roles.set([role])
    return user


def make_coverage(company, user, distributor, coverage_type=UserCoverageArea.CoverageType.DISTRIBUTOR,
                  account=None, state='', county='', city=''):
    return UserCoverageArea.objects.create(
        company=company,
        user=user,
        coverage_type=coverage_type,
        distributor=distributor,
        account=account,
        state=state,
        county=county,
        city=city,
    )


# ---------------------------------------------------------------------------
# Permission / access tests
# ---------------------------------------------------------------------------

class ReportPermissionTest(TestCase):
    """Report redirects to dashboard if user lacks permission."""

    def setUp(self):
        self.company = make_company()
        self.client = Client()

    def test_redirect_without_permission(self):
        """Ambassador role has no can_view_report_account_sales permission."""
        user = make_user(self.company, 'ambassador', username='amb1')
        self.client.force_login(user)
        response = self.client.get(reverse('report_account_sales_by_year'))
        # Should redirect (not render the report)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response['Location'], reverse('dashboard'))

    def test_supplier_admin_can_access(self):
        """Supplier Admin is granted access (may get no_data if no sales)."""
        user = make_user(self.company, 'supplier_admin', username='sa1')
        self.client.force_login(user)
        response = self.client.get(reverse('report_account_sales_by_year'))
        # Either renders the report (200) or redirects for distributor selection —
        # either way, it does NOT redirect to dashboard.
        self.assertNotEqual(response.status_code, 302, msg=(
            'Supplier Admin should not be redirected away from the report.'
            if response.get('Location') != reverse('dashboard')
            else 'Supplier Admin was redirected to dashboard (permission denied).'
        ))

    def test_unauthenticated_redirects_to_login(self):
        response = self.client.get(reverse('report_account_sales_by_year'))
        self.assertEqual(response.status_code, 302)
        self.assertIn('/login', response['Location'])


# ---------------------------------------------------------------------------
# Supplier Admin sees all accounts for distributor
# ---------------------------------------------------------------------------

class SupplierAdminScopeTest(TestCase):
    """Supplier Admin sees all active accounts for the selected distributor."""

    def setUp(self):
        self.company = make_company()
        self.distributor = make_distributor(self.company)
        self.brand = make_brand(self.company)
        self.item = make_item(self.company)
        self.batch = make_batch(self.company, self.distributor)

        self.account_a = make_account(self.company, self.distributor, name='Alpha Wines')
        self.account_b = make_account(self.company, self.distributor, name='Beta Spirits')

        # Sales in a past year and past month
        make_sale(self.company, self.batch, self.account_a, self.item, date(2024, 6, 15), 5)
        make_sale(self.company, self.batch, self.account_b, self.item, date(2024, 6, 20), 8)

        self.user = make_user(self.company, 'supplier_admin', username='sa1')
        self.client = Client()
        self.client.force_login(self.user)

    def test_both_accounts_appear_in_rows(self):
        response = self.client.get(reverse('report_account_sales_by_year'))
        self.assertEqual(response.status_code, 200)
        rows = response.context['rows']
        account_names = [r['account_name'] for r in rows]
        # Both accounts should appear (title-cased)
        self.assertTrue(any('Alpha' in n for n in account_names), account_names)
        self.assertTrue(any('Beta' in n for n in account_names), account_names)

    def test_inactive_account_excluded(self):
        self.account_b.is_active = False
        self.account_b.save()
        response = self.client.get(reverse('report_account_sales_by_year'))
        rows = response.context['rows']
        account_names = [r['account_name'] for r in rows]
        self.assertFalse(any('Beta' in n for n in account_names), account_names)


# ---------------------------------------------------------------------------
# Scoped user sees only coverage area accounts
# ---------------------------------------------------------------------------

class ScopedUserScopeTest(TestCase):
    """Sales Manager with coverage area sees only their accounts."""

    def setUp(self):
        self.company = make_company()
        self.distributor = make_distributor(self.company)
        self.item = make_item(self.company)
        self.batch = make_batch(self.company, self.distributor)

        self.account_covered = make_account(
            self.company, self.distributor, name='Covered Bar', city='Newark'
        )
        self.account_outside = make_account(
            self.company, self.distributor, name='Outside Club', city='Trenton'
        )

        make_sale(self.company, self.batch, self.account_covered, self.item, date(2024, 3, 10), 12)
        make_sale(self.company, self.batch, self.account_outside, self.item, date(2024, 3, 10), 7)

        self.user = make_user(self.company, 'sales_manager', username='sm1')
        # Coverage: account-level coverage for account_covered only
        make_coverage(
            self.company, self.user, self.distributor,
            coverage_type=UserCoverageArea.CoverageType.ACCOUNT,
            account=self.account_covered,
        )

        self.client = Client()
        self.client.force_login(self.user)

    def test_only_covered_account_appears(self):
        response = self.client.get(reverse('report_account_sales_by_year'))
        self.assertEqual(response.status_code, 200)
        rows = response.context['rows']
        account_names = [r['account_name'] for r in rows]
        self.assertTrue(any('Covered' in n for n in account_names), account_names)
        self.assertFalse(any('Outside' in n for n in account_names), account_names)


# ---------------------------------------------------------------------------
# Last full month calculation
# ---------------------------------------------------------------------------

class LastFullMonthTest(TestCase):
    """last_full_month_display is derived from the most recent past month's data."""

    def setUp(self):
        self.company = make_company()
        self.distributor = make_distributor(self.company)
        self.item = make_item(self.company)
        self.batch = make_batch(self.company, self.distributor)
        self.account = make_account(self.company, self.distributor)
        self.user = make_user(self.company, 'supplier_admin', username='sa1')
        self.client = Client()
        self.client.force_login(self.user)

    def test_last_full_month_uses_most_recent_past_month(self):
        """If the most recent sale is in December 2024, display is 'December 2024'."""
        make_sale(self.company, self.batch, self.account, self.item, date(2024, 12, 31), 5)
        make_sale(self.company, self.batch, self.account, self.item, date(2024, 10, 15), 3)
        response = self.client.get(reverse('report_account_sales_by_year'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['last_full_month_display'], 'December 2024')

    def test_last_full_month_excludes_current_month(self):
        """Sales in the current month are not used for last_full_month."""
        today = date.today()
        # Only sale is in current month — should produce no_data
        make_sale(self.company, self.batch, self.account, self.item,
                  today.replace(day=1), 5)
        response = self.client.get(reverse('report_account_sales_by_year'))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context.get('no_data'), 'Expected no_data when only sale is in current month')


# ---------------------------------------------------------------------------
# Diff calculations
# ---------------------------------------------------------------------------

class DiffCalcTest(TestCase):
    """Diff is calculated correctly."""

    def setUp(self):
        self.company = make_company()
        self.distributor = make_distributor(self.company)
        self.item = make_item(self.company)
        self.batch = make_batch(self.company, self.distributor)
        self.account = make_account(self.company, self.distributor)
        self.user = make_user(self.company, 'supplier_admin', username='sa1')
        self.client = Client()
        self.client.force_login(self.user)

    def _get_row(self):
        response = self.client.get(reverse('report_account_sales_by_year'))
        self.assertEqual(response.status_code, 200)
        rows = response.context['rows']
        self.assertEqual(len(rows), 1, f'Expected 1 row, got {len(rows)}')
        return rows[0], response.context['years']

    def test_diff_is_integer(self):
        """diff = last_12_units - most_recent_year_units; always an int."""
        make_sale(self.company, self.batch, self.account, self.item, date(2024, 1, 15), 100)
        make_sale(self.company, self.batch, self.account, self.item, date(2024, 6, 15), 50)
        row, years = self._get_row()
        self.assertIsInstance(row['diff'], int)

    def test_diff_pct_not_in_row(self):
        """diff_pct has been removed from the row dict."""
        make_sale(self.company, self.batch, self.account, self.item, date(2024, 6, 1), 100)
        row, years = self._get_row()
        self.assertNotIn('diff_pct', row)

    def test_negative_quantities_included(self):
        """Negative (return) sales records reduce totals — all quantities included."""
        make_sale(self.company, self.batch, self.account, self.item, date(2024, 6, 1), 100)
        make_sale(self.company, self.batch, self.account, self.item, date(2024, 7, 1), -30)
        response = self.client.get(reverse('report_account_sales_by_year'))
        rows = response.context['rows']
        self.assertEqual(len(rows), 1)
        # Year 2024 units should be 70 (100 sale minus 30 return)
        year_units = rows[0]['year_units']
        if 2024 in year_units:
            self.assertEqual(year_units[2024], 70)


# ---------------------------------------------------------------------------
# CSV export tests
# ---------------------------------------------------------------------------

class CsvExportTest(TestCase):
    """CSV export view returns correct response and respects filters."""

    def setUp(self):
        self.company = make_company()
        self.distributor = make_distributor(self.company)
        self.item_a = make_item(self.company, name='Item Alpha', item_code='IALPHA')
        self.item_b = make_item(self.company, name='Item Beta', item_code='IBETA')
        self.batch = make_batch(self.company, self.distributor)

        self.acc_on = make_account(
            self.company, self.distributor, name='On Premise Bar',
            on_off='ON', city='Newark', county='Essex',
        )
        self.acc_off = make_account(
            self.company, self.distributor, name='Off Premise Store',
            on_off='OFF', city='Trenton', county='Mercer',
        )

        make_sale(self.company, self.batch, self.acc_on, self.item_a, date(2024, 6, 1), 10)
        make_sale(self.company, self.batch, self.acc_off, self.item_b, date(2024, 7, 1), 20)

        self.user = make_user(self.company, 'supplier_admin', username='sa1')
        self.client = Client()
        self.client.force_login(self.user)

    def test_csv_export_returns_200_and_content_disposition(self):
        """CSV export returns 200 with correct Content-Disposition header."""
        response = self.client.get(reverse('report_account_sales_csv'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'text/csv')
        self.assertIn('attachment', response['Content-Disposition'])
        self.assertIn('account_sales_by_year.csv', response['Content-Disposition'])

    def test_csv_export_denied_without_permission(self):
        """Ambassador role is redirected from CSV export."""
        user = make_user(self.company, 'ambassador', username='amb1')
        self.client.force_login(user)
        response = self.client.get(reverse('report_account_sales_csv'))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response['Location'], reverse('dashboard'))

    def test_csv_export_respects_on_off_filter(self):
        """CSV export with on_off=ON only includes ON-premise accounts."""
        response = self.client.get(
            reverse('report_account_sales_csv'),
            {'on_off': 'ON'},
        )
        self.assertEqual(response.status_code, 200)
        content = response.content.decode('utf-8')
        self.assertIn('On Premise Bar', content)
        self.assertNotIn('Off Premise Store', content)

    def test_csv_export_contains_totals_row(self):
        """CSV export includes a TOTAL row at the bottom."""
        response = self.client.get(reverse('report_account_sales_csv'))
        self.assertEqual(response.status_code, 200)
        content = response.content.decode('utf-8')
        self.assertIn('TOTAL', content)


# ---------------------------------------------------------------------------
# Filter tests
# ---------------------------------------------------------------------------

class FilterTest(TestCase):
    """Filters correctly narrow the rows returned."""

    def setUp(self):
        self.company = make_company()
        self.distributor = make_distributor(self.company)
        self.item_a = make_item(self.company, name='Item Alpha', item_code='IALPHA')
        self.item_b = make_item(self.company, name='Item Beta', item_code='IBETA')
        self.batch = make_batch(self.company, self.distributor)

        self.acc_on = make_account(
            self.company, self.distributor, name='On Premise Bar',
            on_off='ON', city='Newark', county='Essex', account_type='Bar',
            distributor_route='Route 1',
        )
        self.acc_off = make_account(
            self.company, self.distributor, name='Off Premise Store',
            on_off='OFF', city='Trenton', county='Mercer', account_type='Retail',
            distributor_route='Route 2',
        )

        make_sale(self.company, self.batch, self.acc_on, self.item_a, date(2024, 6, 1), 10)
        make_sale(self.company, self.batch, self.acc_off, self.item_b, date(2024, 7, 1), 20)

        self.user = make_user(self.company, 'supplier_admin', username='sa1')
        self.client = Client()
        self.client.force_login(self.user)

    def test_on_off_filter_on(self):
        response = self.client.get(
            reverse('report_account_sales_by_year'),
            {'on_off': 'ON'},
        )
        rows = response.context['rows']
        self.assertTrue(all(r['on_off'] == 'ON' for r in rows), rows)
        self.assertTrue(any('On Premise' in r['account_name'] for r in rows), rows)

    def test_on_off_filter_off(self):
        response = self.client.get(
            reverse('report_account_sales_by_year'),
            {'on_off': 'OFF'},
        )
        rows = response.context['rows']
        self.assertTrue(all(r['on_off'] == 'OFF' for r in rows), rows)

    def test_city_filter(self):
        response = self.client.get(
            reverse('report_account_sales_by_year'),
            {'city': 'Newark'},
        )
        rows = response.context['rows']
        self.assertEqual(len(rows), 1)
        self.assertIn('Newark', rows[0]['city'])

    def test_item_name_filter(self):
        response = self.client.get(
            reverse('report_account_sales_by_year'),
            {'item_name': 'Item Alpha'},
        )
        rows = response.context['rows']
        # Only acc_on has Item Alpha sales; rows are grouped by account, no item_name key
        self.assertEqual(len(rows), 1)
        self.assertTrue(any('On Premise' in r['account_name'] for r in rows), rows)

    def test_county_filter(self):
        response = self.client.get(
            reverse('report_account_sales_by_year'),
            {'county': 'Essex'},
        )
        rows = response.context['rows']
        self.assertEqual(len(rows), 1)

    def test_class_of_trade_filter(self):
        response = self.client.get(
            reverse('report_account_sales_by_year'),
            {'class_of_trade': 'Bar'},
        )
        rows = response.context['rows']
        self.assertEqual(len(rows), 1)

    def test_distributor_route_filter(self):
        response = self.client.get(
            reverse('report_account_sales_by_year'),
            {'distributor_route': 'Route 2'},
        )
        rows = response.context['rows']
        self.assertEqual(len(rows), 1)


# ---------------------------------------------------------------------------
# Account Detail Sales view tests
# ---------------------------------------------------------------------------

class AccountDetailTest(TestCase):
    """Tests for account_detail_sales view."""

    def setUp(self):
        from datetime import date as real_date
        self.company = make_company()
        self.distributor = make_distributor(self.company)

        brand = make_brand(self.company, name='Alpha Brand')
        self.item_a = Item.objects.get_or_create(
            brand=brand, item_code='ITMA',
            defaults={'name': 'Item A', 'sort_order': 1},
        )[0]
        self.item_b = Item.objects.get_or_create(
            brand=brand, item_code='ITMB',
            defaults={'name': 'Item B', 'sort_order': 2},
        )[0]

        self.account = make_account(self.company, self.distributor, name='Detail Liquors')
        self.other_account = make_account(self.company, self.distributor, name='Other Liquors')
        self.batch = make_batch(self.company, self.distributor)

        # Sales in last_full_year (2025) — months 3, 6, 9, 12
        for month in (3, 6, 9, 12):
            make_sale(self.company, self.batch, self.account, self.item_a,
                      real_date(2025, month, 15), 10)
            make_sale(self.company, self.batch, self.account, self.item_b,
                      real_date(2025, month, 15), 5)

        # Sales in current year (2026) — Jan and Feb (actual months as of March 2026)
        make_sale(self.company, self.batch, self.account, self.item_a,
                  real_date(2026, 1, 15), 8)
        make_sale(self.company, self.batch, self.account, self.item_a,
                  real_date(2026, 2, 15), 12)

        self.admin_user = make_user(self.company, 'supplier_admin', username='sa_detail')
        self.client = Client()
        self.client.force_login(self.admin_user)

    def _url(self, account=None):
        acc = account or self.account
        return reverse('report_account_detail', kwargs={'account_id': acc.pk})

    # ------------------------------------------------------------------

    def test_account_detail_requires_permission(self):
        """User without can_view_report_account_sales is redirected to dashboard."""
        user = make_user(self.company, 'ambassador', username='amb_detail')
        self.client.force_login(user)
        response = self.client.get(self._url())
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response['Location'], reverse('dashboard'))

    def test_account_detail_403_for_out_of_scope_account(self):
        """Scoped user (Sales Manager) cannot access an account outside their coverage."""
        other_company = make_company(name='Other Co')
        other_dist = make_distributor(other_company, name='Other Dist')
        out_of_scope = make_account(other_company, other_dist, name='Out Of Scope')
        make_sale(other_company, make_batch(other_company, other_dist),
                  out_of_scope, make_item(other_company, item_code='OOS001'),
                  date(2025, 6, 1), 10)

        scoped_user = make_user(self.company, 'sales_manager', username='sm_detail')
        make_coverage(
            self.company, scoped_user, self.distributor,
            coverage_type=UserCoverageArea.CoverageType.ACCOUNT,
            account=self.account,
        )
        self.client.force_login(scoped_user)
        # Account belongs to other_company — get_object_or_404 raises 404
        response = self.client.get(self._url(account=out_of_scope))
        self.assertEqual(response.status_code, 404)

    def test_account_detail_403_for_in_company_out_of_coverage(self):
        """Scoped Sales Manager gets 403 for an in-company account outside their coverage."""
        scoped_user = make_user(self.company, 'sales_manager', username='sm_detail2')
        make_coverage(
            self.company, scoped_user, self.distributor,
            coverage_type=UserCoverageArea.CoverageType.ACCOUNT,
            account=self.account,
        )
        self.client.force_login(scoped_user)
        # other_account is in same company but not in coverage
        response = self.client.get(self._url(account=self.other_account))
        self.assertEqual(response.status_code, 403)

    def test_account_detail_last_full_year_totals(self):
        """Per-item monthly totals for last_full_year are correct."""
        response = self.client.get(self._url())
        self.assertEqual(response.status_code, 200)
        rows = response.context['rows']
        self.assertEqual(len(rows), 2)

        # Status field present on each row
        for row in rows:
            self.assertIn('status', row, 'Each row must have a status key')
            self.assertIn('status_priority', row, 'Each row must have a status_priority key')
            self.assertIn('item_code', row, 'Each row must have an item_code key')

        # Context keys
        self.assertIn('status_counts', response.context)
        self.assertIn('last_reported', response.context)
        self.assertIn('portfolio_totals', response.context)

        row_a = next(r for r in rows if r['item_name'] == 'Item A')
        self.assertEqual(row_a['item_code'], 'ITMA')
        # item_a has 10 units in months 3, 6, 9, 12 of 2025
        lfy = row_a['last_full_year_by_month']
        self.assertEqual(lfy[3], 10)
        self.assertEqual(lfy[6], 10)
        self.assertEqual(lfy[9], 10)
        self.assertEqual(lfy[12], 10)
        self.assertEqual(lfy[1], 0)
        self.assertEqual(row_a['last_full_year_total'], 40)

        row_b = next(r for r in rows if r['item_name'] == 'Item B')
        self.assertEqual(row_b['item_code'], 'ITMB')
        self.assertEqual(row_b['last_full_year_total'], 20)

        # portfolio_totals: last_12_window is Mar 2025 – Feb 2026 (distributor-scoped)
        # item_a last_12_units = months 3,6,9,12 of 2025 (40) + Jan (8) + Feb 2026 (12) = 60
        # item_b last_12_units = months 3,6,9,12 of 2025 = 20
        pt = response.context['portfolio_totals']
        self.assertEqual(pt['last_12_total'], 80)
        self.assertEqual(pt['prior_year_total'], 60)
        self.assertEqual(pt['change_total'], 20)

    def test_account_detail_projection_with_multiplier(self):
        """Projected values use trend multiplier when 6+ actual months exist."""
        from unittest.mock import patch
        from datetime import date as real_date
        from apps.catalog.models import Brand

        brand = Brand.objects.get(company=self.company, name='Alpha Brand')
        # Dedicated item with fully controlled LFY and actual data
        proj_item = Item.objects.create(
            brand=brand, item_code='PROJTEST', name='Proj Test Item', sort_order=99,
        )
        # LFY (2025): months 2–7 each = 5; month 9 = 20
        for m in range(2, 8):
            make_sale(self.company, self.batch, self.account, proj_item,
                      real_date(2025, m, 15), 5)
        make_sale(self.company, self.batch, self.account, proj_item,
                  real_date(2025, 9, 15), 20)
        # 2026 Jan–Jul: 10 each
        for m in range(1, 8):
            make_sale(self.company, self.batch, self.account, proj_item,
                      real_date(2026, m, 15), 10)

        # Mock today = Aug 1 2026 → 7 actual months [1..7]
        with patch('apps.reports.views.date') as MockDate:
            MockDate.today.return_value = real_date(2026, 8, 1)
            MockDate.side_effect = lambda *a, **kw: real_date(*a, **kw)
            response = self.client.get(self._url())

        self.assertEqual(response.status_code, 200)
        rows = response.context['rows']
        row = next(r for r in rows if r['item_name'] == 'Proj Test Item')

        # last_6_actual_months = [2,3,4,5,6,7]
        # actual_6 = 10*6 = 60; prior_6 = 5*6 = 30; multiplier = 2.0
        # projected month 9: lfy[9]=20, projected = round(20*2.0) = 40
        proj = row['current_projected_by_month']
        self.assertIn(9, proj)
        self.assertEqual(proj[9], 40)

    def test_account_detail_projection_fallback_no_prior_year(self):
        """Trailing 6-month average used when item has no prior year data."""
        from unittest.mock import patch
        from datetime import date as real_date
        from apps.catalog.models import Brand

        brand = Brand.objects.get(company=self.company, name='Alpha Brand')
        new_item = Item.objects.create(
            brand=brand, item_code='NEWITEM', name='New Item', sort_order=10,
        )
        # new_item has NO 2025 data — fallback to trailing average
        # Create 7 actual months in 2026 (mocked to Aug 1)
        for m in range(1, 8):
            make_sale(self.company, self.batch, self.account, new_item,
                      real_date(2026, m, 15), 6)

        with patch('apps.reports.views.date') as MockDate:
            MockDate.today.return_value = real_date(2026, 8, 1)
            MockDate.side_effect = lambda *a, **kw: real_date(*a, **kw)
            response = self.client.get(self._url())

        self.assertEqual(response.status_code, 200)
        rows = response.context['rows']
        row_new = next((r for r in rows if r['item_name'] == 'New Item'), None)
        self.assertIsNotNone(row_new, 'New Item row not found')

        # Trailing 6-month average of last_6 actual months (Feb-Jul each = 6) = 6.0
        # projected = round(6.0) = 6
        proj = row_new['current_projected_by_month']
        for m in proj:
            self.assertEqual(proj[m], 6, f'Expected 6 for projected month {m}, got {proj[m]}')

    def test_account_detail_diff_calculations(self):
        """Both diff columns calculate correctly."""
        response = self.client.get(self._url())
        self.assertEqual(response.status_code, 200)
        rows = response.context['rows']
        row_a = next(r for r in rows if r['item_name'] == 'Item A')

        # diff_last_12_vs_last_year = last_12_units - last_full_year_total
        self.assertEqual(
            row_a['diff_last_12_vs_last_year'],
            row_a['last_12_units'] - row_a['last_full_year_total'],
        )
        # diff_current_vs_last_year = current_combined_total - last_full_year_total
        self.assertEqual(
            row_a['diff_current_vs_last_year'],
            row_a['current_combined_total'] - row_a['last_full_year_total'],
        )

    def test_account_detail_item_sort_order(self):
        """Items are ordered by status_priority first, then brand__name, sort_order, name."""
        from apps.catalog.models import Brand
        from itertools import groupby
        brand_b = make_brand(self.company, name='Zebra Brand')
        item_z = Item.objects.get_or_create(
            brand=brand_b, item_code='ZZZ',
            defaults={'name': 'Zebra Item', 'sort_order': 0},
        )[0]
        make_sale(self.company, self.batch, self.account, item_z,
                  date(2025, 6, 1), 3)

        response = self.client.get(self._url())
        self.assertEqual(response.status_code, 200)
        rows = response.context['rows']

        # status_priority must be non-decreasing across all rows
        priorities = [r['status_priority'] for r in rows]
        self.assertEqual(priorities, sorted(priorities),
                         'Rows must be sorted by status_priority ascending')

        # Within each status group, brand_name must be sorted alphabetically
        for priority, group in groupby(rows, key=lambda r: r['status_priority']):
            group_rows = list(group)
            brand_names = [r['brand_name'] for r in group_rows]
            self.assertEqual(brand_names, sorted(brand_names),
                             f'Brand names must be sorted within status_priority {priority}')

    # ------------------------------------------------------------------
    # Status classification tests
    # ------------------------------------------------------------------
    # last_full_month is now scoped to the distributor (not the single account).
    # setUp: self.account has sales through Feb 2026; self.other_account has none.
    # Distributor-scoped max_past_sale = Feb 2026 → window Mar 2025 – Feb 2026.
    # last_full_year = 2025.

    def _get_item_row(self, item_name):
        response = self.client.get(self._url())
        self.assertEqual(response.status_code, 200)
        rows = response.context['rows']
        return next((r for r in rows if r['item_name'] == item_name), None)

    def test_non_buy_status(self):
        """Item with prior year > 0 and last 12m = 0 gets Non-buy status and priority 1."""
        from apps.catalog.models import Brand
        brand = Brand.objects.get(company=self.company, name='Alpha Brand')
        item = Item.objects.create(
            brand=brand, item_code='NONBUY', name='Non Buy Item', sort_order=99,
        )
        # Sale only in Jan 2025 — in LFY but before the 12m window (Mar 2025)
        make_sale(self.company, self.batch, self.account, item, date(2025, 1, 15), 10)

        row = self._get_item_row('Non Buy Item')
        self.assertIsNotNone(row)
        self.assertEqual(row['status'], 'non_buy')
        self.assertEqual(row['status_priority'], 1)

    def test_declining_status(self):
        """Item with last 12m < prior year gets Declining status and priority 2."""
        from apps.catalog.models import Brand
        brand = Brand.objects.get(company=self.company, name='Alpha Brand')
        item = Item.objects.create(
            brand=brand, item_code='DECLINING', name='Declining Item', sort_order=97,
        )
        # High sale in Jan 2025 (LFY only), small sale in Apr 2025 (LFY + window)
        make_sale(self.company, self.batch, self.account, item, date(2025, 1, 15), 100)
        make_sale(self.company, self.batch, self.account, item, date(2025, 4, 15), 5)
        # last_full_year_total=105, last_12_units=5 → declining

        row = self._get_item_row('Declining Item')
        self.assertIsNotNone(row)
        self.assertEqual(row['status'], 'declining')
        self.assertEqual(row['status_priority'], 2)

    def test_steady_status(self):
        """Item with last 12m == prior year gets Steady status and priority 3."""
        from apps.catalog.models import Brand
        brand = Brand.objects.get(company=self.company, name='Alpha Brand')
        item = Item.objects.create(
            brand=brand, item_code='STEADY', name='Steady Item', sort_order=96,
        )
        # Sale only in Apr 2025 — falls in both LFY (2025) and the 12m window
        make_sale(self.company, self.batch, self.account, item, date(2025, 4, 15), 15)
        # last_full_year_total=15, last_12_units=15 → steady

        row = self._get_item_row('Steady Item')
        self.assertIsNotNone(row)
        self.assertEqual(row['status'], 'steady')
        self.assertEqual(row['status_priority'], 3)

    def test_growing_status(self):
        """Item with last 12m > prior year gets Growing status and priority 4."""
        from apps.catalog.models import Brand
        brand = Brand.objects.get(company=self.company, name='Alpha Brand')
        item = Item.objects.create(
            brand=brand, item_code='GROWING', name='Growing Item', sort_order=98,
        )
        # Small sale in LFY, larger sale in the window (Jan 2026 not in LFY)
        make_sale(self.company, self.batch, self.account, item, date(2025, 6, 15), 5)
        make_sale(self.company, self.batch, self.account, item, date(2026, 1, 15), 10)
        # last_full_year_total=5, last_12_units=15 → growing

        row = self._get_item_row('Growing Item')
        self.assertIsNotNone(row)
        self.assertEqual(row['status'], 'growing')
        self.assertEqual(row['status_priority'], 4)

    def test_new_status(self):
        """Item with prior year == 0 and last 12m > 0 gets New status and priority 5."""
        from apps.catalog.models import Brand
        brand = Brand.objects.get(company=self.company, name='Alpha Brand')
        item = Item.objects.create(
            brand=brand, item_code='NEWSTATUS', name='New Status Item', sort_order=95,
        )
        # Sale only in Jan 2026 — in window but not in LFY (2025)
        make_sale(self.company, self.batch, self.account, item, date(2026, 1, 15), 20)
        # last_full_year_total=0, last_12_units=20 → new

        row = self._get_item_row('New Status Item')
        self.assertIsNotNone(row)
        self.assertEqual(row['status'], 'new')
        self.assertEqual(row['status_priority'], 5)

    def test_excluded_items(self):
        """Item with both prior year == 0 and last 12m == 0 is excluded from rows."""
        from apps.catalog.models import Brand
        brand = Brand.objects.get(company=self.company, name='Alpha Brand')
        item = Item.objects.create(
            brand=brand, item_code='EXCLUDED', name='Excluded Item', sort_order=94,
        )
        # Sale only in 2023 — outside both LFY (2025) and the 12m window
        make_sale(self.company, self.batch, self.account, item, date(2023, 6, 15), 10)

        response = self.client.get(self._url())
        self.assertEqual(response.status_code, 200)
        item_names = [r['item_name'] for r in response.context['rows']]
        self.assertNotIn('Excluded Item', item_names)

    def test_sort_order(self):
        """Non-buy appears before Declining, Declining before Steady, etc."""
        from apps.catalog.models import Brand
        brand = Brand.objects.get(company=self.company, name='Alpha Brand')

        # Non-buy: sale only in Jan 2025 (LFY, before 12m window)
        nb = Item.objects.create(brand=brand, item_code='SOTNB', name='ZZ Nonbuy', sort_order=91)
        make_sale(self.company, self.batch, self.account, nb, date(2025, 1, 15), 10)

        # Declining: high in Jan 2025 (LFY only), tiny in Apr 2025 (window)
        dec = Item.objects.create(brand=brand, item_code='SOTDEC', name='ZZ Declining', sort_order=92)
        make_sale(self.company, self.batch, self.account, dec, date(2025, 1, 15), 100)
        make_sale(self.company, self.batch, self.account, dec, date(2025, 4, 15), 5)

        # Steady: sale only in Apr 2025 (in both LFY and window)
        st = Item.objects.create(brand=brand, item_code='SOTST', name='ZZ Steady', sort_order=93)
        make_sale(self.company, self.batch, self.account, st, date(2025, 4, 15), 10)

        response = self.client.get(self._url())
        self.assertEqual(response.status_code, 200)
        rows = response.context['rows']
        names = [r['item_name'] for r in rows]

        self.assertIn('ZZ Nonbuy', names)
        self.assertIn('ZZ Declining', names)
        self.assertIn('ZZ Steady', names)

        idx_nb = names.index('ZZ Nonbuy')
        idx_dec = names.index('ZZ Declining')
        idx_st = names.index('ZZ Steady')

        self.assertLess(idx_nb, idx_dec, 'Non-buy should appear before Declining')
        self.assertLess(idx_dec, idx_st, 'Declining should appear before Steady')

    def test_last_full_month_uses_distributor_scope(self):
        """
        last_full_month is derived from the most recent sale across ALL accounts
        for the distributor, not just the account being viewed.

        Account A has a more recent sale (Dec 2025) than Account B (Jun 2025).
        Viewing Account B's detail page should show last_full_month = December 2025.
        """
        from apps.catalog.models import Brand
        company = make_company(name='Scope Test Co')
        distributor = make_distributor(company, name='Scope Dist')
        brand = make_brand(company, name='Scope Brand')
        item = Item.objects.get_or_create(
            brand=brand, item_code='SCPITM',
            defaults={'name': 'Scope Item', 'sort_order': 1},
        )[0]
        batch = make_batch(company, distributor)

        account_a = make_account(company, distributor, name='Account A Scope')
        account_b = make_account(company, distributor, name='Account B Scope')

        # Account A has a sale in December 2025 (more recent)
        make_sale(company, batch, account_a, item, date(2025, 12, 15), 10)
        # Account B has a sale only in June 2025 (older)
        make_sale(company, batch, account_b, item, date(2025, 6, 15), 5)

        admin = make_user(company, 'supplier_admin', username='sa_scope_test')
        client = self.__class__._get_client_for(admin)

        url = reverse('report_account_detail', kwargs={'account_id': account_b.pk})
        response = client.get(url)

        self.assertEqual(response.status_code, 200)
        # Distributor-scoped: last_full_month should reflect account_a's December 2025
        self.assertEqual(
            response.context['last_full_month_display'],
            'December 2025',
            'last_full_month should use the distributor-wide most recent sale, not just account B',
        )

    @classmethod
    def _get_client_for(cls, user):
        c = Client()
        c.force_login(user)
        return c
