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

    def test_negative_quantities_excluded(self):
        """Negative (return) sales records are excluded from all calculations."""
        make_sale(self.company, self.batch, self.account, self.item, date(2024, 6, 1), 100)
        make_sale(self.company, self.batch, self.account, self.item, date(2024, 7, 1), -30)
        response = self.client.get(reverse('report_account_sales_by_year'))
        rows = response.context['rows']
        self.assertEqual(len(rows), 1)
        # Year 2024 units should be 100, not 70
        year_units = rows[0]['year_units']
        if 2024 in year_units:
            self.assertEqual(year_units[2024], 100)


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
