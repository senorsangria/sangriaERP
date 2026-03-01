"""
Tests for apps.accounts models — AccountItem and AccountItemPriceHistory.

Phase 10.3.2
"""
import datetime

from django.db import IntegrityError
from django.test import TestCase

from apps.accounts.models import Account, AccountItem, AccountItemPriceHistory
from apps.catalog.models import Brand, Item
from apps.core.models import Company, User
from apps.distribution.models import Distributor


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
        self.user = User.objects.create_user(
            username="testuser",
            password="testpass123",
            company=self.company,
            role=User.Role.SUPPLIER_ADMIN,
        )

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
