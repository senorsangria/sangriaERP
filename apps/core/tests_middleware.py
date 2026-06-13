"""
Tests for apps.core.middleware — SentryCompanyTagMiddleware.
"""
from unittest.mock import patch

from django.contrib.auth.models import AnonymousUser
from django.test import RequestFactory, TestCase

from apps.core.middleware import SentryCompanyTagMiddleware
from apps.core.models import Company, User


def _dummy_get_response(request):
    """Minimal get_response callable for middleware unit tests."""
    return None


class SentryCompanyTagMiddlewareTest(TestCase):
    """
    Unit tests for SentryCompanyTagMiddleware.

    Tests call the middleware directly via RequestFactory so they run without
    the full middleware stack, keeping them fast and isolated.
    """

    def setUp(self):
        self.factory = RequestFactory()
        self.middleware = SentryCompanyTagMiddleware(_dummy_get_response)
        self.company = Company.objects.create(name='Acme Beverages')
        self.user_with_company = User.objects.create_user(
            username='user_with_co',
            password='testpass123',
            company=self.company,
        )
        self.user_no_company = User.objects.create_user(
            username='user_no_co',
            password='testpass123',
            company=None,
        )

    def test_sentry_dormant_when_dsn_unset(self):
        """
        With SENTRY_DSN unset (the default test condition), the app loads
        and a normal request returns successfully — Sentry not initialised,
        nothing raised.
        """
        # The login page is publicly accessible; a 200 confirms the request
        # pipeline (including the new middleware) works without Sentry active.
        response = self.client.get('/login/')
        self.assertEqual(response.status_code, 200)

    def test_company_tag_set_for_authenticated_user_with_company(self):
        """
        A request from an authenticated user who has a company causes
        sentry_sdk.set_tag to be called with ('company', <company.slug>).
        """
        request = self.factory.get('/')
        request.user = self.user_with_company

        with patch('apps.core.middleware.sentry_sdk.set_tag') as mock_set_tag:
            self.middleware(request)

        mock_set_tag.assert_called_once_with('company', self.company.slug)

    def test_no_company_tag_for_anonymous_request(self):
        """
        An anonymous request passes through the middleware without error
        and does not set a company tag.
        """
        request = self.factory.get('/')
        request.user = AnonymousUser()

        with patch('apps.core.middleware.sentry_sdk.set_tag') as mock_set_tag:
            self.middleware(request)

        mock_set_tag.assert_not_called()

    def test_no_company_tag_for_user_without_company(self):
        """
        An authenticated user with no company (e.g. saas_admin) passes through
        the middleware without error and sets no company tag.
        """
        request = self.factory.get('/')
        request.user = self.user_no_company

        with patch('apps.core.middleware.sentry_sdk.set_tag') as mock_set_tag:
            self.middleware(request)

        mock_set_tag.assert_not_called()
