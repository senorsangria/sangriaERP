"""
Tests for apps.accounts models — AccountItem and AccountItemPriceHistory.

Phase 10.3.2
"""
import datetime

from django.contrib.messages import get_messages
from django.db import IntegrityError
from django.test import Client, TestCase
from django.urls import reverse

from apps.accounts.models import Account, AccountItem, AccountItemPriceHistory
from apps.catalog.models import Brand, Item
from apps.core.models import Company, User
from apps.distribution.models import Distributor
from apps.events.models import Event


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_company(name="Test Co"):
    return Company.objects.create(name=name)


def make_distributor(company, name="Dist A"):
    return Distributor.objects.create(company=company, name=name)


def make_account(company, distributor=None, name="Test Liquors"):
    return Account.objects.create(
        company=company,
        distributor=distributor,
        name=name,
        street="1 Main St",
        city="Hoboken",
        state="NJ",
        address_normalized="1 MAIN ST",
        city_normalized="HOBOKEN",
        state_normalized="NJ",
    )


def make_item(company, item_code="Red0750"):
    brand, _ = Brand.objects.get_or_create(company=company, name="Test Brand")
    return Item.objects.create(brand=brand, name="Test Item", item_code=item_code)


# ---------------------------------------------------------------------------
# AccountItem model tests
# ---------------------------------------------------------------------------

class AccountItemModelTest(TestCase):
    """AccountItem creation, __str__, and unique constraint."""

    def setUp(self):
        self.company = make_company()
        self.distributor = make_distributor(self.company)
        self.account = make_account(self.company, self.distributor)
        self.item = make_item(self.company)

    def test_create_account_item(self):
        today = datetime.date.today()
        ai = AccountItem.objects.create(
            account=self.account,
            item=self.item,
            date_first_associated=today,
        )
        self.assertEqual(ai.account, self.account)
        self.assertEqual(ai.item, self.item)
        self.assertEqual(ai.date_first_associated, today)
        self.assertIsNone(ai.current_price)

    def test_str(self):
        ai = AccountItem.objects.create(
            account=self.account,
            item=self.item,
            date_first_associated=datetime.date.today(),
        )
        self.assertIn(str(self.account), str(ai))
        self.assertIn(str(self.item), str(ai))

    def test_unique_constraint_account_item(self):
        today = datetime.date.today()
        AccountItem.objects.create(
            account=self.account,
            item=self.item,
            date_first_associated=today,
        )
        with self.assertRaises(IntegrityError):
            AccountItem.objects.create(
                account=self.account,
                item=self.item,
                date_first_associated=today,
            )

    def test_get_or_create_does_not_overwrite_date(self):
        original_date = datetime.date(2024, 1, 15)
        ai, created = AccountItem.objects.get_or_create(
            account=self.account,
            item=self.item,
            defaults={'date_first_associated': original_date},
        )
        self.assertTrue(created)

        # Second call with a newer date — date must NOT be updated
        new_date = datetime.date.today()
        ai2, created2 = AccountItem.objects.get_or_create(
            account=self.account,
            item=self.item,
            defaults={'date_first_associated': new_date},
        )
        self.assertFalse(created2)
        self.assertEqual(ai2.date_first_associated, original_date)

    def test_current_price_optional(self):
        ai = AccountItem.objects.create(
            account=self.account,
            item=self.item,
            date_first_associated=datetime.date.today(),
            current_price=None,
        )
        self.assertIsNone(ai.current_price)

    def test_different_items_same_account_allowed(self):
        item2 = make_item(self.company, item_code="Wht0750")
        today = datetime.date.today()
        ai1 = AccountItem.objects.create(
            account=self.account, item=self.item, date_first_associated=today,
        )
        ai2 = AccountItem.objects.create(
            account=self.account, item=item2, date_first_associated=today,
        )
        self.assertNotEqual(ai1.pk, ai2.pk)

    def test_same_item_different_accounts_allowed(self):
        account2 = make_account(self.company, self.distributor, name="Other Store")
        today = datetime.date.today()
        ai1 = AccountItem.objects.create(
            account=self.account, item=self.item, date_first_associated=today,
        )
        ai2 = AccountItem.objects.create(
            account=account2, item=self.item, date_first_associated=today,
        )
        self.assertNotEqual(ai1.pk, ai2.pk)


# ---------------------------------------------------------------------------
# AccountItemPriceHistory model tests
# ---------------------------------------------------------------------------

class AccountItemPriceHistoryTest(TestCase):
    """AccountItemPriceHistory creation and __str__."""

    def setUp(self):
        self.company = make_company()
        self.distributor = make_distributor(self.company)
        self.account = make_account(self.company, self.distributor)
        self.item = make_item(self.company)
        self.account_item = AccountItem.objects.create(
            account=self.account,
            item=self.item,
            date_first_associated=datetime.date.today(),
        )
        from apps.core.rbac import Role
        self.user = User.objects.create_user(
            username="testuser",
            password="testpass123",
            company=self.company,
        )
        self.user.roles.set([Role.objects.get(codename='supplier_admin')])

    def test_create_price_history_with_user(self):
        ph = AccountItemPriceHistory.objects.create(
            account_item=self.account_item,
            price="12.99",
            recorded_by=self.user,
        )
        self.assertEqual(ph.account_item, self.account_item)
        self.assertEqual(str(ph.price), "12.99")
        self.assertEqual(ph.recorded_by, self.user)
        self.assertIsNotNone(ph.recorded_at)

    def test_create_price_history_system_set_null_user(self):
        ph = AccountItemPriceHistory.objects.create(
            account_item=self.account_item,
            price="9.99",
            recorded_by=None,
        )
        self.assertIsNone(ph.recorded_by)

    def test_str_contains_price(self):
        ph = AccountItemPriceHistory.objects.create(
            account_item=self.account_item,
            price="14.49",
        )
        self.assertIn("14.49", str(ph))

    def test_multiple_price_history_entries_allowed(self):
        AccountItemPriceHistory.objects.create(
            account_item=self.account_item, price="10.00",
        )
        AccountItemPriceHistory.objects.create(
            account_item=self.account_item, price="11.00",
        )
        self.assertEqual(self.account_item.price_history.count(), 2)


# ---------------------------------------------------------------------------
# Phase 10.3.3 — Account delete view
# ---------------------------------------------------------------------------

from django.test import Client
from django.urls import reverse


def make_user(company, role_codename, username="testuser"):
    from apps.core.rbac import Role
    user = User.objects.create_user(
        username=username,
        password="testpass123",
        company=company,
    )
    user.roles.set([Role.objects.get(codename=role_codename)])
    return user


class AccountDeleteTest(TestCase):
    """account_delete: only manual accounts with no associated data can be deleted."""

    def setUp(self):
        self.company = make_company()
        self.admin = make_user(self.company, 'supplier_admin', "admin")
        self.client = Client()
        self.client.login(username="admin", password="testpass123")

    def test_delete_manual_account_with_no_data_succeeds(self):
        account = Account.objects.create(
            company=self.company, name="Delete Me",
            street="1 Main", city="Newark", state="NJ",
            auto_created=False,
        )
        pk = account.pk
        self.client.post(reverse("account_delete", args=[pk]))
        self.assertFalse(Account.objects.filter(pk=pk).exists())

    def test_delete_redirects_to_account_list_on_success(self):
        account = Account.objects.create(
            company=self.company, name="Delete Me",
            street="1 Main", city="Newark", state="NJ",
            auto_created=False,
        )
        resp = self.client.post(reverse("account_delete", args=[account.pk]), follow=True)
        self.assertRedirects(resp, reverse("account_list"))

    def test_delete_blocked_for_imported_account(self):
        account = Account.objects.create(
            company=self.company, name="Imported",
            street="2 Oak", city="Newark", state="NJ",
            auto_created=True,
        )
        pk = account.pk
        self.client.post(reverse("account_delete", args=[pk]))
        self.assertTrue(Account.objects.filter(pk=pk).exists())

    def test_delete_blocked_with_event(self):
        account = Account.objects.create(
            company=self.company, name="Has Event",
            street="3 Elm", city="Newark", state="NJ",
            auto_created=False,
        )
        Event.objects.create(
            company=self.company, account=account,
            event_type=Event.EventType.TASTING,
            created_by=self.admin,
        )
        pk = account.pk
        self.client.post(reverse("account_delete", args=[pk]))
        self.assertTrue(Account.objects.filter(pk=pk).exists())

    def test_delete_error_message_lists_blocking_data(self):
        account = Account.objects.create(
            company=self.company, name="Has Event",
            street="3 Elm", city="Newark", state="NJ",
            auto_created=False,
        )
        Event.objects.create(
            company=self.company, account=account,
            event_type=Event.EventType.TASTING,
            created_by=self.admin,
        )
        resp = self.client.post(reverse("account_delete", args=[account.pk]), follow=True)
        self.assertContains(resp, "cannot be deleted")
        self.assertContains(resp, "deactivate")


# ---------------------------------------------------------------------------
# Phase 10.3.3 — Account deactivate/reactivate for all accounts
# ---------------------------------------------------------------------------

class AccountToggleAllAccountsTest(TestCase):
    """account_toggle works for both manual and imported accounts."""

    def setUp(self):
        self.company = make_company()
        self.admin = make_user(self.company, 'supplier_admin', "admin")
        self.client = Client()
        self.client.login(username="admin", password="testpass123")

    def test_deactivate_manual_account(self):
        account = Account.objects.create(
            company=self.company, name="Manual",
            street="1 Main", city="Newark", state="NJ",
            auto_created=False, is_active=True,
        )
        self.client.post(reverse("account_toggle", args=[account.pk]))
        account.refresh_from_db()
        self.assertFalse(account.is_active)

    def test_reactivate_manual_account(self):
        account = Account.objects.create(
            company=self.company, name="Manual",
            street="1 Main", city="Newark", state="NJ",
            auto_created=False, is_active=False,
        )
        self.client.post(reverse("account_toggle", args=[account.pk]))
        account.refresh_from_db()
        self.assertTrue(account.is_active)

    def test_deactivate_imported_account(self):
        """Imported accounts can now be deactivated."""
        account = Account.objects.create(
            company=self.company, name="Imported",
            street="2 Oak", city="Newark", state="NJ",
            auto_created=True, is_active=True,
        )
        self.client.post(reverse("account_toggle", args=[account.pk]))
        account.refresh_from_db()
        self.assertFalse(account.is_active)

    def test_reactivate_imported_account(self):
        """Imported accounts can be reactivated."""
        account = Account.objects.create(
            company=self.company, name="Imported",
            street="2 Oak", city="Newark", state="NJ",
            auto_created=True, is_active=False,
        )
        self.client.post(reverse("account_toggle", args=[account.pk]))
        account.refresh_from_db()
        self.assertTrue(account.is_active)


# ---------------------------------------------------------------------------
# Phase 10.3.3 — Account detail items display
# ---------------------------------------------------------------------------

class AccountDetailItemsDisplayTest(TestCase):
    """account_detail passes items_by_brand to the template."""

    def setUp(self):
        self.company = make_company()
        self.admin = make_user(self.company, 'supplier_admin', "admin")
        self.account = make_account(self.company)
        self.client = Client()
        self.client.login(username="admin", password="testpass123")

    def test_items_by_brand_in_context(self):
        item = make_item(self.company)
        AccountItem.objects.create(
            account=self.account, item=item,
            date_first_associated=datetime.date.today(),
        )
        resp = self.client.get(reverse("account_detail", args=[self.account.pk]))
        self.assertIn("items_by_brand", resp.context)
        self.assertEqual(len(resp.context["items_by_brand"]), 1)

    def test_items_by_brand_empty_when_no_items(self):
        resp = self.client.get(reverse("account_detail", args=[self.account.pk]))
        self.assertIn("items_by_brand", resp.context)
        self.assertEqual(len(resp.context["items_by_brand"]), 0)

    def test_item_name_in_response(self):
        item = make_item(self.company)
        AccountItem.objects.create(
            account=self.account, item=item,
            date_first_associated=datetime.date.today(),
        )
        resp = self.client.get(reverse("account_detail", args=[self.account.pk]))
        self.assertContains(resp, item.name)

    def test_empty_state_shown_when_no_items(self):
        resp = self.client.get(reverse("account_detail", args=[self.account.pk]))
        self.assertContains(resp, "No items have been associated")


# ---------------------------------------------------------------------------
# ajax_accounts_search — multi-word search
# ---------------------------------------------------------------------------

class AjaxAccountsSearchTest(TestCase):
    """
    ajax_accounts_search: multi-word query splits on whitespace and requires
    ALL terms to match at least one of name/street/city/state (AND across
    terms, OR within each term).
    """

    def setUp(self):
        self.company = make_company()
        # Supplier Admin sees all company accounts — no coverage area setup needed
        from apps.core.rbac import Role
        self.user = User.objects.create_user(
            username='sadmin', password='testpass123',
            company=self.company,
        )
        self.user.roles.set([Role.objects.get(codename='supplier_admin')])
        self.client = Client()
        self.client.login(username='sadmin', password='testpass123')
        self.url = reverse('ajax_accounts_search')

        Account.objects.create(
            company=self.company, name='BuyRite Wine & Spirits',
            street='10 Bergen Ave', city='Kearny', state='NJ',
            state_normalized='NJ',
        )
        Account.objects.create(
            company=self.company, name='BuyRite Liquors',
            street='50 Market St', city='Newark', state='NJ',
            state_normalized='NJ',
        )
        Account.objects.create(
            company=self.company, name='Crown Wine & Spirits',
            street='200 Broad St', city='Newark', state='NJ',
            state_normalized='NJ',
        )

    def _get(self, q):
        return self.client.get(
            self.url, {'q': q},
            HTTP_X_REQUESTED_WITH='XMLHttpRequest',
        )

    def _names(self, resp):
        return {a['name'] for a in resp.json()['accounts']}

    def test_short_query_returns_empty(self):
        resp = self._get('B')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()['accounts'], [])

    def test_single_word_name_match(self):
        names = self._names(self._get('BuyRite'))
        self.assertIn('BuyRite Wine & Spirits', names)
        self.assertIn('BuyRite Liquors', names)
        self.assertNotIn('Crown Wine & Spirits', names)

    def test_single_word_city_match(self):
        names = self._names(self._get('Kearny'))
        self.assertIn('BuyRite Wine & Spirits', names)
        self.assertNotIn('BuyRite Liquors', names)
        self.assertNotIn('Crown Wine & Spirits', names)

    def test_multiword_name_and_city(self):
        """'BuyRite Kearny' must match only the account whose name contains
        'BuyRite' AND whose city contains 'Kearny'."""
        names = self._names(self._get('BuyRite Kearny'))
        self.assertEqual(names, {'BuyRite Wine & Spirits'})

    def test_multiword_both_in_name(self):
        """'BuyRite Liquors' matches the account where both words appear in name."""
        names = self._names(self._get('BuyRite Liquors'))
        self.assertEqual(names, {'BuyRite Liquors'})

    def test_multiword_no_match(self):
        """A term that matches nothing yields no results."""
        names = self._names(self._get('BuyRite Springfield'))
        self.assertEqual(names, set())

    def test_multiword_city_shared_across_two_accounts(self):
        """'Crown Newark' should return only Crown (name=Crown, city=Newark)."""
        names = self._names(self._get('Crown Newark'))
        self.assertEqual(names, {'Crown Wine & Spirits'})


# ---------------------------------------------------------------------------
# Phase 10.6 — Account bulk delete
# ---------------------------------------------------------------------------

class AccountBulkDeleteTest(TestCase):
    """account_bulk_delete: delete accounts with no data; deactivate those with data."""

    def setUp(self):
        self.company = make_company('Bulk Delete Co')

        self.sa_user = make_user(self.company, 'supplier_admin', 'sa_bulk')
        self.amb_user = make_user(self.company, 'ambassador', 'amb_bulk')

        self.client = Client()

    def _make_account(self, name='Test Account'):
        return Account.objects.create(
            company=self.company,
            name=name,
            street='1 Test St',
            city='Testville',
            state='NJ',
            is_active=True,
        )

    def _post_bulk_delete(self, pks):
        return self.client.post(
            reverse('account_bulk_delete'),
            {'account_pks': pks},
        )

    def test_non_supplier_admin_gets_403(self):
        """Ambassador cannot access bulk delete."""
        self.client.force_login(self.amb_user)
        account = self._make_account()
        resp = self._post_bulk_delete([account.pk])
        self.assertEqual(resp.status_code, 403)
        self.assertTrue(Account.objects.filter(pk=account.pk).exists())

    def test_supplier_admin_can_delete_account_with_no_associations(self):
        """Accounts with no associations are permanently deleted."""
        self.client.force_login(self.sa_user)
        account = self._make_account('No-Data Store')
        resp = self._post_bulk_delete([account.pk])
        self.assertRedirects(resp, reverse('account_list'))
        self.assertFalse(Account.objects.filter(pk=account.pk).exists())

    def test_account_with_associations_is_deactivated_not_deleted(self):
        """Accounts with associated events are deactivated, not deleted."""
        self.client.force_login(self.sa_user)
        account = self._make_account('Has-Events Store')

        Event.objects.create(
            company=self.company,
            created_by=self.sa_user,
            event_manager=self.sa_user,
            event_type=Event.EventType.TASTING,
            status=Event.Status.DRAFT,
            account=account,
        )

        resp = self._post_bulk_delete([account.pk])
        self.assertRedirects(resp, reverse('account_list'))

        account.refresh_from_db()
        self.assertTrue(Account.objects.filter(pk=account.pk).exists())
        self.assertFalse(account.is_active)

    def test_success_message_shows_correct_counts(self):
        """Success message reports deleted vs deactivated counts."""
        self.client.force_login(self.sa_user)

        clean_account = self._make_account('Clean Store')
        dirty_account = self._make_account('Dirty Store')

        Event.objects.create(
            company=self.company,
            created_by=self.sa_user,
            event_manager=self.sa_user,
            event_type=Event.EventType.TASTING,
            status=Event.Status.DRAFT,
            account=dirty_account,
        )

        resp = self._post_bulk_delete([clean_account.pk, dirty_account.pk])
        msgs = [str(m) for m in get_messages(resp.wsgi_request)]
        self.assertTrue(any('deleted' in m for m in msgs))
        self.assertTrue(any('deactivated' in m for m in msgs))

    def test_no_pks_selected_shows_warning(self):
        """Posting with no PKs shows a warning message and redirects."""
        self.client.force_login(self.sa_user)
        resp = self._post_bulk_delete([])
        self.assertRedirects(resp, reverse('account_list'))


# ---------------------------------------------------------------------------
# Coverage area restructure — distributor required on every row
# ---------------------------------------------------------------------------

from apps.accounts.models import UserCoverageArea
from apps.accounts.utils import get_distributors_for_user


class GetDistributorsForUserTest(TestCase):
    """get_distributors_for_user() returns correct distributor sets per role."""

    def setUp(self):
        from apps.core.rbac import Role
        self.company = make_company('Dist Test Co')
        self.dist_a = make_distributor(self.company, 'Peerless Beverage')
        self.dist_b = make_distributor(self.company, 'Harbor Distributing')

        def _make(role, username):
            u = User.objects.create_user(username=username, password='testpass123',
                                         company=self.company)
            u.roles.set([Role.objects.get(codename=role)])
            return u

        self.admin = _make('supplier_admin', 'sa')
        self.tm = _make('territory_manager', 'tm')

    def test_supplier_admin_gets_all_company_distributors(self):
        result = list(get_distributors_for_user(self.admin))
        self.assertIn(self.dist_a, result)
        self.assertIn(self.dist_b, result)

    def test_user_with_no_coverage_areas_gets_empty_queryset(self):
        result = list(get_distributors_for_user(self.tm))
        self.assertEqual(result, [])

    def test_user_gets_only_their_assigned_distributors(self):
        UserCoverageArea.objects.create(
            user=self.tm, company=self.company,
            coverage_type=UserCoverageArea.CoverageType.DISTRIBUTOR,
            distributor=self.dist_a,
        )
        result = list(get_distributors_for_user(self.tm))
        self.assertIn(self.dist_a, result)
        self.assertNotIn(self.dist_b, result)

    def test_multiple_coverage_types_return_distinct_distributors(self):
        """Two coverage areas under the same distributor yield only one entry."""
        account = make_account(self.company, self.dist_a)
        UserCoverageArea.objects.create(
            user=self.tm, company=self.company,
            coverage_type=UserCoverageArea.CoverageType.DISTRIBUTOR,
            distributor=self.dist_a,
        )
        UserCoverageArea.objects.create(
            user=self.tm, company=self.company,
            coverage_type=UserCoverageArea.CoverageType.ACCOUNT,
            distributor=self.dist_a,
            account=account,
        )
        result = list(get_distributors_for_user(self.tm))
        self.assertEqual(result.count(self.dist_a), 1)


class CoverageAreaAddViewTest(TestCase):
    """coverage_area_add: distributor always required; sets correctly on all types."""

    def setUp(self):
        from apps.core.rbac import Role
        self.company = make_company('CA Add Test Co')
        self.distributor = make_distributor(self.company, 'Test Dist')

        def _make(role, username):
            u = User.objects.create_user(username=username, password='testpass123',
                                         company=self.company)
            u.roles.set([Role.objects.get(codename=role)])
            return u

        self.admin = _make('supplier_admin', 'sa_ca')
        self.target = _make('territory_manager', 'tm_ca')
        self.client = Client()
        self.client.login(username='sa_ca', password='testpass123')
        self.url = reverse('coverage_area_add', args=[self.target.pk])

    def _post(self, data):
        return self.client.post(self.url, data,
                                HTTP_X_REQUESTED_WITH='XMLHttpRequest')

    # ── Missing distributor ──────────────────────────────────────────────────

    def test_missing_distributor_returns_error_for_distributor_type(self):
        resp = self._post({'coverage_type': 'distributor'})
        self.assertEqual(resp.status_code, 400)
        self.assertIn('distributor', resp.json()['error'].lower())

    def test_missing_distributor_returns_error_for_county_type(self):
        resp = self._post({'coverage_type': 'county', 'state': 'NJ', 'county': 'Hudson'})
        self.assertEqual(resp.status_code, 400)
        self.assertIn('distributor', resp.json()['error'].lower())

    def test_missing_distributor_returns_error_for_city_type(self):
        resp = self._post({'coverage_type': 'city', 'state': 'NJ', 'city': 'Hoboken'})
        self.assertEqual(resp.status_code, 400)
        self.assertIn('distributor', resp.json()['error'].lower())

    def test_missing_distributor_returns_error_for_account_type(self):
        account = make_account(self.company, self.distributor)
        resp = self._post({'coverage_type': 'account', 'account_id': account.pk})
        self.assertEqual(resp.status_code, 400)
        self.assertIn('distributor', resp.json()['error'].lower())

    # ── Distributor correctly stored on all types ────────────────────────────

    def test_distributor_type_stores_distributor(self):
        resp = self._post({'coverage_type': 'distributor',
                           'distributor_id': self.distributor.pk})
        self.assertEqual(resp.status_code, 200)
        ca = UserCoverageArea.objects.get(user=self.target,
                                          coverage_type='distributor')
        self.assertEqual(ca.distributor, self.distributor)

    def test_county_type_stores_distributor(self):
        # Create an account so state can be derived from the accounts table.
        Account.objects.create(
            company=self.company, distributor=self.distributor,
            name='Hudson Store', street='1 Main St',
            city='Jersey City', state='NJ',
            county='Hudson',
            address_normalized='1 MAIN ST', city_normalized='JERSEY CITY',
            state_normalized='NJ',
        )
        resp = self._post({'coverage_type': 'county', 'county': 'Hudson',
                           'distributor_id': self.distributor.pk})
        self.assertEqual(resp.status_code, 200)
        ca = UserCoverageArea.objects.get(user=self.target, coverage_type='county')
        self.assertEqual(ca.distributor, self.distributor)
        self.assertEqual(ca.county, 'Hudson')
        self.assertEqual(ca.state, 'NJ')

    def test_city_type_stores_distributor(self):
        # Create an account so state can be derived from the accounts table.
        account = make_account(self.company, self.distributor)  # city='Hoboken', state_normalized='NJ'
        resp = self._post({'coverage_type': 'city', 'city': 'Hoboken',
                           'distributor_id': self.distributor.pk})
        self.assertEqual(resp.status_code, 200)
        ca = UserCoverageArea.objects.get(user=self.target, coverage_type='city')
        self.assertEqual(ca.distributor, self.distributor)
        self.assertEqual(ca.city, 'Hoboken')
        self.assertEqual(ca.state, 'NJ')

    def test_account_type_stores_distributor(self):
        account = make_account(self.company, self.distributor)
        resp = self._post({'coverage_type': 'account',
                           'account_id': account.pk,
                           'distributor_id': self.distributor.pk})
        self.assertEqual(resp.status_code, 200)
        ca = UserCoverageArea.objects.get(user=self.target, coverage_type='account')
        self.assertEqual(ca.distributor, self.distributor)
        self.assertEqual(ca.account, account)


# ---------------------------------------------------------------------------
# Ambassador Manager account list scope
# ---------------------------------------------------------------------------

class AmbassadorManagerAccountListTest(TestCase):
    """Ambassador Manager only sees accounts linked to their own events."""

    def setUp(self):
        from apps.core.rbac import Role

        self.company = make_company('AM List Test Co')
        self.distributor = make_distributor(self.company, 'Test Dist AM')

        def _make(role, username):
            u = User.objects.create_user(
                username=username, password='testpass123', company=self.company,
            )
            u.roles.set([Role.objects.get(codename=role)])
            return u

        self.am = _make('ambassador_manager', 'am_list')
        self.other = _make('ambassador', 'amb_list')

        # Account linked to AM via event (am is ambassador)
        self.am_account = make_account(self.company, self.distributor, 'AM Account')
        # Account not linked to AM
        self.other_account = make_account(self.company, self.distributor, 'Other Account')

        Event.objects.create(
            company=self.company,
            event_type=Event.EventType.TASTING,
            account=self.am_account,
            ambassador=self.am,
            created_by=self.am,
        )

        self.client = Client()
        self.client.login(username='am_list', password='testpass123')

    def test_ambassador_manager_account_list_only_own(self):
        resp = self.client.get(reverse('account_list'))
        self.assertEqual(resp.status_code, 200)
        accounts = list(resp.context['accounts'])
        self.assertIn(self.am_account, accounts)
        self.assertNotIn(self.other_account, accounts)

    def test_ambassador_manager_inactive_filter(self):
        """AM with active_status=inactive sees only inactive accounts linked to
        their events, not active ones and not inactive accounts from other events."""
        # Make am_account inactive; other_account stays active
        self.am_account.is_active = False
        self.am_account.save()

        # An unrelated inactive account (no link to AM)
        unrelated_inactive = make_account(self.company, self.distributor, 'Unrelated Inactive')
        unrelated_inactive.is_active = False
        unrelated_inactive.save()

        resp = self.client.get(reverse('account_list'), {'active_status': 'inactive'})
        self.assertEqual(resp.status_code, 200)
        accounts = list(resp.context['accounts'])

        self.assertIn(self.am_account, accounts)          # inactive + linked to AM
        self.assertNotIn(self.other_account, accounts)    # active, not linked to AM
        self.assertNotIn(unrelated_inactive, accounts)    # inactive but not linked to AM


# ---------------------------------------------------------------------------
# Combined Account Detail page
# ---------------------------------------------------------------------------

class AccountDetailCombinedTest(TestCase):
    """Tests for the account_detail_combined view."""

    def setUp(self):
        from apps.core.rbac import Role

        self.company = make_company('Combined Test Co')
        self.other_company = make_company('Other Co')
        self.distributor = make_distributor(self.company, 'Test Dist')

        def _make(role, username):
            u = User.objects.create_user(
                username=username, password='testpass123', company=self.company,
            )
            u.roles.set([Role.objects.get(codename=role)])
            return u

        self.admin = _make('supplier_admin', 'cdc_admin')
        self.no_role_user = User.objects.create_user(
            username='cdc_norole', password='testpass123', company=self.company,
        )
        # ambassador_manager has can_view_accounts but not can_view_report_account_sales
        self.am = _make('ambassador_manager', 'cdc_am')

        self.account = make_account(self.company, self.distributor, 'Combined Test Store')
        self.other_account = make_account(self.other_company, None, 'Other Store')

        self.url = reverse('account_detail_combined', args=[self.account.pk])

    def test_combined_requires_can_view_accounts(self):
        """User without can_view_accounts is redirected to dashboard."""
        self.client.login(username='cdc_norole', password='testpass123')
        resp = self.client.get(self.url)
        self.assertRedirects(resp, reverse('dashboard'), fetch_redirect_response=False)

    def test_combined_defaults_to_details_tab(self):
        """No tab param → active_tab='details' in context."""
        self.client.login(username='cdc_admin', password='testpass123')
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.context['active_tab'], 'details')

    def test_combined_sales_tab_requires_permission(self):
        """User without can_view_report_account_sales is redirected to details tab."""
        self.client.login(username='cdc_am', password='testpass123')
        resp = self.client.get(self.url + '?tab=sales')
        # Should redirect to details tab, not render the sales tab
        self.assertEqual(resp.status_code, 302)
        self.assertIn('tab=details', resp['Location'])

    def test_combined_return_to_in_context(self):
        """return_to param is passed through to template context."""
        self.client.login(username='cdc_admin', password='testpass123')
        resp = self.client.get(self.url + '?return_to=report')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.context['return_to'], 'report')

    def test_combined_404_for_wrong_company(self):
        """Account belonging to a different company returns 404."""
        self.client.login(username='cdc_admin', password='testpass123')
        url = reverse('account_detail_combined', args=[self.other_account.pk])
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 404)


# ---------------------------------------------------------------------------
# AccountForm: distributor scope and required fields
# ---------------------------------------------------------------------------

from apps.accounts.forms import AccountForm
from apps.accounts.utils import get_distributors_for_user


class AccountFormScopeAndRequiredTest(TestCase):
    """AccountForm: distributor scoped to user; required fields enforced."""

    def setUp(self):
        from apps.core.rbac import Role
        from apps.accounts.models import UserCoverageArea

        self.company = make_company('Form Test Co')
        self.dist_a = make_distributor(self.company, 'Dist Alpha')
        self.dist_b = make_distributor(self.company, 'Dist Beta')

        def _make(role, username):
            u = User.objects.create_user(
                username=username, password='testpass123', company=self.company,
            )
            u.roles.set([Role.objects.get(codename=role)])
            return u

        self.admin = _make('supplier_admin', 'sa_form')
        self.am = _make('ambassador_manager', 'am_form')

        # Give AM coverage area for dist_a only
        UserCoverageArea.objects.create(
            user=self.am, company=self.company,
            coverage_type=UserCoverageArea.CoverageType.DISTRIBUTOR,
            distributor=self.dist_a,
        )

    def _valid_data(self, distributor):
        return {
            'name': 'Test Store',
            'city': 'Hoboken',
            'state': 'NJ',
            'county': 'Hudson',
            'on_off_premise': 'OFF',
            'distributor': distributor.pk,
            'is_active': True,
        }

    def test_account_create_distributor_scoped_to_user(self):
        form = AccountForm(company=self.company, user=self.am)
        expected = list(get_distributors_for_user(self.am))
        actual = list(form.fields['distributor'].queryset)
        self.assertEqual(actual, expected)
        self.assertIn(self.dist_a, actual)
        self.assertNotIn(self.dist_b, actual)

    def test_account_create_requires_distributor(self):
        data = self._valid_data(self.dist_a)
        del data['distributor']
        form = AccountForm(data=data, company=self.company, user=self.admin)
        self.assertFalse(form.is_valid())
        self.assertIn('distributor', form.errors)

    def test_account_create_requires_on_off(self):
        data = self._valid_data(self.dist_a)
        del data['on_off_premise']
        form = AccountForm(data=data, company=self.company, user=self.admin)
        self.assertFalse(form.is_valid())
        self.assertIn('on_off_premise', form.errors)


# ---------------------------------------------------------------------------
# AccountContact API tests
# ---------------------------------------------------------------------------

import json as _json
from apps.accounts.models import AccountContact


class ContactAPITest(TestCase):
    """Tests for the AccountContact CRUD API views."""

    def setUp(self):
        self.company = make_company('Contact Test Co')
        self.admin   = make_user(self.company, 'supplier_admin', username='ct_admin')
        self.amb     = make_user(self.company, 'ambassador',     username='ct_amb')
        self.dist    = make_distributor(self.company, 'CT Dist')
        self.account = make_account(self.company, self.dist, 'CT Store')

        self.client = Client()
        self.client.login(username='ct_admin', password='testpass123')

    def _post(self, url, data):
        return self.client.post(
            url,
            data=_json.dumps(data),
            content_type='application/json',
            HTTP_X_REQUESTED_WITH='XMLHttpRequest',
        )

    def test_contact_create(self):
        """POST creates an AccountContact with correct fields."""
        url = reverse('contact_create', args=[self.account.pk])
        resp = self._post(url, {
            'name': 'Jane Smith',
            'title': 'manager',
            'phone': '555-1234',
            'email': 'jane@example.com',
            'note': 'Great contact',
            'is_tasting_contact': True,
        })
        self.assertEqual(resp.status_code, 200)
        data = _json.loads(resp.content)
        self.assertTrue(data['success'])
        self.assertEqual(data['contact']['name'], 'Jane Smith')
        self.assertEqual(data['contact']['title'], 'manager')
        self.assertEqual(data['contact']['phone'], '555-1234')
        self.assertEqual(data['contact']['email'], 'jane@example.com')
        self.assertTrue(data['contact']['is_tasting_contact'])
        self.assertEqual(AccountContact.objects.filter(account=self.account).count(), 1)

    def test_contact_create_requires_name(self):
        """POST without name returns success=False with error message."""
        url = reverse('contact_create', args=[self.account.pk])
        resp = self._post(url, {'name': '', 'title': 'other'})
        self.assertEqual(resp.status_code, 200)
        data = _json.loads(resp.content)
        self.assertFalse(data['success'])
        self.assertIn('error', data)
        self.assertEqual(AccountContact.objects.filter(account=self.account).count(), 0)

    def test_contact_update(self):
        """POST to update endpoint updates existing contact."""
        contact = AccountContact.objects.create(
            account=self.account, name='Old Name', title='other',
        )
        url = reverse('contact_update', args=[self.account.pk, contact.pk])
        resp = self._post(url, {'name': 'New Name', 'title': 'owner', 'is_tasting_contact': False})
        self.assertEqual(resp.status_code, 200)
        data = _json.loads(resp.content)
        self.assertTrue(data['success'])
        contact.refresh_from_db()
        self.assertEqual(contact.name, 'New Name')
        self.assertEqual(contact.title, 'owner')

    def test_contact_delete(self):
        """POST to delete endpoint removes the contact."""
        contact = AccountContact.objects.create(
            account=self.account, name='To Delete', title='other',
        )
        url = reverse('contact_delete', args=[self.account.pk, contact.pk])
        resp = self.client.post(
            url, HTTP_X_REQUESTED_WITH='XMLHttpRequest',
        )
        self.assertEqual(resp.status_code, 200)
        data = _json.loads(resp.content)
        self.assertTrue(data['success'])
        self.assertFalse(AccountContact.objects.filter(pk=contact.pk).exists())

    def test_contact_list_scoped_to_account(self):
        """GET returns only contacts for the requested account."""
        other_account = make_account(self.company, self.dist, 'Other Store')
        AccountContact.objects.create(account=self.account, name='Mine', title='other')
        AccountContact.objects.create(account=other_account, name='Theirs', title='other')

        url = reverse('contact_list', args=[self.account.pk])
        resp = self.client.get(url, HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertEqual(resp.status_code, 200)
        data = _json.loads(resp.content)
        names = [c['name'] for c in data['contacts']]
        self.assertIn('Mine', names)
        self.assertNotIn('Theirs', names)

    def test_contact_requires_permission(self):
        """User without can_manage_contacts gets 403 on create."""
        no_perm_user = make_user(self.company, 'ambassador', username='no_perm')
        c = Client()
        c.login(username='no_perm', password='testpass123')
        url = reverse('contact_create', args=[self.account.pk])
        resp = c.post(
            url,
            data=_json.dumps({'name': 'Test', 'title': 'other'}),
            content_type='application/json',
            HTTP_X_REQUESTED_WITH='XMLHttpRequest',
        )
        self.assertEqual(resp.status_code, 403)

    def test_ambassador_cannot_manage_contacts(self):
        """Ambassador cannot create, update, or delete contacts."""
        contact = AccountContact.objects.create(
            account=self.account, name='Existing', title='other',
        )
        amb_client = Client()
        amb_client.login(username='ct_amb', password='testpass123')

        for url in [
            reverse('contact_create', args=[self.account.pk]),
            reverse('contact_update', args=[self.account.pk, contact.pk]),
            reverse('contact_delete', args=[self.account.pk, contact.pk]),
        ]:
            resp = amb_client.post(
                url,
                data=_json.dumps({'name': 'X', 'title': 'other'}),
                content_type='application/json',
                HTTP_X_REQUESTED_WITH='XMLHttpRequest',
            )
            self.assertEqual(resp.status_code, 403, f'Expected 403 for {url}')

    def test_contact_wrong_company_returns_404(self):
        """Contact from a different company's account returns 404."""
        other_company  = make_company('Other Co')
        other_dist     = make_distributor(other_company, 'Other Dist')
        other_account  = make_account(other_company, other_dist, 'Other Store')
        other_contact  = AccountContact.objects.create(
            account=other_account, name='Not Mine', title='other',
        )
        url = reverse('contact_update', args=[other_account.pk, other_contact.pk])
        resp = self._post(url, {'name': 'Hacked', 'title': 'other'})
        # account.pk is in other company — should 404
        self.assertEqual(resp.status_code, 404)


# ---------------------------------------------------------------------------
# AccountNote API tests
# ---------------------------------------------------------------------------

import datetime as _dt
from apps.accounts.models import AccountNote, AccountNotePhoto, UserCoverageArea
from apps.distribution.models import Distributor as _Distributor


class NoteAPITest(TestCase):
    """Tests for the AccountNote CRUD API views."""

    def setUp(self):
        self.company = make_company('Note Test Co')
        self.distributor = make_distributor(self.company, 'Note Dist')
        self.account = make_account(self.company, self.distributor, 'Note Store')

        # Users
        self.admin = make_user(self.company, 'supplier_admin', 'note_admin')
        self.sm = make_user(self.company, 'sales_manager', 'note_sm')
        self.tm = make_user(self.company, 'territory_manager', 'note_tm')
        self.amb_mgr = make_user(self.company, 'ambassador_manager', 'note_amb_mgr')

        # Give sales_manager coverage of the distributor (so _can_delete_note works)
        UserCoverageArea.objects.create(
            company=self.company,
            user=self.sm,
            coverage_type=UserCoverageArea.CoverageType.DISTRIBUTOR,
            distributor=self.distributor,
        )

        self.client = Client()
        self.client.login(username='note_admin', password='testpass123')

    def _post(self, url, data=None):
        return self.client.post(url, data=data or {})

    def test_note_create(self):
        """POST creates an AccountNote with correct fields."""
        url = reverse('note_create', args=[self.account.pk])
        today = _dt.date.today().isoformat()
        resp = self._post(url, {
            'note_type': 'visit',
            'visit_date': today,
            'body': 'Great visit today.',
            'is_task': 'false',
        })
        self.assertEqual(resp.status_code, 200)
        data = _json.loads(resp.content)
        self.assertTrue(data['success'], data.get('error'))
        self.assertEqual(data['note']['body'], 'Great visit today.')
        self.assertEqual(data['note']['note_type'], 'visit')
        self.assertEqual(AccountNote.objects.filter(account=self.account).count(), 1)

    def test_note_create_visit_requires_date(self):
        """Visit note without date returns error."""
        url = reverse('note_create', args=[self.account.pk])
        resp = self._post(url, {
            'note_type': 'visit',
            'visit_date': '',
            'body': 'Missing date.',
            'is_task': 'false',
        })
        self.assertEqual(resp.status_code, 200)
        data = _json.loads(resp.content)
        self.assertFalse(data['success'])
        self.assertIn('Visit date', data['error'])
        self.assertEqual(AccountNote.objects.filter(account=self.account).count(), 0)

    def test_note_create_task_requires_priority(self):
        """Task note without priority returns error."""
        url = reverse('note_create', args=[self.account.pk])
        resp = self._post(url, {
            'note_type': 'general',
            'body': 'Follow up needed.',
            'is_task': 'true',
            'task_priority': '',
        })
        self.assertEqual(resp.status_code, 200)
        data = _json.loads(resp.content)
        self.assertFalse(data['success'])
        self.assertIn('Priority', data['error'])
        self.assertEqual(AccountNote.objects.filter(account=self.account).count(), 0)

    def test_note_update(self):
        """POST updates existing note body and type."""
        note = AccountNote.objects.create(
            account=self.account,
            note_type='visit',
            visit_date=_dt.date.today(),
            body='Original body.',
            created_by=self.admin,
        )
        url = reverse('note_update', args=[self.account.pk, note.pk])
        resp = self._post(url, {
            'note_type': 'general',
            'body': 'Updated body.',
            'is_task': 'false',
        })
        self.assertEqual(resp.status_code, 200)
        data = _json.loads(resp.content)
        self.assertTrue(data['success'], data.get('error'))
        note.refresh_from_db()
        self.assertEqual(note.body, 'Updated body.')
        self.assertEqual(note.note_type, 'general')

    def test_note_delete_by_creator(self):
        """Creator can delete their own note."""
        note = AccountNote.objects.create(
            account=self.account,
            note_type='general',
            body='Creator note.',
            created_by=self.admin,
        )
        url = reverse('note_delete', args=[self.account.pk, note.pk])
        resp = self._post(url)
        self.assertEqual(resp.status_code, 200)
        data = _json.loads(resp.content)
        self.assertTrue(data['success'])
        self.assertFalse(AccountNote.objects.filter(pk=note.pk).exists())

    def test_note_delete_by_sales_manager(self):
        """Sales manager with coverage can delete any note on covered account."""
        note = AccountNote.objects.create(
            account=self.account,
            note_type='general',
            body='SM can delete this.',
            created_by=self.admin,  # created by admin, deleted by SM
        )
        self.client.login(username='note_sm', password='testpass123')
        url = reverse('note_delete', args=[self.account.pk, note.pk])
        resp = self._post(url)
        self.assertEqual(resp.status_code, 200)
        data = _json.loads(resp.content)
        self.assertTrue(data['success'])
        self.assertFalse(AccountNote.objects.filter(pk=note.pk).exists())

    def test_note_delete_denied_for_ambassador_manager(self):
        """Ambassador manager cannot delete a note they didn't create."""
        note = AccountNote.objects.create(
            account=self.account,
            note_type='general',
            body='Amb mgr cannot delete.',
            created_by=self.admin,
        )
        self.client.login(username='note_amb_mgr', password='testpass123')
        url = reverse('note_delete', args=[self.account.pk, note.pk])
        resp = self._post(url)
        self.assertEqual(resp.status_code, 403)
        self.assertTrue(AccountNote.objects.filter(pk=note.pk).exists())

    def test_note_list_returns_notes_for_account(self):
        """GET returns only notes for the requested account, not others."""
        other_account = make_account(self.company, self.distributor, 'Other Store')
        AccountNote.objects.create(
            account=self.account, note_type='general',
            body='Mine.', created_by=self.admin,
        )
        AccountNote.objects.create(
            account=other_account, note_type='general',
            body='Other.', created_by=self.admin,
        )
        url = reverse('note_list', args=[self.account.pk])
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        data = _json.loads(resp.content)
        self.assertEqual(len(data['notes']), 1)
        self.assertEqual(data['notes'][0]['body'], 'Mine.')

    def test_note_requires_permission(self):
        """User without can_manage_account_notes gets 403 on create."""
        self.client.login(username='note_amb_mgr', password='testpass123')
        url = reverse('note_create', args=[self.account.pk])
        resp = self._post(url, {
            'note_type': 'general',
            'body': 'Should be blocked.',
            'is_task': 'false',
        })
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(AccountNote.objects.filter(account=self.account).count(), 0)

    def test_assignee_list_returns_covering_users(self):
        """GET assignees returns users with coverage over this account."""
        url = reverse('note_assignee_list', args=[self.account.pk])
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        data = _json.loads(resp.content)
        ids = [a['id'] for a in data['assignees']]
        # admin is supplier_admin — always included
        self.assertIn(self.admin.pk, ids)
        # sm has distributor coverage
        self.assertIn(self.sm.pk, ids)
