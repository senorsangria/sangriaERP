"""
Tests for the sales import process.

Covers AccountItem creation, de-duplication, multi-distributor logic,
replace-on-import (month-grain overlap detection / delete-and-replace),
and resolve_mappings redirect.
"""
import csv
import datetime
import io
import json as _json

from django.test import Client, TestCase
from django.urls import reverse

from apps.accounts.models import Account, AccountItem
from apps.catalog.models import Brand, Item
from apps.core.models import Company, User
from apps.core.rbac import Role
from apps.distribution.models import Distributor
from apps.imports.models import ImportBatch, ItemMapping
from apps.imports.views import _execute_import, _parse_date
from apps.sales.models import SalesRecord


# ---------------------------------------------------------------------------
# Row dict builder (no CSV file needed for unit tests)
# ---------------------------------------------------------------------------

def _make_rows(rows_spec, distributor):
    """
    Build pre-parsed row dicts suitable for passing directly to _execute_import.
    distributor: a Distributor instance to attach to each row.
    """
    parsed = []
    for r in rows_spec:
        parsed.append({
            'account_name':     r.get('account_name', 'Test Store'),
            'address':          r.get('address', '1 Main St'),
            'city':             r.get('city', 'Hoboken'),
            'state':            r.get('state', 'NJ'),
            'zip_code':         r.get('zip_code', '07030'),
            'vip_outlet_id':    r.get('vip_outlet_id', '12345'),
            'county':           r.get('county', 'Hudson'),
            'on_off_premise':   r.get('on_off', 'OFF'),
            'sale_date':        _parse_date(r.get('date_str', '01/15/2024')),
            'item_id':          r.get('item_id', 'Red0750'),
            'quantity':         int(r.get('quantity', 10)),
            'price':            r.get('price', None),
            'distributor_name': distributor.name,
            'distributor':      distributor,
            'distributor_pk':   distributor.pk,
        })
    return parsed


# ---------------------------------------------------------------------------
# CSV helpers for view-level tests (need actual uploaded file bytes)
# ---------------------------------------------------------------------------

_CSV_HEADERS = [
    'Retail Accounts', 'Address', 'City', 'State', 'Zip Code',
    'VIP Outlet ID', 'Counties', 'OnOff Premises', 'Dates',
    'Item Names', 'Item Name ID', 'Distributors', 'Quantity',
]

_CSV_HEADERS_WITH_PRICE = [
    'Retail Accounts', 'Address', 'City', 'State', 'Zip Code',
    'VIP Outlet ID', 'Counties', 'OnOff Premises', 'Dates',
    'Item Names', 'Item Name ID', 'Price', 'Distributors', 'Quantity',
]


def _csv_bytes(rows, headers=None, distributor_name='Test Dist'):
    """Return CSV content as bytes for SimpleUploadedFile."""
    if headers is None:
        headers = _CSV_HEADERS
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(headers)
    has_price = 'Price' in headers
    for r in rows:
        row_data = [
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
        ]
        if has_price:
            row_data.append(r.get('price', ''))
        row_data.append(r.get('distributor_name', distributor_name))
        row_data.append(r.get('quantity', '10'))
        writer.writerow(row_data)
    return buf.getvalue().encode('utf-8-sig')


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

class ImportTestBase(TestCase):
    """
    Base class: company, distributor, brand, item, item mapping.
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
        self.mapping = ItemMapping.objects.create(
            company=self.company,
            distributor=self.distributor,
            brand=self.brand,
            raw_item_name="Red0750",
            mapped_item=self.item,
            status=ItemMapping.Status.MAPPED,
        )

    def _run_import(self, rows_spec, filename="test_import.csv", distributor=None):
        """Build rows and call _execute_import directly."""
        dist = distributor or self.distributor
        rows = _make_rows(rows_spec, dist)
        return _execute_import(
            request=None,
            company=self.company,
            distributor=dist,
            rows=rows,
            filename=filename,
        )


# ---------------------------------------------------------------------------
# AccountItem creation via import
# ---------------------------------------------------------------------------

class ImportCreatesAccountItemsTest(ImportTestBase):

    def test_creates_account_item_for_new_pair(self):
        rows = [{'date_str': '01/15/2024', 'item_id': 'Red0750'}]
        self._run_import(rows)
        self.assertEqual(AccountItem.objects.filter(item=self.item).count(), 1)

    def test_date_first_associated_set_to_sale_date(self):
        rows = [{'date_str': '01/15/2024', 'item_id': 'Red0750'}]
        self._run_import(rows)
        ai = AccountItem.objects.get(item=self.item)
        self.assertEqual(ai.date_first_associated, datetime.date(2024, 1, 15))

    def test_account_item_date_uses_earliest_sale_date(self):
        rows = [
            {'date_str': '03/10/2024', 'item_id': 'Red0750'},
            {'date_str': '01/05/2024', 'item_id': 'Red0750'},
            {'date_str': '06/20/2024', 'item_id': 'Red0750'},
        ]
        self._run_import(rows)
        ai = AccountItem.objects.get(item=self.item)
        self.assertEqual(ai.date_first_associated, datetime.date(2024, 1, 5))

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
        rows = [{'date_str': f'01/{i:02d}/2024', 'item_id': 'Red0750'} for i in range(1, 11)]
        batch = self._run_import(rows)
        self.assertEqual(AccountItem.objects.filter(item=self.item).count(), 1)
        self.assertEqual(batch.account_items_created, 1)

    def test_different_items_create_separate_account_items(self):
        item2 = Item.objects.create(
            brand=self.brand, name="Classic White 750ml", item_code="Wht0750",
        )
        ItemMapping.objects.create(
            company=self.company, distributor=self.distributor,
            brand=self.brand, raw_item_name="Wht0750",
            mapped_item=item2, status=ItemMapping.Status.MAPPED,
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
            {'date_str': '01/15/2024', 'account_name': 'Store A',
             'address': '1 Main St', 'city': 'Hoboken', 'state': 'NJ', 'item_id': 'Red0750'},
            {'date_str': '01/15/2024', 'account_name': 'Store B',
             'address': '2 Oak Ave', 'city': 'Hoboken', 'state': 'NJ', 'item_id': 'Red0750'},
        ]
        batch = self._run_import(rows)
        self.assertEqual(AccountItem.objects.count(), 2)
        self.assertEqual(batch.account_items_created, 2)


# ---------------------------------------------------------------------------
# AccountItem de-duplication
# ---------------------------------------------------------------------------

class ImportDeduplicatesAccountItemsTest(ImportTestBase):

    def test_reimport_does_not_duplicate_account_items(self):
        rows = [{'date_str': '01/15/2024', 'item_id': 'Red0750'}]
        self._run_import(rows, filename="file1.csv")
        self.assertEqual(AccountItem.objects.count(), 1)
        rows2 = [{'date_str': '02/15/2024', 'item_id': 'Red0750'}]
        self._run_import(rows2, filename="file2.csv")
        self.assertEqual(AccountItem.objects.count(), 1)

    def test_date_first_associated_not_overwritten_on_reimport(self):
        rows = [{'date_str': '01/15/2024', 'item_id': 'Red0750'}]
        self._run_import(rows, filename="file1.csv")
        ai = AccountItem.objects.get(item=self.item)
        original_date = ai.date_first_associated
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
# Ignored item codes
# ---------------------------------------------------------------------------

class ImportIgnoredItemsTest(ImportTestBase):

    def test_ignored_item_code_skipped(self):
        self.mapping.status = ItemMapping.Status.IGNORED
        self.mapping.save()
        rows = [{'date_str': '01/15/2024', 'item_id': 'Red0750'}]
        batch = self._run_import(rows)
        self.assertEqual(AccountItem.objects.count(), 0)
        self.assertEqual(batch.account_items_created, 0)


# ---------------------------------------------------------------------------
# Inactive account reactivation
# ---------------------------------------------------------------------------

class ImportReactivatesInactiveAccountsTest(ImportTestBase):

    def _make_inactive_account(self, address="1 Main St", city="Hoboken", state="NJ"):
        from utils.normalize import normalize_address
        return Account.objects.create(
            company=self.company,
            distributor=self.distributor,
            name="Old Store",
            street=address, city=city, state=state,
            address_normalized=normalize_address(address),
            city_normalized=normalize_address(city),
            state_normalized=normalize_address(state),
            auto_created=True, is_active=False,
        )

    def test_inactive_account_is_reactivated(self):
        self._make_inactive_account()
        self._run_import([{'date_str': '01/15/2024', 'item_id': 'Red0750'}])
        account = Account.objects.get(street="1 Main St", city="Hoboken")
        self.assertTrue(account.is_active)

    def test_accounts_reactivated_count_tracked(self):
        self._make_inactive_account()
        batch = self._run_import([{'date_str': '01/15/2024', 'item_id': 'Red0750'}])
        self.assertEqual(batch.accounts_reactivated, 1)

    def test_active_accounts_not_counted_as_reactivated(self):
        from utils.normalize import normalize_address
        Account.objects.create(
            company=self.company, distributor=self.distributor,
            name="Active Store", street="1 Main St", city="Hoboken", state="NJ",
            address_normalized=normalize_address("1 Main St"),
            city_normalized=normalize_address("Hoboken"),
            state_normalized=normalize_address("NJ"),
            auto_created=True, is_active=True,
        )
        batch = self._run_import([{'date_str': '01/15/2024', 'item_id': 'Red0750'}])
        self.assertEqual(batch.accounts_reactivated, 0)

    def test_new_account_not_counted_as_reactivated(self):
        batch = self._run_import([{'date_str': '01/15/2024', 'item_id': 'Red0750'}])
        self.assertEqual(batch.accounts_reactivated, 0)
        self.assertEqual(batch.accounts_created, 1)

    def test_reactivated_not_counted_as_created(self):
        self._make_inactive_account()
        batch = self._run_import([{'date_str': '01/15/2024', 'item_id': 'Red0750'}])
        self.assertEqual(batch.accounts_created, 0)
        self.assertEqual(batch.accounts_reactivated, 1)


# ---------------------------------------------------------------------------
# Wholesale price
# ---------------------------------------------------------------------------

class ImportWholesalePriceTest(ImportTestBase):

    def test_valid_price_stored(self):
        rows = [{'date_str': '01/15/2024', 'item_id': 'Red0750', 'price': '24.99'}]
        rows_parsed = _make_rows(rows, self.distributor)
        from decimal import Decimal
        rows_parsed[0]['price'] = Decimal('24.99')
        _execute_import(
            request=None, company=self.company, distributor=self.distributor,
            rows=rows_parsed, filename='test.csv',
        )
        record = SalesRecord.objects.get(company=self.company)
        self.assertEqual(record.distributor_wholesale_price, Decimal('24.99'))

    def test_blank_price_stores_null(self):
        rows_parsed = _make_rows([{'date_str': '01/15/2024'}], self.distributor)
        rows_parsed[0]['price'] = None
        _execute_import(
            request=None, company=self.company, distributor=self.distributor,
            rows=rows_parsed, filename='test.csv',
        )
        record = SalesRecord.objects.get(company=self.company)
        self.assertIsNone(record.distributor_wholesale_price)

    def test_no_price_column_stores_null(self):
        self._run_import([{'date_str': '01/15/2024', 'item_id': 'Red0750'}])
        record = SalesRecord.objects.get(company=self.company)
        self.assertIsNone(record.distributor_wholesale_price)


# ---------------------------------------------------------------------------
# Multi-distributor view-level tests
# ---------------------------------------------------------------------------

def _make_supplier_admin(company, username='admin'):
    user = User.objects.create_user(username=username, password='testpass', company=company)
    role = Role.objects.get(codename='supplier_admin')
    user.roles.set([role])
    return user


from django.core.files.uploadedfile import SimpleUploadedFile


class MultipleFileUploadViewTest(ImportTestBase):
    """View-level tests for CSV upload (multi-distributor)."""

    def setUp(self):
        super().setUp()
        self.user = _make_supplier_admin(self.company)
        self.client = Client()
        self.client.login(username='admin', password='testpass')
        self.url = reverse('import_upload')

    def _post_files(self, file_list):
        return self.client.post(self.url, data={'csv_file': file_list})

    def test_upload_multiple_files_combined(self):
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
        self.assertEqual(response.status_code, 302)
        session = self.client.session
        pending = session['pending_import']
        self.assertEqual(pending['preview']['total_records'], 3)
        self.assertEqual(pending['preview']['date_range_start'], '2024-01-15')
        self.assertEqual(pending['preview']['date_range_end'], '2024-02-20')
        # No single distributor_id — should not be present
        self.assertNotIn('distributor_id', pending)

    def test_upload_one_bad_header_aborts_all(self):
        file1 = SimpleUploadedFile(
            'good.csv',
            _csv_bytes([{'date_str': '01/15/2024', 'item_id': 'Red0750'}]),
            content_type='text/csv',
        )
        # CSV missing 'Distributors' column
        bad_headers = [
            'Retail Accounts', 'Address', 'City', 'State', 'Zip Code',
            'VIP Outlet ID', 'Counties', 'OnOff Premises', 'Dates',
            'Item Names', 'Item Name ID', 'Quantity',
        ]
        bad_content = io.StringIO()
        csv.writer(bad_content).writerow(bad_headers)
        file2 = SimpleUploadedFile(
            'bad.csv',
            bad_content.getvalue().encode('utf-8-sig'),
            content_type='text/csv',
        )
        response = self._post_files([file1, file2])
        self.assertEqual(response.status_code, 200)
        messages_list = list(response.wsgi_request._messages)
        self.assertTrue(
            any('bad.csv' in str(m) for m in messages_list),
            msg='Error message should mention the bad filename',
        )
        self.assertNotIn('pending_import', self.client.session)

    def test_upload_filename_stored_as_json_list(self):
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

    def test_unknown_distributor_name_aborts(self):
        """CSV with an unrecognised distributor name causes hard abort."""
        file1 = SimpleUploadedFile(
            'bad_dist.csv',
            _csv_bytes(
                [{'date_str': '01/15/2024', 'item_id': 'Red0750'}],
                distributor_name='Unknown Distributor XYZ',
            ),
            content_type='text/csv',
        )
        response = self._post_files([file1])
        self.assertEqual(response.status_code, 200)
        msgs = [str(m) for m in response.wsgi_request._messages]
        self.assertTrue(any('Unknown Distributor XYZ' in m for m in msgs))
        self.assertNotIn('pending_import', self.client.session)

    def test_distributor_name_case_insensitive(self):
        """Lowercase distributor name matches the stored name."""
        file1 = SimpleUploadedFile(
            'lower.csv',
            _csv_bytes(
                [{'date_str': '01/15/2024', 'item_id': 'Red0750'}],
                distributor_name='test dist',   # stored as "Test Dist"
            ),
            content_type='text/csv',
        )
        response = self._post_files([file1])
        # Should redirect to preview (not abort)
        self.assertEqual(response.status_code, 302)
        self.assertIn('pending_import', self.client.session)

    def test_multi_distributor_upload_creates_distributor_summaries(self):
        """Uploading rows for two distributors produces per-distributor summary."""
        dist2 = Distributor.objects.create(company=self.company, name='Dist Two')
        ItemMapping.objects.create(
            company=self.company, distributor=dist2,
            raw_item_name='Red0750', mapped_item=self.item,
            status=ItemMapping.Status.MAPPED,
        )
        content = _csv_bytes(
            [
                {'date_str': '01/15/2024', 'item_id': 'Red0750', 'distributor_name': 'Test Dist'},
                {'date_str': '01/15/2024', 'item_id': 'Red0750', 'distributor_name': 'Dist Two'},
            ]
        )
        file1 = SimpleUploadedFile('multi.csv', content, content_type='text/csv')
        response = self._post_files([file1])
        self.assertEqual(response.status_code, 302)
        summaries = self.client.session['pending_import']['preview']['distributor_summaries']
        names = [s['name'] for s in summaries]
        self.assertIn('Test Dist', names)
        self.assertIn('Dist Two', names)

    def test_unknown_item_codes_redirect_to_resolve_mappings(self):
        """Unknown item codes redirect to resolve_mappings with sales context."""
        file1 = SimpleUploadedFile(
            'unknown_code.csv',
            _csv_bytes([{'date_str': '01/15/2024', 'item_id': 'UNKNOWN999'}]),
            content_type='text/csv',
        )
        response = self._post_files([file1])
        self.assertRedirects(response, reverse('resolve_mappings'), fetch_redirect_response=False)
        pending = self.client.session.get('pending_mapping_resolution')
        self.assertIsNotNone(pending)
        self.assertEqual(pending['context'], 'sales')
        self.assertEqual(pending['next_url'], reverse('import_upload'))

    def test_resolve_mappings_returns_to_sales_upload(self):
        """After bulk_save_mappings, redirect_url points back to sales upload."""
        file1 = SimpleUploadedFile(
            'unknown_code.csv',
            _csv_bytes([{'date_str': '01/15/2024', 'item_id': 'UNKNOWN999'}]),
            content_type='text/csv',
        )
        self._post_files([file1])
        # Manually simulate bulk_save_mappings response
        pending = self.client.session.get('pending_mapping_resolution')
        self.assertEqual(pending['next_url'], reverse('import_upload'))

    def test_overlap_detected_not_aborted(self):
        """Replace-on-import: an overlapping month no longer aborts — it is detected
        and carried to the preview; a non-overlapping distributor shows no overlap."""
        dist2 = Distributor.objects.create(company=self.company, name='Dist Two')
        ItemMapping.objects.create(
            company=self.company, distributor=dist2,
            raw_item_name='Red0750', mapped_item=self.item,
            status=ItemMapping.Status.MAPPED,
        )
        # Pre-seed: Test Dist has records on 01/15/2024
        account = Account.objects.create(
            company=self.company, distributor=self.distributor,
            name='Existing Store', street='1 Main St', city='Hoboken', state='NJ',
            address_normalized='1 MAIN ST', city_normalized='HOBOKEN', state_normalized='NJ',
        )
        batch = ImportBatch.objects.create(
            company=self.company, distributor=self.distributor,
            import_type=ImportBatch.ImportType.SALES_DATA,
            filename='old.csv', status=ImportBatch.Status.COMPLETE,
        )
        SalesRecord.objects.create(
            company=self.company, import_batch=batch,
            account=account, item=self.item,
            sale_date=datetime.date(2024, 1, 15), quantity=5,
        )

        # Upload with Test Dist in the same month → overlap DETECTED, proceeds to preview
        conflict_file = SimpleUploadedFile(
            'conflict.csv',
            _csv_bytes([{'date_str': '01/20/2024', 'item_id': 'Red0750'}], distributor_name='Test Dist'),
            content_type='text/csv',
        )
        response = self._post_files([conflict_file])
        self.assertEqual(response.status_code, 302)   # not aborted
        pending = self.client.session['pending_import']
        self.assertTrue(pending['replace_preview']['has_overlap'])
        self.assertIn([self.distributor.pk, 2024, 1], pending['overlap'])

        # Upload with Dist Two in the same month → no existing data, no overlap
        ok_file = SimpleUploadedFile(
            'ok.csv',
            _csv_bytes([{'date_str': '01/15/2024', 'item_id': 'Red0750'}], distributor_name='Dist Two'),
            content_type='text/csv',
        )
        response2 = self._post_files([ok_file])
        self.assertEqual(response2.status_code, 302)   # proceeds to preview
        self.assertFalse(self.client.session['pending_import']['replace_preview']['has_overlap'])

    def test_multiple_batches_created_on_confirm(self):
        """One ImportBatch per distributor is created in a single transaction."""
        dist2 = Distributor.objects.create(company=self.company, name='Dist Two')
        ItemMapping.objects.create(
            company=self.company, distributor=dist2,
            raw_item_name='Red0750', mapped_item=self.item,
            status=ItemMapping.Status.MAPPED,
        )
        content = _csv_bytes([
            {'date_str': '01/15/2024', 'item_id': 'Red0750', 'distributor_name': 'Test Dist'},
            {'date_str': '01/15/2024', 'item_id': 'Red0750', 'distributor_name': 'Dist Two'},
        ])
        file1 = SimpleUploadedFile('multi.csv', content, content_type='text/csv')
        # Upload → preview
        self._post_files([file1])
        # Confirm
        response = self.client.post(reverse('import_preview'), {'action': 'confirm'})
        self.assertEqual(response.status_code, 302)
        # Two batches should exist
        batches = ImportBatch.objects.filter(company=self.company)
        self.assertEqual(batches.count(), 2)
        dist_names = set(batches.values_list('distributor__name', flat=True))
        self.assertIn('Test Dist', dist_names)
        self.assertIn('Dist Two', dist_names)


# ---------------------------------------------------------------------------
# ImportBatch.filename_display property
# ---------------------------------------------------------------------------

class ImportBatchFilenameDisplayTest(ImportTestBase):

    def _make_batch(self, filename_value):
        return ImportBatch(
            company=self.company,
            distributor=self.distributor,
            import_type=ImportBatch.ImportType.SALES_DATA,
            filename=filename_value,
        )

    def test_filename_display_single(self):
        batch = self._make_batch(_json.dumps(['sales_jan.csv']))
        self.assertEqual(batch.filename_display, 'sales_jan.csv')

    def test_filename_display_multiple(self):
        batch = self._make_batch(_json.dumps(['jan.csv', 'feb.csv']))
        self.assertEqual(batch.filename_display, '2 files: jan.csv, feb.csv')

    def test_filename_display_legacy(self):
        batch = self._make_batch('old_import.csv')
        self.assertEqual(batch.filename_display, 'old_import.csv')


# ---------------------------------------------------------------------------
# DB integrity
# ---------------------------------------------------------------------------

class ItemMappingProtectTest(ImportTestBase):

    def test_deleting_distributor_with_mapping_raises_protected_error(self):
        from django.db.models import ProtectedError
        with self.assertRaises(ProtectedError):
            self.distributor.delete()

    def test_mapping_still_exists_after_failed_distributor_delete(self):
        from django.db.models import ProtectedError
        try:
            self.distributor.delete()
        except ProtectedError:
            pass
        self.assertTrue(ItemMapping.objects.filter(pk=self.mapping.pk).exists())


# ---------------------------------------------------------------------------
# Replace-on-import (month-grain detect → preview → delete-and-replace)
# ---------------------------------------------------------------------------

from unittest.mock import patch


class ReplaceOnImportTest(ImportTestBase):
    """End-to-end replace-on-import: detection, preview blast-radius, typed
    confirmation, surgical delete + audit note, atomicity, whole-month grain."""

    def setUp(self):
        super().setUp()
        self.user = _make_supplier_admin(self.company)
        self.client = Client()
        self.client.login(username='admin', password='testpass')
        self.upload_url = reverse('import_upload')
        self.preview_url = reverse('import_preview')

    # -- helpers ------------------------------------------------------------

    def _add_distributor(self, name):
        dist = Distributor.objects.create(company=self.company, name=name)
        ItemMapping.objects.create(
            company=self.company, distributor=dist,
            raw_item_name='Red0750', mapped_item=self.item,
            status=ItemMapping.Status.MAPPED,
        )
        return dist

    def _upload(self, rows):
        """rows: list of dicts (date_str, quantity, distributor_name, ...)."""
        f = SimpleUploadedFile('import.csv', _csv_bytes(rows), content_type='text/csv')
        return self.client.post(self.upload_url, data={'csv_file': [f]})

    def _confirm(self, confirm_text=None):
        data = {'action': 'confirm'}
        if confirm_text is not None:
            data['confirm_text'] = confirm_text
        return self.client.post(self.preview_url, data)

    def _may(self, day, qty, dist='Test Dist'):
        return {'date_str': f'05/{day:02d}/2024', 'quantity': str(qty),
                'item_id': 'Red0750', 'distributor_name': dist}

    # -- 1. no overlap ------------------------------------------------------

    def test_import_no_overlap_proceeds_normally(self):
        resp = self._upload([self._may(15, 10)])
        self.assertEqual(resp.status_code, 302)
        pending = self.client.session['pending_import']
        self.assertEqual(pending['overlap'], [])
        self.assertFalse(pending['replace_preview']['has_overlap'])

        # Confirm without any typed confirmation → imports normally.
        resp2 = self._confirm()
        self.assertEqual(resp2.status_code, 302)
        self.assertEqual(SalesRecord.objects.filter(company=self.company).count(), 1)

    # -- 2. detection + preview blast-radius --------------------------------

    def test_import_overlap_detected_and_previewed(self):
        # Seed: Test Dist has 2 May records for one account.
        self._run_import([self._may(3, 5), self._may(17, 5)])
        seeded = SalesRecord.objects.filter(company=self.company).count()
        self.assertEqual(seeded, 2)

        resp = self._upload([self._may(20, 9)])
        self.assertEqual(resp.status_code, 302)
        rp = self.client.session['pending_import']['replace_preview']
        self.assertTrue(rp['has_overlap'])
        self.assertEqual(rp['combo_count'], 1)
        group = rp['groups'][0]
        self.assertEqual(group['distributor'], 'Test Dist')
        month = group['months'][0]
        self.assertEqual(month['label'], 'May 2024')
        self.assertEqual(month['record_count'], 2)
        self.assertEqual(month['account_count'], 1)
        self.assertEqual(rp['total_records'], 2)
        self.assertEqual(rp['total_accounts'], 1)

    # -- 3. delete + reimport -----------------------------------------------

    def test_import_replace_deletes_and_reimports(self):
        self._run_import([self._may(3, 5), self._may(17, 5)])  # old: qty 5, 2 records
        self._upload([self._may(1, 9), self._may(10, 9), self._may(20, 9)])  # new: qty 9, 3
        resp = self._confirm(confirm_text='DELETE')
        self.assertEqual(resp.status_code, 302)

        may = SalesRecord.objects.filter(
            company=self.company, sale_date__year=2024, sale_date__month=5,
        )
        self.assertEqual(may.count(), 3)                       # only the new import
        self.assertEqual(may.filter(quantity=5).count(), 0)    # old gone
        self.assertEqual(may.filter(quantity=9).count(), 3)    # new present

    # -- 4. partial overlap (surgical) --------------------------------------

    def test_import_replace_partial_overlap(self):
        dist_b = self._add_distributor('Dist B')
        dist_c = self._add_distributor('Dist C')

        # Seed A (Test Dist) Jan–May, and C March only — all qty 5.
        self._run_import([
            {'date_str': '01/10/2024', 'quantity': '5', 'item_id': 'Red0750'},
            {'date_str': '02/10/2024', 'quantity': '5', 'item_id': 'Red0750'},
            {'date_str': '03/10/2024', 'quantity': '5', 'item_id': 'Red0750'},
            {'date_str': '04/10/2024', 'quantity': '5', 'item_id': 'Red0750'},
            {'date_str': '05/10/2024', 'quantity': '5', 'item_id': 'Red0750'},
        ], distributor=self.distributor)
        self._run_import([
            {'date_str': '03/10/2024', 'quantity': '5', 'item_id': 'Red0750'},
        ], distributor=dist_c)

        # Upload: A Jan–May, B Apr, C March — all qty 9.
        rows = [
            {'date_str': '01/20/2024', 'quantity': '9', 'item_id': 'Red0750', 'distributor_name': 'Test Dist'},
            {'date_str': '02/20/2024', 'quantity': '9', 'item_id': 'Red0750', 'distributor_name': 'Test Dist'},
            {'date_str': '03/20/2024', 'quantity': '9', 'item_id': 'Red0750', 'distributor_name': 'Test Dist'},
            {'date_str': '04/20/2024', 'quantity': '9', 'item_id': 'Red0750', 'distributor_name': 'Test Dist'},
            {'date_str': '05/20/2024', 'quantity': '9', 'item_id': 'Red0750', 'distributor_name': 'Test Dist'},
            {'date_str': '04/20/2024', 'quantity': '9', 'item_id': 'Red0750', 'distributor_name': 'Dist B'},
            {'date_str': '03/20/2024', 'quantity': '9', 'item_id': 'Red0750', 'distributor_name': 'Dist C'},
        ]
        resp = self._upload(rows)
        self.assertEqual(resp.status_code, 302)
        # Overlap: A Jan–May (5) + C Mar (1) = 6 combos; B Apr is NOT overlap.
        self.assertEqual(self.client.session['pending_import']['replace_preview']['combo_count'], 6)
        self.assertEqual(self._confirm(confirm_text='DELETE').status_code, 302)

        def dist_qs(dist):
            return SalesRecord.objects.filter(company=self.company, account__distributor=dist)

        # A: old qty-5 fully replaced by qty-9 (5 records, all qty 9).
        self.assertEqual(dist_qs(self.distributor).count(), 5)
        self.assertEqual(dist_qs(self.distributor).filter(quantity=5).count(), 0)
        # C: March replaced (old qty-5 gone, new qty-9 present).
        self.assertEqual(dist_qs(dist_c).filter(quantity=5).count(), 0)
        self.assertEqual(dist_qs(dist_c).filter(sale_date__month=3, quantity=9).count(), 1)
        # B: Apr imported new, nothing deleted (had nothing before).
        self.assertEqual(dist_qs(dist_b).filter(sale_date__month=4, quantity=9).count(), 1)
        # No qty-5 record survives anywhere.
        self.assertEqual(SalesRecord.objects.filter(company=self.company, quantity=5).count(), 0)

    # -- 5. typed confirmation enforced server-side -------------------------

    def test_import_replace_requires_typed_confirmation(self):
        self._run_import([self._may(3, 5)])
        before_sales = SalesRecord.objects.filter(company=self.company).count()
        before_batches = ImportBatch.objects.filter(company=self.company).count()

        self._upload([self._may(20, 9)])
        # Confirm with the WRONG value → rejected, nothing changes.
        resp = self._confirm(confirm_text='delete')   # lowercase, must be exact
        self.assertEqual(resp.status_code, 200)
        msgs = [str(m) for m in resp.wsgi_request._messages]
        self.assertTrue(any('type delete' in m.lower() for m in msgs))
        self.assertIn('pending_import', self.client.session)   # still pending
        self.assertEqual(SalesRecord.objects.filter(company=self.company).count(), before_sales)
        self.assertEqual(ImportBatch.objects.filter(company=self.company).count(), before_batches)
        self.assertEqual(SalesRecord.objects.filter(quantity=9).count(), 0)  # nothing imported

    # -- 6. audit note appended; stats unchanged ----------------------------

    def test_import_replace_appends_audit_note(self):
        # Seed A Jan–May as ONE batch.
        batch = self._run_import([
            {'date_str': '01/10/2024', 'item_id': 'Red0750'},
            {'date_str': '02/10/2024', 'item_id': 'Red0750'},
            {'date_str': '03/10/2024', 'item_id': 'Red0750'},
            {'date_str': '04/10/2024', 'item_id': 'Red0750'},
            {'date_str': '05/10/2024', 'item_id': 'Red0750'},
        ])
        original_records_imported = batch.records_imported

        # Replace only May.
        self._upload([self._may(20, 9)])
        self.assertEqual(self._confirm(confirm_text='DELETE').status_code, 302)

        batch.refresh_from_db()
        self.assertIn('May 2024', batch.notes)
        self.assertIn('deleted and replaced', batch.notes)
        self.assertIn(self.user.get_username(), batch.notes)
        # No stat recompute — original counter is preserved.
        self.assertEqual(batch.records_imported, original_records_imported)

    # -- 7. one note line lists all replaced months for that batch ----------

    def test_import_replace_audit_note_lists_multiple_months_per_batch(self):
        batch = self._run_import([
            {'date_str': '01/10/2024', 'item_id': 'Red0750'},
            {'date_str': '02/10/2024', 'item_id': 'Red0750'},
            {'date_str': '03/10/2024', 'item_id': 'Red0750'},
            {'date_str': '04/10/2024', 'item_id': 'Red0750'},
            {'date_str': '05/10/2024', 'item_id': 'Red0750'},
        ])
        # Replace Jan AND Mar in one import.
        self._upload([
            {'date_str': '01/20/2024', 'quantity': '9', 'item_id': 'Red0750'},
            {'date_str': '03/20/2024', 'quantity': '9', 'item_id': 'Red0750'},
        ])
        self.assertEqual(self._confirm(confirm_text='DELETE').status_code, 302)

        batch.refresh_from_db()
        # Exactly ONE appended note line, listing both months chronologically.
        self.assertEqual(batch.notes.count('deleted and replaced'), 1)
        self.assertIn('Jan 2024, Mar 2024', batch.notes)

    # -- 8. atomic: failure rolls back deletion -----------------------------

    def test_import_replace_is_atomic(self):
        self._run_import([self._may(3, 5)])   # old May, qty 5
        self._upload([self._may(20, 9)])

        with patch('apps.imports.views._execute_import', side_effect=Exception('boom')):
            resp = self._confirm(confirm_text='DELETE')
        self.assertEqual(resp.status_code, 302)  # redirected to upload with error

        # Deletion rolled back with the failed import — old data intact.
        self.assertEqual(SalesRecord.objects.filter(quantity=5).count(), 1)
        self.assertEqual(SalesRecord.objects.filter(quantity=9).count(), 0)

    # -- 9. whole-month deletion (not just colliding dates) -----------------

    def test_import_replace_deletes_whole_month_not_just_colliding_dates(self):
        # Seed May 1, 15, 28 (qty 5).
        self._run_import([self._may(1, 5), self._may(15, 5), self._may(28, 5)])
        self.assertEqual(
            SalesRecord.objects.filter(sale_date__year=2024, sale_date__month=5).count(), 3,
        )

        # Re-import only May 5 and May 10.
        self._upload([self._may(5, 9), self._may(10, 9)])
        self.assertEqual(self._confirm(confirm_text='DELETE').status_code, 302)

        may = SalesRecord.objects.filter(sale_date__year=2024, sale_date__month=5)
        self.assertEqual(may.count(), 2)  # whole old month gone, only imported days remain
        days = set(may.values_list('sale_date__day', flat=True))
        self.assertEqual(days, {5, 10})
        # Original days are gone.
        self.assertFalse(may.filter(sale_date__day=1).exists())
        self.assertFalse(may.filter(sale_date__day=28).exists())
