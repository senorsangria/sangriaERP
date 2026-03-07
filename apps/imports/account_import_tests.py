"""
Tests for the Account Import feature — Phase 10.6.

Covers:
- CSV parsing (valid data, missing key fields, zip/county normalization)
- Session-based preview creation
- Account create and update execution
- Access control (403 for non-Supplier-Admin)
- Update does not overwrite key fields or is_active
"""
import io

from django.contrib.messages import get_messages
from django.test import Client, TestCase
from django.urls import reverse

from apps.accounts.models import Account
from apps.core.models import Company, User
from apps.core.rbac import Role
from apps.imports.account_import_views import (
    _parse_account_csv,
    _parse_county,
    _strip_excel_zip,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_csv(rows, include_optional=True):
    """
    Build a CSV string from a list of dicts.

    Minimal required columns: Retail Accounts, Address, City, State.
    Optional columns included by default.
    """
    if include_optional:
        headers = [
            'Retail Accounts', 'Address', 'City', 'State', 'Zip Code',
            'Counties', 'OnOff Premises', 'Classes of Trade',
            'VIP Outlet ID', 'Distributor Routes',
        ]
        def row_values(r):
            return [
                r.get('name', 'Test Store'),
                r.get('street', '1 Main St'),
                r.get('city', 'Hoboken'),
                r.get('state', 'NJ'),
                r.get('zip_code', '07030'),
                r.get('county', 'Hudson, NJ'),
                r.get('on_off', 'OFF'),
                r.get('account_type', 'Liquor Store'),
                r.get('third_party_id', 'VIP001'),
                r.get('distributor_route', 'Route 1'),
            ]
    else:
        headers = ['Retail Accounts', 'Address', 'City', 'State']
        def row_values(r):
            return [
                r.get('name', 'Test Store'),
                r.get('street', '1 Main St'),
                r.get('city', 'Hoboken'),
                r.get('state', 'NJ'),
            ]

    lines = [','.join(headers)]
    for r in rows:
        values = row_values(r)
        lines.append(','.join(str(v) for v in values))
    return '\n'.join(lines)


class AccountImportTestBase(TestCase):
    """Base class: sets up company, supplier admin user, and non-admin user."""

    def setUp(self):
        self.company = Company.objects.create(name='Test Beverage Co')

        # Supplier Admin
        self.sa_role = Role.objects.get(codename='supplier_admin')
        self.sa_user = User.objects.create_user(
            username='supplier_admin_test',
            password='testpass123',
            company=self.company,
        )
        self.sa_user.roles.set([self.sa_role])

        # Ambassador (non-admin)
        self.amb_role = Role.objects.get(codename='ambassador')
        self.amb_user = User.objects.create_user(
            username='ambassador_test',
            password='testpass123',
            company=self.company,
        )
        self.amb_user.roles.set([self.amb_role])

        self.client = Client()

    def _login_sa(self):
        self.client.force_login(self.sa_user)

    def _login_amb(self):
        self.client.force_login(self.amb_user)

    def _upload_csv(self, csv_text):
        """POST the upload form with a CSV string."""
        f = io.BytesIO(csv_text.encode('utf-8'))
        f.name = 'test_accounts.csv'
        return self.client.post(
            reverse('account_import_upload'),
            {'csv_file': f},
        )


# ---------------------------------------------------------------------------
# Unit tests for parsing helpers
# ---------------------------------------------------------------------------

class StripExcelZipTest(TestCase):

    def test_excel_format_stripped(self):
        self.assertEqual(_strip_excel_zip('="07030"'), '07030')

    def test_quoted_format_stripped(self):
        self.assertEqual(_strip_excel_zip('"07030"'), '07030')

    def test_plain_zip_unchanged(self):
        self.assertEqual(_strip_excel_zip('07030'), '07030')

    def test_empty_string(self):
        self.assertEqual(_strip_excel_zip(''), '')

    def test_whitespace_stripped(self):
        self.assertEqual(_strip_excel_zip('  ="07030"  '), '07030')


class ParseCountyTest(TestCase):

    def test_state_suffix_stripped(self):
        self.assertEqual(_parse_county('UNION, NJ'), 'UNION')

    def test_hudson_nj_stripped(self):
        self.assertEqual(_parse_county('HUDSON, NJ'), 'HUDSON')

    def test_no_suffix(self):
        self.assertEqual(_parse_county('UNION'), 'UNION')

    def test_empty(self):
        self.assertEqual(_parse_county(''), '')

    def test_whitespace(self):
        self.assertEqual(_parse_county('  ESSEX ,  NJ  '), 'ESSEX')


# ---------------------------------------------------------------------------
# CSV parsing tests
# ---------------------------------------------------------------------------

class ParseAccountCsvTest(AccountImportTestBase):

    def test_valid_csv_returns_rows(self):
        csv_text = _make_csv([{'name': 'Store A'}, {'name': 'Store B'}])
        rows, skipped = _parse_account_csv(io.BytesIO(csv_text.encode()))
        self.assertEqual(len(rows), 2)
        self.assertEqual(skipped, 0)

    def test_missing_name_row_skipped(self):
        csv_text = _make_csv([
            {'name': 'Good Store'},
            {'name': ''},          # missing name
        ])
        rows, skipped = _parse_account_csv(io.BytesIO(csv_text.encode()))
        self.assertEqual(len(rows), 1)
        self.assertEqual(skipped, 1)

    def test_missing_street_row_skipped(self):
        csv_text = _make_csv([{'name': 'Store', 'street': ''}])
        rows, skipped = _parse_account_csv(io.BytesIO(csv_text.encode()))
        self.assertEqual(skipped, 1)

    def test_missing_city_row_skipped(self):
        csv_text = _make_csv([{'name': 'Store', 'city': ''}])
        rows, skipped = _parse_account_csv(io.BytesIO(csv_text.encode()))
        self.assertEqual(skipped, 1)

    def test_missing_state_row_skipped(self):
        csv_text = _make_csv([{'name': 'Store', 'state': ''}])
        rows, skipped = _parse_account_csv(io.BytesIO(csv_text.encode()))
        self.assertEqual(skipped, 1)

    def test_zip_excel_format_stripped(self):
        csv_text = _make_csv([{'zip_code': '="07030"'}])
        rows, _ = _parse_account_csv(io.BytesIO(csv_text.encode()))
        self.assertEqual(rows[0]['zip_code'], '07030')

    def test_county_state_suffix_stripped(self):
        csv_text = _make_csv([{'county': 'UNION, NJ'}])
        rows, _ = _parse_account_csv(io.BytesIO(csv_text.encode()))
        self.assertEqual(rows[0]['county'], 'UNION')

    def test_on_off_stored_correctly(self):
        csv_text = _make_csv([{'on_off': 'ON'}])
        rows, _ = _parse_account_csv(io.BytesIO(csv_text.encode()))
        self.assertEqual(rows[0]['on_off_premise'], 'ON')

    def test_invalid_on_off_becomes_unknown(self):
        csv_text = _make_csv([{'on_off': 'MAYBE'}])
        rows, _ = _parse_account_csv(io.BytesIO(csv_text.encode()))
        self.assertEqual(rows[0]['on_off_premise'], 'Unknown')

    def test_optional_columns_absent_still_parsed(self):
        csv_text = _make_csv([{}], include_optional=False)
        rows, skipped = _parse_account_csv(io.BytesIO(csv_text.encode()))
        self.assertEqual(len(rows), 1)
        self.assertEqual(skipped, 0)
        self.assertEqual(rows[0]['zip_code'], '')
        self.assertEqual(rows[0]['county'], '')

    def test_blank_rows_ignored(self):
        csv_text = _make_csv([{'name': 'Store A'}]) + '\n,,,,,,,,,\n'
        rows, skipped = _parse_account_csv(io.BytesIO(csv_text.encode()))
        self.assertEqual(len(rows), 1)


# ---------------------------------------------------------------------------
# Upload view tests
# ---------------------------------------------------------------------------

class AccountImportUploadViewTest(AccountImportTestBase):

    def test_get_renders_upload_form(self):
        self._login_sa()
        resp = self.client.get(reverse('account_import_upload'))
        self.assertEqual(resp.status_code, 200)
        self.assertTemplateUsed(resp, 'imports/account_import_upload.html')

    def test_non_supplier_admin_gets_403(self):
        self._login_amb()
        resp = self.client.get(reverse('account_import_upload'))
        self.assertEqual(resp.status_code, 403)

    def test_unauthenticated_redirected(self):
        resp = self.client.get(reverse('account_import_upload'))
        self.assertNotEqual(resp.status_code, 200)

    def test_upload_creates_session_data(self):
        self._login_sa()
        csv_text = _make_csv([{'name': 'Store A'}])
        resp = self._upload_csv(csv_text)
        self.assertRedirects(resp, reverse('account_import_preview'))
        self.assertIn('account_import_preview', self.client.session)

    def test_upload_session_has_correct_counts(self):
        # Create an existing account that should be UPDATE
        Account.objects.create(
            company=self.company,
            name='EXISTING STORE',
            street='1 MAIN ST',
            city='HOBOKEN',
            state='NJ',
        )
        self._login_sa()
        csv_text = _make_csv([
            {'name': 'EXISTING STORE', 'street': '1 Main St',
             'city': 'Hoboken', 'state': 'NJ'},
            {'name': 'Brand New Store', 'street': '2 Oak Ave',
             'city': 'Newark', 'state': 'NJ'},
        ])
        self._upload_csv(csv_text)
        preview = self.client.session['account_import_preview']
        actions = [r['action'] for r in preview['rows']]
        self.assertIn('UPDATE', actions)
        self.assertIn('CREATE', actions)

    def test_no_file_shows_error(self):
        self._login_sa()
        resp = self.client.post(reverse('account_import_upload'), {})
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Please select a CSV file')


# ---------------------------------------------------------------------------
# Preview view tests
# ---------------------------------------------------------------------------

class AccountImportPreviewViewTest(AccountImportTestBase):

    def _seed_session(self, rows=None, skipped=0):
        if rows is None:
            rows = [{'action': 'CREATE', 'existing_pk': None,
                     'name': 'Store A', 'street': '1 Main', 'city': 'Hoboken',
                     'state': 'NJ', 'zip_code': '', 'county': '',
                     'on_off_premise': 'Unknown', 'account_type': '',
                     'third_party_id': '', 'distributor_route': ''}]
        session = self.client.session
        session['account_import_preview'] = {'rows': rows, 'skipped': skipped}
        session.save()

    def test_preview_renders(self):
        self._login_sa()
        self._seed_session()
        resp = self.client.get(reverse('account_import_preview'))
        self.assertEqual(resp.status_code, 200)
        self.assertTemplateUsed(resp, 'imports/account_import_preview.html')

    def test_preview_shows_correct_counts(self):
        self._login_sa()
        rows = [
            {'action': 'CREATE', 'existing_pk': None, 'name': 'New',
             'street': '1 Main', 'city': 'Hoboken', 'state': 'NJ',
             'zip_code': '', 'county': '', 'on_off_premise': 'Unknown',
             'account_type': '', 'third_party_id': '', 'distributor_route': ''},
            {'action': 'UPDATE', 'existing_pk': 1, 'name': 'Existing',
             'street': '2 Oak', 'city': 'Newark', 'state': 'NJ',
             'zip_code': '', 'county': '', 'on_off_premise': 'Unknown',
             'account_type': '', 'third_party_id': '', 'distributor_route': ''},
        ]
        self._seed_session(rows=rows, skipped=3)
        resp = self.client.get(reverse('account_import_preview'))
        self.assertContains(resp, '1')   # creates
        self.assertContains(resp, '3')   # skipped
        self.assertEqual(resp.context['creates'], 1)
        self.assertEqual(resp.context['updates'], 1)
        self.assertEqual(resp.context['skipped'], 3)

    def test_no_session_redirects_to_upload(self):
        self._login_sa()
        resp = self.client.get(reverse('account_import_preview'))
        self.assertRedirects(resp, reverse('account_import_upload'))

    def test_non_supplier_admin_gets_403(self):
        self._login_amb()
        resp = self.client.get(reverse('account_import_preview'))
        self.assertEqual(resp.status_code, 403)


# ---------------------------------------------------------------------------
# Execute view tests
# ---------------------------------------------------------------------------

class AccountImportExecuteViewTest(AccountImportTestBase):

    def _seed_session(self, rows, skipped=0):
        session = self.client.session
        session['account_import_preview'] = {'rows': rows, 'skipped': skipped}
        session.save()

    def _make_create_row(self, **kwargs):
        defaults = {
            'action': 'CREATE', 'existing_pk': None,
            'name': 'New Store', 'street': '5 Elm St',
            'city': 'Trenton', 'state': 'NJ',
            'zip_code': '08601', 'county': 'Mercer',
            'on_off_premise': 'OFF', 'account_type': 'Liquor Store',
            'third_party_id': 'VIP123', 'distributor_route': 'Route 7',
        }
        defaults.update(kwargs)
        return defaults

    def test_non_supplier_admin_gets_403(self):
        self._login_amb()
        resp = self.client.post(reverse('account_import_execute'))
        self.assertEqual(resp.status_code, 403)

    def test_get_redirects_to_preview(self):
        self._login_sa()
        resp = self.client.get(reverse('account_import_execute'))
        self.assertRedirects(resp, reverse('account_import_preview'))

    def test_create_new_account(self):
        self._login_sa()
        row = self._make_create_row()
        self._seed_session([row])

        resp = self.client.post(reverse('account_import_execute'))
        self.assertRedirects(resp, reverse('account_list'))

        account = Account.objects.get(company=self.company, name='New Store')
        self.assertEqual(account.street, '5 Elm St')
        self.assertEqual(account.city, 'Trenton')
        self.assertEqual(account.state, 'NJ')
        self.assertEqual(account.zip_code, '08601')
        self.assertEqual(account.county, 'Mercer')
        self.assertEqual(account.on_off_premise, 'OFF')
        self.assertEqual(account.account_type, 'Liquor Store')
        self.assertEqual(account.third_party_id, 'VIP123')
        self.assertEqual(account.distributor_route, 'Route 7')
        self.assertTrue(account.is_active)
        self.assertTrue(account.auto_created)

    def test_create_clears_session(self):
        self._login_sa()
        self._seed_session([self._make_create_row()])
        self.client.post(reverse('account_import_execute'))
        self.assertNotIn('account_import_preview', self.client.session)

    def test_update_existing_account(self):
        # Create the account first
        existing = Account.objects.create(
            company=self.company,
            name='OLD STORE',
            street='10 OAK AVE',
            city='CAMDEN',
            state='NJ',
            zip_code='',
            county='Unknown',
            on_off_premise='Unknown',
            account_type='',
            third_party_id='',
            distributor_route='',
            is_active=True,
        )
        self._login_sa()
        row = {
            'action': 'UPDATE',
            'existing_pk': existing.pk,
            'name': 'OLD STORE',
            'street': '10 OAK AVE',
            'city': 'CAMDEN',
            'state': 'NJ',
            'zip_code': '08102',
            'county': 'Camden',
            'on_off_premise': 'ON',
            'account_type': 'Bar',
            'third_party_id': 'VIP999',
            'distributor_route': 'Route 3',
        }
        self._seed_session([row])
        self.client.post(reverse('account_import_execute'))

        existing.refresh_from_db()
        # Non-key fields updated
        self.assertEqual(existing.zip_code, '08102')
        self.assertEqual(existing.county, 'Camden')
        self.assertEqual(existing.on_off_premise, 'ON')
        self.assertEqual(existing.account_type, 'Bar')
        self.assertEqual(existing.third_party_id, 'VIP999')
        self.assertEqual(existing.distributor_route, 'Route 3')

    def test_update_does_not_overwrite_key_fields(self):
        existing = Account.objects.create(
            company=self.company,
            name='ORIGINAL NAME',
            street='1 ORIGINAL ST',
            city='ORIGINAL CITY',
            state='NJ',
        )
        self._login_sa()
        row = {
            'action': 'UPDATE',
            'existing_pk': existing.pk,
            'name': 'DIFFERENT NAME',       # should NOT be applied
            'street': '9 DIFFERENT ST',     # should NOT be applied
            'city': 'DIFFERENT CITY',       # should NOT be applied
            'state': 'CA',                  # should NOT be applied
            'zip_code': '07001',
            'county': 'Essex',
            'on_off_premise': 'ON',
            'account_type': 'Bar',
            'third_party_id': '',
            'distributor_route': '',
        }
        self._seed_session([row])
        self.client.post(reverse('account_import_execute'))

        existing.refresh_from_db()
        self.assertEqual(existing.name, 'ORIGINAL NAME')
        self.assertEqual(existing.street, '1 ORIGINAL ST')
        self.assertEqual(existing.city, 'ORIGINAL CITY')
        self.assertEqual(existing.state, 'NJ')

    def test_update_does_not_change_is_active(self):
        existing = Account.objects.create(
            company=self.company,
            name='INACTIVE STORE',
            street='1 MAIN ST',
            city='HOBOKEN',
            state='NJ',
            is_active=False,
        )
        self._login_sa()
        row = {
            'action': 'UPDATE',
            'existing_pk': existing.pk,
            'name': 'INACTIVE STORE',
            'street': '1 MAIN ST',
            'city': 'HOBOKEN',
            'state': 'NJ',
            'zip_code': '07030',
            'county': 'Hudson',
            'on_off_premise': 'ON',
            'account_type': '',
            'third_party_id': '',
            'distributor_route': '',
        }
        self._seed_session([row])
        self.client.post(reverse('account_import_execute'))

        existing.refresh_from_db()
        self.assertFalse(existing.is_active)  # must remain False

    def test_success_message_shows_counts(self):
        self._login_sa()
        self._seed_session([self._make_create_row()])
        resp = self.client.post(reverse('account_import_execute'), follow=True)
        msgs = [str(m) for m in get_messages(resp.wsgi_request)]
        self.assertTrue(any('created' in m and 'updated' in m for m in msgs))

    def test_no_session_redirects_to_upload(self):
        self._login_sa()
        resp = self.client.post(reverse('account_import_execute'))
        self.assertRedirects(resp, reverse('account_import_upload'))
