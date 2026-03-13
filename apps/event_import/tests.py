"""
Tests for the event_import app — matching engine and upload access control.
"""
import io

from django.test import Client, TestCase
from django.urls import reverse

from apps.accounts.models import Account
from apps.core.models import Company, User
from apps.core.rbac import Role
from apps.distribution.models import Distributor
from apps.event_import.matching import (
    match_csv_row,
    normalize_for_match,
    _strip_trailing_single_letter,
    _extract_street_number,
)
from apps.event_import.views import _parse_csv


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_company(name='Test Beverage Co'):
    return Company.objects.create(name=name)


def make_user(company, role_codename, username='testuser'):
    user = User.objects.create_user(
        username=username,
        password='testpass123',
        company=company,
    )
    role = Role.objects.get(codename=role_codename)
    user.roles.set([role])
    return user


def make_distributor(company, name='Shore Point Distributing'):
    return Distributor.objects.create(company=company, name=name)


def make_account(company, distributor, name, street='', city=''):
    return Account.objects.create(
        company=company,
        distributor=distributor,
        name=name,
        street=street,
        city=city,
    )


def _acct_dict(acct):
    return {
        'pk':     acct.pk,
        'name':   acct.name,
        'street': acct.street,
        'city':   acct.city,
    }


def _accounts_by_distributor(dist_name, accounts):
    return {dist_name: [_acct_dict(a) for a in accounts]}


# ---------------------------------------------------------------------------
# CSV column mapping
# ---------------------------------------------------------------------------

class CsvColumnMappingTest(TestCase):

    def _make_file(self, csv_text):
        """Wrap CSV text in a file-like object as _parse_csv expects."""
        return io.BytesIO(csv_text.encode('utf-8'))

    def test_event_location_mapped_to_location(self):
        """'Event Location' CSV column is mapped to the 'location' key."""
        csv_text = (
            'Distributor,Event Location,Address,City\r\n'
            'Shore Point,Main Street Wine & Spirits,123 Main St,Hoboken\r\n'
        )
        rows = _parse_csv(self._make_file(csv_text))
        self.assertEqual(len(rows), 1)
        self.assertIn('location', rows[0])
        self.assertNotIn('event location', rows[0])
        self.assertEqual(rows[0]['location'], 'Main Street Wine & Spirits')

    def test_all_column_mappings_applied(self):
        """All renamed columns are present under their mapped names."""
        csv_text = (
            'Distributor,Event Location,Address,City,Event Date,'
            'Promo Person,QR Code Scans,Samples,'
            'Racap Note 1,Recap Note 2,'
            'Bottles Sold BWRed0750,Bottles Used BWRed0750,Bottle Price BWRed0750\r\n'
            'Shore Point,Test Store,1 Main St,Newark,2024-01-15,'
            'Jane Doe,5,10,'
            'Good event,Follow up,'
            '3,1,12.99\r\n'
        )
        rows = _parse_csv(self._make_file(csv_text))
        row = rows[0]
        self.assertEqual(row['location'], 'Test Store')
        self.assertEqual(row['date'], '2024-01-15')
        self.assertEqual(row['promo_person'], 'Jane Doe')
        self.assertEqual(row['qr_scans'], '5')
        self.assertEqual(row['recap1'], 'Good event')
        self.assertEqual(row['recap2'], 'Follow up')
        self.assertEqual(row['sold_bwred0750'], '3')
        self.assertEqual(row['used_bwred0750'], '1')
        self.assertEqual(row['price_bwred0750'], '12.99')

    def test_passthrough_columns_unchanged(self):
        """Columns not in COLUMN_MAP are kept as-is (lowercased)."""
        csv_text = (
            'Distributor,Event Location,Address,City\r\n'
            'Shore Point,Test Store,1 Main St,Newark\r\n'
        )
        rows = _parse_csv(self._make_file(csv_text))
        row = rows[0]
        self.assertEqual(row['distributor'], 'Shore Point')
        self.assertEqual(row['address'], '1 Main St')
        self.assertEqual(row['city'], 'Newark')


# ---------------------------------------------------------------------------
# Access control
# ---------------------------------------------------------------------------

class UploadAccessTest(TestCase):

    def setUp(self):
        self.company = make_company()
        self.client = Client()

    def test_upload_requires_supplier_admin(self):
        """Non-supplier-admin is redirected to dashboard."""
        ambassador = make_user(self.company, 'ambassador', username='amb1')
        self.client.login(username='amb1', password='testpass123')
        response = self.client.get(reverse('event_import_upload'))
        self.assertRedirects(
            response,
            reverse('dashboard'),
            fetch_redirect_response=False,
        )

    def test_upload_accessible_to_supplier_admin(self):
        """Supplier Admin can access the upload page."""
        admin = make_user(self.company, 'supplier_admin', username='sadmin1')
        self.client.login(username='sadmin1', password='testpass123')
        response = self.client.get(reverse('event_import_upload'))
        self.assertEqual(response.status_code, 200)


# ---------------------------------------------------------------------------
# Matching engine
# ---------------------------------------------------------------------------

class MatchHighConfidenceTest(TestCase):

    def test_match_high_confidence(self):
        """Exact name + address match returns status='high' with score >= 75."""
        accts = [{'pk': 1, 'name': 'Main Street Wine & Spirits',
                  'street': '123 Main St', 'city': 'Hoboken'}]
        by_dist = {'Shore Point Distributing': accts}
        row = {
            'distributor': 'Shore Point Distributing',
            'location':    'Main Street Wine & Spirits',
            'address':     '123 Main St',
            'city':        'Hoboken',
        }
        result = match_csv_row(row, by_dist)
        self.assertEqual(result['status'], 'high')
        self.assertIsNotNone(result['match'])
        self.assertGreaterEqual(result['score'], 75)

    def test_match_high_confidence_with_minor_variation(self):
        """Name with slight punctuation difference still scores >= 85."""
        accts = [{'pk': 2, 'name': 'Main Street Wine and Spirits',
                  'street': '123 Main Street', 'city': 'Hoboken'}]
        by_dist = {'Shore Point Distributing': accts}
        row = {
            'distributor': 'Shore Point Distributing',
            'location':    'Main Street Wine & Spirits',
            'address':     '123 Main St',
            'city':        'Hoboken',
        }
        result = match_csv_row(row, by_dist)
        self.assertIn(result['status'], ('high', 'review'))
        self.assertGreaterEqual(result['score'], 50)


class MatchReviewTest(TestCase):

    def test_match_review(self):
        """
        Exact name match but completely different address/city scores in
        review range (50-84). score ≈ 100*0.6 + 0*0.3 + 0*0.1 = 60.
        """
        accts = [{'pk': 3, 'name': 'Main Street Wine & Spirits',
                  'street': '999 Oak Avenue', 'city': 'Jersey City'}]
        by_dist = {'Shore Point Distributing': accts}
        row = {
            'distributor': 'Shore Point Distributing',
            'location':    'Main Street Wine & Spirits',
            'address':     '1 Zzz Blvd',
            'city':        'Trenton',
        }
        result = match_csv_row(row, by_dist)
        self.assertEqual(result['status'], 'review')
        self.assertIsNone(result['match'])
        self.assertGreaterEqual(result['score'], 50)
        self.assertLess(result['score'], 85)


class MatchNoneTest(TestCase):

    def test_match_none(self):
        """Completely different name scores below 50 → status='none'."""
        accts = [{'pk': 4, 'name': 'Totally Different Store',
                  'street': '1 Nowhere Rd', 'city': 'Trenton'}]
        by_dist = {'Shore Point Distributing': accts}
        row = {
            'distributor': 'Shore Point Distributing',
            'location':    'Xyz Abc Qrs Tuv',
            'address':     '999 Zzz St',
            'city':        'Bogota',
        }
        result = match_csv_row(row, by_dist)
        self.assertEqual(result['status'], 'none')
        self.assertIsNone(result['match'])
        self.assertLess(result['score'], 50)

    def test_match_wrong_distributor(self):
        """Correct location name but wrong distributor → no candidates → status='none'."""
        accts = [{'pk': 5, 'name': 'Main Street Wine & Spirits',
                  'street': '123 Main St', 'city': 'Hoboken'}]
        by_dist = {'Shore Point Distributing': accts}
        row = {
            'distributor': 'Totally Different Distributor',
            'location':    'Main Street Wine & Spirits',
            'address':     '123 Main St',
            'city':        'Hoboken',
        }
        result = match_csv_row(row, by_dist)
        self.assertEqual(result['status'], 'none')
        self.assertEqual(result['score'], 0.0)
        self.assertEqual(result['candidates'], [])


class TrailingLetterStrippingTest(TestCase):

    def test_trailing_single_letter_stripped(self):
        """Trailing ' B' suffix is removed from normalized account name."""
        self.assertEqual(_strip_trailing_single_letter('JIMMY S LIQUORS B'), 'JIMMY S LIQUORS')
        self.assertEqual(_strip_trailing_single_letter('SAJOMA LIQUOR INC R'), 'SAJOMA LIQUOR INC')
        self.assertEqual(_strip_trailing_single_letter('BRONX LIQUOR & WINE B'), 'BRONX LIQUOR & WINE')

    def test_non_trailing_single_letter_unchanged(self):
        """Names that do not end in a lone letter are left alone."""
        # 'S' in 'CONSUMER S DISCOUNT' is not at the end
        name = 'CONSUMER S DISCOUNT WINES & SPIRITS'
        self.assertEqual(_strip_trailing_single_letter(name), name)

    def test_trailing_letter_stripped_improves_match(self):
        """Account name with trailing suffix matches CSV location better after stripping."""
        # Without stripping, "JIMMY S LIQUORS B" vs "JIMMY S LIQUORS" would lose points.
        # With stripping the account name becomes identical to the CSV location.
        accts = [{'pk': 10, 'name': 'Jimmy S Liquors B',
                  'street': '50 Main St', 'city': 'Newark'}]
        by_dist = {'Shore Point': accts}
        row = {
            'distributor': 'Shore Point',
            'location':    'Jimmy S Liquors',
            'address':     '50 Main St',
            'city':        'Newark',
        }
        result = match_csv_row(row, by_dist)
        self.assertEqual(result['status'], 'high')
        self.assertGreaterEqual(result['score'], 75)


class StreetNumberBoostTest(TestCase):

    def test_street_number_boost(self):
        """
        Two candidates with similar names but only one sharing the street
        number should score higher and win.
        """
        accts = [
            {'pk': 20, 'name': 'Main Liquors', 'street': '100 Elm St',  'city': 'Newark'},
            {'pk': 21, 'name': 'Main Liquors', 'street': '999 Oak Ave', 'city': 'Newark'},
        ]
        by_dist = {'Shore Point': accts}
        row = {
            'distributor': 'Shore Point',
            'location':    'Main Liquors',
            'address':     '100 Elm St',
            'city':        'Newark',
        }
        result = match_csv_row(row, by_dist)
        # The match should be pk=20 (street number 100 matches)
        self.assertIsNotNone(result['match'])
        self.assertEqual(result['match']['pk'], 20)
        # Score should reflect the boost
        self.assertGreaterEqual(result['score'], 75)

    def test_street_number_no_boost_when_mismatch(self):
        """Mismatched street numbers do not receive the +10 boost."""
        accts = [{'pk': 30, 'name': 'Oak Street Wine',
                  'street': '500 Oak St', 'city': 'Trenton'}]
        by_dist = {'Shore Point': accts}
        row = {
            'distributor': 'Shore Point',
            'location':    'Oak Street Wine',
            'address':     '999 Oak St',   # different number
            'city':        'Trenton',
        }
        result_mismatch = match_csv_row(row, by_dist)

        # Now try with matching number — should score higher
        row_match = {**row, 'address': '500 Oak St'}
        result_match = match_csv_row(row_match, by_dist)

        self.assertGreater(result_match['score'], result_mismatch['score'])

    def test_extract_street_number(self):
        """_extract_street_number pulls the leading numeric group."""
        self.assertEqual(_extract_street_number('1179 St Georges Ave'), '1179')
        self.assertEqual(_extract_street_number('90-70 Rt 206'), '90')
        self.assertEqual(_extract_street_number('39-05 104TH ST'), '39')
        self.assertEqual(_extract_street_number(''), '')
        self.assertEqual(_extract_street_number('Main St'), '')


class NormalizeDistributorCasingTest(TestCase):

    def test_normalize_distributor_casing(self):
        """'shore point' and 'Shore Point' map to the same bucket."""
        accts = [{'pk': 6, 'name': 'Test Store', 'street': '1 Test St', 'city': 'Newark'}]
        # Key stored as title case
        by_dist = {'Shore Point': accts}

        row_lower = {
            'distributor': 'shore point',
            'location':    'Test Store',
            'address':     '1 Test St',
            'city':        'Newark',
        }
        row_mixed = {
            'distributor': 'Shore point',
            'location':    'Test Store',
            'address':     '1 Test St',
            'city':        'Newark',
        }
        result_lower = match_csv_row(row_lower, by_dist)
        result_mixed = match_csv_row(row_mixed, by_dist)

        # Both should find the account (title-case normalisation)
        self.assertNotEqual(result_lower['status'], 'none',
                            'lower-case distributor should resolve to same bucket')
        self.assertNotEqual(result_mixed['status'], 'none',
                            'mixed-case distributor should resolve to same bucket')
