"""
Tests for apps.core — RBAC permission/role system and management commands.

Phase 10.5 Step 10
"""
from io import StringIO

from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse

from apps.accounts.models import Account
from apps.core.models import Company, User
from apps.core.rbac import Permission, Role


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_company(name="Test Co"):
    return Company.objects.create(name=name)


def make_user(company, role_codename, username="testuser"):
    user = User.objects.create_user(
        username=username, password="testpass123", company=company,
    )
    role = Role.objects.get(codename=role_codename)
    user.roles.set([role])
    return user


# ---------------------------------------------------------------------------
# has_role() tests
# ---------------------------------------------------------------------------

class HasRoleTest(TestCase):
    """user.has_role() returns True/False based on assigned roles."""

    def setUp(self):
        self.company = make_company()

    def test_has_role_true_for_assigned_role(self):
        user = make_user(self.company, 'supplier_admin')
        self.assertTrue(user.has_role('supplier_admin'))

    def test_has_role_false_for_unassigned_role(self):
        user = make_user(self.company, 'ambassador')
        self.assertFalse(user.has_role('supplier_admin'))

    def test_has_role_false_for_no_roles(self):
        user = User.objects.create_user(
            username='noroles', password='testpass123', company=self.company
        )
        self.assertFalse(user.has_role('supplier_admin'))


# ---------------------------------------------------------------------------
# has_permission() tests
# ---------------------------------------------------------------------------

class HasPermissionTest(TestCase):
    """user.has_permission() reflects union of all assigned role permissions."""

    def setUp(self):
        self.company = make_company()

    def test_permission_true_when_role_has_it(self):
        user = make_user(self.company, 'supplier_admin')
        self.assertTrue(user.has_permission('can_manage_brands'))

    def test_permission_false_when_role_lacks_it(self):
        user = make_user(self.company, 'ambassador')
        self.assertFalse(user.has_permission('can_manage_brands'))

    def test_permission_false_for_user_with_no_roles(self):
        user = User.objects.create_user(
            username='noroles', password='testpass123', company=self.company
        )
        self.assertFalse(user.has_permission('can_view_events'))

    def test_multiple_roles_give_union_of_permissions(self):
        """A user with two roles gets all permissions from both."""
        user = User.objects.create_user(
            username='multirole', password='testpass123', company=self.company
        )
        ambassador_role = Role.objects.get(codename='ambassador')
        supplier_role = Role.objects.get(codename='supplier_admin')
        user.roles.set([ambassador_role, supplier_role])

        # Ambassador has can_fill_recap; supplier_admin has can_manage_brands
        self.assertTrue(user.has_permission('can_fill_recap'))
        self.assertTrue(user.has_permission('can_manage_brands'))

    def test_can_mark_ok_to_pay_payroll_reviewer(self):
        user = make_user(self.company, 'payroll_reviewer')
        self.assertTrue(user.has_permission('can_mark_ok_to_pay'))

    def test_can_mark_ok_to_pay_supplier_admin(self):
        user = make_user(self.company, 'supplier_admin')
        self.assertTrue(user.has_permission('can_mark_ok_to_pay'))

    def test_sales_manager_cannot_mark_ok_to_pay(self):
        user = make_user(self.company, 'sales_manager')
        self.assertFalse(user.has_permission('can_mark_ok_to_pay'))


# ---------------------------------------------------------------------------
# Role convenience properties
# ---------------------------------------------------------------------------

class RolePropertyTest(TestCase):
    """is_<role> properties delegate to has_role() correctly."""

    def setUp(self):
        self.company = make_company()

    def test_is_supplier_admin_true(self):
        user = make_user(self.company, 'supplier_admin')
        self.assertTrue(user.is_supplier_admin)
        self.assertFalse(user.is_ambassador)

    def test_is_payroll_reviewer_true(self):
        user = make_user(self.company, 'payroll_reviewer')
        self.assertTrue(user.is_payroll_reviewer)
        self.assertFalse(user.is_supplier_admin)

    def test_is_ambassador_manager_true(self):
        user = make_user(self.company, 'ambassador_manager')
        self.assertTrue(user.is_ambassador_manager)


# ---------------------------------------------------------------------------
# create_saas_admin management command
# ---------------------------------------------------------------------------

class CreateSaasAdminCommandTest(TestCase):
    """create_saas_admin command creates a user with the saas_admin role."""

    def test_creates_user_with_saas_admin_role(self):
        # Simulate interactive input
        input_sequence = '\n'.join([
            'cmdtestadmin',   # username
            'cmd@example.com',  # email
            'Cmd',             # first name
            'Admin',           # last name
        ])
        import unittest.mock as mock

        password_mock = mock.patch(
            'getpass.getpass',
            return_value='SecurePass123!'
        )
        input_mock = mock.patch(
            'builtins.input',
            side_effect=[
                'cmdtestadmin',
                'cmd@example.com',
                'Cmd',
                'Admin',
            ]
        )

        with password_mock, input_mock:
            out = StringIO()
            call_command('create_saas_admin', stdout=out)

        self.assertTrue(User.objects.filter(username='cmdtestadmin').exists())
        user = User.objects.get(username='cmdtestadmin')
        self.assertTrue(user.has_role('saas_admin'))
        self.assertTrue(user.is_staff)
        self.assertIsNone(user.company)

    def test_does_not_duplicate_existing_user(self):
        company = make_company()
        existing = make_user(company, 'ambassador', username='existingadmin')

        import unittest.mock as mock
        input_mock = mock.patch('builtins.input', return_value='existingadmin')

        with input_mock:
            out = StringIO()
            call_command('create_saas_admin', stdout=out)

        # Should still be only one user with that username
        self.assertEqual(User.objects.filter(username='existingadmin').count(), 1)


# ---------------------------------------------------------------------------
# Dashboard render test
# ---------------------------------------------------------------------------

class DashboardRenderTest(TestCase):
    def setUp(self):
        self.company = Company.objects.create(
            name='Test Co', slug='test-co'
        )
        self.user = User.objects.create_user(
            username='testadmin',
            password='password',
            company=self.company,
        )
        role = Role.objects.get(
            codename='supplier_admin'
        )
        self.user.roles.add(role)

    def test_dashboard_renders_without_error(self):
        """Dashboard template must render fully
        without TemplateSyntaxError or other
        template errors."""
        self.client.login(
            username='testadmin',
            password='password'
        )
        response = self.client.get(
            reverse('dashboard')
        )
        self.assertEqual(response.status_code, 200)
        # Force full render — catches template errors
        # that status code check alone misses
        self.assertIn(
            b'search',
            response.content.lower()
        )


# ---------------------------------------------------------------------------
# Dashboard search — smart state token tests
# ---------------------------------------------------------------------------

class DashboardSearchTest(TestCase):
    """Smart state-code detection in the dashboard search query."""

    def setUp(self):
        self.company = Company.objects.create(
            name='Search Test Co', slug='search-test-co'
        )
        self.user = User.objects.create_user(
            username='searchtestadmin',
            password='password',
            company=self.company,
        )
        self.user.roles.add(Role.objects.get(codename='supplier_admin'))
        self.client.login(username='searchtestadmin', password='password')

        def acct(name, city, state):
            return Account.objects.create(
                company=self.company,
                name=name,
                city=city,
                state=state,
                state_normalized=state,
            )

        self.nj_total_wine = acct('Total Wine & More', 'Paramus',     'NJ')
        self.ny_total_wine = acct('Total Wine & More', 'Garden City', 'NY')
        self.nj_ridgewood  = acct('Corner Spirits',   'Ridgewood',   'NJ')
        self.ny_ridgewood  = acct('Corner Spirits',   'Ridgewood',   'NY')
        self.maplewood     = acct('Maplewood Wines',  'Maplewood',   'NJ')
        self.ab_store      = acct('AB Fine Wine',     'Newark',      'NJ')

    def _search(self, q):
        resp = self.client.get(reverse('dashboard'), {'q': q})
        self.assertEqual(resp.status_code, 200)
        return list(resp.context['accounts'])

    def _pks(self, results):
        return {a.pk for a in results}

    def test_state_code_alone_returns_only_that_state(self):
        results = self._search('NJ')
        pks = self._pks(results)
        # NJ accounts present
        self.assertIn(self.nj_total_wine.pk, pks)
        self.assertIn(self.nj_ridgewood.pk,  pks)
        self.assertIn(self.maplewood.pk,     pks)
        self.assertIn(self.ab_store.pk,      pks)
        # NY accounts absent
        self.assertNotIn(self.ny_total_wine.pk, pks)
        self.assertNotIn(self.ny_ridgewood.pk,  pks)

    def test_name_and_state_returns_only_matching_state(self):
        results = self._search('Total Wine NJ')
        pks = self._pks(results)
        self.assertIn(self.nj_total_wine.pk,    pks)
        self.assertNotIn(self.ny_total_wine.pk, pks)

    def test_city_and_state_narrows_to_correct_state(self):
        results = self._search('Ridgewood NJ')
        pks = self._pks(results)
        self.assertIn(self.nj_ridgewood.pk,    pks)
        self.assertNotIn(self.ny_ridgewood.pk, pks)

    def test_name_only_no_state_filter_applied(self):
        results = self._search('Maplewood')
        pks = self._pks(results)
        self.assertIn(self.maplewood.pk, pks)
        # Both NJ accounts with Maplewood in name/city; NY accounts absent (no match)
        self.assertNotIn(self.ny_total_wine.pk, pks)

    def test_unknown_two_char_token_falls_back_to_text_search(self):
        # 'AB' is not a valid state in this dataset, so it text-searches name/street/city
        results = self._search('AB')
        pks = self._pks(results)
        self.assertIn(self.ab_store.pk, pks)
