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
from apps.sales.models import SalesRecord


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

    def test_date_first_associated_set_to_sale_date(self):
        rows = [{'date_str': '01/15/2024', 'item_id': 'Red0750'}]
        self._run_import(rows)

        ai = AccountItem.objects.get(item=self.item)
        self.assertEqual(ai.date_first_associated, datetime.date(2024, 1, 15))

    def test_account_item_date_uses_earliest_sale_date(self):
        """When multiple rows exist for the same account+item, date_first_associated
        is set to the earliest sale_date in the import, not today's date."""
        rows = [
            {'date_str': '03/10/2024', 'item_id': 'Red0750'},
            {'date_str': '01/05/2024', 'item_id': 'Red0750'},
            {'date_str': '06/20/2024', 'item_id': 'Red0750'},
        ]
        self._run_import(rows)

        ai = AccountItem.objects.get(item=self.item)
        self.assertEqual(ai.date_first_associated, datetime.date(2024, 1, 5))
        self.assertNotEqual(ai.date_first_associated, datetime.date.today())

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


# ---------------------------------------------------------------------------
# Phase 10.3.3 — Import reactivates inactive accounts
# ---------------------------------------------------------------------------

class ImportReactivatesInactiveAccountsTest(ImportTestBase):
    """
    If an inactive (deactivated) account appears in an import, it is
    automatically reactivated and counted in accounts_reactivated.
    """

    def _make_inactive_account(self, address="1 Main St", city="Hoboken", state="NJ"):
        """Create an inactive, auto-created account at the given address."""
        from utils.normalize import normalize_address
        return Account.objects.create(
            company=self.company,
            distributor=self.distributor,
            name="Old Store",
            street=address,
            city=city,
            state=state,
            address_normalized=normalize_address(address),
            city_normalized=normalize_address(city),
            state_normalized=normalize_address(state),
            auto_created=True,
            is_active=False,
        )

    def test_inactive_account_is_reactivated(self):
        self._make_inactive_account()
        rows = [{'date_str': '01/15/2024', 'item_id': 'Red0750'}]
        self._run_import(rows)

        account = Account.objects.get(street="1 Main St", city="Hoboken")
        self.assertTrue(account.is_active)

    def test_accounts_reactivated_count_tracked(self):
        self._make_inactive_account()
        rows = [{'date_str': '01/15/2024', 'item_id': 'Red0750'}]
        batch = self._run_import(rows)
        self.assertEqual(batch.accounts_reactivated, 1)

    def test_active_accounts_not_counted_as_reactivated(self):
        from utils.normalize import normalize_address
        Account.objects.create(
            company=self.company,
            distributor=self.distributor,
            name="Active Store",
            street="1 Main St",
            city="Hoboken",
            state="NJ",
            address_normalized=normalize_address("1 Main St"),
            city_normalized=normalize_address("Hoboken"),
            state_normalized=normalize_address("NJ"),
            auto_created=True,
            is_active=True,
        )
        rows = [{'date_str': '01/15/2024', 'item_id': 'Red0750'}]
        batch = self._run_import(rows)
        self.assertEqual(batch.accounts_reactivated, 0)

    def test_new_account_not_counted_as_reactivated(self):
        """A brand-new account (no prior record) is created, not reactivated."""
        rows = [{'date_str': '01/15/2024', 'item_id': 'Red0750'}]
        batch = self._run_import(rows)
        self.assertEqual(batch.accounts_reactivated, 0)
        self.assertEqual(batch.accounts_created, 1)

    def test_reactivated_not_counted_as_created(self):
        """A reactivated account should NOT inflate accounts_created."""
        self._make_inactive_account()
        rows = [{'date_str': '01/15/2024', 'item_id': 'Red0750'}]
        batch = self._run_import(rows)
        self.assertEqual(batch.accounts_created, 0)
        self.assertEqual(batch.accounts_reactivated, 1)


# ---------------------------------------------------------------------------
# distributor_wholesale_price field on SalesRecord
# ---------------------------------------------------------------------------

_CSV_HEADERS_WITH_PRICE = [
    'Retail Accounts', 'Address', 'City', 'State', 'Zip Code',
    'VIP Outlet ID', 'Counties', 'OnOff Premises', 'Dates',
    'Item Names', 'Item Name ID', 'Price', 'Quantity',
]


def _build_csv_with_price(rows):
    """Like _build_csv but includes a Price column before Quantity."""
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(_CSV_HEADERS_WITH_PRICE)
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
            r.get('price', ''),
            r.get('quantity', '10'),
        ])
    tmp = tempfile.NamedTemporaryFile(
        mode='w', suffix='.csv', delete=False, encoding='utf-8-sig',
    )
    tmp.write(buf.getvalue())
    tmp.flush()
    tmp.close()
    return tmp.name


class ImportWholesalePriceTest(ImportTestBase):
    """distributor_wholesale_price is captured from the optional Price column."""

    def _run_import_with_price(self, rows, filename='test_price.csv'):
        filepath = _build_csv_with_price(rows)
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

    def test_valid_price_stored(self):
        """A valid Price value is stored on SalesRecord."""
        self._run_import_with_price([{'date_str': '01/15/2024', 'price': '24.99'}])
        record = SalesRecord.objects.get(company=self.company)
        from decimal import Decimal
        self.assertEqual(record.distributor_wholesale_price, Decimal('24.99'))

    def test_blank_price_stores_null(self):
        """A blank Price cell stores null, not an error."""
        self._run_import_with_price([{'date_str': '01/15/2024', 'price': ''}])
        record = SalesRecord.objects.get(company=self.company)
        self.assertIsNone(record.distributor_wholesale_price)

    def test_no_price_column_stores_null(self):
        """When the Price column is absent entirely, price is stored as null."""
        rows = [{'date_str': '01/15/2024', 'item_id': 'Red0750'}]
        self._run_import(rows)   # uses _build_csv — no Price column
        record = SalesRecord.objects.get(company=self.company)
        self.assertIsNone(record.distributor_wholesale_price)

    def test_invalid_price_stores_null(self):
        """A non-numeric Price value stores null without crashing."""
        self._run_import_with_price([{'date_str': '01/15/2024', 'price': 'N/A'}])
        record = SalesRecord.objects.get(company=self.company)
        self.assertIsNone(record.distributor_wholesale_price)


# ---------------------------------------------------------------------------
# Multiple file upload — view tests
# ---------------------------------------------------------------------------

import json as _json

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from django.urls import reverse

from apps.core.rbac import Role


def _make_supplier_admin(company, username='admin'):
    """Create and return a supplier_admin user for the given company."""
    from apps.core.models import User
    user = User.objects.create_user(
        username=username, password='testpass', company=company,
    )
    role = Role.objects.get(codename='supplier_admin')
    user.roles.set([role])
    return user


def _csv_bytes(rows, headers=None):
    """Return CSV content as bytes for SimpleUploadedFile."""
    if headers is None:
        headers = _CSV_HEADERS
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(headers)
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
    return buf.getvalue().encode('utf-8-sig')


class MultipleFileUploadViewTest(ImportTestBase):
    """View-level tests for multiple CSV file upload."""

    def setUp(self):
        super().setUp()
        self.user = _make_supplier_admin(self.company)
        self.client = Client()
        self.client.login(username='admin', password='testpass')
        self.url = reverse('import_upload')

    def _post_files(self, file_list):
        """POST to import_upload with the given list of SimpleUploadedFile objects."""
        return self.client.post(
            self.url,
            data={'distributor': str(self.distributor.pk), 'csv_file': file_list},
        )

    def test_upload_multiple_files_combined(self):
        """Rows from two valid CSV files are combined and sorted by date."""
        file1 = SimpleUploadedFile(
            'jan.csv',
            _csv_bytes([{'date_str': '01/15/2024', 'item_id': 'Red0750'}]),
            content_type='text/csv',
        )
        file2 = SimpleUploadedFile(
            'feb.csv',
            _csv_bytes([
                {'date_str': '02/10/2024', 'item_id': 'Red0750'},
                {'date_str': '02/20/2024', 'item_id': 'Red0750'},
            ]),
            content_type='text/csv',
        )
        response = self._post_files([file1, file2])

        # Should redirect to preview
        self.assertEqual(response.status_code, 302)
        session = self.client.session
        pending = session['pending_import']
        # All 3 rows combined
        self.assertEqual(pending['preview']['total_records'], 3)
        # Date range spans both files
        self.assertEqual(pending['preview']['date_range_start'], '2024-01-15')
        self.assertEqual(pending['preview']['date_range_end'], '2024-02-20')

    def test_upload_one_bad_header_aborts_all(self):
        """If one file has a missing required column the entire import is aborted."""
        file1 = SimpleUploadedFile(
            'good.csv',
            _csv_bytes([{'date_str': '01/15/2024', 'item_id': 'Red0750'}]),
            content_type='text/csv',
        )
        # Build a CSV with a missing required column (no 'Dates' column)
        bad_headers = [
            'Retail Accounts', 'Address', 'City', 'State', 'Zip Code',
            'VIP Outlet ID', 'Counties', 'OnOff Premises',
            'Item Names', 'Item Name ID', 'Quantity',  # 'Dates' deliberately omitted
        ]
        bad_content = io.StringIO()
        csv.writer(bad_content).writerow(bad_headers)
        file2 = SimpleUploadedFile(
            'bad.csv',
            bad_content.getvalue().encode('utf-8-sig'),
            content_type='text/csv',
        )
        response = self._post_files([file1, file2])

        # Should stay on upload page (200) with an error message
        self.assertEqual(response.status_code, 200)
        messages_list = list(response.wsgi_request._messages)
        self.assertTrue(
            any('bad.csv' in str(m) for m in messages_list),
            msg='Error message should mention the bad filename',
        )
        self.assertNotIn('pending_import', self.client.session)

    def test_upload_filename_stored_as_json_list(self):
        """After a successful upload the session filename is a JSON list."""
        file1 = SimpleUploadedFile(
            'jan.csv',
            _csv_bytes([{'date_str': '01/15/2024', 'item_id': 'Red0750'}]),
            content_type='text/csv',
        )
        file2 = SimpleUploadedFile(
            'feb.csv',
            _csv_bytes([{'date_str': '02/15/2024', 'item_id': 'Red0750'}]),
            content_type='text/csv',
        )
        response = self._post_files([file1, file2])

        self.assertEqual(response.status_code, 302)
        session = self.client.session
        raw_filename = session['pending_import']['filename']
        parsed = _json.loads(raw_filename)
        self.assertIsInstance(parsed, list)
        self.assertEqual(sorted(parsed), ['feb.csv', 'jan.csv'])


# ---------------------------------------------------------------------------
# ImportBatch.filename_display property
# ---------------------------------------------------------------------------

class ImportBatchFilenameDisplayTest(ImportTestBase):
    """filename_display property handles JSON lists and legacy plain strings."""

    def _make_batch(self, filename_value):
        return ImportBatch(
            company=self.company,
            distributor=self.distributor,
            import_type=ImportBatch.ImportType.SALES_DATA,
            filename=filename_value,
        )

    def test_filename_display_single(self):
        """JSON list with one filename displays just the filename."""
        batch = self._make_batch(_json.dumps(['sales_jan.csv']))
        self.assertEqual(batch.filename_display, 'sales_jan.csv')

    def test_filename_display_multiple(self):
        """JSON list with multiple filenames displays 'X files: ...'."""
        batch = self._make_batch(_json.dumps(['jan.csv', 'feb.csv']))
        self.assertEqual(batch.filename_display, '2 files: jan.csv, feb.csv')

    def test_filename_display_legacy(self):
        """A plain string (legacy) is returned as-is."""
        batch = self._make_batch('old_import.csv')
        self.assertEqual(batch.filename_display, 'old_import.csv')
