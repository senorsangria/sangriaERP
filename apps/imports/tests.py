"""
Tests for the sales import process — Phase 10.3.2.

Covers AccountItem creation and de-duplication during _execute_import().
"""
import csv
import datetime
import io
import os
import tempfile

from django.test import TestCase

from apps.accounts.models import Account, AccountItem
from apps.catalog.models import Brand, Item
from apps.core.models import Company, User
from apps.distribution.models import Distributor
from apps.imports.models import ImportBatch, ItemMapping
from apps.imports.views import _execute_import


# ---------------------------------------------------------------------------
# CSV builder helpers
# ---------------------------------------------------------------------------

# Required CSV column order matching _parse_csv_headers()
_CSV_HEADERS = [
    'Retail Accounts', 'Address', 'City', 'State', 'Zip Code',
    'VIP Outlet ID', 'Counties', 'OnOff Premises', 'Dates',
    'Item Names', 'Item Name ID', 'Quantity',
]


def _build_csv(rows):
    """
    Write rows to a named temp file and return the file path.

    Each row is a dict with keys matching the logical field names:
      account_name, address, city, state, zip_code, vip_outlet_id,
      county, on_off, date_str, item_name, item_id, quantity
    """
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(_CSV_HEADERS)
    for r in rows:
        writer.writerow([
            r.get('account_name', 'Test Store'),
            r.get('address', '1 Main St'),
            r.get('city', 'Hoboken'),
            r.get('state', 'NJ'),
            r.get('zip_code', '07030'),
            r.get('vip_outlet_id', '12345'),
            r.get('county', 'Hudson, NJ'),
            r.get('on_off', 'OFF'),
            r.get('date_str', '01/15/2024'),
            r.get('item_name', 'Classic Red 750ml'),
            r.get('item_id', 'Red0750'),
            r.get('quantity', '10'),
        ])

    # Write to a real temp file (csv.reader needs seekable file)
    tmp = tempfile.NamedTemporaryFile(
        mode='w', suffix='.csv', delete=False, encoding='utf-8-sig',
    )
    tmp.write(buf.getvalue())
    tmp.flush()
    tmp.close()
    return tmp.name


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

class ImportTestBase(TestCase):
    """
    Base class that sets up a company, distributor, brand, item, and
    item mapping so that _execute_import() can run successfully.
    """

    def setUp(self):
        self.company = Company.objects.create(name="Test Beverage Co")
        self.distributor = Distributor.objects.create(
            company=self.company, name="Test Dist",
        )
        self.brand = Brand.objects.create(company=self.company, name="Test Brand")
        self.item = Item.objects.create(
            brand=self.brand, name="Classic Red 750ml", item_code="Red0750",
        )
        # Item mapping — maps raw code "Red0750" to the Item
        self.mapping = ItemMapping.objects.create(
            company=self.company,
            distributor=self.distributor,
            brand=self.brand,
            raw_item_name="Red0750",
            mapped_item=self.item,
            status=ItemMapping.Status.MAPPED,
        )

    def tearDown(self):
        # Clean up any temp files created during the test
        pass

    def _run_import(self, rows, filename="test_import.csv"):
        filepath = _build_csv(rows)
        try:
            batch = _execute_import(
                request=None,
                company=self.company,
                distributor=self.distributor,
                filepath=filepath,
                filename=filename,
            )
        finally:
            if os.path.exists(filepath):
                os.unlink(filepath)
        return batch


# ---------------------------------------------------------------------------
# AccountItem creation via import
# ---------------------------------------------------------------------------

class ImportCreatesAccountItemsTest(ImportTestBase):
    """_execute_import() creates AccountItem records for new (account, item) pairs."""

    def test_creates_account_item_for_new_pair(self):
        rows = [{'date_str': '01/15/2024', 'item_id': 'Red0750'}]
        self._run_import(rows)

        self.assertEqual(AccountItem.objects.filter(item=self.item).count(), 1)
        ai = AccountItem.objects.get(item=self.item)
        self.assertEqual(ai.item, self.item)
        self.assertIsNotNone(ai.account)

    def test_date_first_associated_set_to_today(self):
        rows = [{'date_str': '01/15/2024', 'item_id': 'Red0750'}]
        self._run_import(rows)

        ai = AccountItem.objects.get(item=self.item)
        self.assertEqual(ai.date_first_associated, datetime.date.today())

    def test_current_price_not_set_during_import(self):
        rows = [{'date_str': '01/15/2024', 'item_id': 'Red0750'}]
        self._run_import(rows)

        ai = AccountItem.objects.get(item=self.item)
        self.assertIsNone(ai.current_price)

    def test_no_price_history_created_during_import(self):
        from apps.accounts.models import AccountItemPriceHistory
        rows = [{'date_str': '01/15/2024', 'item_id': 'Red0750'}]
        self._run_import(rows)

        self.assertEqual(AccountItemPriceHistory.objects.count(), 0)

    def test_batch_account_items_created_count(self):
        rows = [{'date_str': '01/15/2024', 'item_id': 'Red0750'}]
        batch = self._run_import(rows)

        self.assertEqual(batch.account_items_created, 1)

    def test_multiple_rows_same_pair_creates_one_account_item(self):
        """Ten rows with the same account+item should create exactly 1 AccountItem."""
        rows = [
            {'date_str': f'01/{i:02d}/2024', 'item_id': 'Red0750'}
            for i in range(1, 11)
        ]
        batch = self._run_import(rows)

        self.assertEqual(AccountItem.objects.filter(item=self.item).count(), 1)
        self.assertEqual(batch.account_items_created, 1)

    def test_different_items_create_separate_account_items(self):
        # Create a second item and its mapping
        item2 = Item.objects.create(
            brand=self.brand, name="Classic White 750ml", item_code="Wht0750",
        )
        ItemMapping.objects.create(
            company=self.company,
            distributor=self.distributor,
            brand=self.brand,
            raw_item_name="Wht0750",
            mapped_item=item2,
            status=ItemMapping.Status.MAPPED,
        )

        rows = [
            {'date_str': '01/15/2024', 'item_id': 'Red0750'},
            {'date_str': '01/15/2024', 'item_id': 'Wht0750'},
        ]
        batch = self._run_import(rows)

        self.assertEqual(AccountItem.objects.count(), 2)
        self.assertEqual(batch.account_items_created, 2)

    def test_different_accounts_create_separate_account_items(self):
        rows = [
            {
                'date_str': '01/15/2024',
                'account_name': 'Store A',
                'address': '1 Main St',
                'city': 'Hoboken',
                'state': 'NJ',
                'item_id': 'Red0750',
            },
            {
                'date_str': '01/15/2024',
                'account_name': 'Store B',
                'address': '2 Oak Ave',
                'city': 'Hoboken',
                'state': 'NJ',
                'item_id': 'Red0750',
            },
        ]
        batch = self._run_import(rows)

        self.assertEqual(AccountItem.objects.count(), 2)
        self.assertEqual(batch.account_items_created, 2)


# ---------------------------------------------------------------------------
# AccountItem de-duplication across imports
# ---------------------------------------------------------------------------

class ImportDeduplicatesAccountItemsTest(ImportTestBase):
    """Re-importing the same data does not create duplicate AccountItem records."""

    def test_reimport_does_not_duplicate_account_items(self):
        rows = [{'date_str': '01/15/2024', 'item_id': 'Red0750'}]
        self._run_import(rows, filename="file1.csv")

        self.assertEqual(AccountItem.objects.count(), 1)

        # Second import with different dates (to avoid duplicate-date abort)
        rows2 = [{'date_str': '02/15/2024', 'item_id': 'Red0750'}]
        batch2 = self._run_import(rows2, filename="file2.csv")

        # Still only 1 AccountItem — same account+item pair
        self.assertEqual(AccountItem.objects.count(), 1)
        # Second import created 0 new AccountItems
        self.assertEqual(batch2.account_items_created, 0)

    def test_date_first_associated_not_overwritten_on_reimport(self):
        rows = [{'date_str': '01/15/2024', 'item_id': 'Red0750'}]
        self._run_import(rows, filename="file1.csv")

        ai = AccountItem.objects.get(item=self.item)
        original_date = ai.date_first_associated

        # Second import
        rows2 = [{'date_str': '02/15/2024', 'item_id': 'Red0750'}]
        self._run_import(rows2, filename="file2.csv")

        ai.refresh_from_db()
        self.assertEqual(ai.date_first_associated, original_date)

    def test_first_import_sets_batch_account_items_created(self):
        rows = [{'date_str': '01/15/2024', 'item_id': 'Red0750'}]
        batch1 = self._run_import(rows, filename="file1.csv")
        self.assertEqual(batch1.account_items_created, 1)

    def test_second_import_shows_zero_account_items_created(self):
        rows = [{'date_str': '01/15/2024', 'item_id': 'Red0750'}]
        self._run_import(rows, filename="file1.csv")

        rows2 = [{'date_str': '02/15/2024', 'item_id': 'Red0750'}]
        batch2 = self._run_import(rows2, filename="file2.csv")
        self.assertEqual(batch2.account_items_created, 0)


# ---------------------------------------------------------------------------
# Ignored item codes are excluded
# ---------------------------------------------------------------------------

class ImportIgnoredItemsTest(ImportTestBase):
    """Rows with ignored item codes are skipped and produce no AccountItem records."""

    def test_ignored_item_code_skipped(self):
        self.mapping.status = ItemMapping.Status.IGNORED
        self.mapping.save()

        rows = [{'date_str': '01/15/2024', 'item_id': 'Red0750'}]
        batch = self._run_import(rows)

        self.assertEqual(AccountItem.objects.count(), 0)
        self.assertEqual(batch.account_items_created, 0)
