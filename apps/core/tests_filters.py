"""
Tests for apps.core.filters — session filter utilities.
"""
from django.test import RequestFactory, TestCase

from apps.core.filters import (
    apply_session_filters,
    compute_active_filter_count,
    is_filter_active,
)

DEFAULTS = {
    'distributor':   '',
    'on_off':        '',
    'account_type':  [],
    'county':        '',
}


def _mock_session():
    """Return a dict that quacks like request.session for these tests."""
    return {}


class FakeRequest:
    """Minimal request-like object for testing session filter helpers."""

    def __init__(self, get_data=None, session_data=None):
        from django.http import QueryDict
        raw = ''
        for k, v in (get_data or {}).items():
            if isinstance(v, list):
                for item in v:
                    raw += f'&{k}={item}'
            else:
                raw += f'&{k}={v}'
        self.GET = QueryDict(raw.lstrip('&'))
        self.session = dict(session_data or {})


class ApplySessionFiltersTest(TestCase):

    def test_saves_to_session_on_filter_submit(self):
        req = FakeRequest(get_data={'distributor': '5', 'on_off': 'ON'})
        filters, was_set = apply_session_filters(req, 'test_key', DEFAULTS)
        self.assertTrue(was_set)
        self.assertEqual(filters['distributor'], '5')
        self.assertEqual(filters['on_off'], 'ON')
        self.assertEqual(req.session['test_key']['distributor'], '5')

    def test_restores_from_session_on_bare_get(self):
        session = {'test_key': {'distributor': '3', 'on_off': 'OFF', 'account_type': [], 'county': ''}}
        req = FakeRequest(session_data=session)
        filters, was_set = apply_session_filters(req, 'test_key', DEFAULTS)
        self.assertFalse(was_set)
        self.assertEqual(filters['distributor'], '3')
        self.assertEqual(filters['on_off'], 'OFF')

    def test_clear_resets_to_defaults(self):
        session = {'test_key': {'distributor': '3', 'on_off': 'OFF', 'account_type': [], 'county': ''}}
        req = FakeRequest(get_data={'clear_filters': '1'}, session_data=session)
        filters, was_set = apply_session_filters(req, 'test_key', DEFAULTS)
        self.assertFalse(was_set)
        self.assertEqual(filters, DEFAULTS)
        self.assertNotIn('test_key', req.session)

    def test_returns_defaults_when_no_session_and_no_params(self):
        req = FakeRequest()
        filters, was_set = apply_session_filters(req, 'test_key', DEFAULTS)
        self.assertFalse(was_set)
        self.assertEqual(filters['distributor'], '')
        self.assertEqual(filters['account_type'], [])

    def test_handles_multi_value_list_filters(self):
        req = FakeRequest(get_data={'account_type': ['Bar', 'Restaurant'], 'distributor': ''})
        filters, _ = apply_session_filters(req, 'test_key', DEFAULTS)
        self.assertIn('Bar', filters['account_type'])
        self.assertIn('Restaurant', filters['account_type'])

    def test_backward_compat_string_to_list(self):
        # Old session stored a string where a list is expected
        session = {'test_key': {'distributor': '', 'on_off': '', 'account_type': 'Bar', 'county': ''}}
        req = FakeRequest(session_data=session)
        filters, _ = apply_session_filters(req, 'test_key', DEFAULTS)
        self.assertIsInstance(filters['account_type'], list)
        self.assertIn('Bar', filters['account_type'])

    def test_backward_compat_empty_string_becomes_empty_list(self):
        session = {'test_key': {'distributor': '', 'on_off': '', 'account_type': '', 'county': ''}}
        req = FakeRequest(session_data=session)
        filters, _ = apply_session_filters(req, 'test_key', DEFAULTS)
        self.assertIsInstance(filters['account_type'], list)
        self.assertEqual(filters['account_type'], [])


class ComputeActiveFilterCountTest(TestCase):

    def test_counts_non_default_scalar_filters(self):
        active = {'distributor': '5', 'on_off': '', 'account_type': [], 'county': ''}
        self.assertEqual(compute_active_filter_count(active, DEFAULTS), 1)

    def test_counts_non_empty_list_filters(self):
        active = {'distributor': '', 'on_off': '', 'account_type': ['Bar'], 'county': ''}
        self.assertEqual(compute_active_filter_count(active, DEFAULTS), 1)

    def test_ignores_default_values(self):
        active = {'distributor': '', 'on_off': '', 'account_type': [], 'county': ''}
        self.assertEqual(compute_active_filter_count(active, DEFAULTS), 0)

    def test_counts_multiple_active_dimensions(self):
        active = {'distributor': 'none', 'on_off': 'ON', 'account_type': ['Bar', 'Restaurant'], 'county': 'Hudson'}
        self.assertEqual(compute_active_filter_count(active, DEFAULTS), 4)

    def test_empty_list_does_not_count(self):
        active = {'distributor': '', 'on_off': '', 'account_type': [], 'county': ''}
        self.assertEqual(compute_active_filter_count(active, DEFAULTS), 0)


class IsFilterActiveTest(TestCase):

    def test_returns_true_when_any_filter_set(self):
        active = {'distributor': '5', 'on_off': '', 'account_type': [], 'county': ''}
        self.assertTrue(is_filter_active(active, DEFAULTS))

    def test_returns_false_when_all_defaults(self):
        active = {'distributor': '', 'on_off': '', 'account_type': [], 'county': ''}
        self.assertFalse(is_filter_active(active, DEFAULTS))
