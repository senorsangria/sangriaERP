"""
Tests for resolve_mappings view, bulk_save_mappings endpoint, and inventory upload
integration — Inventory Mapping UX feature.
"""
import json

from django.test import Client, TestCase
from django.urls import reverse

from apps.catalog.models import Brand, Item
from apps.core.models import Company, User
from apps.core.rbac import Role
from apps.distribution.models import Distributor, InventorySnapshot
from apps.imports.models import ItemMapping


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_company(name='Test Co'):
    return Company.objects.create(name=name)


def _make_supplier_admin(company, username='admin'):
    user = User.objects.create_user(username=username, password='pass', company=company)
    user.roles.set([Role.objects.get(codename='supplier_admin')])
    return user


def _make_user_no_perms(company, username='noperms'):
    return User.objects.create_user(username=username, password='pass', company=company)


def _make_distributor(company, name='Dist A'):
    return Distributor.objects.create(company=company, name=name)


def _make_brand(company, name='Brand'):
    return Brand.objects.create(company=company, name=name)


def _make_item(brand, name='Item', item_code='CODE001'):
    return Item.objects.create(brand=brand, name=name, item_code=item_code)


def _make_mapping(company, distributor, raw_code, item, status=ItemMapping.Status.MAPPED):
    return ItemMapping.objects.create(
        company=company,
        distributor=distributor,
        raw_item_name=raw_code,
        mapped_item=item,
        status=status,
    )


# ---------------------------------------------------------------------------
# resolve_mappings view tests
# ---------------------------------------------------------------------------

class ResolveMappingsPermissionTest(TestCase):

    def setUp(self):
        self.company = _make_company()
        self.client = Client()

    def test_requires_both_permissions_unauthenticated(self):
        resp = self.client.get(reverse('resolve_mappings'))
        # Unauthenticated users are redirected to login (no ?next= since we use redirect() directly)
        self.assertRedirects(resp, reverse('login'), fetch_redirect_response=False)

    def test_requires_both_permissions_no_perms(self):
        user = _make_user_no_perms(self.company)
        self.client.force_login(user)
        resp = self.client.get(reverse('resolve_mappings'))
        self.assertEqual(resp.status_code, 403)

    def test_supplier_admin_can_access(self):
        user = _make_supplier_admin(self.company)
        self.client.force_login(user)
        # Set session so it doesn't redirect to upload
        session = self.client.session
        session['pending_mapping_resolution'] = {
            'unknown_codes': {},
            'next_url': reverse('inventory_upload'),
            'context': 'inventory',
        }
        session.save()
        resp = self.client.get(reverse('resolve_mappings'))
        # Empty unknown_codes redirects with warning — that's the session-expiry path
        self.assertRedirects(resp, reverse('inventory_upload'), fetch_redirect_response=False)


class ResolveMappingsSessionExpiryTest(TestCase):

    def setUp(self):
        self.company = _make_company()
        self.user = _make_supplier_admin(self.company)
        self.client = Client()
        self.client.force_login(self.user)

    def test_redirects_with_warning_when_session_missing(self):
        resp = self.client.get(reverse('resolve_mappings'))
        self.assertRedirects(resp, reverse('inventory_upload'), fetch_redirect_response=False)
        msgs = list(resp.wsgi_request._messages) if hasattr(resp.wsgi_request, '_messages') else []
        # Follow redirect to check message
        resp2 = self.client.get(reverse('resolve_mappings'), follow=True)
        messages = [str(m) for m in resp2.context['messages']]
        self.assertTrue(any('session expired' in m.lower() or 'upload session' in m.lower()
                            for m in messages))

    def test_redirects_when_unknown_codes_empty(self):
        session = self.client.session
        session['pending_mapping_resolution'] = {
            'unknown_codes': {},
            'next_url': reverse('inventory_upload'),
        }
        session.save()
        resp = self.client.get(reverse('resolve_mappings'))
        self.assertRedirects(resp, reverse('inventory_upload'), fetch_redirect_response=False)


class ResolveMappingsRenderTest(TestCase):

    def setUp(self):
        self.company = _make_company()
        self.brand = _make_brand(self.company)
        self.dist_a = _make_distributor(self.company, 'Dist A')
        self.dist_b = _make_distributor(self.company, 'Dist B')
        self.item_red = _make_item(self.brand, 'Classic Red 750ml', 'Red0750')
        self.item_white = _make_item(self.brand, 'Classic White 750ml', 'Wht0750')
        self.user = _make_supplier_admin(self.company)
        self.client = Client()
        self.client.force_login(self.user)

    def _set_pending(self, unknown_codes):
        session = self.client.session
        session['pending_mapping_resolution'] = {
            'unknown_codes': unknown_codes,
            'next_url': reverse('inventory_upload'),
            'context': 'inventory',
        }
        session.save()

    def test_groups_by_distributor(self):
        self._set_pending({
            str(self.dist_a.id): ['Red0750'],
            str(self.dist_b.id): ['Wht0750'],
        })
        resp = self.client.get(reverse('resolve_mappings'))
        self.assertEqual(resp.status_code, 200)
        groups = resp.context['groups']
        self.assertEqual(len(groups), 2)
        dist_names = {g['distributor'].name for g in groups}
        self.assertIn('Dist A', dist_names)
        self.assertIn('Dist B', dist_names)

    def test_pre_fills_priority_1_high_confidence(self):
        """When same raw_code is mapped at Dist B, Dist A's row gets a high-confidence suggestion."""
        _make_mapping(self.company, self.dist_b, 'Red0750', self.item_red)
        self._set_pending({str(self.dist_a.id): ['Red0750']})
        resp = self.client.get(reverse('resolve_mappings'))
        groups = resp.context['groups']
        row = groups[0]['rows'][0]
        self.assertIsNotNone(row['best_match'])
        self.assertEqual(row['best_match']['confidence'], 'high')
        self.assertEqual(row['best_match']['item'], self.item_red)

    def test_pre_fills_priority_2_medium_confidence(self):
        """When raw_code exactly matches an item_code, row gets medium-confidence suggestion."""
        self._set_pending({str(self.dist_a.id): ['Red0750']})
        resp = self.client.get(reverse('resolve_mappings'))
        groups = resp.context['groups']
        row = groups[0]['rows'][0]
        self.assertIsNotNone(row['best_match'])
        self.assertEqual(row['best_match']['confidence'], 'medium')
        self.assertEqual(row['best_match']['item'], self.item_red)

    def test_no_pre_fill_when_no_match(self):
        """Unknown code with no matching item gets no suggestion."""
        self._set_pending({str(self.dist_a.id): ['UNKNOWN999']})
        resp = self.client.get(reverse('resolve_mappings'))
        groups = resp.context['groups']
        row = groups[0]['rows'][0]
        self.assertIsNone(row['best_match'])

    def test_all_items_in_context(self):
        """all_items context variable contains company items for the dropdown."""
        self._set_pending({str(self.dist_a.id): ['UNKNOWN999']})
        resp = self.client.get(reverse('resolve_mappings'))
        item_pks = {item.pk for item in resp.context['all_items']}
        self.assertIn(self.item_red.pk, item_pks)
        self.assertIn(self.item_white.pk, item_pks)


# ---------------------------------------------------------------------------
# bulk_save_mappings endpoint tests
# ---------------------------------------------------------------------------

class BulkSaveMappingsPermissionTest(TestCase):

    def setUp(self):
        self.company = _make_company()
        self.client = Client()

    def _post(self, payload, user=None):
        if user:
            self.client.force_login(user)
        return self.client.post(
            reverse('bulk_save_mappings'),
            data=json.dumps(payload),
            content_type='application/json',
        )

    def test_requires_authentication(self):
        resp = self._post({'mappings': []})
        self.assertEqual(resp.status_code, 401)

    def test_requires_both_permissions(self):
        user = _make_user_no_perms(self.company)
        resp = self._post({'mappings': []}, user=user)
        self.assertEqual(resp.status_code, 403)


class BulkSaveMappingsCreateTest(TestCase):

    def setUp(self):
        self.company = _make_company()
        self.brand = _make_brand(self.company)
        self.dist = _make_distributor(self.company)
        self.item = _make_item(self.brand, 'Classic Red 750ml', 'Red0750')
        self.user = _make_supplier_admin(self.company)
        self.client = Client()
        self.client.force_login(self.user)

    def _post(self, payload):
        return self.client.post(
            reverse('bulk_save_mappings'),
            data=json.dumps(payload),
            content_type='application/json',
        )

    def test_creates_item_mapping(self):
        resp = self._post({'mappings': [{
            'distributor_id': self.dist.id,
            'raw_item_name': 'Red0750',
            'item_id': self.item.id,
            'apply_to_all': False,
        }]})
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data['ok'])
        self.assertTrue(ItemMapping.objects.filter(
            company=self.company,
            distributor=self.dist,
            raw_item_name='Red0750',
            mapped_item=self.item,
            status=ItemMapping.Status.MAPPED,
        ).exists())

    def test_creates_all_mappings_atomically(self):
        dist2 = _make_distributor(self.company, 'Dist B')
        item2 = _make_item(self.brand, 'White 750ml', 'Wht0750')
        resp = self._post({'mappings': [
            {'distributor_id': self.dist.id, 'raw_item_name': 'Red0750',
             'item_id': self.item.id, 'apply_to_all': False},
            {'distributor_id': dist2.id, 'raw_item_name': 'Wht0750',
             'item_id': item2.id, 'apply_to_all': False},
        ]})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(ItemMapping.objects.filter(company=self.company).count(), 2)

    def test_rolls_back_on_invalid_item_id(self):
        """If any mapping references an invalid item, nothing is saved."""
        resp = self._post({'mappings': [
            {'distributor_id': self.dist.id, 'raw_item_name': 'Red0750',
             'item_id': self.item.id, 'apply_to_all': False},
            {'distributor_id': self.dist.id, 'raw_item_name': 'Bad0750',
             'item_id': 99999, 'apply_to_all': False},
        ]})
        self.assertEqual(resp.status_code, 400)
        # Nothing saved
        self.assertFalse(ItemMapping.objects.filter(company=self.company).exists())

    def test_update_or_create_idempotent(self):
        """Re-submitting the same mapping updates the existing record without error."""
        _make_mapping(self.company, self.dist, 'Red0750', self.item)
        item2 = _make_item(self.brand, 'White 750ml', 'Wht0750')
        resp = self._post({'mappings': [{
            'distributor_id': self.dist.id,
            'raw_item_name': 'Red0750',
            'item_id': item2.id,
            'apply_to_all': False,
        }]})
        self.assertEqual(resp.status_code, 200)
        mapping = ItemMapping.objects.get(company=self.company, distributor=self.dist, raw_item_name='Red0750')
        self.assertEqual(mapping.mapped_item, item2)

    def test_success_message_format(self):
        resp = self._post({'mappings': [{
            'distributor_id': self.dist.id,
            'raw_item_name': 'Red0750',
            'item_id': self.item.id,
            'apply_to_all': False,
        }]})
        self.assertEqual(resp.status_code, 200)
        # Follow redirect to check message
        follow_resp = self.client.get(reverse('inventory_upload'))
        messages = [str(m) for m in follow_resp.context.get('messages', [])]
        self.assertTrue(any('mapping' in m.lower() and 'saved' in m.lower()
                            for m in messages))

    def test_no_mappings_returns_400(self):
        resp = self._post({'mappings': []})
        self.assertEqual(resp.status_code, 400)

    def test_returns_redirect_url_in_json(self):
        resp = self._post({'mappings': [{
            'distributor_id': self.dist.id,
            'raw_item_name': 'Red0750',
            'item_id': self.item.id,
            'apply_to_all': False,
        }]})
        data = resp.json()
        self.assertIn('redirect_url', data)

    def test_sales_context_next_url_returned_to_sales_upload(self):
        """When pending_mapping_resolution has next_url=import_upload, redirect goes there."""
        from django.urls import reverse as _rev
        session = self.client.session
        session['pending_mapping_resolution'] = {
            'unknown_codes': {str(self.dist.id): ['Red0750']},
            'next_url': _rev('import_upload'),
            'context': 'sales',
        }
        session.save()
        resp = self._post({'mappings': [{
            'distributor_id': self.dist.id,
            'raw_item_name': 'Red0750',
            'item_id': self.item.id,
            'apply_to_all': False,
        }]})
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data['redirect_url'], _rev('import_upload'))

    def test_clears_session_after_save(self):
        session = self.client.session
        session['pending_mapping_resolution'] = {
            'unknown_codes': {str(self.dist.id): ['Red0750']},
            'next_url': reverse('inventory_upload'),
        }
        session.save()
        self._post({'mappings': [{
            'distributor_id': self.dist.id,
            'raw_item_name': 'Red0750',
            'item_id': self.item.id,
            'apply_to_all': False,
        }]})
        # Reload session
        self.client.get('/')
        self.assertNotIn('pending_mapping_resolution', self.client.session)


class BulkSaveApplyToAllTest(TestCase):

    def setUp(self):
        self.company = _make_company()
        self.brand = _make_brand(self.company)
        self.dist_a = _make_distributor(self.company, 'Dist A')
        self.dist_b = _make_distributor(self.company, 'Dist B')
        self.dist_c = _make_distributor(self.company, 'Dist C')
        self.item = _make_item(self.brand, 'Classic Red 750ml', 'Red0750')
        self.user = _make_supplier_admin(self.company)
        self.client = Client()
        self.client.force_login(self.user)

    def _post(self, payload):
        return self.client.post(
            reverse('bulk_save_mappings'),
            data=json.dumps(payload),
            content_type='application/json',
        )

    def test_apply_to_all_creates_for_all_distributors(self):
        """apply_to_all=true creates mappings for all 3 active distributors."""
        resp = self._post({'mappings': [{
            'distributor_id': self.dist_a.id,
            'raw_item_name': 'Red0750',
            'item_id': self.item.id,
            'apply_to_all': True,
        }]})
        self.assertEqual(resp.status_code, 200)
        count = ItemMapping.objects.filter(
            company=self.company, raw_item_name='Red0750', mapped_item=self.item
        ).count()
        self.assertEqual(count, 3)  # dist_a (update_or_create) + dist_b + dist_c

    def test_apply_to_all_skips_existing_mappings(self):
        """apply_to_all does not overwrite an existing mapping at another distributor."""
        item2 = _make_item(self.brand, 'White 750ml', 'Wht0750')
        _make_mapping(self.company, self.dist_b, 'Red0750', item2)  # pre-existing, different item
        resp = self._post({'mappings': [{
            'distributor_id': self.dist_a.id,
            'raw_item_name': 'Red0750',
            'item_id': self.item.id,
            'apply_to_all': True,
        }]})
        self.assertEqual(resp.status_code, 200)
        # dist_b's existing mapping should not have been overwritten
        dist_b_mapping = ItemMapping.objects.get(
            company=self.company, distributor=self.dist_b, raw_item_name='Red0750'
        )
        self.assertEqual(dist_b_mapping.mapped_item, item2)

    def test_apply_to_all_only_active_distributors(self):
        """Inactive distributors are excluded from apply_to_all."""
        self.dist_c.is_active = False
        self.dist_c.save()
        resp = self._post({'mappings': [{
            'distributor_id': self.dist_a.id,
            'raw_item_name': 'Red0750',
            'item_id': self.item.id,
            'apply_to_all': True,
        }]})
        self.assertEqual(resp.status_code, 200)
        count = ItemMapping.objects.filter(
            company=self.company, raw_item_name='Red0750'
        ).count()
        self.assertEqual(count, 2)  # dist_a + dist_b only


# ---------------------------------------------------------------------------
# Inventory upload integration tests
# ---------------------------------------------------------------------------

class InventoryUploadMappingIntegrationTest(TestCase):
    """
    Integration tests for the inventory_upload → resolve_mappings redirect flow.
    These use the distribution views directly.
    """

    def setUp(self):
        self.company = _make_company()
        self.brand = _make_brand(self.company)
        self.dist = _make_distributor(self.company, 'Test Dist')
        self.item = _make_item(self.brand, 'Classic Red 750ml', 'Red0750')
        self.user = _make_supplier_admin(self.company)
        self.client = Client()
        self.client.force_login(self.user)

    def _build_inventory_csv(self, rows):
        """Build a minimal inventory CSV and return as an in-memory file."""
        import io
        buf = io.StringIO()
        buf.write('Distributors,Item Name ID,Cases\n')
        for row in rows:
            buf.write(f"{row['dist']},{row['code']},{row.get('qty', '10')}\n")
        buf.seek(0)

        from django.core.files.uploadedfile import SimpleUploadedFile
        return SimpleUploadedFile(
            'test_inventory.csv',
            buf.read().encode('utf-8-sig'),
            content_type='text/csv',
        )

    def test_unmapped_codes_redirects_to_resolve_mappings(self):
        """Uploading a CSV with an unmapped code redirects to the resolution UI."""
        csv_file = self._build_inventory_csv([
            {'dist': 'Test Dist', 'code': 'UNKNOWN999'},
        ])
        resp = self.client.post(
            reverse('inventory_upload'),
            {'year': 2024, 'month': 1, 'csv_file': csv_file},
        )
        self.assertRedirects(resp, reverse('resolve_mappings'), fetch_redirect_response=False)
        # Session should contain the pending resolution data
        self.assertIn('pending_mapping_resolution', self.client.session)
        pending = self.client.session['pending_mapping_resolution']
        self.assertEqual(pending['context'], 'inventory')
        self.assertIn(str(self.dist.id), pending['unknown_codes'])

    def test_mapped_codes_proceeds_to_preview(self):
        """Uploading a CSV with all codes mapped proceeds normally to preview."""
        _make_mapping(self.company, self.dist, 'Red0750', self.item)
        csv_file = self._build_inventory_csv([
            {'dist': 'Test Dist', 'code': 'Red0750'},
        ])
        resp = self.client.post(
            reverse('inventory_upload'),
            {'year': 2024, 'month': 1, 'csv_file': csv_file},
        )
        self.assertRedirects(resp, reverse('inventory_preview'), fetch_redirect_response=False)

    def test_distributor_not_found_shows_error_page(self):
        """Distributor-not-found errors still show the error page, not the mapping UI."""
        csv_file = self._build_inventory_csv([
            {'dist': 'Nonexistent Distributor', 'code': 'Red0750'},
        ])
        resp = self.client.post(
            reverse('inventory_upload'),
            {'year': 2024, 'month': 1, 'csv_file': csv_file},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertTemplateUsed(resp, 'distribution/inventory_upload.html')
        self.assertIn('import_errors', resp.context)
        errors = resp.context['import_errors']
        self.assertTrue(any('Distributor not found' in e for e in errors))

    def test_period_conflict_shows_error_page(self):
        """Period-conflict errors still show the error page."""
        _make_mapping(self.company, self.dist, 'Red0750', self.item)
        InventorySnapshot.objects.create(
            distributor=self.dist, item=self.item,
            quantity_cases=10, year=2024, month=1,
        )
        csv_file = self._build_inventory_csv([
            {'dist': 'Test Dist', 'code': 'Red0750'},
        ])
        resp = self.client.post(
            reverse('inventory_upload'),
            {'year': 2024, 'month': 1, 'csv_file': csv_file},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertTemplateUsed(resp, 'distribution/inventory_upload.html')
        errors = resp.context['import_errors']
        self.assertTrue(any('already has inventory data' in e for e in errors))


# ---------------------------------------------------------------------------
# InventorySnapshot unique constraint
# ---------------------------------------------------------------------------

class InventorySnapshotUniqueConstraintTest(TestCase):

    def setUp(self):
        self.company = _make_company()
        self.brand = _make_brand(self.company)
        self.dist = _make_distributor(self.company)
        self.item = _make_item(self.brand)

    def test_unique_constraint_enforced(self):
        """Creating two InventorySnapshot records for the same (dist, item, year, month) fails."""
        from django.db import IntegrityError
        InventorySnapshot.objects.create(
            distributor=self.dist, item=self.item,
            quantity_cases=10, year=2024, month=1,
        )
        with self.assertRaises(IntegrityError):
            InventorySnapshot.objects.create(
                distributor=self.dist, item=self.item,
                quantity_cases=5, year=2024, month=1,
            )
