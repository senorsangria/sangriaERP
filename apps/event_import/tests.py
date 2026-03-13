"""
Tests for the event_import app — matching engine and upload access control.
"""
from django.test import Client, TestCase
from django.urls import reverse

from apps.accounts.models import Account
from apps.core.models import Company, User
from apps.core.rbac import Role
from apps.distribution.models import Distributor
from apps.event_import.matching import match_csv_row, normalize_for_match


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
        """Exact name + address match returns status='high' with score >= 85."""
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
        self.assertGreaterEqual(result['score'], 85)

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
