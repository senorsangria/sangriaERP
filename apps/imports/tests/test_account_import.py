"""
Tests for the account import flow (multi-distributor).

First test coverage for account_import_views.py.
"""
import csv
import io

from django.contrib.messages import get_messages
from django.test import Client, TestCase
from django.urls import reverse

from apps.accounts.models import Account
from apps.core.models import Company, User
from apps.core.rbac import Role
from apps.distribution.models import Distributor
from django.core.files.uploadedfile import SimpleUploadedFile


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_company(name='Acme Bev'):
    return Company.objects.create(name=name)


def _make_supplier_admin(company, username='admin'):
    user = User.objects.create_user(username=username, password='pass', company=company)
    user.roles.set([Role.objects.get(codename='supplier_admin')])
    return user


def _make_distributor(company, name='Dist A'):
    return Distributor.objects.create(company=company, name=name)


def _csv_bytes(rows, headers=None):
    """Return UTF-8-sig encoded CSV bytes."""
    if headers is None:
        headers = [
            'Distributors', 'Retail Accounts', 'Address', 'City', 'State',
            'Zip Code', 'Counties', 'OnOff Premises',
        ]
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(headers)
    for r in rows:
        writer.writerow([
            r.get('dist', 'Dist A'),
            r.get('name', 'Test Store'),
            r.get('address', '1 Main St'),
            r.get('city', 'Hoboken'),
            r.get('state', 'NJ'),
            r.get('zip', '07030'),
            r.get('county', 'Hudson, NJ'),
            r.get('on_off', 'OFF'),
        ])
    return buf.getvalue().encode('utf-8-sig')


def _upload(client, content, filename='accounts.csv'):
    f = SimpleUploadedFile(filename, content, content_type='text/csv')
    return client.post(reverse('account_import_upload'), {'csv_file': f})


# ---------------------------------------------------------------------------
# Upload: valid Distributors column
# ---------------------------------------------------------------------------

class AccountImportUploadValidTest(TestCase):

    def setUp(self):
        self.company = _make_company()
        self.dist = _make_distributor(self.company, 'Dist A')
        self.user = _make_supplier_admin(self.company)
        self.client = Client()
        self.client.force_login(self.user)

    def test_upload_with_valid_distributors_column_proceeds_to_preview(self):
        resp = _upload(self.client, _csv_bytes([{'dist': 'Dist A'}]))
        self.assertRedirects(resp, reverse('account_import_preview'), fetch_redirect_response=False)
        self.assertIn('account_import_preview', self.client.session)

    def test_distributor_summaries_stored_in_session(self):
        _upload(self.client, _csv_bytes([{'dist': 'Dist A'}, {'dist': 'Dist A'}]))
        preview = self.client.session['account_import_preview']
        summaries = preview['distributor_summaries']
        self.assertEqual(len(summaries), 1)
        self.assertEqual(summaries[0]['name'], 'Dist A')

    def test_missing_distributors_column_raises_error(self):
        headers_no_dist = ['Retail Accounts', 'Address', 'City', 'State']
        bad_csv = (
            ','.join(headers_no_dist) + '\n'
            'Store,1 Main St,Hoboken,NJ\n'
        ).encode('utf-8-sig')
        resp = _upload(self.client, bad_csv)
        self.assertEqual(resp.status_code, 200)
        self.assertIn('Distributors', resp.content.decode())

    def test_empty_csv_shows_error(self):
        resp = _upload(self.client, b'')
        self.assertEqual(resp.status_code, 200)


# ---------------------------------------------------------------------------
# Upload: distributor name validation
# ---------------------------------------------------------------------------

class AccountImportDistributorValidationTest(TestCase):

    def setUp(self):
        self.company = _make_company()
        self.dist = _make_distributor(self.company, 'Colonial Beverage')
        self.user = _make_supplier_admin(self.company)
        self.client = Client()
        self.client.force_login(self.user)

    def test_unknown_distributor_name_aborts_import(self):
        resp = _upload(self.client, _csv_bytes([{'dist': 'Unknown Dist XYZ'}]))
        self.assertEqual(resp.status_code, 200)
        self.assertIn('Unknown Dist XYZ', resp.content.decode())
        self.assertNotIn('account_import_preview', self.client.session)

    def test_case_insensitive_match_passes(self):
        """'colonial beverage' (lowercase) matches stored 'Colonial Beverage'."""
        resp = _upload(self.client, _csv_bytes([{'dist': 'colonial beverage'}]))
        self.assertRedirects(resp, reverse('account_import_preview'), fetch_redirect_response=False)

    def test_inactive_distributor_treated_as_unknown(self):
        self.dist.is_active = False
        self.dist.save()
        resp = _upload(self.client, _csv_bytes([{'dist': 'Colonial Beverage'}]))
        self.assertEqual(resp.status_code, 200)
        self.assertNotIn('account_import_preview', self.client.session)


# ---------------------------------------------------------------------------
# Execute: accounts created with correct distributor
# ---------------------------------------------------------------------------

class AccountImportExecuteTest(TestCase):

    def setUp(self):
        self.company = _make_company()
        self.dist_a = _make_distributor(self.company, 'Dist A')
        self.dist_b = _make_distributor(self.company, 'Dist B')
        self.user = _make_supplier_admin(self.company)
        self.client = Client()
        self.client.force_login(self.user)

    def _do_full_import(self, rows):
        """Upload → preview → execute for given rows."""
        _upload(self.client, _csv_bytes(rows))
        self.client.post(
            reverse('account_import_execute'),
            {},
        )

    def test_account_created_with_correct_distributor(self):
        self._do_full_import([{'dist': 'Dist A', 'name': 'Store A', 'address': '1 Main St'}])
        acct = Account.objects.get(name='Store A')
        self.assertEqual(acct.distributor, self.dist_a)

    def test_multi_distributor_accounts_use_per_row_distributor(self):
        rows = [
            {'dist': 'Dist A', 'name': 'Store A', 'address': '1 Main St'},
            {'dist': 'Dist B', 'name': 'Store B', 'address': '2 Oak Ave'},
        ]
        self._do_full_import(rows)
        self.assertEqual(Account.objects.get(name='Store A').distributor, self.dist_a)
        self.assertEqual(Account.objects.get(name='Store B').distributor, self.dist_b)

    def test_account_matching_scoped_per_distributor(self):
        """Same address under different distributors = two separate accounts (CREATE both)."""
        rows = [
            {'dist': 'Dist A', 'name': 'Store A', 'address': '10 Elm St',
             'city': 'Newark', 'state': 'NJ'},
            {'dist': 'Dist B', 'name': 'Store B', 'address': '10 Elm St',
             'city': 'Newark', 'state': 'NJ'},
        ]
        self._do_full_import(rows)
        self.assertEqual(Account.objects.filter(street='10 Elm St').count(), 2)
        self.assertNotEqual(
            Account.objects.get(distributor=self.dist_a).pk,
            Account.objects.get(distributor=self.dist_b).pk,
        )

    def test_existing_account_updated_not_duplicated(self):
        from utils.normalize import normalize_address
        existing = Account.objects.create(
            company=self.company, distributor=self.dist_a,
            name='Store A', street='1 Main St', city='Hoboken', state='NJ',
            address_normalized=normalize_address('1 Main St'),
            city_normalized='HOBOKEN', state_normalized='NJ',
        )
        rows = [{'dist': 'Dist A', 'name': 'Store A', 'address': '1 Main St'}]
        self._do_full_import(rows)
        # Should still be only one account
        self.assertEqual(Account.objects.filter(name='Store A').count(), 1)

    def test_session_cleared_after_execute(self):
        self._do_full_import([{'dist': 'Dist A'}])
        self.assertNotIn('account_import_preview', self.client.session)


# ---------------------------------------------------------------------------
# Preview page
# ---------------------------------------------------------------------------

class AccountImportPreviewTest(TestCase):

    def setUp(self):
        self.company = _make_company()
        self.dist = _make_distributor(self.company, 'Dist A')
        self.user = _make_supplier_admin(self.company)
        self.client = Client()
        self.client.force_login(self.user)

    def test_preview_shows_distributor_summary_table(self):
        _upload(self.client, _csv_bytes([{'dist': 'Dist A'}]))
        resp = self.client.get(reverse('account_import_preview'))
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b'Dist A', resp.content)

    def test_preview_shows_distributor_column_in_rows_table(self):
        _upload(self.client, _csv_bytes([{'dist': 'Dist A', 'name': 'Store X'}]))
        resp = self.client.get(reverse('account_import_preview'))
        self.assertIn(b'Store X', resp.content)

    def test_preview_redirects_when_no_session(self):
        resp = self.client.get(reverse('account_import_preview'))
        self.assertRedirects(
            resp, reverse('account_import_upload'), fetch_redirect_response=False
        )
