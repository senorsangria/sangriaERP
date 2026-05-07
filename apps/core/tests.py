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


# ---------------------------------------------------------------------------
# Navigation system tests (PART 5)
# ---------------------------------------------------------------------------

class NavSystemTest(TestCase):
    """Tests for apps.core.nav.get_nav_for_user()."""

    def setUp(self):
        self.company = make_company('NavTest Co')
        self.factory = __import__('django.test', fromlist=['RequestFactory']).RequestFactory()

    def _make_request(self, url='/'):
        from django.urls import resolve
        req = self.factory.get(url)
        try:
            req.resolver_match = resolve(url)
        except Exception:
            req.resolver_match = None
        return req

    def test_nav_for_supplier_admin_includes_all_sections(self):
        from apps.core.nav import get_nav_for_user
        user = make_user(self.company, 'supplier_admin', 'nav_sa')
        req = self._make_request('/')
        req.user = user
        sections = get_nav_for_user(user, req)
        keys = [s['key'] for s in sections]
        self.assertIn('main', keys)
        self.assertIn('reports', keys)
        self.assertIn('admin_tools', keys)

    def test_nav_supplier_admin_main_section_items(self):
        from apps.core.nav import get_nav_for_user
        user = make_user(self.company, 'supplier_admin', 'nav_sa2')
        req = self._make_request('/')
        req.user = user
        sections = get_nav_for_user(user, req)
        main = next(s for s in sections if s['key'] == 'main')
        labels = [i['label'] for i in main['items']]
        self.assertIn('Events', labels)
        self.assertIn('Accounts', labels)
        self.assertIn('Distributors', labels)

    def test_nav_supplier_admin_admin_tools_items(self):
        from apps.core.nav import get_nav_for_user
        user = make_user(self.company, 'supplier_admin', 'nav_sa3')
        req = self._make_request('/')
        req.user = user
        sections = get_nav_for_user(user, req)
        at = next(s for s in sections if s['key'] == 'admin_tools')
        labels = [i['label'] for i in at['items']]
        self.assertIn('Users', labels)
        self.assertIn('Brands', labels)
        self.assertIn('Sales Import', labels)
        self.assertIn('Sales Import History', labels)
        self.assertIn('Item Mapping', labels)
        self.assertIn('Historical Event Import', labels)

    def test_nav_for_ambassador_only_main_section(self):
        from apps.core.nav import get_nav_for_user
        user = make_user(self.company, 'ambassador', 'nav_amb')
        req = self._make_request('/')
        req.user = user
        sections = get_nav_for_user(user, req)
        keys = [s['key'] for s in sections]
        self.assertIn('main', keys)
        self.assertNotIn('reports', keys)
        self.assertNotIn('admin_tools', keys)
        main = next(s for s in sections if s['key'] == 'main')
        labels = [i['label'] for i in main['items']]
        self.assertIn('Events', labels)
        self.assertNotIn('Accounts', labels)

    def test_nav_for_sales_manager_includes_main_and_reports_no_admin(self):
        from apps.core.nav import get_nav_for_user
        user = make_user(self.company, 'sales_manager', 'nav_sm')
        req = self._make_request('/')
        req.user = user
        sections = get_nav_for_user(user, req)
        keys = [s['key'] for s in sections]
        self.assertIn('main', keys)
        self.assertIn('reports', keys)
        self.assertNotIn('admin_tools', keys)

    def test_nav_filters_items_by_permission(self):
        from apps.core.nav import get_nav_for_user
        user = User.objects.create_user(
            username='noperms', password='testpass123', company=self.company
        )
        req = self._make_request('/')
        req.user = user
        sections = get_nav_for_user(user, req)
        self.assertEqual(sections, [])

    def test_nav_unauthenticated_returns_empty(self):
        from apps.core.nav import get_nav_for_user
        from django.contrib.auth.models import AnonymousUser
        req = self._make_request('/')
        req.user = AnonymousUser()
        sections = get_nav_for_user(req.user, req)
        self.assertEqual(sections, [])

    def test_nav_active_match_set_on_matching_url(self):
        from apps.core.nav import get_nav_for_user
        user = make_user(self.company, 'supplier_admin', 'nav_active')
        req = self._make_request('/accounts/')
        req.user = user
        sections = get_nav_for_user(user, req)
        main = next(s for s in sections if s['key'] == 'main')
        accounts_item = next(i for i in main['items'] if i['label'] == 'Accounts')
        events_item = next(i for i in main['items'] if i['label'] == 'Events')
        self.assertTrue(accounts_item['is_active'])
        self.assertFalse(events_item['is_active'])

    def test_nav_for_payroll_reviewer_no_admin_tools(self):
        from apps.core.nav import get_nav_for_user
        user = make_user(self.company, 'payroll_reviewer', 'nav_pr')
        req = self._make_request('/')
        req.user = user
        sections = get_nav_for_user(user, req)
        keys = [s['key'] for s in sections]
        self.assertIn('main', keys)
        self.assertNotIn('admin_tools', keys)

    def test_nav_for_distributor_contact_empty(self):
        from apps.core.nav import get_nav_for_user
        user = make_user(self.company, 'distributor_contact', 'nav_dc')
        req = self._make_request('/')
        req.user = user
        sections = get_nav_for_user(user, req)
        # Distributor contact has no permissions — empty nav
        self.assertEqual(sections, [])

    def test_admin_tools_section_is_collapsible(self):
        from apps.core.nav import get_nav_for_user
        user = make_user(self.company, 'supplier_admin', 'nav_coll')
        req = self._make_request('/')
        req.user = user
        sections = get_nav_for_user(user, req)
        at = next(s for s in sections if s['key'] == 'admin_tools')
        self.assertTrue(at['collapsible'])

    def test_reports_section_not_collapsible(self):
        from apps.core.nav import get_nav_for_user
        user = make_user(self.company, 'sales_manager', 'nav_rpt')
        req = self._make_request('/')
        req.user = user
        sections = get_nav_for_user(user, req)
        rpt = next(s for s in sections if s['key'] == 'reports')
        self.assertFalse(rpt['collapsible'])


class AdminToolsStateEndpointTest(TestCase):
    """Tests for the /ui/admin-tools-state/ AJAX endpoint."""

    def setUp(self):
        self.company = make_company('State Co')
        self.user = make_user(self.company, 'supplier_admin', 'state_user')
        self.url = reverse('save_admin_tools_state')

    def test_stores_true_in_session(self):
        self.client.force_login(self.user)
        resp = self.client.post(self.url, {'collapsed': 'true'})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), {'success': True})
        self.assertTrue(self.client.session['admin_tools_collapsed'])

    def test_stores_false_in_session(self):
        self.client.force_login(self.user)
        resp = self.client.post(self.url, {'collapsed': 'false'})
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(self.client.session['admin_tools_collapsed'])

    def test_rejects_invalid_value(self):
        self.client.force_login(self.user)
        resp = self.client.post(self.url, {'collapsed': 'maybe'})
        self.assertEqual(resp.status_code, 400)
        self.assertFalse(resp.json()['success'])

    def test_rejects_get(self):
        self.client.force_login(self.user)
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 405)

    def test_requires_login(self):
        resp = self.client.post(self.url, {'collapsed': 'true'})
        self.assertEqual(resp.status_code, 302)
        self.assertIn('/login/', resp['Location'])


class NewPermissionsTest(TestCase):
    """Tests for the three new admin-tools permissions."""

    def test_new_permissions_exist(self):
        for codename in ('can_view_import_history', 'can_manage_item_mapping',
                         'can_run_historical_event_import'):
            self.assertTrue(
                Permission.objects.filter(codename=codename).exists(),
                msg=f'Permission {codename} does not exist',
            )

    def test_new_permissions_granted_to_supplier_admin_only(self):
        supplier_admin = Role.objects.get(codename='supplier_admin')
        for codename in ('can_view_import_history', 'can_manage_item_mapping',
                         'can_run_historical_event_import'):
            perm = Permission.objects.get(codename=codename)
            # supplier_admin has it
            self.assertIn(perm, supplier_admin.permissions.all(),
                          msg=f'supplier_admin missing {codename}')
            # no other role has it
            other_roles = Role.objects.exclude(codename='supplier_admin').filter(
                permissions=perm
            )
            self.assertFalse(
                other_roles.exists(),
                msg=f'{codename} granted to unexpected roles: '
                    f'{list(other_roles.values_list("codename", flat=True))}',
            )

    def test_supplier_admin_user_has_new_permissions(self):
        company = make_company('Perm Test Co')
        user = make_user(company, 'supplier_admin', 'permtest_sa')
        for codename in ('can_view_import_history', 'can_manage_item_mapping',
                         'can_run_historical_event_import'):
            self.assertTrue(user.has_permission(codename),
                            msg=f'User missing {codename}')

    def test_sales_manager_does_not_have_new_permissions(self):
        company = make_company('Perm Test Co2')
        user = make_user(company, 'sales_manager', 'permtest_sm')
        for codename in ('can_view_import_history', 'can_manage_item_mapping',
                         'can_run_historical_event_import'):
            self.assertFalse(user.has_permission(codename),
                             msg=f'sales_manager unexpectedly has {codename}')


class NavTemplateRenderTest(TestCase):
    """Test that the nav include renders in base.html for logged-in users."""

    def setUp(self):
        self.company = make_company('Render Co')
        self.user = make_user(self.company, 'supplier_admin', 'render_sa')

    def test_nav_renders_dashboard_link(self):
        self.client.force_login(self.user)
        resp = self.client.get(reverse('dashboard'))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Dashboard')

    def test_nav_renders_events_link_for_supplier_admin(self):
        self.client.force_login(self.user)
        resp = self.client.get(reverse('dashboard'))
        self.assertContains(resp, reverse('event_list'))

    def test_nav_renders_admin_tools_toggle(self):
        self.client.force_login(self.user)
        resp = self.client.get(reverse('dashboard'))
        self.assertContains(resp, 'admin_tools')
        self.assertContains(resp, 'Admin Tools')

    def test_nav_does_not_render_admin_tools_for_ambassador(self):
        amb_user = make_user(self.company, 'ambassador', 'render_amb')
        self.client.force_login(amb_user)
        # Ambassadors redirect from dashboard → events; follow to get the rendered page.
        resp = self.client.get(reverse('event_list'), follow=True)
        self.assertEqual(resp.status_code, 200)
        self.assertNotContains(resp, 'Admin Tools')


# ---------------------------------------------------------------------------
# Ambassador Manager — report permission revocation (migration 0012)
# ---------------------------------------------------------------------------

class AmbassadorManagerReportPermissionTest(TestCase):

    def setUp(self):
        self.company = make_company('AM Perm Co')

    def test_ambassador_manager_has_no_report_permissions_after_migration(self):
        """ambassador_manager has none of the three report-viewing permissions."""
        report_perms = [
            'can_view_report_account_sales',
            'can_view_report_item_sales',
            'can_view_report_account_distribution',
        ]
        am_role = Role.objects.get(codename='ambassador_manager')
        am_perm_codenames = set(am_role.permissions.values_list('codename', flat=True))
        for perm in report_perms:
            self.assertNotIn(
                perm, am_perm_codenames,
                f'ambassador_manager should not have {perm}',
            )

    def test_ambassador_manager_menu_has_no_reports_section(self):
        """Ambassador Manager nav renders no Reports section label."""
        from django.test import Client
        am_user = make_user(self.company, 'ambassador_manager', 'am_nav_test')
        client = Client()
        client.force_login(am_user)
        resp = client.get(reverse('event_list'))
        self.assertEqual(resp.status_code, 200)
        self.assertNotContains(resp, 'nav-section">Reports')

    def test_other_roles_still_have_report_permissions(self):
        """Regression: supplier_admin, sales_manager, territory_manager still hold all three."""
        report_perms = [
            'can_view_report_account_sales',
            'can_view_report_item_sales',
            'can_view_report_account_distribution',
        ]
        for role_codename in ('supplier_admin', 'sales_manager', 'territory_manager'):
            role = Role.objects.get(codename=role_codename)
            perm_codenames = set(role.permissions.values_list('codename', flat=True))
            for perm in report_perms:
                self.assertIn(
                    perm, perm_codenames,
                    f'{role_codename} should still have {perm}',
                )
