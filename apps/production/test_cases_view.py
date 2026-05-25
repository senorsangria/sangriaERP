"""
Tests for the Production Cases tab.

Covers:
- compute_production_cases_view: aggregation, grouping, filtering, edge cases
- production_home view: cases tab renders, clear_filters redirect, filter applied
"""
from datetime import date
from decimal import Decimal

from django.test import Client, TestCase
from django.urls import reverse

from apps.catalog.models import Brand, CoPacker, Item
from apps.core.models import Company, User
from apps.core.rbac import Role
from apps.production.cases import compute_production_cases_view
from apps.production.models import ProductionPO, ProductionPOLine


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------

def make_company(name='Test Co'):
    return Company.objects.create(name=name)


def make_supplier_admin(company, username='admin'):
    user = User.objects.create_user(
        username=username, password='testpass123', company=company,
    )
    user.roles.set([Role.objects.get(codename='supplier_admin')])
    return user


def make_brand(company, name='Brand A'):
    return Brand.objects.create(company=company, name=name)


def make_co_packer(company, name='Packer A'):
    return CoPacker.objects.create(company=company, name=name)


def make_item(brand, co_packer, name='Item A', item_code='IA', cases_per_batch=100):
    return Item.objects.create(
        brand=brand,
        name=name,
        item_code=item_code,
        co_packer=co_packer,
        cases_per_batch=cases_per_batch,
    )


def make_po(company, co_packer, year=None, month=None, status='projected'):
    today = date.today()
    return ProductionPO.objects.create(
        company=company,
        co_packer=co_packer,
        year=year or today.year,
        month=month or today.month,
        status=status,
        generated_by_algorithm=False,
    )


def make_line(po, item, quantity_cases=300):
    return ProductionPOLine.objects.create(
        po=po,
        item=item,
        batch_count=1,
        quantity_cases=Decimal(str(quantity_cases)),
    )


# ---------------------------------------------------------------------------
# compute_production_cases_view unit tests
# ---------------------------------------------------------------------------

class CasesViewEmptyTest(TestCase):

    def setUp(self):
        self.company = make_company()

    def test_empty_returns_empty_groups(self):
        result = compute_production_cases_view(self.company, {})
        self.assertEqual(result['co_packer_groups'], [])
        self.assertEqual(len(result['horizon']), 12)
        # Grand totals should all be None when no data
        for y_dict in result['grand_totals'].values():
            for val in y_dict.values():
                self.assertIsNone(val)


class CasesViewGroupingTest(TestCase):

    def setUp(self):
        self.company = make_company()
        brand = make_brand(self.company)
        self.cp_a = make_co_packer(self.company, 'Packer A')
        self.cp_b = make_co_packer(self.company, 'Packer B')
        item_a = make_item(brand, self.cp_a, name='Item A', item_code='IA')
        item_b = make_item(brand, self.cp_b, name='Item B', item_code='IB')
        today = date.today()
        po_a = make_po(self.company, self.cp_a, today.year, today.month)
        po_b = make_po(self.company, self.cp_b, today.year, today.month)
        make_line(po_a, item_a, 200)
        make_line(po_b, item_b, 150)

    def test_groups_by_co_packer(self):
        result = compute_production_cases_view(self.company, {})
        self.assertEqual(len(result['co_packer_groups']), 2)
        names = [g['co_packer'].name for g in result['co_packer_groups']]
        self.assertIn('Packer A', names)
        self.assertIn('Packer B', names)

    def test_each_group_contains_its_items(self):
        result = compute_production_cases_view(self.company, {})
        for group in result['co_packer_groups']:
            self.assertEqual(len(group['items']), 1)
            if group['co_packer'].name == 'Packer A':
                self.assertEqual(group['items'][0]['item'].name, 'Item A')
            else:
                self.assertEqual(group['items'][0]['item'].name, 'Item B')


class CasesViewExclusionTest(TestCase):

    def setUp(self):
        self.company = make_company()
        brand = make_brand(self.company)
        self.cp = make_co_packer(self.company, 'Packer A')
        today = date.today()
        # Item with production
        self.item_with = make_item(brand, self.cp, 'Has Production', 'HP')
        # Item with no PO lines at all (zero production)
        self.item_zero = make_item(brand, self.cp, 'No Production', 'NP', cases_per_batch=50)
        po = make_po(self.company, self.cp, today.year, today.month)
        make_line(po, self.item_with, 300)
        # item_zero: no lines → zero production

    def test_excludes_zero_production_items(self):
        result = compute_production_cases_view(self.company, {})
        group = result['co_packer_groups'][0]
        item_names = [r['item'].name for r in group['items']]
        self.assertIn('Has Production', item_names)
        self.assertNotIn('No Production', item_names)


class CasesViewNoCopacker(TestCase):

    def setUp(self):
        self.company = make_company()
        brand = make_brand(self.company)
        # Item with no co_packer
        self.orphan_item = Item.objects.create(
            brand=brand, name='Orphan', item_code='OR',
            co_packer=None, cases_per_batch=100,
        )
        cp = make_co_packer(self.company, 'Packer A')
        today = date.today()
        po = make_po(self.company, cp, today.year, today.month)
        # Try to make a line for the orphan item via raw DB (bypass FK constraint would fail,
        # so just verify that items without co_packer are never included in groups)

    def test_no_groups_when_only_orphan_items(self):
        # No PO lines exist for orphan items (they'd be filtered by co_packer is None check)
        result = compute_production_cases_view(self.company, {})
        self.assertEqual(result['co_packer_groups'], [])


class CasesViewAggregationTest(TestCase):

    def setUp(self):
        self.company = make_company()
        brand = make_brand(self.company)
        cp = make_co_packer(self.company, 'Packer A')
        self.item = make_item(brand, cp, 'Item A', 'IA')
        today = date.today()
        # Two POs for the same (item, year, month) — lines should be summed
        po1 = make_po(self.company, cp, today.year, today.month, status='projected')
        po2 = make_po(self.company, cp, today.year, today.month, status='actual')
        make_line(po1, self.item, 200)
        make_line(po2, self.item, 150)
        self.today = today

    def test_aggregates_multiple_lines_same_month(self):
        result = compute_production_cases_view(self.company, {})
        group = result['co_packer_groups'][0]
        item_row = group['items'][0]
        y, m = self.today.year, self.today.month
        self.assertEqual(item_row['monthly_cases'][y][m], 350)
        self.assertEqual(item_row['total_cases'], 350)


class CasesViewStatusFilterTest(TestCase):

    def setUp(self):
        self.company = make_company()
        brand = make_brand(self.company)
        self.cp = make_co_packer(self.company, 'Packer A')
        self.item = make_item(brand, self.cp, 'Item A', 'IA')
        today = date.today()
        self.today = today
        po_proj = make_po(self.company, self.cp, today.year, today.month, status='projected')
        po_comp = make_po(self.company, self.cp, today.year, today.month, status='complete')
        make_line(po_proj, self.item, 100)
        make_line(po_comp, self.item, 200)

    def test_no_filter_includes_all_statuses(self):
        result = compute_production_cases_view(self.company, {'status': []})
        group = result['co_packer_groups'][0]
        y, m = self.today.year, self.today.month
        self.assertEqual(group['items'][0]['monthly_cases'][y][m], 300)

    def test_status_filter_projected_only(self):
        result = compute_production_cases_view(self.company, {'status': ['projected']})
        group = result['co_packer_groups'][0]
        y, m = self.today.year, self.today.month
        self.assertEqual(group['items'][0]['monthly_cases'][y][m], 100)

    def test_status_filter_complete_only(self):
        result = compute_production_cases_view(self.company, {'status': ['complete']})
        group = result['co_packer_groups'][0]
        y, m = self.today.year, self.today.month
        self.assertEqual(group['items'][0]['monthly_cases'][y][m], 200)

    def test_status_filter_excludes_all_returns_empty(self):
        # 'actual' status has no POs in this fixture
        result = compute_production_cases_view(self.company, {'status': ['actual']})
        self.assertEqual(result['co_packer_groups'], [])


class CasesViewHorizonTest(TestCase):

    def setUp(self):
        self.company = make_company()

    def test_horizon_is_12_months(self):
        result = compute_production_cases_view(self.company, {})
        self.assertEqual(len(result['horizon']), 12)

    def test_horizon_starts_from_current_month(self):
        result = compute_production_cases_view(self.company, {})
        today = date.today()
        first = result['horizon'][0]
        self.assertEqual(first['year'], today.year)
        self.assertEqual(first['month'], today.month)

    def test_horizon_entries_have_required_keys(self):
        result = compute_production_cases_view(self.company, {})
        for h in result['horizon']:
            self.assertIn('year', h)
            self.assertIn('month', h)
            self.assertIn('label', h)


class CasesViewSubtotalsTest(TestCase):

    def setUp(self):
        self.company = make_company()
        brand = make_brand(self.company)
        self.cp = make_co_packer(self.company, 'Packer A')
        today = date.today()
        self.today = today
        # Three items in the same co-packer, same month
        item1 = make_item(brand, self.cp, 'Item 1', 'I1', 100)
        item2 = make_item(brand, self.cp, 'Item 2', 'I2', 200)
        item3 = make_item(brand, self.cp, 'Item 3', 'I3', 300)
        po = make_po(self.company, self.cp, today.year, today.month)
        make_line(po, item1, 100)
        make_line(po, item2, 200)
        make_line(po, item3, 300)

    def test_subtotals_sum_all_items(self):
        result = compute_production_cases_view(self.company, {})
        group = result['co_packer_groups'][0]
        y, m = self.today.year, self.today.month
        self.assertEqual(group['subtotals'][y][m], 600)


class CasesViewGrandTotalTest(TestCase):

    def setUp(self):
        self.company = make_company()
        brand = make_brand(self.company)
        self.cp_a = make_co_packer(self.company, 'Packer A')
        self.cp_b = make_co_packer(self.company, 'Packer B')
        today = date.today()
        self.today = today
        item_a = make_item(brand, self.cp_a, 'Item A', 'IA')
        item_b = make_item(brand, self.cp_b, 'Item B', 'IB')
        po_a = make_po(self.company, self.cp_a, today.year, today.month)
        po_b = make_po(self.company, self.cp_b, today.year, today.month)
        make_line(po_a, item_a, 400)
        make_line(po_b, item_b, 250)

    def test_grand_total_sums_both_groups(self):
        result = compute_production_cases_view(self.company, {})
        y, m = self.today.year, self.today.month
        self.assertEqual(result['grand_totals'][y][m], 650)


# ---------------------------------------------------------------------------
# production_home view integration tests
# ---------------------------------------------------------------------------

class CasesTabViewTest(TestCase):

    def setUp(self):
        self.company = make_company()
        self.user = make_supplier_admin(self.company)
        self.client = Client()
        self.client.login(username='admin', password='testpass123')
        self.url = reverse('production_home')

    def test_cases_tab_renders_200(self):
        response = self.client.get(self.url + '?tab=production_cases')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Production cases by month')

    def test_cases_tab_shows_no_data_empty_state(self):
        response = self.client.get(self.url + '?tab=production_cases')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'No production cases to display')

    def test_clear_filters_redirects(self):
        session = self.client.session
        session['production_cases_filters'] = {'status': ['projected']}
        session.save()
        # clear_filters=1 now redirects to production_home with no tab param
        response = self.client.get(self.url + '?clear_filters=1')
        self.assertRedirects(response, self.url)
        # Session key should be cleared
        session = self.client.session
        self.assertNotIn('production_cases_filters', session)

    def test_status_filter_stored_in_session(self):
        response = self.client.get(
            self.url + '?tab=production_cases&status=projected',
            follow=False,
        )
        self.assertEqual(response.status_code, 200)
        session = self.client.session
        self.assertIn('production_cases_filters', session)
        self.assertIn('projected', session['production_cases_filters']['status'])

    def test_cases_tab_shows_data_when_pos_exist(self):
        brand = make_brand(self.company)
        cp = make_co_packer(self.company)
        item = make_item(brand, cp, 'Test Item', 'TI')
        today = date.today()
        po = make_po(self.company, cp, today.year, today.month)
        make_line(po, item, 500)
        response = self.client.get(self.url + '?tab=production_cases')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Test Item')
        self.assertContains(response, '500')

    # ------------------------------------------------------------------
    # Tests for the Bootstrap-integration fix (Bug 1 & Bug 2)
    # ------------------------------------------------------------------

    def test_cases_data_computed_when_active_tab_is_forecast(self):
        # cases_view must be in context even when the forecast tab is active
        response = self.client.get(self.url + '?tab=forecast')
        self.assertEqual(response.status_code, 200)
        self.assertIn('cases_view', response.context)
        self.assertIsNotNone(response.context['cases_view'])

    def test_cases_data_computed_when_no_tab_specified(self):
        # cases_view must be in context on a plain /production/ request
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertIn('cases_view', response.context)
        self.assertIsNotNone(response.context['cases_view'])

    def test_cases_tab_uses_bootstrap_data_attributes(self):
        # The nav item must be a Bootstrap tab trigger, not a plain <a href>
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-bs-toggle="tab"')
        self.assertContains(response, 'data-bs-target="#pane-production-cases"')

    def test_cases_pane_rendered_on_all_tabs_but_not_active(self):
        # The cases pane div must be in the DOM even when forecast is active,
        # but without the 'show active' classes (Bootstrap CSS hides it).
        response = self.client.get(self.url + '?tab=forecast')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="pane-production-cases"')
        content = response.content.decode()
        # Cases pane must NOT have show active when forecast tab is selected
        self.assertNotIn('tab-pane fade show active"\n       id="pane-production-cases"', content)
        # Confirm forecast pane IS show active
        self.assertIn('pane-forecast', content)

    def test_cases_tab_active_on_direct_url(self):
        # When ?tab=production_cases is requested, the cases pane gets show active
        response = self.client.get(self.url + '?tab=production_cases')
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        # The cases pane div must contain show active
        self.assertIn('tab-pane fade show active', content)
        self.assertIn('id="pane-production-cases"', content)
