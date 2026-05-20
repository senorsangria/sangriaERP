"""
Tests for Phase 2b-1: Inventory snapshot CSV upload flow.

Covers:
- Model: InventorySnapshot.quantity_cases (DecimalField), InventoryImportBatch,
  import_batch FK (SET_NULL on batch delete)
- CSV parser: parse_inventory_csv
- Validator: validate_inventory_import
- Upload flow: inventory_upload, inventory_preview, inventory_confirm views
- Inventory tab: empty state, populated state, most-recent-per-pair logic,
  permission gating
"""
import csv
import io
import os
import tempfile
from decimal import Decimal
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.test import Client, TestCase
from django.urls import reverse

from apps.catalog.models import Brand, Item
from apps.core.models import Company, User
from apps.core.rbac import Role
from apps.distribution.models import (
    Distributor,
    DistributorItemProfile,
    InventoryImportBatch,
    InventorySnapshot,
)
from apps.distribution.views import parse_inventory_csv, validate_inventory_import
from apps.imports.models import ItemMapping


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------

def make_company(name='Test Co'):
    return Company.objects.create(name=name)


def make_supplier_admin(company, username='admin'):
    user = User.objects.create_user(
        username=username, password='testpass123', company=company,
    )
    user.roles.set([Role.objects.get(codename='supplier_admin')])
    return user


def make_user_no_inventory_perm(company, username='limited'):
    """Create a user with can_manage_distributors but NOT can_manage_distributor_inventory.

    saas_admin has can_manage_distributors but was never granted
    can_manage_distributor_inventory (see migration 0013).
    """
    user = User.objects.create_user(
        username=username, password='testpass123', company=company,
    )
    user.roles.set([Role.objects.get(codename='saas_admin')])
    return user


def make_distributor(company, name='Test Distributor'):
    return Distributor.objects.create(company=company, name=name)


def make_brand(company, name='Test Brand'):
    return Brand.objects.create(company=company, name=name)


def make_item(brand, name='Red 750ml', item_code='RED750'):
    return Item.objects.create(brand=brand, name=name, item_code=item_code)


def make_item_mapping(company, distributor, item, raw_item_name=None, status=None):
    """Create a MAPPED ItemMapping for the given item."""
    return ItemMapping.objects.create(
        company=company,
        distributor=distributor,
        raw_item_name=raw_item_name or item.item_code,
        mapped_item=item,
        status=status or ItemMapping.Status.MAPPED,
    )


def make_csv_file(rows, headers=None):
    """
    Build a temp CSV file with the given rows.
    Default headers: ['Distributors', 'Item Name ID', 'Quantity On Hand']
    Each row: [distributor_name, item_code, quantity_str]
    Returns the temp file path (caller must clean up).
    """
    if headers is None:
        headers = ['Distributors', 'Item Name ID', 'Quantity On Hand']
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(headers)
    for row in rows:
        writer.writerow(row)
    tmp = tempfile.NamedTemporaryFile(
        mode='w', suffix='.csv', delete=False, encoding='utf-8-sig',
    )
    tmp.write(buf.getvalue())
    tmp.flush()
    tmp.close()
    return tmp.name


def make_csv_bytes(rows, headers=None):
    """Return CSV content as UTF-8 bytes for Django's SimpleUploadedFile."""
    if headers is None:
        headers = ['Distributors', 'Item Name ID', 'Quantity On Hand']
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(headers)
    for row in rows:
        writer.writerow(row)
    return buf.getvalue().encode('utf-8-sig')


# ---------------------------------------------------------------------------
# 1. Model: quantity_cases DecimalField
# ---------------------------------------------------------------------------

class InventorySnapshotDecimalFieldTest(TestCase):

    def setUp(self):
        self.company = make_company()
        self.distributor = make_distributor(self.company)
        self.brand = make_brand(self.company)
        self.item = make_item(self.brand)

    def test_quantity_cases_accepts_decimal(self):
        snapshot = InventorySnapshot.objects.create(
            distributor=self.distributor,
            item=self.item,
            quantity_cases=Decimal('54.166667'),
            year=2026,
            month=4,
        )
        snapshot.refresh_from_db()
        self.assertEqual(snapshot.quantity_cases, Decimal('54.166667'))

    def test_quantity_cases_rejects_negative(self):
        snapshot = InventorySnapshot(
            distributor=self.distributor,
            item=self.item,
            quantity_cases=Decimal('-1'),
            year=2026,
            month=4,
        )
        with self.assertRaises(ValidationError):
            snapshot.full_clean()

    def test_quantity_cases_accepts_zero(self):
        snapshot = InventorySnapshot.objects.create(
            distributor=self.distributor,
            item=self.item,
            quantity_cases=Decimal('0'),
            year=2026,
            month=4,
        )
        snapshot.refresh_from_db()
        self.assertEqual(snapshot.quantity_cases, Decimal('0'))

    def test_quantity_cases_accepts_whole_number(self):
        snapshot = InventorySnapshot.objects.create(
            distributor=self.distributor,
            item=self.item,
            quantity_cases=100,
            year=2026,
            month=5,
        )
        snapshot.refresh_from_db()
        self.assertEqual(snapshot.quantity_cases, 100)


# ---------------------------------------------------------------------------
# 2. Model: InventoryImportBatch
# ---------------------------------------------------------------------------

class InventoryImportBatchTest(TestCase):

    def setUp(self):
        self.company = make_company()
        self.user = make_supplier_admin(self.company)

    def test_inventory_import_batch_create_and_str(self):
        batch = InventoryImportBatch.objects.create(
            company=self.company,
            year=2026,
            month=4,
            uploaded_by=self.user,
            filename='inv_april.csv',
            distributor_count=3,
            snapshots_created=42,
        )
        batch.refresh_from_db()
        self.assertEqual(batch.year, 2026)
        self.assertEqual(batch.month, 4)
        self.assertEqual(batch.distributor_count, 3)
        self.assertEqual(batch.snapshots_created, 42)
        self.assertIn('2026-04', str(batch))
        self.assertIn('3 distributors', str(batch))
        self.assertIn('42 items', str(batch))

    def test_inventory_snapshot_import_batch_set_null_on_batch_delete(self):
        distributor = make_distributor(self.company)
        brand = make_brand(self.company)
        item = make_item(brand)
        batch = InventoryImportBatch.objects.create(
            company=self.company,
            year=2026,
            month=4,
            uploaded_by=self.user,
            filename='inv.csv',
            distributor_count=1,
            snapshots_created=1,
        )
        snapshot = InventorySnapshot.objects.create(
            distributor=distributor,
            item=item,
            quantity_cases=Decimal('10'),
            year=2026,
            month=4,
            import_batch=batch,
        )
        batch.delete()
        snapshot.refresh_from_db()
        self.assertIsNone(snapshot.import_batch)


# ---------------------------------------------------------------------------
# 3–11. CSV parser tests
# ---------------------------------------------------------------------------

class ParseInventoryCSVTest(TestCase):

    def tearDown(self):
        # Clean up any temp files created by tests that don't clean up themselves
        pass

    def test_parse_csv_valid_file(self):
        filepath = make_csv_file([
            ['Acme Dist', 'RED750', '100'],
            ['Acme Dist', 'WHT750', '54.166667'],
        ])
        try:
            rows, errors = parse_inventory_csv(filepath)
            self.assertEqual(errors, [])
            self.assertEqual(len(rows), 2)
            self.assertEqual(rows[0]['distributor_name'], 'Acme Dist')
            self.assertEqual(rows[0]['item_code'], 'RED750')
            self.assertEqual(rows[0]['quantity'], Decimal('100'))
            self.assertEqual(rows[1]['quantity'], Decimal('54.166667'))
        finally:
            os.unlink(filepath)

    def test_parse_csv_missing_headers(self):
        filepath = make_csv_file(
            [['Acme', 'RED750', '100']],
            headers=['WrongCol1', 'Item Name ID', 'Qty'],
        )
        try:
            rows, errors = parse_inventory_csv(filepath)
            self.assertEqual(rows, [])
            self.assertTrue(len(errors) > 0)
            self.assertIn('Distributors', errors[0])
        finally:
            os.unlink(filepath)

    def test_parse_csv_wrong_column_count_two_cols(self):
        filepath = make_csv_file(
            [['Acme', 'RED750']],
            headers=['Distributors', 'Item Name ID'],
        )
        try:
            rows, errors = parse_inventory_csv(filepath)
            self.assertEqual(rows, [])
            self.assertEqual(len(errors), 1)
            self.assertIn('3 columns', errors[0])
        finally:
            os.unlink(filepath)

    def test_parse_csv_wrong_column_count_four_cols(self):
        filepath = make_csv_file(
            [['Acme', 'RED750', '100', 'extra']],
            headers=['Distributors', 'Item Name ID', 'Qty', 'Extra'],
        )
        try:
            rows, errors = parse_inventory_csv(filepath)
            self.assertEqual(rows, [])
            self.assertEqual(len(errors), 1)
            self.assertIn('3 columns', errors[0])
        finally:
            os.unlink(filepath)

    def test_parse_csv_fractional_quantities(self):
        filepath = make_csv_file([
            ['Acme Dist', 'RED750', '54.166667'],
            ['Acme Dist', 'WHT750', '0.5'],
        ])
        try:
            rows, errors = parse_inventory_csv(filepath)
            self.assertEqual(errors, [])
            self.assertEqual(rows[0]['quantity'], Decimal('54.166667'))
            self.assertEqual(rows[1]['quantity'], Decimal('0.5'))
        finally:
            os.unlink(filepath)

    def test_parse_csv_negative_quantity(self):
        filepath = make_csv_file([
            ['Acme Dist', 'RED750', '-5'],
        ])
        try:
            rows, errors = parse_inventory_csv(filepath)
            self.assertEqual(len(errors), 1)
            self.assertIn('-5', errors[0])
        finally:
            os.unlink(filepath)

    def test_parse_csv_non_numeric_quantity(self):
        filepath = make_csv_file([
            ['Acme Dist', 'RED750', 'abc'],
        ])
        try:
            rows, errors = parse_inventory_csv(filepath)
            self.assertEqual(len(errors), 1)
            self.assertIn('abc', errors[0])
        finally:
            os.unlink(filepath)

    def test_parse_csv_empty_rows_skipped(self):
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(['Distributors', 'Item Name ID', 'Quantity On Hand'])
        writer.writerow(['Acme Dist', 'RED750', '100'])
        writer.writerow(['', '', ''])  # blank row
        writer.writerow(['Acme Dist', 'WHT750', '50'])
        tmp = tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False, encoding='utf-8-sig')
        tmp.write(buf.getvalue())
        tmp.flush()
        tmp.close()
        try:
            rows, errors = parse_inventory_csv(tmp.name)
            self.assertEqual(errors, [])
            self.assertEqual(len(rows), 2)
        finally:
            os.unlink(tmp.name)

    def test_parse_csv_header_case_insensitive(self):
        filepath = make_csv_file(
            [['Acme Dist', 'RED750', '100']],
            headers=['distributors', 'item name id', 'qty'],  # lowercase
        )
        try:
            rows, errors = parse_inventory_csv(filepath)
            self.assertEqual(errors, [])
            self.assertEqual(len(rows), 1)
        finally:
            os.unlink(filepath)


# ---------------------------------------------------------------------------
# 12–16. Validator tests
# ---------------------------------------------------------------------------

class ValidateInventoryImportTest(TestCase):

    def setUp(self):
        self.company = make_company()
        self.distributor = make_distributor(self.company, name='Acme Dist')
        self.brand = make_brand(self.company)
        self.item = make_item(self.brand, item_code='RED750')
        self.mapping = make_item_mapping(self.company, self.distributor, self.item)

    def _make_rows(self, dist_name='Acme Dist', item_code='RED750', qty='100'):
        return [{'row_number': 2, 'distributor_name': dist_name, 'item_code': item_code, 'quantity': Decimal(qty)}]

    def test_validate_unknown_distributor(self):
        rows = self._make_rows(dist_name='Unknown Dist')
        resolved, errors, unmapped = validate_inventory_import(rows, self.company, 2026, 4)
        self.assertEqual(resolved, [])
        self.assertTrue(any('Unknown Dist' in e for e in errors))

    def test_validate_unmapped_item_code(self):
        # Unmapped codes are now returned in the third value, not in errors.
        rows = self._make_rows(item_code='UNMAPPED')
        resolved, errors, unmapped = validate_inventory_import(rows, self.company, 2026, 4)
        self.assertEqual(resolved, [])
        self.assertEqual(errors, [])
        self.assertTrue(unmapped)
        # All unmapped codes are keyed by distributor ID
        all_codes = [c for codes in unmapped.values() for c in codes]
        self.assertIn('UNMAPPED', all_codes)

    def test_validate_ignored_item_code(self):
        # An IGNORED mapping is treated the same as unmapped — goes to resolution UI.
        self.mapping.status = ItemMapping.Status.IGNORED
        self.mapping.save()
        rows = self._make_rows()
        resolved, errors, unmapped = validate_inventory_import(rows, self.company, 2026, 4)
        self.assertEqual(resolved, [])
        self.assertEqual(errors, [])
        all_codes = [c for codes in unmapped.values() for c in codes]
        self.assertIn('RED750', all_codes)

    def test_validate_period_conflict(self):
        InventorySnapshot.objects.create(
            distributor=self.distributor,
            item=self.item,
            quantity_cases=Decimal('50'),
            year=2026,
            month=4,
        )
        rows = self._make_rows()
        resolved, errors, unmapped = validate_inventory_import(rows, self.company, 2026, 4)
        self.assertEqual(resolved, [])
        self.assertTrue(any('already has inventory data' in e for e in errors))
        self.assertTrue(any('April 2026' in e for e in errors))

    def test_validate_success_path(self):
        rows = self._make_rows()
        resolved, errors, unmapped = validate_inventory_import(rows, self.company, 2026, 4)
        self.assertEqual(errors, [])
        self.assertEqual(unmapped, {})
        self.assertEqual(len(resolved), 1)
        self.assertEqual(resolved[0]['distributor'], self.distributor)
        self.assertEqual(resolved[0]['item'], self.item)
        self.assertEqual(resolved[0]['quantity'], Decimal('100'))

    def test_validate_item_code_case_insensitive(self):
        """Mapping stored as 'SPRITZwht' resolves when CSV has 'SPRITZWHT'."""
        spritz_item = make_item(self.brand, name='Spritz White 750ml', item_code='SPRITZwht')
        make_item_mapping(self.company, self.distributor, spritz_item, raw_item_name='SPRITZwht')
        # CSV uses uppercase variant
        rows = self._make_rows(item_code='SPRITZWHT')
        resolved, errors, unmapped = validate_inventory_import(rows, self.company, 2026, 4)
        self.assertEqual(errors, [])
        self.assertEqual(unmapped, {})
        self.assertEqual(len(resolved), 1)
        self.assertEqual(resolved[0]['item'], spritz_item)


# ---------------------------------------------------------------------------
# 17–20. Upload view tests
# ---------------------------------------------------------------------------

class InventoryUploadViewTest(TestCase):

    def setUp(self):
        self.company = make_company()
        self.admin = make_supplier_admin(self.company)
        self.distributor = make_distributor(self.company, name='Acme Dist')
        self.brand = make_brand(self.company)
        self.item = make_item(self.brand)
        self.mapping = make_item_mapping(self.company, self.distributor, self.item)
        self.client = Client()
        self.client.login(username='admin', password='testpass123')
        self.url = reverse('inventory_upload')

    def test_upload_view_get_renders_form(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Upload Inventory Snapshot')

    def test_upload_view_requires_permission(self):
        limited = make_user_no_inventory_perm(self.company)
        self.client.login(username='limited', password='testpass123')
        response = self.client.get(self.url)
        # Should redirect (lacks can_manage_distributor_inventory)
        self.assertEqual(response.status_code, 302)

    def test_upload_view_post_valid_file_redirects_to_preview(self):
        from django.core.files.uploadedfile import SimpleUploadedFile
        csv_content = make_csv_bytes([['Acme Dist', 'RED750', '100']])
        f = SimpleUploadedFile('inv.csv', csv_content, content_type='text/csv')
        response = self.client.post(self.url, {
            'year': 2026,
            'month': 4,
            'csv_file': f,
        })
        self.assertRedirects(response, reverse('inventory_preview'))
        self.assertIn('pending_inventory_import', self.client.session)

    def test_upload_view_post_invalid_file_re_renders(self):
        from django.core.files.uploadedfile import SimpleUploadedFile
        bad_csv = make_csv_bytes(
            [['Acme Dist', 'RED750', '100']],
            headers=['WrongCol', 'Item Name ID', 'Qty'],
        )
        f = SimpleUploadedFile('inv.csv', bad_csv, content_type='text/csv')
        response = self.client.post(self.url, {
            'year': 2026,
            'month': 4,
            'csv_file': f,
        })
        self.assertEqual(response.status_code, 200)
        self.assertNotIn('pending_inventory_import', self.client.session)
        self.assertContains(response, 'Distributors')  # error message mentions expected col


# ---------------------------------------------------------------------------
# 21–22. Preview view tests
# ---------------------------------------------------------------------------

class InventoryPreviewViewTest(TestCase):

    def setUp(self):
        self.company = make_company()
        self.admin = make_supplier_admin(self.company)
        self.client = Client()
        self.client.login(username='admin', password='testpass123')

    def _set_session(self, year=2026, month=4):
        session = self.client.session
        session['pending_inventory_import'] = {
            'year': year,
            'month': month,
            'filename': 'inv.csv',
            'temp_file_path': '/nonexistent/file.csv',
            'preview': {
                'total_rows': 5,
                'distributor_summaries': [
                    {'name': 'Acme Dist', 'item_count': 5, 'total_cases': '500'},
                ],
            },
        }
        session.save()

    def test_preview_view_renders_summary(self):
        self._set_session()
        response = self.client.get(reverse('inventory_preview'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'April 2026')
        self.assertContains(response, 'Acme Dist')
        self.assertContains(response, '5')  # item count

    def test_preview_view_no_session_redirects_to_upload(self):
        response = self.client.get(reverse('inventory_preview'))
        self.assertRedirects(response, reverse('inventory_upload'))


# ---------------------------------------------------------------------------
# 23–25. Confirm view tests
# ---------------------------------------------------------------------------

class InventoryConfirmViewTest(TestCase):

    def setUp(self):
        self.company = make_company()
        self.admin = make_supplier_admin(self.company)
        self.distributor = make_distributor(self.company, name='Acme Dist')
        self.brand = make_brand(self.company)
        self.item = make_item(self.brand)
        self.mapping = make_item_mapping(self.company, self.distributor, self.item)
        self.client = Client()
        self.client.login(username='admin', password='testpass123')

    def _build_csv_and_session(self, rows=None, year=2026, month=4):
        """Create a real temp CSV and populate session."""
        if rows is None:
            rows = [['Acme Dist', 'RED750', '100']]
        filepath = make_csv_file(rows)
        session = self.client.session
        session['pending_inventory_import'] = {
            'year': year,
            'month': month,
            'filename': 'inv.csv',
            'temp_file_path': filepath,
            'preview': {
                'total_rows': len(rows),
                'distributor_summaries': [
                    {'name': 'Acme Dist', 'item_count': len(rows), 'total_cases': '100'},
                ],
            },
        }
        session.save()
        return filepath

    def test_confirm_creates_snapshots_and_batch(self):
        filepath = self._build_csv_and_session()
        try:
            response = self.client.post(reverse('inventory_confirm'))
            self.assertEqual(response.status_code, 302)
            # Session cleared
            self.assertNotIn('pending_inventory_import', self.client.session)
            # Temp file cleaned up
            self.assertFalse(os.path.exists(filepath))
            # Batch and snapshot created
            self.assertEqual(InventoryImportBatch.objects.count(), 1)
            self.assertEqual(InventorySnapshot.objects.count(), 1)
            batch = InventoryImportBatch.objects.get()
            self.assertEqual(batch.year, 2026)
            self.assertEqual(batch.month, 4)
            self.assertEqual(batch.snapshots_created, 1)
            self.assertEqual(batch.distributor_count, 1)
            snap = InventorySnapshot.objects.get()
            self.assertEqual(snap.quantity_cases, Decimal('100'))
            self.assertEqual(snap.import_batch, batch)
        finally:
            if os.path.exists(filepath):
                os.unlink(filepath)

    def test_confirm_is_transactional(self):
        filepath = self._build_csv_and_session()
        try:
            with patch('apps.distribution.views.InventorySnapshot.objects.create', side_effect=Exception('DB error')):
                response = self.client.post(reverse('inventory_confirm'))
            # Should redirect back to preview (session still has pending)
            self.assertRedirects(response, reverse('inventory_preview'))
            # Nothing created
            self.assertEqual(InventoryImportBatch.objects.count(), 0)
            self.assertEqual(InventorySnapshot.objects.count(), 0)
        finally:
            if os.path.exists(filepath):
                os.unlink(filepath)

    def test_confirm_auto_activates_inactive_profiles(self):
        # Pre-create an inactive profile for this (distributor, item)
        DistributorItemProfile.objects.create(
            distributor=self.distributor,
            item=self.item,
            is_active=False,
        )
        filepath = self._build_csv_and_session()
        try:
            self.client.post(reverse('inventory_confirm'))
            profile = DistributorItemProfile.objects.get(
                distributor=self.distributor, item=self.item
            )
            self.assertTrue(profile.is_active)
        finally:
            if os.path.exists(filepath):
                os.unlink(filepath)


# ---------------------------------------------------------------------------
# 26–29. Inventory tab UI tests
# ---------------------------------------------------------------------------

class InventoryTabTest(TestCase):

    def setUp(self):
        self.company = make_company()
        self.admin = make_supplier_admin(self.company)
        self.distributor = make_distributor(self.company)
        self.brand = make_brand(self.company)
        self.item = make_item(self.brand)
        self.client = Client()
        self.client.login(username='admin', password='testpass123')
        self.url = reverse('distributor_list') + '?tab=inventory'

    def test_inventory_tab_empty_state(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'No inventory data yet')

    def test_inventory_tab_populated(self):
        InventorySnapshot.objects.create(
            distributor=self.distributor,
            item=self.item,
            quantity_cases=Decimal('100'),
            year=2026,
            month=4,
        )
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.item.item_code)
        self.assertContains(response, '100')

    def test_inventory_tab_shows_all_periods_for_pair(self):
        # Jan snapshot
        InventorySnapshot.objects.create(
            distributor=self.distributor,
            item=self.item,
            quantity_cases=Decimal('50'),
            year=2026,
            month=1,
        )
        # Feb snapshot (more recent)
        InventorySnapshot.objects.create(
            distributor=self.distributor,
            item=self.item,
            quantity_cases=Decimal('75'),
            year=2026,
            month=2,
        )
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        rows = response.context['inventory_rows']
        # Phase 2b-2: all periods shown, not just most recent
        self.assertEqual(len(rows), 2)
        period_displays = [r['period_display'] for r in rows]
        self.assertIn('Jan 2026', period_displays)
        self.assertIn('Feb 2026', period_displays)

    def test_inventory_tab_upload_button_hidden_without_permission(self):
        limited = make_user_no_inventory_perm(self.company)
        self.client.login(username='limited', password='testpass123')
        # Without inventory permission, tab falls back to distributors
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, reverse('inventory_upload'))

    def test_inventory_tab_upload_button_visible_with_permission(self):
        InventorySnapshot.objects.create(
            distributor=self.distributor,
            item=self.item,
            quantity_cases=Decimal('10'),
            year=2026,
            month=4,
        )
        response = self.client.get(self.url)
        self.assertContains(response, reverse('inventory_upload'))

    def test_inventory_tab_data_loads_when_default_tab_active(self):
        """Inventory data must be populated even when landing on the default tab.

        Regression test: the Bootstrap tab button doesn't reload the page, so
        inventory data must be loaded server-side regardless of active_tab.
        Previously the data was gated behind active_tab == 'inventory', which
        meant a user arriving at /distributors/ (active_tab='distributors') and
        clicking the Inventory tab via Bootstrap JS would always see empty state.
        """
        InventorySnapshot.objects.create(
            distributor=self.distributor,
            item=self.item,
            quantity_cases=Decimal('42'),
            year=2026,
            month=4,
        )
        # GET without ?tab= param → active_tab defaults to 'distributors'
        base_url = reverse('distributor_list')
        response = self.client.get(base_url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['active_tab'], 'distributors')
        # Inventory data must still be populated
        self.assertTrue(response.context['has_any_snapshots'])
        self.assertEqual(len(response.context['inventory_rows']), 1)
        self.assertEqual(response.context['inventory_rows'][0]['quantity_display'], '42')
