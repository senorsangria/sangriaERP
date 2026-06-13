"""
Tests for the R16 infrastructure endpoints: /healthz and /ops/status.

/healthz is an unauthenticated liveness + DB-connectivity probe.
/ops/status is an operator-only (staff-gated) mirror of the live config
that reports booleans/names but NEVER any secret value.
"""
import json

from django.conf import settings
from django.test import TestCase
from django.urls import reverse

from apps.core.models import User


class HealthzTest(TestCase):
    """The unauthenticated liveness probe."""

    def test_healthz_returns_ok(self):
        # No authentication; DB is reachable during tests.
        resp = self.client.get(reverse('healthz'))
        self.assertEqual(resp.status_code, 200)
        data = json.loads(resp.content)
        self.assertEqual(data['status'], 'ok')
        self.assertEqual(data['database'], 'ok')

    def test_healthz_is_minimal(self):
        # Liveness only — no version strings or config detail leak.
        resp = self.client.get(reverse('healthz'))
        data = json.loads(resp.content)
        self.assertEqual(set(data.keys()), {'status', 'database'})


class OpsStatusGatingTest(TestCase):
    """Access gating: 404 for everyone who is not a staff operator."""

    def test_ops_status_404_for_anonymous(self):
        resp = self.client.get(reverse('ops_status'))
        self.assertEqual(resp.status_code, 404)

    def test_ops_status_404_for_non_staff_user(self):
        user = User.objects.create_user(
            username='regular', password='testpass123',
        )
        self.assertFalse(user.is_staff)
        self.client.force_login(user)
        resp = self.client.get(reverse('ops_status'))
        self.assertEqual(resp.status_code, 404)


class OpsStatusContentTest(TestCase):
    """The payload returned to a staff operator."""

    def setUp(self):
        self.staff = User.objects.create_user(
            username='operator', password='testpass123', is_staff=True,
        )
        self.client.force_login(self.staff)

    def test_ops_status_ok_for_staff(self):
        resp = self.client.get(reverse('ops_status'))
        self.assertEqual(resp.status_code, 200)
        data = json.loads(resp.content)
        expected_keys = {
            'server_software',
            'debug',
            'allowed_hosts',
            'session_cookie_secure',
            'csrf_cookie_secure',
            'secure_ssl_redirect',
            'secure_hsts_seconds',
            'secure_hsts_include_subdomains',
            'secure_hsts_preload',
            'secure_proxy_ssl_header_set',
            'database',
            'pending_migrations',
            'deployed_commit',
            'web_concurrency',
            'storage_backend',
            'deploy_check_warnings',
        }
        self.assertTrue(expected_keys.issubset(set(data.keys())))
        # The live DEBUG value is a boolean; DB reports connectivity.
        self.assertIsInstance(data['debug'], bool)
        self.assertIsInstance(data['allowed_hosts'], list)
        self.assertTrue(data['database']['connected'])

    def test_ops_status_leaks_no_secrets(self):
        resp = self.client.get(reverse('ops_status'))
        body = resp.content.decode('utf-8')

        # The actual SECRET_KEY value must never appear in the payload.
        secret_key = settings.SECRET_KEY
        self.assertTrue(secret_key)  # sanity: there is a secret to leak
        self.assertNotIn(secret_key, body)

        # Nor the database password (only meaningful if one is configured).
        db_password = settings.DATABASES['default'].get('PASSWORD', '')
        if db_password:
            self.assertNotIn(db_password, body)
