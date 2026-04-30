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
# LFY Diff tests
# ---------------------------------------------------------------------------

class LfyDiffTest(TestCase):
    """lfy_diff = most_recent_year units - prior_year units per row."""

    def setUp(self):
        self.company = make_company()
        self.distributor = make_distributor(self.company)
        self.item = make_item(self.company)
        self.batch = make_batch(self.company, self.distributor)
        self.account = make_account(self.company, self.distributor)
        self.user = make_user(self.company, 'supplier_admin', username='sa1')
        self.client = Client()
        self.client.force_login(self.user)

    def test_lfy_diff_calculated(self):
        """lfy_diff equals most_recent_year total minus prior_year total."""
        # prior_year: 2023 = 40 units; most_recent_year: 2024 = 100 units
        make_sale(self.company, self.batch, self.account, self.item, date(2023, 6, 1), 40)
        make_sale(self.company, self.batch, self.account, self.item, date(2024, 6, 1), 100)
        response = self.client.get(reverse('report_account_sales_by_year'))
        self.assertEqual(response.status_code, 200)
        rows = response.context['rows']
        self.assertEqual(len(rows), 1)
        prior_year = response.context['prior_year']
        most_recent_year = response.context['most_recent_year']
        self.assertIsNotNone(prior_year)
        self.assertEqual(most_recent_year, 2024)
        self.assertEqual(prior_year, 2023)
        self.assertEqual(rows[0]['lfy_diff'], 60)  # 100 - 40

    def test_lfy_diff_none_when_only_one_year(self):
        """lfy_diff is None when only one year of data exists."""
        make_sale(self.company, self.batch, self.account, self.item, date(2024, 6, 1), 50)
        response = self.client.get(reverse('report_account_sales_by_year'))
        self.assertEqual(response.status_code, 200)
        rows = response.context['rows']
        self.assertEqual(len(rows), 1)
        self.assertIsNone(response.context['prior_year'])
        self.assertIsNone(rows[0]['lfy_diff'])


# ---------------------------------------------------------------------------
# Clear filters test
# ---------------------------------------------------------------------------

class ClearFiltersTest(TestCase):
    """clear_filters=1 clears session filters and shows unfiltered report."""

    def setUp(self):
        self.company = make_company()
        self.distributor = make_distributor(self.company)
        self.item = make_item(self.company)
        self.batch = make_batch(self.company, self.distributor)
        self.account = make_account(
            self.company, self.distributor, name='Clear Test Bar', city='Newark'
        )
        make_sale(self.company, self.batch, self.account, self.item, date(2024, 6, 1), 20)
        self.user = make_user(self.company, 'supplier_admin', username='sa1')
        self.client = Client()
        self.client.force_login(self.user)

    def test_clear_filters_resets_report(self):
        """After applying an account_name filter, clear_filters=1 restores all rows."""
        # Apply a filter that returns no results
        self.client.get(
            reverse('report_account_sales_by_year'),
            {'account_name': 'zzznomatch'}
        )
        # Confirm filter is stored in session
        session = self.client.session
        self.assertIn('report_account_sales_filters', session)

        # Now clear filters
        response = self.client.get(
            reverse('report_account_sales_by_year'),
            {'clear_filters': '1'}
        )
        self.assertEqual(response.status_code, 200)
        # Session filters should be cleared
        session = self.client.session
        self.assertNotIn('report_account_sales_filters', session)
        # Account should appear again
        rows = response.context['rows']
        account_names = [r['account_name'] for r in rows]
        self.assertTrue(any('Clear Test' in n for n in account_names), account_names)


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
# New filter tests (account_name, county OR, account_type OR, session)
# ---------------------------------------------------------------------------

class NewFilterTest(TestCase):
    """Tests for account_name (word search), county OR, account_type OR, session persistence."""

    def setUp(self):
        self.company = make_company()
        self.distributor = make_distributor(self.company)
        self.item = make_item(self.company)
        self.batch = make_batch(self.company, self.distributor)

        # Account with "Wine" and "Total" in name — Essex county, Bar type
        self.acc_wine = make_account(
            self.company, self.distributor, name='Wine Total Shop',
            county='Essex', account_type='Bar',
        )
        # Account with "Beer" in name — Mercer county, Retail type
        self.acc_beer = make_account(
            self.company, self.distributor, name='Beer Garden',
            county='Mercer', account_type='Retail',
        )
        # Third account — Hudson county, Restaurant type
        self.acc_third = make_account(
            self.company, self.distributor, name='Spirit House',
            county='Hudson', account_type='Restaurant',
        )

        make_sale(self.company, self.batch, self.acc_wine, self.item, date(2024, 6, 1), 10)
        make_sale(self.company, self.batch, self.acc_beer, self.item, date(2024, 6, 1), 20)
        make_sale(self.company, self.batch, self.acc_third, self.item, date(2024, 6, 1), 15)

        self.user = make_user(self.company, 'supplier_admin', username='sa_nf')
        self.client = Client()
        self.client.force_login(self.user)

    def _get_rows(self, params):
        response = self.client.get(reverse('report_account_sales_by_year'), params)
        self.assertEqual(response.status_code, 200)
        return response.context['rows'], response

    def test_account_name_filter_single_word(self):
        """Filtering by one word returns only accounts containing that word (case-insensitive)."""
        rows, _ = self._get_rows({'account_name': 'wine'})
        self.assertEqual(len(rows), 1)
        self.assertIn('Wine', rows[0]['account_name'])

    def test_account_name_filter_multiple_words(self):
        """Filtering by 'wine total' returns accounts containing both words in any order."""
        rows, _ = self._get_rows({'account_name': 'wine total'})
        self.assertEqual(len(rows), 1)
        # Wine Total Shop has both; Beer Garden has neither
        self.assertIn('Wine', rows[0]['account_name'])

    def test_county_filter_or_logic(self):
        """Selecting two counties returns accounts in either county."""
        rows, _ = self._get_rows({'county': ['Essex', 'Mercer']})
        self.assertEqual(len(rows), 2)
        names = [r['account_name'] for r in rows]
        self.assertTrue(any('Wine' in n for n in names))
        self.assertTrue(any('Beer' in n for n in names))
        self.assertFalse(any('Spirit' in n for n in names))

    def test_account_type_filter_or_logic(self):
        """Selecting two account types returns accounts of either type."""
        rows, _ = self._get_rows({'account_type': ['Bar', 'Retail']})
        self.assertEqual(len(rows), 2)
        names = [r['account_name'] for r in rows]
        self.assertTrue(any('Wine' in n for n in names))
        self.assertTrue(any('Beer' in n for n in names))
        self.assertFalse(any('Spirit' in n for n in names))

    def test_new_filters_persisted_in_session(self):
        """Applying new filters saves them to session correctly."""
        self.client.get(
            reverse('report_account_sales_by_year'),
            {'account_name': 'wine', 'county': ['Essex'], 'account_type': ['Bar']},
        )
        session = self.client.session
        saved = session.get('report_account_sales_filters')
        self.assertIsNotNone(saved, 'Filters were not saved to session')
        self.assertEqual(saved['account_name'], 'wine')
        self.assertIn('Essex', saved['county'])
        self.assertIn('Bar', saved['account_type'])

    def test_totals_reflect_filtered_rows(self):
        """Totals row matches sum of visible rows after filtering."""
        rows, response = self._get_rows({'county': 'Essex'})
        total_last_12 = response.context['total_last_12']
        expected = sum(r['last_12_units'] for r in rows)
        self.assertEqual(total_last_12, expected)


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
        """Projected values use multiplier = last_12m / last_full_year_total."""
        from unittest.mock import patch
        from datetime import date as real_date
        from apps.catalog.models import Brand

        brand = Brand.objects.get(company=self.company, name='Alpha Brand')
        # Dedicated item with fully controlled LFY and actual data
        proj_item = Item.objects.create(
            brand=brand, item_code='PROJTEST', name='Proj Test Item', sort_order=99,
        )
        # LFY (2025): months 2–7 each = 5 (total 30); month 9 = 20 → last_full_year_total = 50
        for m in range(2, 8):
            make_sale(self.company, self.batch, self.account, proj_item,
                      real_date(2025, m, 15), 5)
        make_sale(self.company, self.batch, self.account, proj_item,
                  real_date(2025, 9, 15), 20)
        # 2026 Jan–Jul: 10 each → contributes to last_12m window (Aug 2025–Jul 2026)
        for m in range(1, 8):
            make_sale(self.company, self.batch, self.account, proj_item,
                      real_date(2026, m, 15), 10)

        # Mock today = Aug 1 2026 → lfm = Jul 2026, actual_months = [1..7], projected = [8..12]
        # distributor-scoped max_past_sale = Jul 2026 (from proj_item sales)
        # window: Aug 2025 – Jul 2026
        # last_12m for proj_item: Sep 2025 (20) + Jan-Jul 2026 (10*7=70) = 90
        #   (Sep 2025 is within the Aug 2025–Jul 2026 window)
        # multiplier = 90 / 50 = 1.8
        # projected month 9: lfy[9]=20, projected = round(20 * 1.8) = round(36) = 36
        with patch('apps.reports.views.date') as MockDate:
            MockDate.today.return_value = real_date(2026, 8, 1)
            MockDate.side_effect = lambda *a, **kw: real_date(*a, **kw)
            response = self.client.get(self._url())

        self.assertEqual(response.status_code, 200)
        rows = response.context['rows']
        row = next(r for r in rows if r['item_name'] == 'Proj Test Item')

        proj = row['current_projected_by_month']
        self.assertIn(9, proj)
        self.assertEqual(proj[9], 36)

    def test_account_detail_projection_fallback_no_prior_year(self):
        """New item (last_full_year_total == 0): all projected months are None."""
        from unittest.mock import patch
        from datetime import date as real_date
        from apps.catalog.models import Brand

        brand = Brand.objects.get(company=self.company, name='Alpha Brand')
        new_item = Item.objects.create(
            brand=brand, item_code='NEWITEM', name='New Item', sort_order=10,
        )
        # new_item has NO 2025 data — last_full_year_total = 0 → no projection
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

        # last_full_year_total == 0 → multiplier = None → all projected months = None
        proj = row_new['current_projected_by_month']
        for m, val in proj.items():
            self.assertIsNone(val, f'Expected None for projected month {m}, got {val}')

    def test_projection_non_buy(self):
        """Non-buy item (last_full_year_total > 0, last_12_units == 0) gets multiplier 0.0
        and all projected months equal 0."""
        from apps.catalog.models import Brand

        brand = Brand.objects.get(company=self.company, name='Alpha Brand')
        nb_item = Item.objects.create(
            brand=brand, item_code='NBPROJ', name='Non Buy Proj Item', sort_order=88,
        )
        # Sale only in Jan 2025 (in LFY but before the 12m window Mar 2025–Feb 2026)
        make_sale(self.company, self.batch, self.account, nb_item, date(2025, 1, 15), 10)
        # last_full_year_total = 10, last_12_units = 0
        # multiplier = 0 / 10 = 0.0 → all projected months = 0

        response = self.client.get(self._url())
        self.assertEqual(response.status_code, 200)
        rows = response.context['rows']
        row = next((r for r in rows if r['item_name'] == 'Non Buy Proj Item'), None)
        self.assertIsNotNone(row, 'Non Buy Proj Item row not found')
        self.assertEqual(row['last_full_year_total'], 10)
        self.assertEqual(row['last_12_units'], 0)
        self.assertEqual(row['status'], 'non_buy')

        proj = row['current_projected_by_month']
        for m, val in proj.items():
            self.assertEqual(val, 0, f'Expected 0 for projected month {m}, got {val}')

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

    def test_change_pct_calculated_correctly(self):
        """change_pct = round((last_12 - lfy_total) / lfy_total * 100, 1)."""
        response = self.client.get(self._url())
        self.assertEqual(response.status_code, 200)
        rows = response.context['rows']
        # item_a: last_full_year_total=40, last_12_units=60 → change_pct=50.0
        row_a = next(r for r in rows if r['item_name'] == 'Item A')
        self.assertEqual(row_a['last_full_year_total'], 40)
        self.assertEqual(row_a['last_12_units'], 60)
        self.assertEqual(row_a['change_pct'], 50.0)

    def test_change_pct_none_for_new_item(self):
        """change_pct is None when last_full_year_total == 0 (new item)."""
        from apps.catalog.models import Brand
        brand = Brand.objects.get(company=self.company, name='Alpha Brand')
        item = Item.objects.create(
            brand=brand, item_code='NEWPCT', name='New Pct Item', sort_order=87,
        )
        # Sale only in Jan 2026 — in window but not in LFY → last_full_year_total = 0
        make_sale(self.company, self.batch, self.account, item, date(2026, 1, 15), 15)

        response = self.client.get(self._url())
        self.assertEqual(response.status_code, 200)
        rows = response.context['rows']
        row = next((r for r in rows if r['item_name'] == 'New Pct Item'), None)
        self.assertIsNotNone(row)
        self.assertEqual(row['last_full_year_total'], 0)
        self.assertIsNone(row['change_pct'])

    def test_diff_lfy_by_month_calculated(self):
        """diff_lfy_by_month[m] = last_full_year_by_month[m] - prior_year_by_month[m]."""
        # Add prior year (2024) sales for item_a in months 3 and 6
        make_sale(self.company, self.batch, self.account, self.item_a, date(2024, 3, 15), 4)
        make_sale(self.company, self.batch, self.account, self.item_a, date(2024, 6, 15), 6)

        response = self.client.get(self._url())
        self.assertEqual(response.status_code, 200)
        rows = response.context['rows']
        row_a = next(r for r in rows if r['item_name'] == 'Item A')

        # LFY (2025) month 3 = 10, prior year (2024) month 3 = 4 → diff = 6
        self.assertEqual(row_a['diff_lfy_by_month'][3], 10 - 4)
        # LFY month 6 = 10, prior year month 6 = 6 → diff = 4
        self.assertEqual(row_a['diff_lfy_by_month'][6], 10 - 6)
        # LFY month 1 = 0, prior year month 1 = 0 → diff = 0
        self.assertEqual(row_a['diff_lfy_by_month'][1], 0)

    def test_diff_cy_actual_calculated(self):
        """diff_cy_actual_by_month[m] = current_actual_by_month[m] - last_full_year_by_month[m]."""
        response = self.client.get(self._url())
        self.assertEqual(response.status_code, 200)
        rows = response.context['rows']
        row_a = next(r for r in rows if r['item_name'] == 'Item A')

        # CY Jan (2026) = 8, LFY Jan (2025) = 0 → diff = 8
        self.assertEqual(row_a['diff_cy_actual_by_month'][1], 8 - 0)
        # CY Feb (2026) = 12, LFY Feb (2025) = 0 → diff = 12
        self.assertEqual(row_a['diff_cy_actual_by_month'][2], 12 - 0)

    def test_events_by_month_counted(self):
        """lfy_events_by_month counts events for the account in LFY correctly."""
        from apps.events.models import Event

        # Create 2 events in Jan 2025 and 1 event in Mar 2025 (LFY = 2025)
        Event.objects.create(
            company=self.company, account=self.account, date=date(2025, 1, 10),
        )
        Event.objects.create(
            company=self.company, account=self.account, date=date(2025, 1, 20),
        )
        Event.objects.create(
            company=self.company, account=self.account, date=date(2025, 3, 15),
        )
        # Event for a different account — should not be counted
        Event.objects.create(
            company=self.company, account=self.other_account, date=date(2025, 1, 5),
        )

        response = self.client.get(self._url())
        self.assertEqual(response.status_code, 200)
        lem = response.context['lfy_events_by_month']

        self.assertEqual(lem.get(1), 2, 'Expected 2 events in Jan 2025')
        self.assertEqual(lem.get(3), 1, 'Expected 1 event in Mar 2025')
        self.assertIsNone(lem.get(6), 'Expected no events in Jun 2025')

    def test_projected_diff_none_when_no_prior(self):
        """diff_cy_projected_by_month[m] is None when projected value is None (new item)."""
        from apps.catalog.models import Brand
        brand = Brand.objects.get(company=self.company, name='Alpha Brand')
        # New item: sale only in CY 2026, no LFY data → multiplier=None → projected=None
        new_item = Item.objects.create(
            brand=brand, item_code='DIFFNEW', name='Diff New Item', sort_order=84,
        )
        make_sale(self.company, self.batch, self.account, new_item, date(2026, 1, 15), 10)

        response = self.client.get(self._url())
        self.assertEqual(response.status_code, 200)
        rows = response.context['rows']
        row = next((r for r in rows if r['item_name'] == 'Diff New Item'), None)
        self.assertIsNotNone(row)
        self.assertEqual(row['last_full_year_total'], 0)

        # All projected months should have None diff
        proj_diff = row['diff_cy_projected_by_month']
        for m, val in proj_diff.items():
            self.assertIsNone(val, f'Expected None diff for projected month {m} on new item')

    def test_total_change_pct_in_portfolio_totals(self):
        """portfolio_totals.total_change_pct = round((last_12_total - prior) / prior * 100, 1)."""
        response = self.client.get(self._url())
        self.assertEqual(response.status_code, 200)
        pt = response.context['portfolio_totals']
        # prior_year_total=60, last_12_total=80 → (80-60)/60*100 = 33.3...
        self.assertEqual(pt['prior_year_total'], 60)
        self.assertEqual(pt['last_12_total'], 80)
        self.assertEqual(pt['total_change_pct'], 33.3)

    @classmethod
    def _get_client_for(cls, user):
        c = Client()
        c.force_login(user)
        return c


# ---------------------------------------------------------------------------
# Ambassador Manager cannot access account sales report
# ---------------------------------------------------------------------------

class AmbassadorManagerReportAccessTest(TestCase):
    """Ambassador Manager is redirected away from the account sales report."""

    def setUp(self):
        self.company = make_company('AM Report Co')
        self.client = Client()

    def test_ambassador_manager_cannot_see_report(self):
        user = make_user(self.company, 'ambassador_manager', username='am_report')
        self.client.force_login(user)
        response = self.client.get(reverse('report_account_sales_by_year'))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response['Location'], reverse('dashboard'))


# ---------------------------------------------------------------------------
# Regression tests for filter-clearing bugs (Bug 1 + Bug 2)
# ---------------------------------------------------------------------------

class FilterActiveNoDataTest(TestCase):
    """
    Regression tests for the filter-active zero-results (no_data) state.

    Bug 1: Empty-state "Clear Filters" link was a plain /reports/ URL with no
    ?clear_filters=1, so the view restored filters from session on every visit.

    Bug 2: #filterModal was inside the {% else %} branch of {% if no_data %},
    so the top-bar "Filters" button had no modal target in the DOM when filters
    produced zero results.
    """

    def setUp(self):
        self.company = make_company()
        self.distributor = make_distributor(self.company)
        self.item = make_item(self.company)
        self.batch = make_batch(self.company, self.distributor)
        self.account = make_account(
            self.company, self.distributor, name='Regression Bar', city='Newark'
        )
        make_sale(self.company, self.batch, self.account, self.item, date(2024, 6, 1), 10)
        self.user = make_user(self.company, 'supplier_admin', username='sa_reg')
        self.client = Client()
        self.client.force_login(self.user)

    def _apply_nonmatching_filter(self):
        """GET the report with a filter that produces zero results, saving it to session."""
        return self.client.get(
            reverse('report_account_sales_by_year'),
            {'account_name': 'zzznomatch'},
        )

    def test_clear_filters_param_clears_session(self):
        """GET ?clear_filters=1&distributor=<pk> removes report_account_sales_filters from session."""
        self._apply_nonmatching_filter()
        self.assertIn('report_account_sales_filters', self.client.session)

        response = self.client.get(
            reverse('report_account_sales_by_year'),
            {'clear_filters': '1', 'distributor': str(self.distributor.pk)},
        )
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(
            'report_account_sales_filters', self.client.session,
            'Session filter key must be absent after ?clear_filters=1',
        )

    def test_no_data_state_renders_filter_modal(self):
        """When filters produce zero results, #filterModal must be present in the DOM."""
        response = self._apply_nonmatching_filter()
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context.get('no_data'), 'Expected no_data=True')
        self.assertContains(
            response, 'id="filterModal"',
            msg_prefix='#filterModal must be in the DOM even when no_data=True with active filters',
        )

    def test_no_data_state_clear_filters_link_has_param(self):
        """The empty-state Clear Filters button must include ?clear_filters=1 in its href."""
        response = self._apply_nonmatching_filter()
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context.get('no_data'), 'Expected no_data=True')
        self.assertContains(
            response, 'clear_filters=1',
            msg_prefix='Empty-state Clear Filters link must include ?clear_filters=1',
        )


# ---------------------------------------------------------------------------
# Regression tests for the mobile layout refactor
# ---------------------------------------------------------------------------

class MobileLayoutRefactorTest(TestCase):
    """
    Tests covering the narrower-layout refactor:
    - Account and City merged into a single stacked column
    - Year headers abbreviated to 'YY
    - old-year-col class applied when > 2 years of data
    - Toggle button suppressed when <= 2 years
    - CSV export unchanged (City remains a separate column)
    """

    def setUp(self):
        self.company = make_company()
        self.distributor = make_distributor(self.company)
        self.item = make_item(self.company)
        self.batch = make_batch(self.company, self.distributor)
        self.account = make_account(
            self.company, self.distributor, name='Refactor Test Bar', city='Newark'
        )
        self.user = make_user(self.company, 'supplier_admin', username='sa_layout')
        self.client = Client()
        self.client.force_login(self.user)

    def _make_sales_for_years(self, *years):
        for yr in years:
            make_sale(self.company, self.batch, self.account, self.item,
                      date(yr, 6, 1), 10)

    def test_account_city_merged_column(self):
        """data-account / data-city on TD; no standalone <th>City</th> column."""
        self._make_sales_for_years(2024, 2025)
        response = self.client.get(reverse('report_account_sales_by_year'))
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertIn('data-account=', content,
                      'data-account attribute must appear on merged Account+City TD')
        self.assertIn('data-city=', content,
                      'data-city attribute must appear on merged Account+City TD')
        # The old standalone City <th> must not appear
        self.assertNotIn('<th>City</th>', content)
        self.assertNotIn('width:100px;">City', content)

    def test_year_headers_abbreviated(self):
        """Year headers render as 'YY (apostrophe + 2 digits), not 4-digit full year."""
        self._make_sales_for_years(2024, 2025)
        response = self.client.get(reverse('report_account_sales_by_year'))
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        # Abbreviated form present
        self.assertIn("'24", content)
        self.assertIn("'25", content)
        # LFY diff header no longer says "2024 Diff" or "2025 Diff"
        self.assertNotIn('2024 Diff', content)
        self.assertNotIn('2025 Diff', content)

    def test_old_year_col_class_applied_when_more_than_two_years(self):
        """With 4 years of data, old-year-col class and toggle button are present."""
        self._make_sales_for_years(2022, 2023, 2024, 2025)
        response = self.client.get(reverse('report_account_sales_by_year'))
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        # Space-prefixed form as it appears in HTML class attributes (not JS .old-year-col)
        self.assertIn(' old-year-col', content,
                      'old-year-col class must appear on oldest year columns when > 2 years')
        # id= form distinguishes from JS getElementById string
        self.assertIn('id="report-older-years-toggle"', content,
                      'Toggle button element must be present when there are > 2 years of data')

    def test_old_year_toggle_hidden_when_two_or_fewer_years(self):
        """With only 2 years of data, toggle button element and old-year-col class are absent."""
        self._make_sales_for_years(2024, 2025)
        response = self.client.get(reverse('report_account_sales_by_year'))
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        # id= form distinguishes from JS getElementById string literal
        self.assertNotIn('id="report-older-years-toggle"', content,
                         'Toggle button element must not appear when <= 2 years of data')
        # Space-prefixed: only appears in HTML class attr, not in JS .old-year-col selector
        self.assertNotIn(' old-year-col', content,
                         'old-year-col class must not appear when <= 2 years of data')

    def test_csv_export_unchanged_after_template_refactor(self):
        """CSV still has Account Name, City, On/Off as separate columns."""
        self._make_sales_for_years(2024, 2025)
        response = self.client.get(reverse('report_account_sales_csv'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'text/csv')
        content = response.content.decode()
        first_line = content.splitlines()[0]
        self.assertIn('Account Name', first_line)
        self.assertIn('City', first_line)
        self.assertIn('On/Off', first_line)
        # They must be separate columns, not merged
        cols = first_line.split(',')
        self.assertEqual(cols[0].strip(), 'Account Name')
        self.assertEqual(cols[1].strip(), 'City')
        self.assertEqual(cols[2].strip(), 'On/Off')
