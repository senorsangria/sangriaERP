"""
Tests for apps.routes — Route and RouteAccount API views.
"""
import json

from django.test import Client, TestCase
from django.urls import reverse

from apps.accounts.models import Account, UserCoverageArea
from apps.catalog.models import Brand, Item
from apps.core.models import Company, User
from apps.core.rbac import Role
from apps.distribution.models import Distributor
from apps.imports.models import ImportBatch
from apps.sales.models import SalesRecord

from .models import Route, RouteAccount


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_company(name='Test Co'):
    return Company.objects.create(name=name)


def make_distributor(company, name='Dist A'):
    return Distributor.objects.create(company=company, name=name)


def make_account(company, distributor, name='Test Liquors'):
    return Account.objects.create(
        company=company,
        distributor=distributor,
        name=name,
        city='Hoboken',
        state='NJ',
        state_normalized='NJ',
        county='Hudson',
        on_off_premise='OFF',
        is_active=True,
    )


def make_user(company, role_codename, username='testuser'):
    user = User.objects.create_user(
        username=username, password='testpass123', company=company,
    )
    role = Role.objects.get(codename=role_codename)
    user.roles.set([role])
    return user


def make_route(company, distributor, user, name='Route A'):
    return Route.objects.create(
        company=company,
        distributor=distributor,
        created_by=user,
        name=name,
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


def make_item(company, name='Test Item', item_code='TST001'):
    brand, _ = Brand.objects.get_or_create(company=company, name='Test Brand')
    item, _ = Item.objects.get_or_create(brand=brand, item_code=item_code, defaults={'name': name})
    return item


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class RouteViewTests(TestCase):

    def setUp(self):
        self.company = make_company()
        self.distributor = make_distributor(self.company)
        self.user = make_user(self.company, 'territory_manager', username='tmuser')
        # Give the user distributor-level coverage so the report can find them
        UserCoverageArea.objects.create(
            company=self.company,
            user=self.user,
            coverage_type=UserCoverageArea.CoverageType.DISTRIBUTOR,
            distributor=self.distributor,
        )
        self.client = Client()
        self.client.login(username='tmuser', password='testpass123')

    def _post_save(self, payload):
        return self.client.post(
            reverse('route_save'),
            data=json.dumps(payload),
            content_type='application/json',
        )

    # ------------------------------------------------------------------
    # test_route_save_new_creates_route
    # ------------------------------------------------------------------
    def test_route_save_new_creates_route(self):
        account = make_account(self.company, self.distributor)
        payload = {
            'account_ids': [account.pk],
            'distributor_id': self.distributor.pk,
            'action': 'new',
            'route_name': 'Morning Route',
        }
        resp = self._post_save(payload)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data['success'])
        self.assertEqual(data['added'], 1)
        self.assertEqual(data['already_in_route'], 0)
        self.assertEqual(data['route_name'], 'Morning Route')

        route = Route.objects.get(name='Morning Route', created_by=self.user)
        self.assertEqual(route.route_accounts.count(), 1)

    # ------------------------------------------------------------------
    # test_route_save_existing_adds_accounts
    # ------------------------------------------------------------------
    def test_route_save_existing_adds_accounts(self):
        route = make_route(self.company, self.distributor, self.user, name='Existing Route')
        account1 = make_account(self.company, self.distributor, name='Acc 1')
        account2 = make_account(self.company, self.distributor, name='Acc 2')

        payload = {
            'account_ids': [account1.pk, account2.pk],
            'distributor_id': self.distributor.pk,
            'action': 'existing',
            'route_id': route.pk,
        }
        resp = self._post_save(payload)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data['success'])
        self.assertEqual(data['added'], 2)
        self.assertEqual(data['already_in_route'], 0)
        self.assertEqual(route.route_accounts.count(), 2)

    # ------------------------------------------------------------------
    # test_route_save_duplicate_skipped
    # ------------------------------------------------------------------
    def test_route_save_duplicate_skipped(self):
        route = make_route(self.company, self.distributor, self.user, name='Dup Route')
        account = make_account(self.company, self.distributor)
        RouteAccount.objects.create(route=route, account=account, position=0)

        payload = {
            'account_ids': [account.pk],
            'distributor_id': self.distributor.pk,
            'action': 'existing',
            'route_id': route.pk,
        }
        resp = self._post_save(payload)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data['success'])
        self.assertEqual(data['added'], 0)
        self.assertEqual(data['already_in_route'], 1)
        self.assertEqual(route.route_accounts.count(), 1)

    # ------------------------------------------------------------------
    # test_route_save_duplicate_name_returns_error
    # ------------------------------------------------------------------
    def test_route_save_duplicate_name_returns_error(self):
        make_route(self.company, self.distributor, self.user, name='Taken Name')
        account = make_account(self.company, self.distributor)

        payload = {
            'account_ids': [account.pk],
            'distributor_id': self.distributor.pk,
            'action': 'new',
            'route_name': 'Taken Name',
        }
        resp = self._post_save(payload)
        self.assertEqual(resp.status_code, 400)
        data = resp.json()
        self.assertIn('error', data)
        self.assertEqual(data['error'], 'A route with this name already exists.')

    # ------------------------------------------------------------------
    # test_route_list_returns_user_routes
    # ------------------------------------------------------------------
    def test_route_list_returns_user_routes(self):
        route1 = make_route(self.company, self.distributor, self.user, name='Route 1')
        route2 = make_route(self.company, self.distributor, self.user, name='Route 2')

        # Another user's route — should NOT appear
        other_user = make_user(self.company, 'territory_manager', username='other')
        make_route(self.company, self.distributor, other_user, name='Other Route')

        resp = self.client.get(
            reverse('route_list'),
            {'distributor_id': self.distributor.pk},
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        ids = [r['id'] for r in data['routes']]
        self.assertIn(route1.pk, ids)
        self.assertIn(route2.pk, ids)
        self.assertEqual(len(ids), 2)

    # ------------------------------------------------------------------
    # test_route_save_requires_permission
    # ------------------------------------------------------------------
    def test_route_save_requires_permission(self):
        # Create user with no can_view_report_account_sales permission
        # Use a role that doesn't have it — we'll create a plain user with no roles
        no_perm_user = User.objects.create_user(
            username='noperm', password='testpass123', company=self.company,
        )
        client = Client()
        client.login(username='noperm', password='testpass123')

        account = make_account(self.company, self.distributor)
        payload = {
            'account_ids': [account.pk],
            'distributor_id': self.distributor.pk,
            'action': 'new',
            'route_name': 'Should Fail',
        }
        resp = client.post(
            reverse('route_save'),
            data=json.dumps(payload),
            content_type='application/json',
        )
        self.assertEqual(resp.status_code, 403)

    # ------------------------------------------------------------------
    # test_report_filtered_by_route
    # ------------------------------------------------------------------
    def test_report_filtered_by_route(self):
        from datetime import date

        account_in = make_account(self.company, self.distributor, name='In Route')
        account_out = make_account(self.company, self.distributor, name='Out Route')

        item = make_item(self.company)
        batch = make_batch(self.company, self.distributor)
        make_sale(self.company, batch, account_in, item, date(2024, 6, 1), quantity=10)
        make_sale(self.company, batch, account_out, item, date(2024, 6, 1), quantity=5)

        route = make_route(self.company, self.distributor, self.user)
        RouteAccount.objects.create(route=route, account=account_in, position=0)

        resp = self.client.get(
            reverse('report_account_sales_by_year'),
            {'route_id': route.pk},
        )
        self.assertEqual(resp.status_code, 200)
        rows = resp.context.get('rows', [])
        account_ids_in_rows = [r['account_id'] for r in rows]
        self.assertIn(account_in.pk, account_ids_in_rows)
        self.assertNotIn(account_out.pk, account_ids_in_rows)
