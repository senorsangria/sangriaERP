"""
Tests for the Account Import feature — Phase 10.6.

Covers:
- CSV parsing (valid data, missing key fields, zip/county normalization)
- Session-based preview creation
- Account create and update execution
- Access control (403 for non-Supplier-Admin)
- Update does not overwrite key fields or is_active
- Distributor selection required on upload
- Match scoped to selected distributor
- Distributor set on CREATE, updated on UPDATE
- Bulk delete (Supplier Admin only, associations → deactivate)
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

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(headers)
    for r in rows:
        writer.writerow([str(v) for v in row_values(r)])
    return buf.getvalue()


class AccountImportTestBase(TestCase):
    """Base class: sets up company, distributor, supplier admin user, and non-admin user."""

    def setUp(self):
        self.company = Company.objects.create(name='Test Beverage Co')

        # Distributor
        self.distributor = Distributor.objects.create(
            company=self.company,
            name='Test Distributor',
            is_active=True,
        )

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

    def _upload_csv(self, csv_text, distributor_pk=None):
        """POST the upload form with a CSV string and distributor selection."""
        if distributor_pk is None:
            distributor_pk = self.distributor.pk
        f = io.BytesIO(csv_text.encode('utf-8'))
        f.name = 'test_accounts.csv'
        return self.client.post(
            reverse('account_import_upload'),
            {'csv_file': f, 'distributor_pk': distributor_pk},
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

    def test_upload_without_distributor_shows_error(self):
        """Submitting without selecting a distributor shows a validation error."""
        self._login_sa()
        csv_text = _make_csv([{'name': 'Store A'}])
        f = io.BytesIO(csv_text.encode('utf-8'))
        f.name = 'test_accounts.csv'
        resp = self.client.post(
            reverse('account_import_upload'),
            {'csv_file': f, 'distributor_pk': ''},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Please select a distributor')

    def test_upload_creates_session_data(self):
        self._login_sa()
        csv_text = _make_csv([{'name': 'Store A'}])
        resp = self._upload_csv(csv_text)
        self.assertRedirects(resp, reverse('account_import_preview'))
        self.assertIn('account_import_preview', self.client.session)

    def test_upload_session_stores_distributor_pk(self):
        self._login_sa()
        csv_text = _make_csv([{'name': 'Store A'}])
        self._upload_csv(csv_text)
        preview = self.client.session['account_import_preview']
        self.assertEqual(preview['distributor_pk'], self.distributor.pk)
        self.assertEqual(preview['distributor_name'], self.distributor.name)

    def test_upload_session_has_correct_counts(self):
        # Create an existing account under the same distributor that should be UPDATE
        Account.objects.create(
            company=self.company,
            distributor=self.distributor,
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

    def test_match_scoped_to_distributor(self):
        """
        An account with the same name/address but under a DIFFERENT distributor
        must be treated as CREATE, not UPDATE.
        """
        other_distributor = Distributor.objects.create(
            company=self.company,
            name='Other Distributor',
            is_active=True,
        )
        # Account exists under other_distributor
        Account.objects.create(
            company=self.company,
            distributor=other_distributor,
            name='SAME NAME STORE',
            street='1 MAIN ST',
            city='HOBOKEN',
            state='NJ',
        )
        self._login_sa()
        # Upload CSV for self.distributor — should see CREATE, not UPDATE
        csv_text = _make_csv([
            {'name': 'SAME NAME STORE', 'street': '1 Main St',
             'city': 'Hoboken', 'state': 'NJ'},
        ])
        self._upload_csv(csv_text, distributor_pk=self.distributor.pk)
        preview = self.client.session['account_import_preview']
        self.assertEqual(preview['rows'][0]['action'], 'CREATE')

    def test_no_file_shows_error(self):
        self._login_sa()
        resp = self.client.post(
            reverse('account_import_upload'),
            {'distributor_pk': self.distributor.pk},
        )
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
        session['account_import_preview'] = {
            'rows': rows,
            'skipped': skipped,
            'distributor_pk': self.distributor.pk,
            'distributor_name': self.distributor.name,
        }
        session.save()

    def test_preview_renders(self):
        self._login_sa()
        self._seed_session()
        resp = self.client.get(reverse('account_import_preview'))
        self.assertEqual(resp.status_code, 200)
        self.assertTemplateUsed(resp, 'imports/account_import_preview.html')

    def test_preview_shows_distributor_name(self):
        self._login_sa()
        self._seed_session()
        resp = self.client.get(reverse('account_import_preview'))
        self.assertContains(resp, self.distributor.name)

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
        session['account_import_preview'] = {
            'rows': rows,
            'skipped': skipped,
            'distributor_pk': self.distributor.pk,
            'distributor_name': self.distributor.name,
        }
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
        self.assertRedirects(resp, reverse('account_import_preview'),
                             fetch_redirect_response=False)

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

    def test_create_sets_distributor(self):
        """CREATE rows must have the selected distributor set."""
        self._login_sa()
        self._seed_session([self._make_create_row()])
        self.client.post(reverse('account_import_execute'))
        account = Account.objects.get(company=self.company, name='New Store')
        self.assertEqual(account.distributor, self.distributor)

    def test_create_clears_session(self):
        self._login_sa()
        self._seed_session([self._make_create_row()])
        self.client.post(reverse('account_import_execute'))
        self.assertNotIn('account_import_preview', self.client.session)

    def test_update_existing_account(self):
        # Create the account first
        existing = Account.objects.create(
            company=self.company,
            distributor=self.distributor,
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

    def test_update_sets_distributor(self):
        """UPDATE rows must have the distributor updated to the selected distributor."""
        other_distributor = Distributor.objects.create(
            company=self.company,
            name='Old Distributor',
            is_active=True,
        )
        existing = Account.objects.create(
            company=self.company,
            distributor=other_distributor,
            name='EXISTING STORE',
            street='1 MAIN ST',
            city='HOBOKEN',
            state='NJ',
        )
        self._login_sa()
        row = {
            'action': 'UPDATE',
            'existing_pk': existing.pk,
            'name': 'EXISTING STORE',
            'street': '1 MAIN ST',
            'city': 'HOBOKEN',
            'state': 'NJ',
            'zip_code': '',
            'county': '',
            'on_off_premise': 'Unknown',
            'account_type': '',
            'third_party_id': '',
            'distributor_route': '',
        }
        self._seed_session([row])
        self.client.post(reverse('account_import_execute'))

        existing.refresh_from_db()
        self.assertEqual(existing.distributor, self.distributor)

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


# ---------------------------------------------------------------------------
# Bulk delete tests
# ---------------------------------------------------------------------------

class AccountBulkDeleteTest(TestCase):
    """account_bulk_delete: delete accounts with no data; deactivate those with data."""

    def setUp(self):
        self.company = Company.objects.create(name='Bulk Delete Co')

        self.sa_role = Role.objects.get(codename='supplier_admin')
        self.sa_user = User.objects.create_user(
            username='sa_bulk',
            password='testpass123',
            company=self.company,
        )
        self.sa_user.roles.set([self.sa_role])

        self.amb_role = Role.objects.get(codename='ambassador')
        self.amb_user = User.objects.create_user(
            username='amb_bulk',
            password='testpass123',
            company=self.company,
        )
        self.amb_user.roles.set([self.amb_role])

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
        """Accounts with associated events/photos are deactivated, not deleted."""
        from apps.events.models import Event
        from apps.catalog.models import Brand

        self.client.force_login(self.sa_user)
        account = self._make_account('Has-Events Store')

        creator = self.sa_user
        Event.objects.create(
            company=self.company,
            created_by=creator,
            event_manager=creator,
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
        from apps.events.models import Event

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
        resp_follow = self.client.get(reverse('account_list'), follow=True)
        # Follow the redirect manually and check messages
        resp2 = self.client.post(
            reverse('account_bulk_delete'),
            {'account_pks': []},
            follow=True,
        )
        msgs_direct = [str(m) for m in get_messages(resp.wsgi_request)]
        self.assertTrue(any('deleted' in m for m in msgs_direct))
        self.assertTrue(any('deactivated' in m for m in msgs_direct))

    def test_no_pks_selected_shows_warning(self):
        """Posting with no PKs shows a warning message."""
        self.client.force_login(self.sa_user)
        resp = self._post_bulk_delete([])
        self.assertRedirects(resp, reverse('account_list'))
        resp2 = self.client.get(reverse('account_list'))
        # No accounts should have been changed
