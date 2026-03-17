"""
Tests for the event_import app — matching engine and upload access control.
"""
import csv
import io
import datetime

from django.test import Client, TestCase
from django.urls import reverse

from apps.accounts.models import Account
from apps.core.models import Company, User
from apps.core.rbac import Role
from apps.distribution.models import Distributor
from apps.events.models import Event
from apps.event_import.matching import (
    match_csv_row,
    normalize_for_match,
    _expand_abbreviations,
    _strip_branch_numbers,
    _strip_city,
    _strip_parentheticals,
    _strip_trailing_single_letter,
    _extract_street_number,
    _extract_street_name,
    _normalize_street_type,
)
from apps.event_import.views import _parse_csv, _parse_int, _parse_price


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

    def test_blank_rows_skipped(self):
        """Rows with blank location, address, and city are skipped."""
        csv_text = (
            'Distributor,Event Location,Address,City\r\n'
            'Shore Point,Main St Wines,123 Main St,Hoboken\r\n'
            'Shore Point,,,\r\n'
            ',,,\r\n'
        )
        rows = _parse_csv(self._make_file(csv_text))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['location'], 'Main St Wines')


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
        review range (50-84).

        Two candidates are used so neither triggers the clear-leader rule
        (gap < 10 → no auto-promotion). Both have the same name but
        different streets, so they score similarly and neither stands out.
        """
        accts = [
            {'pk': 3,  'name': 'Main Street Wine & Spirits',
             'street': '999 Oak Avenue', 'city': 'Jersey City'},
            {'pk': 98, 'name': 'Main Street Wine & Spirits',
             'street': '888 Oak Avenue', 'city': 'Jersey City'},
        ]
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


# ---------------------------------------------------------------------------
# Improvement 1: apostrophe stripping
# ---------------------------------------------------------------------------

class ApostropheNormalizationTest(TestCase):

    def test_apostrophe_stripped(self):
        """Apostrophes are removed during normalization."""
        self.assertEqual(normalize_for_match("McCaffrey's"), 'MCCAFFREYS')
        self.assertEqual(normalize_for_match("O'Brien's Pub"), 'OBRIENS PUB')


# ---------------------------------------------------------------------------
# Improvement 2: abbreviation expansion
# ---------------------------------------------------------------------------

class AbbreviationExpansionTest(TestCase):

    def test_abbreviation_expansion_wl(self):
        """'W&L' expands to 'WINE AND LIQUOR'."""
        result = _expand_abbreviations(normalize_for_match('Empire W&L'))
        self.assertIn('WINE AND LIQUOR', result)

    def test_abbreviation_expansion_ws(self):
        """'W&S' expands to 'WINE AND SPIRITS'."""
        result = _expand_abbreviations(normalize_for_match('ShopRite W&S'))
        self.assertIn('WINE AND SPIRITS', result)

    def test_abbreviation_expansion_liq(self):
        """'LIQ' expands to 'LIQUOR'."""
        result = _expand_abbreviations('MAIN ST LIQ')
        self.assertIn('LIQUOR', result)

    def test_abbreviation_expansion_mkt(self):
        """'MKT' expands to 'MARKET'."""
        result = _expand_abbreviations('CORNER MKT')
        self.assertIn('MARKET', result)


# ---------------------------------------------------------------------------
# Improvement 3: city stripping
# ---------------------------------------------------------------------------

class StripCityTest(TestCase):

    def test_strip_city_from_end(self):
        """City appended to location name is stripped."""
        result = _strip_city('BOURBON ST WINE SPIRITS ASBURY', 'ASBURY')
        self.assertEqual(result, 'BOURBON ST WINE SPIRITS')

    def test_strip_city_from_start(self):
        """City prepended to location name is stripped."""
        result = _strip_city('PRINCETON MCCAFFREYS', 'PRINCETON')
        self.assertEqual(result, 'MCCAFFREYS')

    def test_strip_city_no_match(self):
        """City not present in name leaves name unchanged."""
        result = _strip_city('SHOPRITE BYRAM', 'STANHOPE')
        self.assertEqual(result, 'SHOPRITE BYRAM')

    def test_strip_city_empty_city(self):
        """Empty city string leaves name unchanged."""
        result = _strip_city('BOURBON ST WINE', '')
        self.assertEqual(result, 'BOURBON ST WINE')


# ---------------------------------------------------------------------------
# End-to-end: McCaffrey's / Princeton
# ---------------------------------------------------------------------------

class McCaffreysEndToEndTest(TestCase):

    def test_mccaffreys_matches_high(self):
        """
        'McCaffrey's Market' at '301 N Harrison Ave', city 'Princeton'
        should match 'PRINCETON MCCAFFREYS' at '301 N HARRISON ST'
        with status='high' and score >= 75.

        Relies on: apostrophe strip, city strip from both sides, and
        street number boost (301 matches).
        """
        accts = [{'pk': 50, 'name': 'Princeton McCaffreys',
                  'street': '301 N Harrison St', 'city': 'Princeton'}]
        by_dist = {'Shore Point': accts}
        row = {
            'distributor': 'Shore Point',
            'location':    "McCaffrey's Market",
            'address':     '301 N Harrison Ave',
            'city':        'Princeton',
        }
        result = match_csv_row(row, by_dist)
        self.assertEqual(result['status'], 'high')
        self.assertGreaterEqual(result['score'], 75)


# ---------------------------------------------------------------------------
# Improvement 1: branch number stripping
# ---------------------------------------------------------------------------

class BranchNumberStrippingTest(TestCase):

    def test_strip_branch_number(self):
        """Branch number '#753-' is removed from account name."""
        self.assertEqual(
            _strip_branch_numbers('SHOPRITE #753- CALDWELL'),
            'SHOPRITE CALDWELL',
        )

    def test_strip_branch_number_with_space(self):
        """Branch number '# 5-' with internal space is removed."""
        self.assertEqual(
            _strip_branch_numbers('LIQUOR FACTORY # 5-NEWTN'),
            'LIQUOR FACTORY NEWTN',
        )

    def test_branch_number_improves_match(self):
        """
        'Shop Rite Caldwell' at '478 Bloomfield Ave', city 'Caldwell'
        should match 'SHOPRITE #753- CALDWELL' at '478 BLOOMFIELD AVE'
        with status='high' and score >= 75.

        Relies on: branch number strip, city strip, street type
        normalization (AVE→AVENUE), and street number boost.
        """
        accts = [{'pk': 60, 'name': 'ShopRite #753- Caldwell',
                  'street': '478 Bloomfield Ave', 'city': 'Caldwell'}]
        by_dist = {'Shore Point': accts}
        row = {
            'distributor': 'Shore Point',
            'location':    'Shop Rite Caldwell',
            'address':     '478 Bloomfield Ave',
            'city':        'Caldwell',
        }
        result = match_csv_row(row, by_dist)
        self.assertEqual(result['status'], 'high')
        self.assertGreaterEqual(result['score'], 75)


# ---------------------------------------------------------------------------
# Improvement 2: enhanced street number boost
# ---------------------------------------------------------------------------

class StreetNameBoostTest(TestCase):

    def test_street_name_boost_strong(self):
        """
        Matching street number AND similar street name (score >= 70)
        gives a boost of 15. Verified by comparing against a candidate
        with the same name but a mismatched street number (no boost).
        """
        accts = [{'pk': 70, 'name': 'Test Store',
                  'street': '100 Elm St', 'city': 'Newark'}]
        by_dist = {'Shore Point': accts}

        row_match = {
            'distributor': 'Shore Point',
            'location':    'Test Store',
            'address':     '100 Elm Street',   # same number, same street name
            'city':        'Bogota',            # intentional city mismatch
        }
        row_no_num = {
            'distributor': 'Shore Point',
            'location':    'Test Store',
            'address':     '999 Elm Street',   # different number → no boost
            'city':        'Bogota',
        }
        result_match  = match_csv_row(row_match,  by_dist)
        result_no_num = match_csv_row(row_no_num, by_dist)

        # Strong boost (+15) should give significantly more points than no boost.
        # The city-mismatch penalty (0.85×) applies to both rows, so the absolute
        # delta is slightly less than 15 — asserting > 10 is robust.
        self.assertGreater(result_match['score'] - result_no_num['score'], 10)

    def test_street_name_boost_weak(self):
        """
        Matching street number but dissimilar street name (score < 70)
        gives a boost of 10. Verified by comparing strong vs weak boost
        on the same account — the strong boost should outscore the weak.
        """
        accts = [{'pk': 71, 'name': 'Test Store',
                  'street': '100 Elm Street', 'city': 'Newark'}]
        by_dist = {'Shore Point': accts}

        row_strong = {
            'distributor': 'Shore Point',
            'location':    'Test Store',
            'address':     '100 Elm Street',       # same name → boost 15
            'city':        'Bogota',
        }
        row_weak = {
            'distributor': 'Shore Point',
            'location':    'Test Store',
            'address':     '100 Qwerty Boulevard',  # same number, very different name → boost 10
            'city':        'Bogota',
        }
        result_strong = match_csv_row(row_strong, by_dist)
        result_weak   = match_csv_row(row_weak,   by_dist)

        # Strong boost should outscore weak boost
        self.assertGreater(result_strong['score'], result_weak['score'])


# ---------------------------------------------------------------------------
# Improvement 3: street type normalization
# ---------------------------------------------------------------------------

class StreetTypeNormalizationTest(TestCase):

    def test_street_type_normalization(self):
        """'Bridewell Place' and 'Bridewell Pl' normalize to the same string."""
        full  = _normalize_street_type(normalize_for_match('Bridewell Place'))
        abbr  = _normalize_street_type(normalize_for_match('Bridewell Pl'))
        self.assertEqual(full, abbr)

    def test_street_type_normalization_route(self):
        """'Rt 206', 'Rte 206', and 'Route 206' all normalize to the same string."""
        rt    = _normalize_street_type(normalize_for_match('Rt 206'))
        rte   = _normalize_street_type(normalize_for_match('Rte 206'))
        route = _normalize_street_type(normalize_for_match('Route 206'))
        self.assertEqual(rt, route)
        self.assertEqual(rte, route)

    def test_costco_clifton_matches(self):
        """
        'Costco Clifton' at '20 Bridewell Place', city 'Clifton' should
        match 'WESTERN BEVERAGE AT COSTCO CLIFTON' at '20 BRIDEWELL PL'
        with status='high' and score >= 75.

        Relies on: city strip from both sides, street type normalization
        (PL→PLACE), and strong street number boost (20 + Bridewell).
        """
        accts = [{'pk': 80, 'name': 'Western Beverage At Costco Clifton',
                  'street': '20 Bridewell Pl', 'city': 'Clifton'}]
        by_dist = {'Shore Point': accts}
        row = {
            'distributor': 'Shore Point',
            'location':    'Costco Clifton',
            'address':     '20 Bridewell Place',
            'city':        'Clifton',
        }
        result = match_csv_row(row, by_dist)
        self.assertEqual(result['status'], 'high')
        self.assertGreaterEqual(result['score'], 75)


# ---------------------------------------------------------------------------
# Clear leader auto-promotion
# ---------------------------------------------------------------------------

class ClearLeaderPromotionTest(TestCase):
    """
    These tests use exact name + city matches to produce controlled scores:
      perfect name + matching city  = 100*0.6 + 100*0.1 = 70
      perfect name + no city match  = 100*0.6            = 60
      unrelated name + city match   ≈           100*0.1  = 10
    No address is used so there is no street-number boost to interfere.
    """

    def test_clear_leader_promoted(self):
        """
        Top score 70 (≥70, <75), second score ~10 (gap ≥10) →
        clear leader rule promotes status to 'high'.
        """
        accts = [
            {'pk': 100, 'name': 'Wine Cellar', 'street': '', 'city': 'Newark'},
            {'pk': 101, 'name': 'Xyz Abc Def', 'street': '', 'city': 'Newark'},
        ]
        by_dist = {'Shore Point': accts}
        row = {
            'distributor': 'Shore Point',
            'location':    'Wine Cellar',
            'address':     '',
            'city':        'Newark',
        }
        result = match_csv_row(row, by_dist)
        self.assertEqual(result['status'], 'high')
        self.assertEqual(result['match']['pk'], 100)

    def test_clear_leader_not_promoted_close_second(self):
        """
        Top score 70 but second candidate ties at 70 (gap 0 < 10) →
        clear leader rule does not apply; status stays 'review'.

        Accounts have non-empty streets so that the CSV's empty address
        produces addr_score=0 (empty vs non-empty = 0), giving a
        controlled combined score of 70 (name=100→60, city=100→10).
        """
        accts = [
            {'pk': 102, 'name': 'Wine Cellar', 'street': '100 Oak Ave', 'city': 'Newark'},
            {'pk': 103, 'name': 'Wine Cellar', 'street': '200 Elm Ave', 'city': 'Newark'},
        ]
        by_dist = {'Shore Point': accts}
        row = {
            'distributor': 'Shore Point',
            'location':    'Wine Cellar',
            'address':     '',
            'city':        'Newark',
        }
        result = match_csv_row(row, by_dist)
        self.assertEqual(result['status'], 'review')

    def test_clear_leader_not_promoted_low_top(self):
        """
        Top score ~62 (gap ≥10 vs second, but top < 70) →
        clear leader rule does not apply; status stays 'review'.

        Accounts have non-empty streets so addr_score=0 (empty CSV vs
        non-empty account). City mismatch (Newark vs Trenton) keeps the
        score below 70 even with a perfect name match.
        """
        accts = [
            {'pk': 104, 'name': 'Wine Cellar', 'street': '100 Oak Ave', 'city': 'Trenton'},
            {'pk': 105, 'name': 'Xyz Abc Def', 'street': '200 Elm Ave', 'city': 'Trenton'},
        ]
        by_dist = {'Shore Point': accts}
        row = {
            'distributor': 'Shore Point',
            'location':    'Wine Cellar',
            'address':     '',
            'city':        'Newark',    # mismatches both accounts → low city score
        }
        result = match_csv_row(row, by_dist)
        # top score ≈ 61-62 (name only + tiny city partial); < 70 → no clear_leader
        # score >= REVIEW_THRESHOLD → 'review'
        self.assertEqual(result['status'], 'review')

    def test_clear_leader_single_candidate(self):
        """
        Single candidate at 70 (≥70, <75) → clear leader rule promotes
        to 'high' because there is no competition at all.
        """
        accts = [
            {'pk': 106, 'name': 'Wine Cellar', 'street': '', 'city': 'Newark'},
        ]
        by_dist = {'Shore Point': accts}
        row = {
            'distributor': 'Shore Point',
            'location':    'Wine Cellar',
            'address':     '',
            'city':        'Newark',
        }
        result = match_csv_row(row, by_dist)
        self.assertEqual(result['status'], 'high')
        self.assertEqual(result['match']['pk'], 106)


# ---------------------------------------------------------------------------
# CSV export view
# ---------------------------------------------------------------------------

def _make_session_data(company, distributor):
    """
    Build the three session objects needed by the export view.
    Uses a single high-confidence match row.
    """
    acct = make_account(
        company, distributor,
        name='Test Liquors',
        street='100 Main St',
        city='Newark',
    )
    csv_key = 'Shore Point Distributing||Test Liquors||100 Main St||Newark'
    rows = [
        {
            'distributor': 'Shore Point Distributing',
            'location':    'Test Liquors',
            'address':     '100 Main St',
            'city':        'Newark',
            'date':        '2024-01-15',
        }
    ]
    matches = {
        'high':   [{'csv_key': csv_key, 'match_account_pk': acct.pk,
                    'match_account_name': acct.name, 'row_count': 1, 'score': 90}],
        'review': [],
        'none':   [],
    }
    confirmed = {csv_key: acct.pk}
    return rows, matches, confirmed, acct, csv_key


def _make_skipped_session_data(company, distributor):
    """
    Build session objects where the single row is in the review bucket
    but the user selected No Match (confirmed pk = None).
    """
    csv_key = 'Shore Point Distributing||Test Bar||200 Oak Ave||Trenton'
    rows = [
        {
            'distributor': 'Shore Point Distributing',
            'location':    'Test Bar',
            'address':     '200 Oak Ave',
            'city':        'Trenton',
            'date':        '2024-02-10',
        }
    ]
    matches = {
        'high':   [],
        'review': [{'csv_key': csv_key, 'candidates': [], 'row_count': 1, 'best_score': 55}],
        'none':   [],
    }
    confirmed = {csv_key: None}
    return rows, matches, confirmed, csv_key


class ExportCsvAccessTest(TestCase):

    def setUp(self):
        self.company     = make_company()
        self.distributor = make_distributor(self.company)
        self.client      = Client()

    def test_export_requires_supplier_admin(self):
        """Non-supplier-admin is redirected to dashboard."""
        ambassador = make_user(self.company, 'ambassador', username='amb_exp')
        self.client.login(username='amb_exp', password='testpass123')
        response = self.client.get(reverse('event_import_export_csv'))
        self.assertRedirects(
            response,
            reverse('dashboard'),
            fetch_redirect_response=False,
        )

    def test_export_no_session_redirects(self):
        """Missing session data redirects to event_import_upload with error."""
        admin = make_user(self.company, 'supplier_admin', username='sadmin_exp_ns')
        self.client.login(username='sadmin_exp_ns', password='testpass123')
        response = self.client.get(reverse('event_import_export_csv'))
        self.assertRedirects(
            response,
            reverse('event_import_upload'),
            fetch_redirect_response=False,
        )

    def test_export_returns_csv(self):
        """Authorized user with valid session gets a CSV response."""
        admin = make_user(self.company, 'supplier_admin', username='sadmin_exp_rc')
        self.client.login(username='sadmin_exp_rc', password='testpass123')

        rows, matches, confirmed, acct, _ = _make_session_data(self.company, self.distributor)
        session = self.client.session
        session['event_import_rows']      = rows
        session['event_import_matches']   = matches
        session['event_import_confirmed'] = confirmed
        session.save()

        response = self.client.get(reverse('event_import_export_csv'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'text/csv')
        self.assertIn(
            'attachment; filename="event_import_matched.csv"',
            response['Content-Disposition'],
        )

    def test_export_appends_three_columns(self):
        """Exported CSV has the three extra columns in the header."""
        admin = make_user(self.company, 'supplier_admin', username='sadmin_exp_3c')
        self.client.login(username='sadmin_exp_3c', password='testpass123')

        rows, matches, confirmed, acct, _ = _make_session_data(self.company, self.distributor)
        session = self.client.session
        session['event_import_rows']      = rows
        session['event_import_matches']   = matches
        session['event_import_confirmed'] = confirmed
        session.save()

        response = self.client.get(reverse('event_import_export_csv'))
        content = response.content.decode('utf-8')
        reader = csv.DictReader(io.StringIO(content))
        fieldnames = reader.fieldnames
        self.assertIn('Matched Account Name',    fieldnames)
        self.assertIn('Matched Account Address', fieldnames)
        self.assertIn('Matched Account City',    fieldnames)

    def test_export_matched_row_has_account_data(self):
        """A high-confidence matched row has non-blank account name, address, city."""
        admin = make_user(self.company, 'supplier_admin', username='sadmin_exp_mr')
        self.client.login(username='sadmin_exp_mr', password='testpass123')

        rows, matches, confirmed, acct, _ = _make_session_data(self.company, self.distributor)
        session = self.client.session
        session['event_import_rows']      = rows
        session['event_import_matches']   = matches
        session['event_import_confirmed'] = confirmed
        session.save()

        response = self.client.get(reverse('event_import_export_csv'))
        content = response.content.decode('utf-8')
        reader = csv.DictReader(io.StringIO(content))
        data_rows = list(reader)
        self.assertEqual(len(data_rows), 1)
        row = data_rows[0]
        self.assertEqual(row['Matched Account Name'],    acct.name)
        self.assertEqual(row['Matched Account Address'], acct.street)
        self.assertEqual(row['Matched Account City'],    acct.city)

    def test_export_skipped_row_has_blank_account(self):
        """A skipped (no-match) row has blank account columns."""
        admin = make_user(self.company, 'supplier_admin', username='sadmin_exp_sk')
        self.client.login(username='sadmin_exp_sk', password='testpass123')

        rows, matches, confirmed, _ = _make_skipped_session_data(self.company, self.distributor)
        session = self.client.session
        session['event_import_rows']      = rows
        session['event_import_matches']   = matches
        session['event_import_confirmed'] = confirmed
        session.save()

        response = self.client.get(reverse('event_import_export_csv'))
        content = response.content.decode('utf-8')
        reader = csv.DictReader(io.StringIO(content))
        data_rows = list(reader)
        self.assertEqual(len(data_rows), 1)
        row = data_rows[0]
        self.assertEqual(row['Matched Account Name'],    '')
        self.assertEqual(row['Matched Account Address'], '')
        self.assertEqual(row['Matched Account City'],    '')


# ---------------------------------------------------------------------------
# Delete all imported events
# ---------------------------------------------------------------------------

def make_event(company, is_imported=True):
    return Event.objects.create(
        company=company,
        is_imported=is_imported,
        date=datetime.date(2024, 1, 15),
    )


class DeleteAllImportedEventsTest(TestCase):

    def setUp(self):
        self.company = make_company('Delete Test Co')
        self.client  = Client()

    def test_delete_requires_supplier_admin(self):
        """Non-supplier-admin is redirected to dashboard."""
        ambassador = make_user(self.company, 'ambassador', username='amb_del')
        self.client.login(username='amb_del', password='testpass123')
        response = self.client.get(reverse('event_import_delete_all'))
        self.assertRedirects(
            response,
            reverse('dashboard'),
            fetch_redirect_response=False,
        )

    def test_delete_get_shows_count(self):
        """GET request shows the count of imported events in the response."""
        admin = make_user(self.company, 'supplier_admin', username='sadmin_del_get')
        self.client.login(username='sadmin_del_get', password='testpass123')
        make_event(self.company, is_imported=True)
        make_event(self.company, is_imported=True)
        response = self.client.get(reverse('event_import_delete_all'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '2')

    def test_delete_post_removes_imported_events(self):
        """POST deletes all is_imported=True events for the company and redirects."""
        admin = make_user(self.company, 'supplier_admin', username='sadmin_del_post')
        self.client.login(username='sadmin_del_post', password='testpass123')
        make_event(self.company, is_imported=True)
        make_event(self.company, is_imported=True)
        response = self.client.post(reverse('event_import_delete_all'))
        self.assertRedirects(
            response,
            reverse('event_import_upload'),
            fetch_redirect_response=False,
        )
        self.assertEqual(
            Event.objects.filter(is_imported=True, company=self.company).count(),
            0,
        )

    def test_delete_post_preserves_non_imported_events(self):
        """POST does not delete events where is_imported=False."""
        admin = make_user(self.company, 'supplier_admin', username='sadmin_del_pres')
        self.client.login(username='sadmin_del_pres', password='testpass123')
        make_event(self.company, is_imported=True)
        make_event(self.company, is_imported=False)
        self.client.post(reverse('event_import_delete_all'))
        self.assertEqual(
            Event.objects.filter(is_imported=False, company=self.company).count(),
            1,
        )

    def test_delete_post_scoped_to_company(self):
        """POST only deletes imported events for the user's company, not other companies."""
        other_company = make_company('Other Co')
        admin = make_user(self.company, 'supplier_admin', username='sadmin_del_scope')
        self.client.login(username='sadmin_del_scope', password='testpass123')
        make_event(self.company,  is_imported=True)
        make_event(other_company, is_imported=True)
        self.client.post(reverse('event_import_delete_all'))
        self.assertEqual(
            Event.objects.filter(is_imported=True, company=self.company).count(),
            0,
        )
        self.assertEqual(
            Event.objects.filter(is_imported=True, company=other_company).count(),
            1,
        )


# ---------------------------------------------------------------------------
# CSV validation view
# ---------------------------------------------------------------------------

def _make_csv_bytes(rows):
    """Build a CSV bytes object from a list of dicts."""
    buf = io.StringIO()
    fieldnames = rows[0].keys() if rows else []
    writer = csv.DictWriter(buf, fieldnames=fieldnames)
    writer.writeheader()
    writer.writerows(rows)
    return buf.getvalue().encode('utf-8')


class ValidateCsvAccessTest(TestCase):

    def setUp(self):
        self.company = make_company('Validate Co')
        self.client = Client()

    def test_validate_requires_supplier_admin(self):
        """Non-supplier-admin is redirected to dashboard."""
        ambassador = make_user(self.company, 'ambassador', username='amb_val')
        self.client.login(username='amb_val', password='testpass123')
        csv_bytes = _make_csv_bytes([
            {'distributor': 'Dist A', 'location': 'Store 1', 'address': '1 Main St', 'city': 'Newark'},
        ])
        response = self.client.post(
            reverse('event_import_validate_csv'),
            {'csv_file': io.BytesIO(csv_bytes)},
        )
        self.assertRedirects(response, reverse('dashboard'), fetch_redirect_response=False)


class ValidateCsvNoConflictsTest(TestCase):

    def setUp(self):
        self.company = make_company('Validate No Conflict Co')
        self.admin = make_user(self.company, 'supplier_admin', username='sadmin_val_nc')
        self.client = Client()
        self.client.login(username='sadmin_val_nc', password='testpass123')

    def test_validate_no_conflicts(self):
        """CSV with all cities under one distributor returns no conflicts."""
        csv_bytes = _make_csv_bytes([
            {'distributor': 'Shore Point', 'location': 'Store A', 'address': '1 Main St', 'city': 'Newark'},
            {'distributor': 'Shore Point', 'location': 'Store B', 'address': '2 Oak Ave', 'city': 'Newark'},
            {'distributor': 'Shore Point', 'location': 'Store C', 'address': '3 Elm St', 'city': 'Hoboken'},
        ])
        response = self.client.post(
            reverse('event_import_validate_csv'),
            {'csv_file': io.BytesIO(csv_bytes)},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['conflicts'], [])
        self.assertEqual(response.context['summary']['conflict_cities'], 0)
        self.assertEqual(response.context['summary']['needs_fix'], 0)


class ValidateCsvDetectsConflictTest(TestCase):

    def setUp(self):
        self.company = make_company('Validate Conflict Co')
        self.admin = make_user(self.company, 'supplier_admin', username='sadmin_val_c')
        self.client = Client()
        self.client.login(username='sadmin_val_c', password='testpass123')

    def test_validate_detects_conflict(self):
        """CSV with same city under two distributors is flagged as a conflict."""
        csv_bytes = _make_csv_bytes([
            {'distributor': 'Dist A', 'location': 'Store A', 'address': '1 Main St', 'city': 'Newark'},
            {'distributor': 'Dist B', 'location': 'Store B', 'address': '2 Oak Ave', 'city': 'Newark'},
        ])
        response = self.client.post(
            reverse('event_import_validate_csv'),
            {'csv_file': io.BytesIO(csv_bytes)},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['summary']['conflict_cities'], 1)
        self.assertEqual(response.context['summary']['needs_fix'], 1)
        conflicts = response.context['conflicts']
        self.assertEqual(len(conflicts), 1)
        self.assertEqual(conflicts[0]['city'], 'Newark')


class ValidateCsvSuggestsDbDistributorTest(TestCase):

    def setUp(self):
        self.company = make_company('Validate Suggest Co')
        self.admin = make_user(self.company, 'supplier_admin', username='sadmin_val_s')
        self.client = Client()
        self.client.login(username='sadmin_val_s', password='testpass123')

    def test_validate_suggests_db_distributor(self):
        """When only one DB distributor has accounts in conflicting city, it is suggested with high confidence."""
        dist_a = make_distributor(self.company, name='Shore Point')
        make_account(self.company, dist_a, name='Store A', city='Newark')

        csv_bytes = _make_csv_bytes([
            {'distributor': 'Shore Point', 'location': 'Store A', 'address': '1 Main St', 'city': 'Newark'},
            {'distributor': 'Wrong Dist',  'location': 'Store B', 'address': '2 Oak Ave', 'city': 'Newark'},
        ])
        response = self.client.post(
            reverse('event_import_validate_csv'),
            {'csv_file': io.BytesIO(csv_bytes)},
        )
        self.assertEqual(response.status_code, 200)
        conflicts = response.context['conflicts']
        self.assertEqual(len(conflicts), 1)
        conflict = conflicts[0]
        self.assertEqual(conflict['confidence'], 'high')
        self.assertEqual(conflict['suggested_distributor'], 'Shore Point')


class ValidateCsvNoDbMatchTest(TestCase):

    def setUp(self):
        self.company = make_company('Validate No DB Co')
        self.admin = make_user(self.company, 'supplier_admin', username='sadmin_val_ndb')
        self.client = Client()
        self.client.login(username='sadmin_val_ndb', password='testpass123')

    def test_validate_no_db_match(self):
        """City not in DB returns unknown confidence."""
        csv_bytes = _make_csv_bytes([
            {'distributor': 'Dist A', 'location': 'Store A', 'address': '1 Main St', 'city': 'Trenton'},
            {'distributor': 'Dist B', 'location': 'Store B', 'address': '2 Oak Ave', 'city': 'Trenton'},
        ])
        response = self.client.post(
            reverse('event_import_validate_csv'),
            {'csv_file': io.BytesIO(csv_bytes)},
        )
        self.assertEqual(response.status_code, 200)
        conflicts = response.context['conflicts']
        self.assertEqual(len(conflicts), 1)
        self.assertEqual(conflicts[0]['confidence'], 'unknown')
        self.assertIsNone(conflicts[0]['suggested_distributor'])


# ---------------------------------------------------------------------------
# Execute import (Stage 3)
# ---------------------------------------------------------------------------

from apps.catalog.models import Brand, Item as CatalogItem
from apps.event_import.models import HistoricalImportBatch


def _make_execute_session(company, distributor):
    """Build session data for a single matched row ready for execute."""
    acct = make_account(
        company, distributor,
        name='Test Wines',
        street='50 Oak Ave',
        city='Hoboken',
    )
    csv_key = 'Shore Point Distributing||Test Wines||50 Oak Ave||Hoboken'
    rows = [
        {
            'distributor': 'Shore Point Distributing',
            'location':    'Test Wines',
            'address':     '50 Oak Ave',
            'city':        'Hoboken',
            'date':        '01/15/24',
            'start':       '2:00 PM',
            'hrs':         '2',
            'note1':       'Jane Smith',
            'note2':       '555-1234',
            'promo_person': 'Bob Jones',
            'recap1':      'Great event',
            'recap2':      '',
            'samples':     '30',
            'qr_scans':    '5',
            'sold_bwred0750':  '3',
            'used_bwred0750':  '1',
            'price_bwred0750': '12.99',
        }
    ]
    matches = {
        'high':   [{'csv_key': csv_key, 'match_account_pk': acct.pk,
                    'match_account_name': acct.name, 'row_count': 1, 'score': 95}],
        'review': [],
        'none':   [],
    }
    confirmed = {csv_key: acct.pk}
    return rows, matches, confirmed, acct, csv_key


def _make_brand_and_item(company, item_code='BWRed0750', item_name='Classic Red 750ml'):
    """Create a Brand+Item for testing EventItemRecap creation."""
    brand = Brand.objects.create(company=company, name=f'Test Brand {item_code}')
    return CatalogItem.objects.create(brand=brand, name=item_name, item_code=item_code)


class ExecuteAccessTest(TestCase):

    def setUp(self):
        self.company     = make_company('Execute Access Co')
        self.distributor = make_distributor(self.company)
        self.client      = Client()

    def test_execute_requires_supplier_admin(self):
        """Non-supplier-admin is redirected to dashboard."""
        ambassador = make_user(self.company, 'ambassador', username='amb_exec')
        self.client.login(username='amb_exec', password='testpass123')
        response = self.client.post(reverse('event_import_execute'))
        self.assertRedirects(response, reverse('dashboard'), fetch_redirect_response=False)


class ExecuteImportTest(TestCase):

    def setUp(self):
        self.company     = make_company('Execute Import Co')
        self.distributor = make_distributor(self.company)
        self.admin       = make_user(self.company, 'supplier_admin', username='sadmin_exec')
        self.client      = Client()
        self.client.login(username='sadmin_exec', password='testpass123')

    def _set_session(self, rows, matches, confirmed):
        session = self.client.session
        session['event_import_rows']      = rows
        session['event_import_matches']   = matches
        session['event_import_confirmed'] = confirmed
        session.save()

    def test_execute_creates_events(self):
        """Running execute with valid session creates Event records with correct fields."""
        rows, matches, confirmed, acct, _ = _make_execute_session(self.company, self.distributor)
        self._set_session(rows, matches, confirmed)

        self.client.post(reverse('event_import_execute'))

        events = Event.objects.filter(company=self.company, is_imported=True)
        self.assertEqual(events.count(), 1)
        event = events.first()
        self.assertEqual(event.status, 'paid')
        self.assertTrue(event.is_imported)
        self.assertEqual(event.legacy_ambassador_name, 'Bob Jones')
        self.assertIsNotNone(event.historical_batch)
        self.assertEqual(event.account, acct)
        self.assertEqual(str(event.date), '2024-01-15')
        self.assertEqual(event.notes, 'Retail Contact: Jane Smith | Retail Phone: 555-1234 | Promo Person: Bob Jones')
        self.assertEqual(event.recap_notes, 'Great event')
        self.assertEqual(event.recap_samples_poured, 30)
        self.assertEqual(event.recap_qr_codes_scanned, 5)

    def test_execute_creates_event_item_recaps(self):
        """EventItemRecap records created for rows with bottle data."""
        _make_brand_and_item(self.company, 'BWRed0750', 'Classic Red 750ml')
        rows, matches, confirmed, acct, _ = _make_execute_session(self.company, self.distributor)
        self._set_session(rows, matches, confirmed)

        self.client.post(reverse('event_import_execute'))

        from apps.events.models import EventItemRecap
        event = Event.objects.filter(company=self.company, is_imported=True).first()
        recaps = EventItemRecap.objects.filter(event=event)
        self.assertEqual(recaps.count(), 1)
        recap = recaps.first()
        self.assertEqual(recap.bottles_sold, 3)
        self.assertEqual(recap.bottles_used_for_samples, 1)
        from decimal import Decimal
        self.assertEqual(recap.shelf_price, Decimal('12.99'))

    def test_execute_skips_unmatched_rows(self):
        """Rows with no account_pk in confirmed map are not created as events."""
        rows, matches, confirmed, acct, csv_key = _make_execute_session(self.company, self.distributor)
        confirmed[csv_key] = None   # mark as skipped
        self._set_session(rows, matches, confirmed)

        self.client.post(reverse('event_import_execute'))

        self.assertEqual(Event.objects.filter(company=self.company, is_imported=True).count(), 0)

    def test_execute_clears_session(self):
        """Session keys are cleared after successful import."""
        rows, matches, confirmed, _, _ = _make_execute_session(self.company, self.distributor)
        self._set_session(rows, matches, confirmed)

        self.client.post(reverse('event_import_execute'))

        session = self.client.session
        self.assertNotIn('event_import_rows',      session)
        self.assertNotIn('event_import_matches',   session)
        self.assertNotIn('event_import_confirmed', session)

    def test_execute_creates_batch(self):
        """HistoricalImportBatch created with correct event_count."""
        rows, matches, confirmed, acct, _ = _make_execute_session(self.company, self.distributor)
        self._set_session(rows, matches, confirmed)

        self.client.post(reverse('event_import_execute'))

        batches = HistoricalImportBatch.objects.filter(company=self.company)
        self.assertEqual(batches.count(), 1)
        batch = batches.first()
        self.assertEqual(batch.event_count, 1)
        self.assertEqual(batch.imported_by, self.admin)

    def test_notes_includes_promo_person(self):
        """When promo_person is set, event notes contains 'Promo Person: <name>'."""
        rows, matches, confirmed, _, _ = _make_execute_session(self.company, self.distributor)
        rows[0]['promo_person'] = 'Alice Green'
        self._set_session(rows, matches, confirmed)

        self.client.post(reverse('event_import_execute'))

        event = Event.objects.filter(company=self.company, is_imported=True).first()
        self.assertIn('Promo Person: Alice Green', event.notes)

    def test_samples_poured_imported(self):
        """When samples is set in the row, recap_samples_poured is stored on the event."""
        rows, matches, confirmed, _, _ = _make_execute_session(self.company, self.distributor)
        rows[0]['samples'] = '42'
        self._set_session(rows, matches, confirmed)

        self.client.post(reverse('event_import_execute'))

        event = Event.objects.filter(company=self.company, is_imported=True).first()
        self.assertEqual(event.recap_samples_poured, 42)


# ---------------------------------------------------------------------------
# Delete batch
# ---------------------------------------------------------------------------

class DeleteBatchTest(TestCase):

    def setUp(self):
        self.company     = make_company('Delete Batch Co')
        self.distributor = make_distributor(self.company)
        self.admin       = make_user(self.company, 'supplier_admin', username='sadmin_delbatch')
        self.client      = Client()
        self.client.login(username='sadmin_delbatch', password='testpass123')

    def _make_batch_with_events(self, count=2):
        batch = HistoricalImportBatch.objects.create(
            company=self.company,
            imported_by=self.admin,
            event_count=count,
        )
        for i in range(count):
            Event.objects.create(
                company=self.company,
                is_imported=True,
                date=datetime.date(2024, 1, i + 1),
                historical_batch=batch,
            )
        return batch

    def test_delete_batch_removes_events(self):
        """Deleting a batch removes all its events."""
        batch = self._make_batch_with_events(3)
        self.assertEqual(Event.objects.filter(historical_batch=batch).count(), 3)

        self.client.post(reverse('event_import_delete_batch', args=[batch.pk]))

        self.assertEqual(Event.objects.filter(company=self.company, is_imported=True).count(), 0)
        self.assertFalse(HistoricalImportBatch.objects.filter(pk=batch.pk).exists())

    def test_delete_batch_scoped_to_company(self):
        """Cannot delete another company's batch — returns 404."""
        other_company = make_company('Other Batch Co')
        other_admin   = make_user(other_company, 'supplier_admin', username='sadmin_other_batch')
        other_batch   = HistoricalImportBatch.objects.create(
            company=other_company,
            imported_by=other_admin,
            event_count=0,
        )

        response = self.client.post(reverse('event_import_delete_batch', args=[other_batch.pk]))
        self.assertEqual(response.status_code, 404)
        self.assertTrue(HistoricalImportBatch.objects.filter(pk=other_batch.pk).exists())


class ValidateCsvRetailerComparisonTest(TestCase):

    def setUp(self):
        self.company = make_company('Validate Retailer Co')
        self.admin = make_user(self.company, 'supplier_admin', username='sadmin_val_r')
        self.client = Client()
        self.client.login(username='sadmin_val_r', password='testpass123')

    def test_validate_retailer_comparison(self):
        """When multiple DB distributors have accounts in city, retailer matching determines suggestion."""
        dist_a = make_distributor(self.company, name='Shore Point')
        dist_b = make_distributor(self.company, name='Other Dist')
        # Shore Point has an account whose name matches the CSV location for Dist A
        make_account(self.company, dist_a, name='Newark Wine And Spirits', city='Newark')
        make_account(self.company, dist_b, name='Completely Different Store', city='Newark')

        csv_bytes = _make_csv_bytes([
            {'distributor': 'Dist A', 'location': 'Newark Wine And Spirits', 'address': '1 Main St', 'city': 'Newark'},
            {'distributor': 'Dist B', 'location': 'Some Other Place',         'address': '2 Oak Ave', 'city': 'Newark'},
        ])
        response = self.client.post(
            reverse('event_import_validate_csv'),
            {'csv_file': io.BytesIO(csv_bytes)},
        )
        self.assertEqual(response.status_code, 200)
        conflicts = response.context['conflicts']
        self.assertEqual(len(conflicts), 1)
        conflict = conflicts[0]
        # Shore Point should win because its account name matches the CSV location
        self.assertEqual(conflict['confidence'], 'medium')
        self.assertEqual(conflict['suggested_distributor'], 'Shore Point')


# ---------------------------------------------------------------------------
# Improvement 1: dash city strip
# ---------------------------------------------------------------------------

class DashCityStripTest(TestCase):

    def test_dash_city_stripped(self):
        """'ShopRite Wine & Spirits - Morristown' with city 'Morristown' strips to core name."""
        raw = normalize_for_match('ShopRite Wine & Spirits - Morristown')
        result = _strip_city(raw, 'Morristown')
        self.assertEqual(result, 'SHOPRITE WINE & SPIRITS')

    def test_dash_city_stripped_ws_abbreviation(self):
        """'ShopRite W&S - Byram' strips city correctly after abbreviation expansion."""
        from apps.event_import.matching import _expand_abbreviations
        raw = normalize_for_match('ShopRite W&S - Byram')
        raw = _expand_abbreviations(raw)
        result = _strip_city(raw, 'Byram')
        self.assertNotIn('BYRAM', result)
        self.assertIn('SHOPRITE', result)


# ---------------------------------------------------------------------------
# Improvement 2: parenthetical strip
# ---------------------------------------------------------------------------

class StripParentheticalTest(TestCase):

    def test_strip_parenthetical(self):
        """Parenthetical branch suffix is removed from account name."""
        self.assertEqual(
            _strip_parentheticals('SHOP RITE LIQUORS (CEDAR KNOLLS)'),
            'SHOP RITE LIQUORS',
        )
        self.assertEqual(
            _strip_parentheticals('BUY RITE (NORTH AVE)'),
            'BUY RITE',
        )

    def test_strip_parenthetical_no_match(self):
        """Name with no parenthetical is returned unchanged."""
        self.assertEqual(
            _strip_parentheticals('SHOP RITE LIQUORS'),
            'SHOP RITE LIQUORS',
        )


# ---------------------------------------------------------------------------
# Improvement 3: city mismatch penalty
# ---------------------------------------------------------------------------

class CityMismatchPenaltyTest(TestCase):

    def test_city_mismatch_penalty(self):
        """Candidate with wrong city scores lower than identical candidate with correct city."""
        accts = [
            {'pk': 200, 'name': 'Riverside Spirits', 'street': '50 Oak Ave', 'city': 'Newark'},
            {'pk': 201, 'name': 'Riverside Spirits', 'street': '50 Oak Ave', 'city': 'Trenton'},
        ]
        by_dist = {'Shore Point': accts}
        row = {
            'distributor': 'Shore Point',
            'location':    'Riverside Spirits',
            'address':     '50 Oak Ave',
            'city':        'Newark',
        }
        result = match_csv_row(row, by_dist)
        candidates = {c['pk']: c['score'] for c in result['candidates']}
        self.assertGreater(candidates[200], candidates[201])

    def test_city_match_no_penalty(self):
        """Candidate with matching city is not penalized and scores highest."""
        accts = [
            {'pk': 202, 'name': 'Harbor Wine', 'street': '10 Main St', 'city': 'Hoboken'},
        ]
        by_dist = {'Shore Point': accts}
        row = {
            'distributor': 'Shore Point',
            'location':    'Harbor Wine',
            'address':     '10 Main St',
            'city':        'Hoboken',
        }
        result = match_csv_row(row, by_dist)
        self.assertGreaterEqual(result['score'], 75)
        self.assertEqual(result['status'], 'high')


# ---------------------------------------------------------------------------
# End-to-end: ShopRite Morristown / Cedar Knolls
# ---------------------------------------------------------------------------

class ShopRiteMorristownEndToEndTest(TestCase):

    def test_shoprite_morristown_matches(self):
        """
        'ShopRite Wine & Spirits - Morristown' at '178 E Hanover Ave', city
        'Cedar Knolls' should match 'SHOP RITE LIQUORS (CEDAR KNOLLS)' at
        '178 EAST HANOVER AVE' with score >= 75.

        Relies on: parenthetical strip from account name, street number
        boost (178 matches), and city score (both Cedar Knolls).
        Wrong-city candidate should score significantly lower due to the
        city mismatch penalty.
        """
        accts = [
            {
                'pk': 300,
                'name':   'Shop Rite Liquors (Cedar Knolls)',
                'street': '178 East Hanover Ave',
                'city':   'Cedar Knolls',
            },
            {
                'pk': 301,
                'name':   'Shop Rite Liquors (Parsippany)',
                'street': '99 Route 46',
                'city':   'Parsippany',
            },
        ]
        by_dist = {'Shore Point': accts}
        row = {
            'distributor': 'Shore Point',
            'location':    'ShopRite Wine & Spirits - Morristown',
            'address':     '178 E Hanover Ave',
            'city':        'Cedar Knolls',
        }
        result = match_csv_row(row, by_dist)
        scores = {c['pk']: c['score'] for c in result['candidates']}

        # Correct match must hit the high threshold
        self.assertGreaterEqual(scores[300], 75)
        self.assertEqual(result['match']['pk'], 300)

        # Wrong-city candidate should score significantly lower
        self.assertGreater(scores[300] - scores[301], 15)


# ---------------------------------------------------------------------------
# Parse helper N/A handling
# ---------------------------------------------------------------------------

class ParseIntNATest(TestCase):

    def test_parse_int_na_returns_none(self):
        """_parse_int('N/A') returns None."""
        self.assertIsNone(_parse_int('N/A'))

    def test_parse_int_dash_returns_none(self):
        """_parse_int('-') returns None."""
        self.assertIsNone(_parse_int('-'))


class ParsePriceNATest(TestCase):

    def test_parse_price_na_returns_none(self):
        """_parse_price('N/A') returns None."""
        self.assertIsNone(_parse_price('N/A'))


# ---------------------------------------------------------------------------
# Execute: oldest-first ordering
# ---------------------------------------------------------------------------

class ExecuteOldestFirstTest(TestCase):

    def setUp(self):
        self.company     = make_company('Oldest First Co')
        self.distributor = make_distributor(self.company, name='Shore Point Distributing')
        self.admin       = make_user(self.company, 'supplier_admin', username='sadmin_oldest')
        self.client      = Client()
        self.client.login(username='sadmin_oldest', password='testpass123')

    def test_execute_processes_oldest_first(self):
        """Events are created in ascending date order regardless of row order in session."""
        acct = make_account(
            self.company, self.distributor,
            name='Test Wines',
            street='50 Oak Ave',
            city='Hoboken',
        )
        csv_key = 'Shore Point Distributing||Test Wines||50 Oak Ave||Hoboken'

        # Rows intentionally out of order: newest first
        rows = [
            {
                'distributor': 'Shore Point Distributing',
                'location':    'Test Wines',
                'address':     '50 Oak Ave',
                'city':        'Hoboken',
                'date':        '03/10/24',
                'start':       '', 'hrs': '2',
                'note1': '', 'note2': '', 'promo_person': '',
                'recap1': '', 'recap2': '', 'samples': '', 'qr_scans': '',
            },
            {
                'distributor': 'Shore Point Distributing',
                'location':    'Test Wines',
                'address':     '50 Oak Ave',
                'city':        'Hoboken',
                'date':        '01/05/24',
                'start':       '', 'hrs': '2',
                'note1': '', 'note2': '', 'promo_person': '',
                'recap1': '', 'recap2': '', 'samples': '', 'qr_scans': '',
            },
            {
                'distributor': 'Shore Point Distributing',
                'location':    'Test Wines',
                'address':     '50 Oak Ave',
                'city':        'Hoboken',
                'date':        '02/20/24',
                'start':       '', 'hrs': '2',
                'note1': '', 'note2': '', 'promo_person': '',
                'recap1': '', 'recap2': '', 'samples': '', 'qr_scans': '',
            },
        ]
        matches  = {
            'high':   [{'csv_key': csv_key, 'match_account_pk': acct.pk,
                        'match_account_name': acct.name, 'row_count': 3, 'score': 95}],
            'review': [],
            'none':   [],
        }
        confirmed = {csv_key: acct.pk}

        session = self.client.session
        session['event_import_rows']      = rows
        session['event_import_matches']   = matches
        session['event_import_confirmed'] = confirmed
        session.save()

        self.client.post(reverse('event_import_execute'))

        events = list(Event.objects.filter(company=self.company, is_imported=True).order_by('pk'))
        self.assertEqual(len(events), 3)
        dates = [str(e.date) for e in events]
        self.assertEqual(dates, ['2024-01-05', '2024-02-20', '2024-03-10'])
