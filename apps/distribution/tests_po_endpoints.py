"""
Tests for DistributorPO model, PO modal endpoints, and integration.

Phase 4-step-2b: 29 tests across 5 classes.
"""
import json
from datetime import date
from decimal import Decimal
from unittest.mock import patch

from django.test import Client, TestCase
from django.urls import reverse

from apps.catalog.models import Brand, Item
from apps.core.models import Company, User
from apps.core.rbac import Permission, Role
from apps.distribution.forecast import compute_distributor_forecast
from apps.distribution.models import (
    Distributor, DistributorGroup, DistributorItemProfile, DistributorPO,
    DistributorPOLine, InventorySnapshot,
)
from apps.distribution.tests_forecast import (
    _make_company, _make_supplier_admin, _make_distributor,
    _make_brand, _make_item, _make_account, _make_batch,
    _make_snapshot, _make_sale,
)


# ---------------------------------------------------------------------------
# Extra helpers
# ---------------------------------------------------------------------------

class _FrozenApril2026Date(date):
    """Frozen "today" = 2026-04-15 for forecast/suggestion tests.

    The forecast pivots past-vs-future on ``date.today()`` (referenced as
    ``apps.distribution.forecast.date``). Fixtures that anchor a snapshot at
    April 2026 with a May 2026 lookahead require "now" to be on/before April
    2026 so the lookahead month stays a *future* projection month and uses the
    prior-year (May 2025) sales path. Patch ``apps.distribution.forecast.date``
    with this subclass to make those tests time-independent.
    """

    @classmethod
    def today(cls):
        return cls(2026, 4, 15)


def _make_inventory_user(company, username):
    """Supplier admin WITH can_manage_distributor_inventory permission."""
    return _make_supplier_admin(company, username)


def _make_limited_user(company, username='limited_user'):
    """Supplier admin WITHOUT can_manage_distributor_inventory."""
    role, _ = Role.objects.get_or_create(
        codename='test_no_inv_' + username,
        defaults={'name': 'No Inventory ' + username},
    )
    perm = Permission.objects.get(codename='can_manage_distributors')
    role.permissions.set([perm])
    user = User.objects.create_user(username=username, password='testpass123', company=company)
    user.roles.set([role])
    return user


def _make_po(distributor, year, month, status='projected', ext_po='', notes='',
             generated_by_algorithm=True, created_by=None):
    return DistributorPO.objects.create(
        distributor=distributor,
        year=year,
        month=month,
        status=status,
        external_po_number=ext_po,
        notes=notes,
        generated_by_algorithm=generated_by_algorithm,
        created_by=created_by,
    )


def _make_po_line(po, item, quantity_cases):
    return DistributorPOLine.objects.create(po=po, item=item, quantity_cases=quantity_cases)


def _ajax_post(client, url, data):
    return client.post(
        url,
        data=json.dumps(data),
        content_type='application/json',
        HTTP_X_REQUESTED_WITH='XMLHttpRequest',
    )


# ---------------------------------------------------------------------------
# 1. Model tests
# ---------------------------------------------------------------------------

class DistributorPOModelTest(TestCase):

    def setUp(self):
        self.company = _make_company('PO Model Co')
        self.dist = _make_distributor(self.company)
        self.brand = _make_brand(self.company)
        self.item = _make_item(self.brand)

    # 1. clean() passes with projected status and no PO number
    def test_clean_projected_status_no_po_number_valid(self):
        po = DistributorPO(
            distributor=self.dist, year=2026, month=6,
            status=DistributorPO.Status.PROJECTED, external_po_number='',
        )
        po.full_clean()  # should not raise

    # 2. clean() passes with actual status and PO number present
    def test_clean_actual_status_with_po_number_valid(self):
        po = DistributorPO(
            distributor=self.dist, year=2026, month=6,
            status=DistributorPO.Status.ACTUAL, external_po_number='PO-001',
        )
        po.full_clean()  # should not raise

    # 3. clean() fails with actual status and no PO number
    def test_clean_actual_status_no_po_number_invalid(self):
        from django.core.exceptions import ValidationError
        po = DistributorPO(
            distributor=self.dist, year=2026, month=6,
            status=DistributorPO.Status.ACTUAL, external_po_number='',
        )
        with self.assertRaises(ValidationError) as ctx:
            po.full_clean()
        self.assertIn('external_po_number', ctx.exception.message_dict)

    # 4. POLine cascade-deletes when PO is deleted
    def test_po_line_cascade_deleted_with_po(self):
        po = _make_po(self.dist, 2026, 6)
        _make_po_line(po, self.item, 24)
        self.assertEqual(DistributorPOLine.objects.filter(po=po).count(), 1)
        po.delete()
        self.assertEqual(DistributorPOLine.objects.filter(po_id=po.pk).count(), 0)

    # 5. POLine unique_together (po, item) enforced
    def test_po_line_unique_together_enforced(self):
        from django.db import IntegrityError
        po = _make_po(self.dist, 2026, 6)
        _make_po_line(po, self.item, 24)
        with self.assertRaises(IntegrityError):
            DistributorPOLine.objects.create(po=po, item=self.item, quantity_cases=12)


# ---------------------------------------------------------------------------
# 2. distributor_po_modal_data GET view
# ---------------------------------------------------------------------------

class DistributorPOModalDataTest(TestCase):

    def setUp(self):
        self.company = _make_company('Modal Co')
        self.admin = _make_inventory_user(self.company, 'modal_admin')
        self.dist = _make_distributor(self.company)
        self.brand = _make_brand(self.company)
        self.item = _make_item(self.brand, item_code='ITMA')
        self.item.cases_per_pallet = 12
        self.item.save()
        self.account = _make_account(self.company, self.dist)
        self.batch = _make_batch(self.company, self.dist)
        _make_snapshot(self.dist, self.item, 2026, 4, quantity=100)
        self.client = Client()
        self.client.login(username='modal_admin', password='testpass123')
        self.url = reverse('distributor_po_modal_data',
                           kwargs={'dist_pk': self.dist.pk, 'year': 2026, 'month': 5})

    # 6. Returns 403 without permission
    def test_modal_data_403_without_permission(self):
        limited = _make_limited_user(self.company, 'modal_limited')
        c = Client()
        c.login(username='modal_limited', password='testpass123')
        resp = c.get(self.url)
        self.assertEqual(resp.status_code, 403)

    # 7. Returns 404 for distributor belonging to another company
    def test_modal_data_404_wrong_company(self):
        other_company = _make_company('Other Co')
        other_dist = _make_distributor(other_company, 'Other Dist')
        url = reverse('distributor_po_modal_data',
                      kwargs={'dist_pk': other_dist.pk, 'year': 2026, 'month': 5})
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 404)

    # 8. Includes saved orders for the given month
    def test_modal_data_includes_saved_orders(self):
        po = _make_po(self.dist, 2026, 5)
        _make_po_line(po, self.item, 24)
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(len(data['saved_orders']), 1)
        self.assertEqual(data['saved_orders'][0]['id'], po.pk)
        self.assertEqual(len(data['saved_orders'][0]['lines']), 1)
        self.assertEqual(data['saved_orders'][0]['lines'][0]['item_id'], self.item.pk)

    # saved_orders include so_number (for SO# display in the modal)
    def test_single_modal_data_includes_so_number(self):
        po = _make_po(self.dist, 2026, 5, status='submitted', ext_po='PO-3')
        po.so_number = 8888
        po.save(update_fields=['so_number'])
        _make_po_line(po, self.item, 24)
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn('so_number', data['saved_orders'][0])
        self.assertEqual(data['saved_orders'][0]['so_number'], 8888)

    # 9. Modal data does NOT include suggested_orders (suggestions are now on-demand)
    def test_modal_data_includes_suggested_orders_structure(self):
        self.dist.order_quantity_value = 2
        self.dist.order_quantity_unit = 'pallets'
        self.dist.save()
        _make_sale(self.company, self.batch, self.account, self.item, 2025, 5, 200)

        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertNotIn('suggested_orders', data)
        self.assertIn('saved_orders', data)
        self.assertIn('items', data)
        self.assertIn('distributor', data)

    # 10. Items include cases_per_pallet
    def test_modal_data_items_include_cases_per_pallet(self):
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(len(data['items']) >= 1)
        item_data = next(i for i in data['items'] if i['id'] == self.item.pk)
        self.assertEqual(item_data['cases_per_pallet'], 12)

    # 11. Inactive items excluded from items list
    def test_modal_data_excludes_inactive_items(self):
        inactive_item = _make_item(self.brand, name='Inactive Item', item_code='INACT', sort_order=99)
        DistributorItemProfile.objects.create(
            distributor=self.dist, item=inactive_item, is_active=False
        )
        resp = self.client.get(self.url)
        data = resp.json()
        item_ids = [i['id'] for i in data['items']]
        self.assertNotIn(inactive_item.pk, item_ids)

    # 12. No saved POs → saved_orders is empty list (empty state, no auto-tab)
    def test_modal_open_with_no_saved_pos_returns_empty_saved_orders(self):
        # No POs exist for this distributor/month; backend should return saved_orders=[]
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn('saved_orders', data)
        self.assertEqual(data['saved_orders'], [])


# ---------------------------------------------------------------------------
# 3. distributor_po_save POST view
# ---------------------------------------------------------------------------

class DistributorPOSaveTest(TestCase):

    def setUp(self):
        self.company = _make_company('Save Co')
        self.admin = _make_inventory_user(self.company, 'save_admin')
        self.dist = _make_distributor(self.company)
        self.brand = _make_brand(self.company)
        self.item = _make_item(self.brand)
        self.client = Client()
        self.client.login(username='save_admin', password='testpass123')
        self.url = reverse('distributor_po_save', kwargs={'dist_pk': self.dist.pk})

    def _post(self, payload):
        return _ajax_post(self.client, self.url, payload)

    def _payload(self, year=2026, month=6, orders=None):
        if orders is None:
            orders = [{'id': None, 'status': 'projected', 'external_po_number': '',
                       'notes': '', 'lines': [{'item_id': self.item.pk, 'quantity_cases': 24}]}]
        return {'year': year, 'month': month, 'orders': orders}

    # 12. Creates a new PO with lines
    def test_save_creates_new_po_with_lines(self):
        resp = self._post(self._payload())
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()['ok'])
        po = DistributorPO.objects.get(distributor=self.dist, year=2026, month=6)
        self.assertEqual(po.lines.count(), 1)
        self.assertEqual(float(po.lines.first().quantity_cases), 24.0)

    # 13. Updates an existing PO's lines
    def test_save_updates_existing_po(self):
        po = _make_po(self.dist, 2026, 6)
        _make_po_line(po, self.item, 12)
        payload = self._payload(orders=[{
            'id': po.pk, 'status': 'projected', 'external_po_number': '', 'notes': '',
            'lines': [{'item_id': self.item.pk, 'quantity_cases': 36}],
        }])
        resp = self._post(payload)
        self.assertTrue(resp.json()['ok'])
        po.refresh_from_db()
        self.assertEqual(float(po.lines.first().quantity_cases), 36.0)

    # 14. Deletes existing PO when all lines are zero
    def test_save_deletes_existing_po_on_all_zero_lines(self):
        po = _make_po(self.dist, 2026, 6)
        _make_po_line(po, self.item, 24)
        payload = self._payload(orders=[{
            'id': po.pk, 'status': 'projected', 'external_po_number': '', 'notes': '',
            'lines': [{'item_id': self.item.pk, 'quantity_cases': 0}],
        }])
        resp = self._post(payload)
        self.assertTrue(resp.json()['ok'])
        self.assertFalse(DistributorPO.objects.filter(pk=po.pk).exists())

    # Save-path deletion is restricted to projected POs (matches the delete
    # endpoint). Eligibility is based on the PERSISTED status, not the submitted
    # dropdown value. Rejection is whole-save (atomic).
    def test_save_path_delete_allowed_when_projected(self):
        po = _make_po(self.dist, 2026, 6, status='projected')
        _make_po_line(po, self.item, 24)
        payload = self._payload(orders=[{
            'id': po.pk, 'status': 'projected', 'external_po_number': '', 'notes': '',
            'lines': [{'item_id': self.item.pk, 'quantity_cases': 0}],
        }])
        resp = self._post(payload)
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()['ok'])
        self.assertFalse(DistributorPO.objects.filter(pk=po.pk).exists())

    def test_save_path_delete_rejected_when_not_projected(self):
        for bad_status in ('actual', 'submitted', 'in_transit', 'delivered',
                           'invoiced', 'cancelled'):
            po = _make_po(self.dist, 2026, 6, status=bad_status,
                          ext_po='PO-1' if bad_status != 'cancelled' else '')
            _make_po_line(po, self.item, 24)
            # Submit status='projected' (the unsaved dropdown) with emptied lines —
            # the guard must still reject because the PERSISTED status isn't projected.
            payload = self._payload(orders=[{
                'id': po.pk, 'status': 'projected', 'external_po_number': '', 'notes': '',
                'lines': [{'item_id': self.item.pk, 'quantity_cases': 0}],
            }])
            resp = self._post(payload)
            self.assertEqual(resp.status_code, 400, msg=f'status={bad_status}')
            self.assertIn('projected', resp.json()['error'].lower())
            self.assertTrue(
                DistributorPO.objects.filter(pk=po.pk).exists(),
                msg=f'PO with status={bad_status} should NOT be deleted via save path',
            )
            po.delete()

    def test_save_path_delete_rejection_is_whole_save_atomic(self):
        # A non-projected PO being emptied in the same batch as a valid new PO
        # rejects the ENTIRE save — the new PO is not created either.
        bad_po = _make_po(self.dist, 2026, 6, status='actual', ext_po='PO-9')
        _make_po_line(bad_po, self.item, 24)
        payload = self._payload(orders=[
            {'id': bad_po.pk, 'status': 'actual', 'external_po_number': 'PO-9',
             'notes': '', 'lines': [{'item_id': self.item.pk, 'quantity_cases': 0}]},
            {'id': None, 'status': 'projected', 'external_po_number': '', 'notes': '',
             'lines': [{'item_id': self.item.pk, 'quantity_cases': 12}]},
        ])
        resp = self._post(payload)
        self.assertEqual(resp.status_code, 400)
        # bad PO untouched, and the new PO was NOT created (atomic rejection).
        self.assertTrue(DistributorPO.objects.filter(pk=bad_po.pk).exists())
        self.assertEqual(
            DistributorPO.objects.filter(distributor=self.dist, year=2026, month=6).count(),
            1,
        )

    # 15. Skips new PO when all lines are zero (no error, no creation)
    def test_save_skips_new_po_with_all_zero_lines(self):
        payload = self._payload(orders=[{
            'id': None, 'status': 'projected', 'external_po_number': '', 'notes': '',
            'lines': [{'item_id': self.item.pk, 'quantity_cases': 0}],
        }])
        resp = self._post(payload)
        self.assertTrue(resp.json()['ok'])
        self.assertEqual(DistributorPO.objects.filter(distributor=self.dist).count(), 0)

    # 16. Returns 403 without permission
    def test_save_403_without_permission(self):
        limited = _make_limited_user(self.company, 'save_limited')
        c = Client()
        c.login(username='save_limited', password='testpass123')
        resp = _ajax_post(c, self.url, self._payload())
        self.assertEqual(resp.status_code, 403)

    # 17. Returns 400 for non-AJAX request (missing X-Requested-With header)
    def test_save_400_without_ajax_header(self):
        resp = self.client.post(
            self.url,
            data=json.dumps(self._payload()),
            content_type='application/json',
        )
        self.assertEqual(resp.status_code, 400)

    # 18. Returns 400 for invalid JSON body
    def test_save_400_invalid_json(self):
        resp = self.client.post(
            self.url,
            data='not-valid-json',
            content_type='application/json',
            HTTP_X_REQUESTED_WITH='XMLHttpRequest',
        )
        self.assertEqual(resp.status_code, 400)

    # 19. Returns 400 for actual status without PO number
    def test_save_400_actual_status_no_po_number(self):
        payload = self._payload(orders=[{
            'id': None, 'status': 'actual', 'external_po_number': '', 'notes': '',
            'lines': [{'item_id': self.item.pk, 'quantity_cases': 24}],
        }])
        resp = self._post(payload)
        self.assertEqual(resp.status_code, 400)
        self.assertIn('PO number', resp.json()['error'])

    # 20. Returns 400 for negative quantity
    def test_save_400_negative_quantity(self):
        payload = self._payload(orders=[{
            'id': None, 'status': 'projected', 'external_po_number': '', 'notes': '',
            'lines': [{'item_id': self.item.pk, 'quantity_cases': -5}],
        }])
        resp = self._post(payload)
        self.assertEqual(resp.status_code, 400)

    # 21. Returns 400 for item_id belonging to a different company
    def test_save_400_item_from_wrong_company(self):
        other_company = _make_company('Wrong Co')
        other_brand = _make_brand(other_company, 'Other Brand')
        other_item = _make_item(other_brand, name='Other Item', item_code='OTH')
        payload = self._payload(orders=[{
            'id': None, 'status': 'projected', 'external_po_number': '', 'notes': '',
            'lines': [{'item_id': other_item.pk, 'quantity_cases': 10}],
        }])
        resp = self._post(payload)
        self.assertEqual(resp.status_code, 400)

    # 22. New PO has generated_by_algorithm=False
    def test_save_new_po_has_generated_by_algorithm_false(self):
        resp = self._post(self._payload())
        self.assertTrue(resp.json()['ok'])
        po = DistributorPO.objects.get(distributor=self.dist, year=2026, month=6)
        self.assertFalse(po.generated_by_algorithm)

    # 23. Updating an algorithm-generated PO flips generated_by_algorithm to False
    def test_save_update_flips_generated_by_algorithm_to_false(self):
        po = _make_po(self.dist, 2026, 6, generated_by_algorithm=True)
        _make_po_line(po, self.item, 24)
        self.assertTrue(po.generated_by_algorithm)

        payload = self._payload(orders=[{
            'id': po.pk, 'status': 'projected', 'external_po_number': '', 'notes': '',
            'lines': [{'item_id': self.item.pk, 'quantity_cases': 48}],
        }])
        self._post(payload)
        po.refresh_from_db()
        self.assertFalse(po.generated_by_algorithm)

    # 24. Atomicity: failure mid-save rolls back all changes
    def test_save_atomicity_on_failure(self):
        call_count = [0]
        real_create = DistributorPO.objects.create.__func__

        def failing_create(manager, **kwargs):
            call_count[0] += 1
            if call_count[0] > 1:
                raise Exception('Simulated DB failure')
            return real_create(manager, **kwargs)

        orders = [
            {'id': None, 'status': 'projected', 'external_po_number': '', 'notes': '',
             'lines': [{'item_id': self.item.pk, 'quantity_cases': 10}]},
            {'id': None, 'status': 'projected', 'external_po_number': '', 'notes': '',
             'lines': [{'item_id': self.item.pk, 'quantity_cases': 20}]},
        ]
        payload = {'year': 2026, 'month': 6, 'orders': orders}

        with patch.object(type(DistributorPO.objects), 'create',
                          side_effect=lambda **kw: failing_create(DistributorPO.objects, **kw)):
            resp = self._post(payload)

        self.assertEqual(resp.status_code, 500)
        self.assertEqual(DistributorPO.objects.filter(distributor=self.dist).count(), 0)

    # --- #8: bidirectional case/pallet entry — endpoint CONTRACT -------------
    # The live two-way sync itself is JS (no JS engine in the test client); these
    # assert the stored contract: only quantity_cases is saved, always WHOLE.

    def test_save_payload_cases_only(self):
        """Save persists quantity_cases; a stray 'pallets' key is ignored (never
        stored — pallets is a UI convenience only)."""
        payload = self._payload(orders=[{
            'id': None, 'status': 'projected', 'external_po_number': '', 'notes': '',
            'lines': [{'item_id': self.item.pk, 'quantity_cases': 24, 'pallets': 2}],
        }])
        resp = self._post(payload)
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()['ok'])
        po = DistributorPO.objects.get(distributor=self.dist, year=2026, month=6)
        line = po.lines.first()
        self.assertEqual(float(line.quantity_cases), 24.0)
        # No pallets attribute is stored on the line model.
        self.assertFalse(hasattr(line, 'pallets'))

    def test_cases_stored_whole(self):
        """Cases are always whole — a fractional quantity_cases is rounded to the
        nearest whole case on save (defensive backend guard; UI guarantees it too)."""
        payload = self._payload(orders=[{
            'id': None, 'status': 'projected', 'external_po_number': '', 'notes': '',
            'lines': [{'item_id': self.item.pk, 'quantity_cases': 24.6}],
        }])
        resp = self._post(payload)
        self.assertEqual(resp.status_code, 200)
        po = DistributorPO.objects.get(distributor=self.dist, year=2026, month=6)
        stored = po.lines.first().quantity_cases
        self.assertEqual(stored, stored.to_integral_value())
        self.assertEqual(float(stored), 25.0)


# ---------------------------------------------------------------------------
# 4. distributor_po_delete POST view
# ---------------------------------------------------------------------------

class DistributorPODeleteTest(TestCase):

    def setUp(self):
        self.company = _make_company('Delete Co')
        self.admin = _make_inventory_user(self.company, 'delete_admin')
        self.dist = _make_distributor(self.company)
        self.brand = _make_brand(self.company)
        self.item = _make_item(self.brand)
        self.client = Client()
        self.client.login(username='delete_admin', password='testpass123')

    # 25. Deletes the PO successfully
    def test_delete_po_succeeds(self):
        po = _make_po(self.dist, 2026, 6)
        url = reverse('distributor_po_delete',
                      kwargs={'dist_pk': self.dist.pk, 'po_pk': po.pk})
        resp = _ajax_post(self.client, url, {'po_id': po.pk})
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()['ok'])
        self.assertFalse(DistributorPO.objects.filter(pk=po.pk).exists())

    # 26. Returns 403 without inventory permission
    def test_delete_403_without_permission(self):
        po = _make_po(self.dist, 2026, 6)
        limited = _make_limited_user(self.company, 'del_limited')
        c = Client()
        c.login(username='del_limited', password='testpass123')
        url = reverse('distributor_po_delete',
                      kwargs={'dist_pk': self.dist.pk, 'po_pk': po.pk})
        resp = _ajax_post(c, url, {'po_id': po.pk})
        self.assertEqual(resp.status_code, 403)
        self.assertTrue(DistributorPO.objects.filter(pk=po.pk).exists())

    # 27. Returns 404 for PO belonging to a different distributor
    def test_delete_404_po_from_wrong_distributor(self):
        other_dist = _make_distributor(self.company, 'Other Dist')
        po = _make_po(other_dist, 2026, 6)
        url = reverse('distributor_po_delete',
                      kwargs={'dist_pk': self.dist.pk, 'po_pk': po.pk})
        resp = _ajax_post(self.client, url, {'po_id': po.pk})
        self.assertEqual(resp.status_code, 404)
        self.assertTrue(DistributorPO.objects.filter(pk=po.pk).exists())

    # 28. Delete allowed when status is projected (backend)
    def test_po_delete_allowed_when_projected(self):
        po = _make_po(self.dist, 2026, 6, status='projected')
        url = reverse('distributor_po_delete',
                      kwargs={'dist_pk': self.dist.pk, 'po_pk': po.pk})
        resp = _ajax_post(self.client, url, {'po_id': po.pk})
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()['ok'])
        self.assertFalse(DistributorPO.objects.filter(pk=po.pk).exists())

    # 29. Delete rejected when status is NOT projected (backend enforcement).
    #     Eligibility is based on the saved DB status, not a modal dropdown.
    def test_po_delete_rejected_when_not_projected(self):
        for bad_status in ('actual', 'submitted', 'in_transit', 'delivered',
                           'invoiced', 'cancelled'):
            po = _make_po(self.dist, 2026, 6, status=bad_status,
                          ext_po='PO-1' if bad_status != 'cancelled' else '')
            url = reverse('distributor_po_delete',
                          kwargs={'dist_pk': self.dist.pk, 'po_pk': po.pk})
            resp = _ajax_post(self.client, url, {'po_id': po.pk})
            self.assertEqual(resp.status_code, 400, msg=f'status={bad_status}')
            self.assertIn('projected', resp.json()['error'].lower())
            self.assertTrue(
                DistributorPO.objects.filter(pk=po.pk).exists(),
                msg=f'PO with status={bad_status} should NOT be deleted',
            )
            po.delete()


# ---------------------------------------------------------------------------
# 5. Integration tests
# ---------------------------------------------------------------------------

class DistributorPOIntegrationTest(TestCase):

    def setUp(self):
        self.company = _make_company('Integ Co')
        self.admin = _make_inventory_user(self.company, 'integ_admin')
        self.dist = Distributor.objects.create(
            company=self.company, name='Integ Dist',
            order_quantity_value=2, order_quantity_unit='pallets',
        )
        self.brand = _make_brand(self.company)
        self.item = _make_item(self.brand)
        self.item.cases_per_pallet = 12
        self.item.save()
        self.account = _make_account(self.company, self.dist)
        self.batch = _make_batch(self.company, self.dist)
        _make_snapshot(self.dist, self.item, 2026, 4, quantity=100)

    # 28. Saved PO increases projected inventory in forecast
    def test_saved_po_increases_inventory_in_forecast(self):
        _make_sale(self.company, self.batch, self.account, self.item, 2025, 5, 50)

        today = date(2026, 4, 20)
        # Without PO: 100 - 50 = 50 in May 2026
        result_no_po = compute_distributor_forecast(self.dist, today=today)
        row_no_po = result_no_po['rows'][0]
        may_cell = next(c for c in row_no_po['monthly_data'] if c['year'] == 2026 and c['month'] == 5)
        self.assertEqual(may_cell['inventory'], 50.0)

        # With PO of 24 cases in May 2026: 100 + 24 - 50 = 74
        po = _make_po(self.dist, 2026, 5)
        _make_po_line(po, self.item, 24)

        po_additions = {(self.item.pk, 2026, 5): 24.0}
        result_with_po = compute_distributor_forecast(self.dist, today=today, po_additions=po_additions)
        row_with = result_with_po['rows'][0]
        may_cell_with = next(c for c in row_with['monthly_data'] if c['year'] == 2026 and c['month'] == 5)
        self.assertEqual(may_cell_with['inventory'], 74.0)

    # 29. distributor_list view shows saved_count in orders_per_horizon
    def test_distributor_list_shows_saved_count_in_orders(self):
        _make_sale(self.company, self.batch, self.account, self.item, 2025, 5, 200)
        _make_po(self.dist, 2026, 5)

        client = Client()
        client.login(username='integ_admin', password='testpass123')
        url = reverse('distributor_list')
        resp = client.get(url + f'?tab=forecast&forecast_distributor={self.dist.pk}')
        self.assertEqual(resp.status_code, 200)

        orders_result = resp.context['orders_result']
        slots_by_ym = {
            (s['year'], s['month']): s
            for s in orders_result['orders_per_horizon']
            if not s['is_snapshot']
        }
        may_slot = slots_by_ym.get((2026, 5))
        self.assertIsNotNone(may_slot)
        self.assertEqual(may_slot['saved_count'], 1)
        self.assertGreaterEqual(may_slot['total_count'], 1)


# ---------------------------------------------------------------------------
# 6. distributor_po_suggest endpoint
# ---------------------------------------------------------------------------

class SuggestEndpointTest(TestCase):

    def setUp(self):
        self.company = _make_company('Suggest EP Co')
        self.admin = _make_inventory_user(self.company, 'sug_admin')
        self.dist = Distributor.objects.create(
            company=self.company, name='Sug Dist',
            order_quantity_value=10, order_quantity_unit='cases',
        )
        self.brand = _make_brand(self.company)
        self.item = _make_item(self.brand, item_code='SUGA')
        self.account = _make_account(self.company, self.dist)
        self.batch = _make_batch(self.company, self.dist)
        # Snapshot anchor = April 2026, item below safety stock in May 2026
        _make_snapshot(self.dist, self.item, 2026, 4, quantity=10)
        # Prior year May 2025 depletion = 100 → May 2026 projected inv = 10 - 100 = -90
        _make_sale(self.company, self.batch, self.account, self.item, 2025, 5, 100)
        DistributorItemProfile.objects.create(
            distributor=self.dist, item=self.item, safety_stock_cases=0
        )
        self.client = Client()
        self.client.login(username='sug_admin', password='testpass123')
        # Modal month = April 2026 → lookahead = May 2026
        self.url = reverse('distributor_po_suggest',
                           kwargs={'dist_pk': self.dist.pk, 'year': 2026, 'month': 4})

    # 13. Suggest endpoint returns correct lines when shortage exists
    @patch('apps.distribution.forecast.date', _FrozenApril2026Date)
    def test_suggest_endpoint_returns_correct_lines(self):
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn('lines', data)
        self.assertGreater(len(data['lines']), 0)
        line = data['lines'][0]
        self.assertEqual(line['item_id'], self.item.pk)
        self.assertIn('cases', line)
        self.assertIsNone(line['pallets'])

    # 14. Returns empty lines when no shortage
    def test_suggest_endpoint_returns_empty_when_no_shortage(self):
        # Update the snapshot to a large quantity so May projected inv stays above 0
        InventorySnapshot.objects.filter(
            distributor=self.dist, item=self.item, year=2026, month=4
        ).update(quantity_cases=9999)
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data['lines'], [])

    # 15. Returns 403 without permission
    def test_suggest_endpoint_requires_permission(self):
        limited = _make_limited_user(self.company, 'sug_limited')
        c = Client()
        c.login(username='sug_limited', password='testpass123')
        resp = c.get(self.url)
        self.assertEqual(resp.status_code, 403)

    # 16. Returns 404 for distributor belonging to wrong company
    def test_suggest_endpoint_404_for_wrong_company(self):
        other_co = _make_company('Other Sug Co')
        other_dist = _make_distributor(other_co, 'Other Sug Dist')
        url = reverse('distributor_po_suggest',
                      kwargs={'dist_pk': other_dist.pk, 'year': 2026, 'month': 4})
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 404)


# ---------------------------------------------------------------------------
# 7. SO# assignment tests
# ---------------------------------------------------------------------------

from apps.distribution.models import assign_so_number


class SONumberAssignmentTest(TestCase):

    def setUp(self):
        self.company = _make_company('SO Co')
        self.company.so_sequence_start = 2006
        self.company.save()
        self.dist = _make_distributor(self.company)
        self.brand = _make_brand(self.company)
        self.item = _make_item(self.brand)

    def _make_submitted_po(self, **kwargs):
        return DistributorPO.objects.create(
            distributor=self.dist, year=2026, month=6,
            status=DistributorPO.Status.SUBMITTED,
            **kwargs
        )

    # 1. First SO uses company.so_sequence_start
    def test_assign_so_number_uses_company_start_for_first_po(self):
        po = DistributorPO(
            distributor=self.dist, year=2026, month=6,
            status=DistributorPO.Status.SUBMITTED,
        )
        result = assign_so_number(po)
        self.assertEqual(result, 2006)
        self.assertEqual(po.so_number, 2006)

    # 2. Subsequent SO uses MAX + 1
    def test_assign_so_number_uses_max_plus_one(self):
        for so in (2006, 2007, 2008):
            DistributorPO.objects.create(
                distributor=self.dist, year=2025, month=so - 2005,
                status=DistributorPO.Status.SUBMITTED,
                so_number=so,
            )
        po = DistributorPO(
            distributor=self.dist, year=2026, month=7,
            status=DistributorPO.Status.SUBMITTED,
        )
        result = assign_so_number(po)
        self.assertEqual(result, 2009)

    # 3. Idempotent — skips if already set
    def test_assign_so_number_skips_if_already_set(self):
        po = DistributorPO(
            distributor=self.dist, year=2026, month=6,
            status=DistributorPO.Status.SUBMITTED,
            so_number=5000,
        )
        result = assign_so_number(po)
        self.assertEqual(result, 5000)
        self.assertEqual(po.so_number, 5000)

    # 4. clean() raises when status=SUBMITTED and so_number=None
    def test_so_number_required_when_submitted(self):
        from django.core.exceptions import ValidationError
        po = DistributorPO(
            distributor=self.dist, year=2026, month=6,
            status=DistributorPO.Status.SUBMITTED,
            so_number=None,
        )
        with self.assertRaises(ValidationError) as ctx:
            po.full_clean()
        self.assertIn('so_number', ctx.exception.message_dict)

    # 5. clean() passes with actual status and PO number (existing rule unchanged)
    def test_external_po_required_when_actual(self):
        from django.core.exceptions import ValidationError
        po = DistributorPO(
            distributor=self.dist, year=2026, month=6,
            status=DistributorPO.Status.ACTUAL,
            external_po_number='',
        )
        with self.assertRaises(ValidationError) as ctx:
            po.full_clean()
        self.assertIn('external_po_number', ctx.exception.message_dict)

    # 6. so_number persists when status changes back to Actual
    def test_so_number_persists_on_status_change_back_to_actual(self):
        po = DistributorPO.objects.create(
            distributor=self.dist, year=2026, month=6,
            status=DistributorPO.Status.SUBMITTED,
            so_number=2010,
        )
        po.status = DistributorPO.Status.ACTUAL
        po.external_po_number = 'PO-999'
        po.save(update_fields=['status', 'external_po_number'])
        po.refresh_from_db()
        self.assertEqual(po.so_number, 2010)

    # 7. so_number persists on cancel
    def test_so_number_persists_on_cancel(self):
        po = DistributorPO.objects.create(
            distributor=self.dist, year=2026, month=6,
            status=DistributorPO.Status.SUBMITTED,
            so_number=2015,
        )
        po.status = DistributorPO.Status.CANCELLED
        po.save(update_fields=['status'])
        po.refresh_from_db()
        self.assertEqual(po.so_number, 2015)

    # 8. SO# is scoped per company (Company B uses its own so_sequence_start)
    def test_assign_so_number_scoped_per_company(self):
        company_b = _make_company('SO Co B')
        company_b.so_sequence_start = 3001
        company_b.save()
        dist_b = _make_distributor(company_b, 'Dist B')

        # Company A has POs with so_number=5000
        DistributorPO.objects.create(
            distributor=self.dist, year=2026, month=1,
            status=DistributorPO.Status.SUBMITTED,
            so_number=5000,
        )

        # Company B's first PO should use Company B's so_sequence_start
        po_b = DistributorPO(
            distributor=dist_b, year=2026, month=1,
            status=DistributorPO.Status.SUBMITTED,
        )
        result = assign_so_number(po_b)
        self.assertEqual(result, 3001)


# ---------------------------------------------------------------------------
# 9. Distributor POs tab view tests
# ---------------------------------------------------------------------------

class DistributorPOsTabTest(TestCase):

    def setUp(self):
        self.company = _make_company('Tab Co')
        self.admin = _make_inventory_user(self.company, 'tab_admin')
        self.dist = _make_distributor(self.company)
        self.brand = _make_brand(self.company)
        self.item = _make_item(self.brand, item_code='TABIT')
        self.client = Client()
        self.client.login(username='tab_admin', password='testpass123')
        self.url = reverse('distributor_list')

    def _get_tab(self, tab='distributor_pos', **params):
        query = '&'.join(f'{k}={v}' for k, v in params.items())
        url = f'{self.url}?tab={tab}'
        if query:
            url += '&' + query
        return self.client.get(url)

    # 13. Distributor POs tab renders
    def test_distributor_pos_tab_renders(self):
        _make_po(self.dist, 2026, 5, status='projected')
        resp = self._get_tab()
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.context['active_tab'], 'distributor_pos')
        self.assertIsNotNone(resp.context['pos_page_obj'])

    # 14. Distributor POs tab includes Invoiced POs (all statuses shown)
    def test_distributor_pos_tab_includes_invoiced(self):
        _make_po(self.dist, 2026, 5, status='projected')
        _make_po(self.dist, 2026, 6, status='invoiced')
        resp = self._get_tab()
        self.assertEqual(resp.status_code, 200)
        count = resp.context['pos_page_obj'].paginator.count
        self.assertEqual(count, 2)

    # 15. Visiting invoiced_pos tab falls back to distributors tab (tab removed)
    def test_invoiced_pos_tab_redirects_to_default(self):
        resp = self._get_tab(tab='invoiced_pos')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.context['active_tab'], 'distributors')

    # 16. Filter by status works
    def test_distributor_pos_tab_filter_by_status(self):
        _make_po(self.dist, 2026, 5, status='projected')
        _make_po(self.dist, 2026, 6, status='actual', ext_po='PO123')
        resp = self._get_tab(status='projected')
        self.assertEqual(resp.status_code, 200)
        count = resp.context['pos_page_obj'].paginator.count
        self.assertEqual(count, 1)

    # 17. Filter by distributor works
    def test_distributor_pos_tab_filter_by_distributor(self):
        other_dist = _make_distributor(self.company, 'Other Dist')
        _make_po(self.dist, 2026, 5, status='projected')
        _make_po(other_dist, 2026, 5, status='projected')
        resp = self._get_tab(distributor=self.dist.pk)
        self.assertEqual(resp.status_code, 200)
        count = resp.context['pos_page_obj'].paginator.count
        self.assertEqual(count, 1)

    # 18. Column-sort params are ignored — ordering is fixed (year, month, sort_position).
    def test_sort_param_ignored_ordering_fixed(self):
        # Two POs same month; sort_position drives within-month order regardless of ?sort.
        po1 = _make_po(self.dist, 2026, 5, status='submitted')
        po1.sort_position = 2
        po1.save(update_fields=['sort_position'])
        po2 = _make_po(self.dist, 2026, 5, status='projected')
        po2.sort_position = 1
        po2.save(update_fields=['sort_position'])
        # A legacy ?sort=so_number param must NOT change the order.
        resp = self._get_tab(sort='so_number')
        self.assertEqual(resp.status_code, 200)
        order = [r['po'].pk for r in resp.context['pos_rows']]
        self.assertEqual(order, [po2.pk, po1.pk])  # by sort_position 1, 2

    # 19. Paginator at 50 per page
    def test_distributor_pos_tab_paginates_at_50(self):
        for i in range(55):
            _make_po(self.dist, 2025, (i % 12) + 1, status='projected')
        resp = self._get_tab()
        self.assertEqual(resp.status_code, 200)
        page_obj = resp.context['pos_page_obj']
        self.assertEqual(len(page_obj.object_list), 50)
        self.assertEqual(page_obj.paginator.num_pages, 2)

    # 20. PO Month label rendered as 'YY-Mon (e.g., '26-Nov)
    def test_po_month_label_format(self):
        _make_po(self.dist, 2026, 11, status='projected')
        resp = self._get_tab()
        self.assertEqual(resp.status_code, 200)
        labels = [r['po_month_label'] for r in resp.context['pos_rows']]
        self.assertIn("'26-Nov", labels)
        # Apostrophe is HTML-escaped to &#x27; in rendered output
        self.assertContains(resp, "26-Nov")

    # 21. Filter modal has Apply and Clear buttons
    def test_filter_modal_has_apply_and_clear_buttons(self):
        _make_po(self.dist, 2026, 5, status='projected')
        resp = self._get_tab()
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Apply Filters')
        self.assertContains(resp, 'Clear All')

    # 22. Ordering is year, month, then manual sort_position (status no longer affects order)
    def test_order_follows_sort_position_within_month(self):
        # Same month, mixed statuses; manual sort_position decides the order.
        po_a = _make_po(self.dist, 2026, 5, status='cancelled')
        po_a.sort_position = 1
        po_a.save(update_fields=['sort_position'])
        po_b = _make_po(self.dist, 2026, 5, status='projected')
        po_b.sort_position = 2
        po_b.save(update_fields=['sort_position'])
        po_c = _make_po(self.dist, 2026, 5, status='submitted')
        po_c.sort_position = 3
        po_c.save(update_fields=['sort_position'])
        # A later month always sorts after May regardless of its sort_position.
        po_next = _make_po(self.dist, 2026, 6, status='projected')
        po_next.sort_position = 1
        po_next.save(update_fields=['sort_position'])
        resp = self._get_tab()
        self.assertEqual(resp.status_code, 200)
        order = [r['po'].pk for r in resp.context['pos_rows']]
        self.assertEqual(order, [po_a.pk, po_b.pk, po_c.pk, po_next.pk])

    # 23. sort_position drives ordering; the row carries data-position from sort_position.
    def test_sort_position_drives_row_order_and_data_attr(self):
        po1 = _make_po(self.dist, 2026, 5, status='projected')
        po1.sort_position = 1
        po1.save(update_fields=['sort_position'])
        po2 = _make_po(self.dist, 2026, 5, status='projected')
        po2.sort_position = 2
        po2.save(update_fields=['sort_position'])
        resp = self._get_tab()
        self.assertEqual(resp.status_code, 200)
        order = [r['po'].pk for r in resp.context['pos_rows']]
        self.assertEqual(order, [po1.pk, po2.pk])
        # data-position attribute is sourced from sort_position (no Order column).
        content = resp.content.decode()
        self.assertIn(f'data-po-pk="{po1.pk}"', content)
        self.assertIn('data-position="1"', content)
        self.assertIn('data-position="2"', content)

    # 24. Filters button renders only on the Distributor POs tab
    def test_filter_button_only_on_distributor_pos_tab(self):
        _make_po(self.dist, 2026, 5, status='projected')
        resp = self._get_tab(tab='distributor_pos')
        self.assertContains(resp, 'data-bs-target="#posFilterModal"')
        resp_forecast = self._get_tab(tab='forecast')
        self.assertNotContains(resp_forecast, 'data-bs-target="#posFilterModal"')

    # 25. Selected-PO count renders (now in the brand-name header row)
    def test_selected_count_in_header_row(self):
        for m in (5, 6):
            po = _make_po(self.dist, 2026, m, status='projected')
            po.selected_for_projection = True
            po.save(update_fields=['selected_for_projection'])
        resp = self._get_tab()
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.context['selected_po_count'], 2)
        self.assertContains(resp, 'POs selected')
        self.assertContains(resp, 'id="selected-po-count">2<')

    # 26. PO row click path: v1 endpoint with ?po_pk returns only that PO
    def test_po_modal_single_po_mode(self):
        po1 = _make_po(self.dist, 2026, 5, status='projected')
        _make_po_line(po1, self.item, 10)
        po2 = _make_po(self.dist, 2026, 5, status='actual', ext_po='PO-2')
        _make_po_line(po2, self.item, 20)
        base = reverse('distributor_po_modal_data', args=[self.dist.pk, 2026, 5])

        single = self.client.get(
            f'{base}?po_pk={po1.pk}', HTTP_X_REQUESTED_WITH='XMLHttpRequest'
        )
        self.assertEqual(single.status_code, 200)
        saved = single.json()['saved_orders']
        self.assertEqual(len(saved), 1)
        self.assertEqual(saved[0]['id'], po1.pk)

        # Without ?po_pk, both POs for the month are returned (multi-PO mode)
        both = self.client.get(base, HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertEqual(len(both.json()['saved_orders']), 2)

    # 27. Inventory modal groups items under brand headers (no repeated brand name)
    def test_inventory_modal_groups_by_brand(self):
        brand2 = _make_brand(self.company, 'Second Brand')
        _make_item(brand2, name='Item Two', item_code='ITM2')
        resp = self._get_tab()
        self.assertEqual(resp.status_code, 200)
        content = resp.content.decode()
        # Brand-group header markup present, with each brand name as a header
        self.assertIn(f'text-muted mt-3 mb-1">{self.brand.name}</div>', content)
        self.assertIn('text-muted mt-3 mb-1">Second Brand</div>', content)
        # Item names appear; the old "Brand — Item" combined label is gone
        self.assertIn('Item Two', content)
        self.assertNotIn(f'{self.brand.name} &mdash; ', content)
        self.assertNotIn(f'{self.brand.name} — ', content)


# ---------------------------------------------------------------------------
# 10. Distributor.code tests + new tab refinements
# ---------------------------------------------------------------------------

class DistributorCodeTest(TestCase):

    def setUp(self):
        self.company = _make_company('Code Co')

    # 1. Code auto-generated from name (strips state after comma, strips Co suffix)
    def test_distributor_code_auto_generated_from_name(self):
        from apps.distribution.models import Distributor
        dist = Distributor.objects.create(
            company=self.company, name='Shore Point Dist Co, NJ'
        )
        # Strip ", NJ" → "Shore Point Dist Co" → strip "Co" suffix → S, P, D = "SPD"
        self.assertEqual(dist.code, 'SPD')

    # 2. Code can be explicitly set and is preserved
    def test_distributor_code_can_be_overridden(self):
        from apps.distribution.models import Distributor
        dist = Distributor.objects.create(
            company=self.company, name='Some Distributor', code='MYCODE'
        )
        dist.refresh_from_db()
        self.assertEqual(dist.code, 'MYCODE')

    # 3. Code max 10 chars
    def test_distributor_code_max_10_chars(self):
        from apps.distribution.models import Distributor
        dist = Distributor(company=self.company, name='A B C D E F G H I J K L M N')
        code = Distributor._generate_code_from_name(dist.name)
        self.assertLessEqual(len(code), 10)

    # 4. Code skips common short words
    def test_distributor_code_skips_stop_words(self):
        from apps.distribution.models import Distributor
        code = Distributor._generate_code_from_name('Colonial Beverage of New Jersey')
        # "of" skipped; no comma/hyphen → C, B, N, J = "CBNJ"
        self.assertNotIn('O', code.split('C')[0] if 'C' in code else '')
        self.assertEqual(code, 'CBNJ')

    # New algorithm tests
    def test_distributor_code_strips_state_after_comma(self):
        from apps.distribution.models import Distributor
        code = Distributor._generate_code_from_name('Peerless Beverage, NJ')
        self.assertEqual(code, 'PB')

    def test_distributor_code_strips_city_after_hyphen(self):
        from apps.distribution.models import Distributor
        code = Distributor._generate_code_from_name('Burke Distributing Corp.- Randolph, MA')
        self.assertEqual(code, 'BD')

    def test_distributor_code_excludes_legal_suffixes(self):
        from apps.distribution.models import Distributor
        self.assertEqual(Distributor._generate_code_from_name('Atlas Distributing Inc., MA'), 'AD')
        self.assertEqual(Distributor._generate_code_from_name('Acme LLC, NY'), 'A')
        self.assertEqual(Distributor._generate_code_from_name('Test Corp, NJ'), 'T')

    def test_distributor_display_code_includes_state(self):
        from apps.distribution.models import Distributor
        dist = Distributor(company=self.company, name='Shore Point Dist', state='NJ', code='SPD')
        self.assertEqual(dist.display_code, 'NJ-SPD')

    def test_distributor_display_code_no_state(self):
        from apps.distribution.models import Distributor
        dist = Distributor(company=self.company, name='Shore Point Dist', state='', code='SPD')
        self.assertEqual(dist.display_code, 'SPD')

    # 5. All 7 statuses in modal endpoint response
    def test_all_seven_statuses_in_po_status_choices(self):
        company = _make_company('Status Co')
        admin = _make_inventory_user(company, 'sts_admin')
        dist = _make_distributor(company)
        brand = _make_brand(company)
        item = _make_item(brand)
        po = _make_po(dist, 2026, 5, status='projected')

        client = Client()
        client.login(username='sts_admin', password='testpass123')
        url = reverse('distributor_list') + '?tab=distributor_pos'
        resp = client.get(url)
        self.assertEqual(resp.status_code, 200)
        choices = resp.context.get('po_status_choices', [])
        values = [c[0] for c in choices]
        for expected in ('projected', 'actual', 'submitted', 'in_transit', 'delivered', 'invoiced', 'cancelled'):
            self.assertIn(expected, values, f"Status '{expected}' missing from po_status_choices")

    # 6. Default sort is ascending (oldest first)
    def test_distributor_pos_default_sort_ascending(self):
        company = _make_company('Sort Co')
        admin = _make_inventory_user(company, 'sort_admin')
        dist = _make_distributor(company)
        _make_po(dist, 2026, 6, status='projected')
        _make_po(dist, 2026, 3, status='projected')
        _make_po(dist, 2026, 9, status='projected')

        client = Client()
        client.login(username='sort_admin', password='testpass123')
        resp = client.get(reverse('distributor_list') + '?tab=distributor_pos')
        self.assertEqual(resp.status_code, 200)
        rows = resp.context['pos_rows']
        months = [(r['po'].year, r['po'].month) for r in rows]
        self.assertEqual(months, sorted(months))

    # 7. Filter distributors restricted to those with POs
    def test_filter_distributors_only_shows_those_with_pos(self):
        company = _make_company('FiltDist Co')
        admin = _make_inventory_user(company, 'fd_admin')
        dist_with_po = _make_distributor(company, 'Has PO')
        dist_no_po = _make_distributor(company, 'No PO')
        _make_po(dist_with_po, 2026, 5, status='projected')

        client = Client()
        client.login(username='fd_admin', password='testpass123')
        resp = client.get(reverse('distributor_list') + '?tab=distributor_pos')
        self.assertEqual(resp.status_code, 200)
        filter_dists = resp.context['all_distributors_for_filter']
        filter_pks = [d.pk for d in filter_dists]
        self.assertIn(dist_with_po.pk, filter_pks)
        self.assertNotIn(dist_no_po.pk, filter_pks)

    # 8. Filter modal has no date_from/date_to fields in context filters
    def test_filter_modal_no_date_range_fields(self):
        company = _make_company('NoDate Co')
        admin = _make_inventory_user(company, 'nd_admin')
        dist = _make_distributor(company)
        _make_po(dist, 2026, 5, status='projected')

        client = Client()
        client.login(username='nd_admin', password='testpass123')
        resp = client.get(reverse('distributor_list') + '?tab=distributor_pos')
        self.assertEqual(resp.status_code, 200)
        pos_active_filters = resp.context['pos_active_filters']
        self.assertNotIn('date_from', pos_active_filters)
        self.assertNotIn('date_to', pos_active_filters)

    def test_invoiced_pos_appear_in_distributor_pos_tab(self):
        company = _make_company('InvApp Co')
        admin = _make_inventory_user(company, 'invapp_admin')
        dist = _make_distributor(company)
        _make_po(dist, 2026, 6, status='invoiced')
        client = Client()
        client.login(username='invapp_admin', password='testpass123')
        resp = client.get(reverse('distributor_list') + '?tab=distributor_pos')
        self.assertEqual(resp.status_code, 200)
        count = resp.context['pos_page_obj'].paginator.count
        self.assertGreaterEqual(count, 1)
        po_statuses = [r['po'].status for r in resp.context['pos_rows']]
        self.assertIn('invoiced', po_statuses)

    def test_cancelled_pos_appear_in_distributor_pos_tab(self):
        company = _make_company('CanApp Co')
        admin = _make_inventory_user(company, 'canapp_admin')
        dist = _make_distributor(company)
        _make_po(dist, 2026, 6, status='cancelled')
        client = Client()
        client.login(username='canapp_admin', password='testpass123')
        resp = client.get(reverse('distributor_list') + '?tab=distributor_pos')
        self.assertEqual(resp.status_code, 200)
        count = resp.context['pos_page_obj'].paginator.count
        self.assertGreaterEqual(count, 1)
        po_statuses = [r['po'].status for r in resp.context['pos_rows']]
        self.assertIn('cancelled', po_statuses)

    def test_invoiced_pos_tab_removed(self):
        company = _make_company('TabRem Co')
        admin = _make_inventory_user(company, 'tabrem_admin')
        client = Client()
        client.login(username='tabrem_admin', password='testpass123')
        resp = client.get(reverse('distributor_list') + '?tab=invoiced_pos')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.context['active_tab'], 'distributors')
        self.assertIsNone(resp.context['pos_page_obj'])


# ---------------------------------------------------------------------------
# 11. Inventory projection tool (Distributor POs tab)
# ---------------------------------------------------------------------------

class InventoryProjectionTest(TestCase):

    def setUp(self):
        self.company = _make_company('Projection Co')
        self.admin = _make_inventory_user(self.company, 'proj_admin')
        self.dist = _make_distributor(self.company)
        self.brand = _make_brand(self.company)
        self.item = _make_item(self.brand, item_code='PROJIT')
        self.client = Client()
        self.client.login(username='proj_admin', password='testpass123')

    # 1. Save inventory updates item field
    def test_save_forecast_inventory_updates_items(self):
        url = reverse('save_forecast_inventory')
        resp = _ajax_post(self.client, url, {'inventory': {str(self.item.pk): 100}})
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()['ok'])
        self.item.refresh_from_db()
        self.assertEqual(self.item.forecast_current_inventory, Decimal('100'))

    # 2. Only company-owned items are updated
    def test_save_forecast_inventory_only_company_items(self):
        other_co = _make_company('Other Proj Co')
        other_brand = _make_brand(other_co, 'Other Brand')
        other_item = _make_item(other_brand, name='Other Item', item_code='OTHPR')
        url = reverse('save_forecast_inventory')
        resp = _ajax_post(self.client, url, {'inventory': {str(other_item.pk): 999}})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()['updated'], 0)
        other_item.refresh_from_db()
        self.assertEqual(other_item.forecast_current_inventory, Decimal('0'))

    # 3. Requires permission
    def test_save_forecast_inventory_requires_permission(self):
        limited = _make_limited_user(self.company, 'proj_limited')
        c = Client()
        c.login(username='proj_limited', password='testpass123')
        url = reverse('save_forecast_inventory')
        resp = _ajax_post(c, url, {'inventory': {str(self.item.pk): 50}})
        self.assertEqual(resp.status_code, 403)

    # 4. Toggle sets the flag
    def test_toggle_po_selection_sets_flag(self):
        po = _make_po(self.dist, 2026, 5, status='projected')
        url = reverse('toggle_po_selection')
        resp = _ajax_post(self.client, url, {'po_pk': po.pk, 'selected': True})
        self.assertEqual(resp.status_code, 200)
        po.refresh_from_db()
        self.assertTrue(po.selected_for_projection)

    # 5. Toggle unsets the flag
    def test_toggle_po_selection_unsets_flag(self):
        po = _make_po(self.dist, 2026, 5, status='projected')
        po.selected_for_projection = True
        po.save(update_fields=['selected_for_projection'])
        url = reverse('toggle_po_selection')
        resp = _ajax_post(self.client, url, {'po_pk': po.pk, 'selected': False})
        self.assertEqual(resp.status_code, 200)
        po.refresh_from_db()
        self.assertFalse(po.selected_for_projection)

    # 6. Toggle on another company's PO → 404
    def test_toggle_po_selection_other_company_404(self):
        other_co = _make_company('Other Tog Co')
        other_dist = _make_distributor(other_co, 'Other Tog Dist')
        po = _make_po(other_dist, 2026, 5, status='projected')
        url = reverse('toggle_po_selection')
        resp = _ajax_post(self.client, url, {'po_pk': po.pk, 'selected': True})
        self.assertEqual(resp.status_code, 404)
        po.refresh_from_db()
        self.assertFalse(po.selected_for_projection)

    # 7. Bulk toggle flags multiple POs
    def test_bulk_toggle_po_selection(self):
        po1 = _make_po(self.dist, 2026, 5, status='projected')
        po2 = _make_po(self.dist, 2026, 6, status='projected')
        url = reverse('bulk_toggle_po_selection')
        resp = _ajax_post(self.client, url, {'po_pks': [po1.pk, po2.pk], 'selected': True})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()['updated'], 2)
        po1.refresh_from_db()
        po2.refresh_from_db()
        self.assertTrue(po1.selected_for_projection)
        self.assertTrue(po2.selected_for_projection)

    # 8. Tab renders the two projection rows
    def test_distributor_pos_tab_renders_projection_rows(self):
        _make_po(self.dist, 2026, 5, status='projected')
        resp = self.client.get(reverse('distributor_list') + '?tab=distributor_pos')
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Current Inventory')
        self.assertContains(resp, 'Projected Ending Inventory')

    # 9. Selected PO renders a checked checkbox
    def test_distributor_pos_tab_checkbox_reflects_selection(self):
        po = _make_po(self.dist, 2026, 5, status='projected')
        po.selected_for_projection = True
        po.save(update_fields=['selected_for_projection'])
        resp = self.client.get(reverse('distributor_list') + '?tab=distributor_pos')
        self.assertEqual(resp.status_code, 200)
        # The checkbox input carries data-po-pk + checked
        self.assertContains(resp, f'data-po-pk="{po.pk}" checked')

    # 10. selected_po_count reflects all selected POs (across pages)
    def test_selected_po_count_in_context(self):
        for m in (3, 4, 5):
            po = _make_po(self.dist, 2026, m, status='projected')
            po.selected_for_projection = True
            po.save(update_fields=['selected_for_projection'])
        resp = self.client.get(reverse('distributor_list') + '?tab=distributor_pos')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.context['selected_po_count'], 3)

    # 11. pos_data_json carries per-item line quantities for current page
    def test_pos_data_json_contains_line_quantities(self):
        po = _make_po(self.dist, 2026, 5, status='projected')
        _make_po_line(po, self.item, 42)
        resp = self.client.get(reverse('distributor_list') + '?tab=distributor_pos')
        self.assertEqual(resp.status_code, 200)
        pos_data = json.loads(resp.context['pos_data_json'])
        self.assertIn(str(po.pk), pos_data)
        self.assertEqual(pos_data[str(po.pk)][str(self.item.pk)], 42.0)

    # 12. POs-tab script must render AFTER the bootstrap bundle, so the IIFE's
    #     `new bootstrap.Tooltip()` call does not throw on an undefined global.
    def test_pos_tab_script_loads_after_bootstrap(self):
        _make_po(self.dist, 2026, 5, status='projected')
        resp = self.client.get(reverse('distributor_list') + '?tab=distributor_pos')
        self.assertEqual(resp.status_code, 200)
        content = resp.content.decode()

        bootstrap_pos = content.find('bootstrap.bundle')
        # `recalcProjection` is defined only inside the moved POs-tab script.
        script_pos = content.find('recalcProjection')

        self.assertGreater(bootstrap_pos, 0, 'Bootstrap bundle not found')
        self.assertGreater(script_pos, 0, 'POs tab script not found')
        self.assertLess(
            bootstrap_pos, script_pos,
            'POs tab script must load AFTER the bootstrap bundle',
        )

    # 13. Inventory edit modal renders an input row per active item.
    def test_inventory_modal_renders_item_inputs(self):
        resp = self.client.get(reverse('distributor_list') + '?tab=distributor_pos')
        self.assertEqual(resp.status_code, 200)
        content = resp.content.decode()
        self.assertIn('class="form-control form-control-sm inventory-input"', content)
        self.assertIn(f'data-item-id="{self.item.pk}"', content)


# ---------------------------------------------------------------------------
# 11. Cleanup tests — v2 removal, band parity, v1 regression guard
# ---------------------------------------------------------------------------

class DistributorPOCleanupTest(TestCase):

    def setUp(self):
        self.company = _make_company('Cleanup Co')
        self.admin = _make_inventory_user(self.company, 'cleanup_admin')
        self.dist = _make_distributor(self.company)
        self.brand = _make_brand(self.company)
        self.item = _make_item(self.brand, item_code='CLNIT')
        self.client = Client()
        self.client.login(username='cleanup_admin', password='testpass123')

    # 1. v2 endpoint removed — URL resolves to nothing
    def test_v2_endpoint_removed(self):
        from django.urls import NoReverseMatch
        with self.assertRaises(NoReverseMatch):
            reverse('distributor_po_modal_data_v2')

    # 2. Band parity alternates by month; same month shares parity
    def test_band_parity_alternates_by_month(self):
        # Create POs in 3 distinct months (month order: 3, 4, 5 ascending = default sort)
        po_m3a = _make_po(self.dist, 2026, 3, status='projected')
        po_m3b = _make_po(self.dist, 2026, 3, status='actual', ext_po='PO-3B')
        po_m4 = _make_po(self.dist, 2026, 4, status='projected')
        po_m5 = _make_po(self.dist, 2026, 5, status='projected')
        resp = self.client.get(
            reverse('distributor_list') + '?tab=distributor_pos&sort=po_month'
        )
        self.assertEqual(resp.status_code, 200)
        rows = resp.context['pos_rows']
        parity_by_pk = {r['po'].pk: r['band_parity'] for r in rows}
        # Both March POs share the same parity
        self.assertEqual(parity_by_pk[po_m3a.pk], parity_by_pk[po_m3b.pk])
        # Consecutive months alternate
        self.assertNotEqual(parity_by_pk[po_m3a.pk], parity_by_pk[po_m4.pk])
        self.assertNotEqual(parity_by_pk[po_m4.pk], parity_by_pk[po_m5.pk])
        # Parity values are 0 or 1 only
        for r in rows:
            self.assertIn(r['band_parity'], (0, 1))
        # Band classes rendered in HTML
        content = resp.content.decode()
        self.assertIn('band-0', content)
        self.assertIn('band-1', content)

    # 3. v1 endpoint still handles ?po_pk regression guard
    #    (Covered by test #26 test_po_modal_single_po_mode; this confirms it
    #     remains exercised after the v2 removal.)
    def test_v1_po_pk_still_works(self):
        po = _make_po(self.dist, 2026, 6, status='projected')
        _make_po_line(po, self.item, 12)
        base = reverse('distributor_po_modal_data', args=[self.dist.pk, 2026, 6])
        resp = self.client.get(
            f'{base}?po_pk={po.pk}', HTTP_X_REQUESTED_WITH='XMLHttpRequest'
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        saved = data['saved_orders']
        self.assertEqual(len(saved), 1)
        self.assertEqual(saved[0]['id'], po.pk)


# ---------------------------------------------------------------------------
# 12. Manual within-month ordering — move endpoint, seeding, Order column
# ---------------------------------------------------------------------------

class DistributorPOMoveTest(TestCase):

    def setUp(self):
        self.company = _make_company('Move Co')
        self.admin = _make_inventory_user(self.company, 'move_admin')
        self.dist = _make_distributor(self.company)
        self.brand = _make_brand(self.company)
        self.item = _make_item(self.brand, item_code='MOVIT')
        self.client = Client()
        self.client.login(username='move_admin', password='testpass123')
        self.move_url = reverse('move_distributor_po')

    def _make_month(self, year, month, n):
        """Create n POs in (year, month) with sort_position 1..n; return them in order."""
        pos = []
        for i in range(1, n + 1):
            po = _make_po(self.dist, year, month, status='projected')
            po.sort_position = i
            po.save(update_fields=['sort_position'])
            pos.append(po)
        return pos

    def _positions(self, year, month):
        return {
            po.pk: po.sort_position
            for po in DistributorPO.objects.filter(
                distributor__company=self.company, year=year, month=month
            )
        }

    # 1. Seeding logic numbers a month by status-workflow rank then distributor name.
    def test_seed_sort_position_orders_by_status_then_distributor(self):
        # Call the exact callable the data migration uses, against live POs.
        import importlib
        from django.apps import apps as global_apps
        seed_mod = importlib.import_module(
            'apps.distribution.migrations.0018_seed_sort_position'
        )
        zeta = _make_distributor(self.company, 'Zeta Move Dist')
        alpha = _make_distributor(self.company, 'Alpha Move Dist')
        # status ranks: projected(0) < actual(1) < submitted(2)
        p_sub = _make_po(self.dist, 2030, 1, status='submitted')
        p_sub.so_number = 9001
        p_sub.save(update_fields=['so_number'])
        p_act_z = _make_po(zeta, 2030, 1, status='actual', ext_po='PO-Z')
        p_act_a = _make_po(alpha, 2030, 1, status='actual', ext_po='PO-A')
        p_proj = _make_po(self.dist, 2030, 1, status='projected')

        # Reset positions, then run the migration's seeding function directly.
        DistributorPO.objects.filter(
            distributor__company=self.company, year=2030, month=1
        ).update(sort_position=0)
        seed_mod.seed_sort_position(global_apps, None)

        group = list(
            DistributorPO.objects.filter(distributor__company=self.company, year=2030, month=1)
            .order_by('sort_position')
        )
        order = [p.pk for p in group]
        positions = [p.sort_position for p in group]
        # projected first, then actual(Alpha < Zeta by name), then submitted
        self.assertEqual(order, [p_proj.pk, p_act_a.pk, p_act_z.pk, p_sub.pk])
        self.assertEqual(positions, [1, 2, 3, 4])

    # 2. Move within month renumbers (slide-down).
    def test_move_po_within_month_renumbers(self):
        p1, p2, p3, p4 = self._make_month(2026, 5, 4)
        # Move the position-4 PO to position 2.
        resp = _ajax_post(self.client, self.move_url, {
            'po_pk': p4.pk, 'target_year': 2026, 'target_month': 5, 'target_position': 2,
        })
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()['ok'])
        pos = self._positions(2026, 5)
        self.assertEqual(pos[p1.pk], 1)
        self.assertEqual(pos[p4.pk], 2)   # inserted at 2
        self.assertEqual(pos[p2.pk], 3)   # old 2 -> 3
        self.assertEqual(pos[p3.pk], 4)   # old 3 -> 4

    # 3. Cross-month move renumbers both months.
    def test_move_po_to_different_month(self):
        a1, a2, a3 = self._make_month(2026, 4, 3)   # month A
        b1, b2 = self._make_month(2026, 7, 2)       # month B
        # Move a2 (pos 2 of 3 in A) to month B at position 1.
        resp = _ajax_post(self.client, self.move_url, {
            'po_pk': a2.pk, 'target_year': 2026, 'target_month': 7, 'target_position': 1,
        })
        self.assertEqual(resp.status_code, 200)
        a2.refresh_from_db()
        self.assertEqual((a2.year, a2.month), (2026, 7))
        # Month B: a2 at 1, b1 at 2, b2 at 3
        posB = self._positions(2026, 7)
        self.assertEqual(posB[a2.pk], 1)
        self.assertEqual(posB[b1.pk], 2)
        self.assertEqual(posB[b2.pk], 3)
        # Month A renumbered to close the gap: a1=1, a3=2
        posA = self._positions(2026, 4)
        self.assertEqual(posA[a1.pk], 1)
        self.assertEqual(posA[a3.pk], 2)
        self.assertNotIn(a2.pk, posA)

    # 4. Out-of-range target position clamps to the end.
    def test_move_po_clamps_out_of_range_position(self):
        p1, p2, p3 = self._make_month(2026, 8, 3)
        resp = _ajax_post(self.client, self.move_url, {
            'po_pk': p1.pk, 'target_year': 2026, 'target_month': 8, 'target_position': 999,
        })
        self.assertEqual(resp.status_code, 200)
        pos = self._positions(2026, 8)
        # p1 lands at the end (position 3), others slide up.
        self.assertEqual(pos[p2.pk], 1)
        self.assertEqual(pos[p3.pk], 2)
        self.assertEqual(pos[p1.pk], 3)

    # 5. Moving another company's PO → 404.
    def test_move_po_other_company_404(self):
        other_co = _make_company('Other Move Co')
        other_dist = _make_distributor(other_co, 'Other Move Dist')
        po = _make_po(other_dist, 2026, 5, status='projected')
        resp = _ajax_post(self.client, self.move_url, {
            'po_pk': po.pk, 'target_year': 2026, 'target_month': 5, 'target_position': 1,
        })
        self.assertEqual(resp.status_code, 404)

    # 6. Move requires can_manage_distributor_inventory.
    def test_move_po_requires_permission(self):
        limited = _make_limited_user(self.company, 'move_limited')
        c = Client()
        c.login(username='move_limited', password='testpass123')
        po = _make_po(self.dist, 2026, 5, status='projected')
        resp = _ajax_post(c, self.move_url, {
            'po_pk': po.pk, 'target_year': 2026, 'target_month': 5, 'target_position': 1,
        })
        self.assertEqual(resp.status_code, 403)

    # 7. Default order uses sort_position (year, month, sort_position).
    def test_default_order_uses_sort_position(self):
        # Create out of position order; assert the tab lists them by sort_position.
        p2 = _make_po(self.dist, 2026, 9, status='projected')
        p2.sort_position = 2
        p2.save(update_fields=['sort_position'])
        p1 = _make_po(self.dist, 2026, 9, status='projected')
        p1.sort_position = 1
        p1.save(update_fields=['sort_position'])
        resp = self.client.get(reverse('distributor_list') + '?tab=distributor_pos')
        self.assertEqual(resp.status_code, 200)
        order = [r['po'].pk for r in resp.context['pos_rows']]
        self.assertEqual(order, [p1.pk, p2.pk])

    # 8. Column-header sorts removed (no ?sort= links in the rendered tab).
    def test_column_sorts_removed(self):
        _make_po(self.dist, 2026, 5, status='projected')
        resp = self.client.get(reverse('distributor_list') + '?tab=distributor_pos')
        self.assertEqual(resp.status_code, 200)
        content = resp.content.decode()
        # The old sortable links used '?tab=distributor_pos&sort=' hrefs — gone now.
        self.assertNotIn('&sort=po_month', content)
        self.assertNotIn('&sort=distributor', content)
        self.assertNotIn('&sort=so_number', content)

    # 9. Order column removed; move icon lives in the PO row (PO Month cell).
    def test_order_column_removed(self):
        _make_po(self.dist, 2026, 5, status='projected')
        resp = self.client.get(reverse('distributor_list') + '?tab=distributor_pos')
        self.assertEqual(resp.status_code, 200)
        content = resp.content.decode()
        # Old Order-column markers are gone. (Avoid the bare 'order-col' substring —
        # it false-matches inside CSS 'border-color'.)
        self.assertNotIn('sticky-left-order', content)
        self.assertNotIn('order-number', content)
        self.assertNotIn('>Order<', content)

    def test_move_icon_present_in_po_month_cell(self):
        _make_po(self.dist, 2026, 5, status='projected')
        resp = self.client.get(reverse('distributor_list') + '?tab=distributor_pos')
        self.assertEqual(resp.status_code, 200)
        content = resp.content.decode()
        # The move button still renders (now beside the PO Month label) inside the
        # sticky-left-2 (PO Month) cell — assert both markers are present.
        self.assertIn('move-po-btn', content)
        self.assertIn('sticky-left-2', content)

    def test_no_stray_template_comment(self):
        _make_po(self.dist, 2026, 5, status='projected')
        resp = self.client.get(reverse('distributor_list') + '?tab=distributor_pos')
        self.assertEqual(resp.status_code, 200)
        content = resp.content.decode()
        # A malformed/multi-line {# #} comment would render as literal text.
        self.assertNotIn('Row 2: column headers', content)
        self.assertNotIn('{#', content)

    def test_no_stray_multiline_comment_in_distribution_templates(self):
        """Structural guard (4th instance of this bug class): Django {# #} comments
        are single-line only — a multi-line one renders as visible text. Scan EVERY
        distribution template (including partials) for a {# not closed by #} on the
        same line, and fail if any are found."""
        import glob
        import os
        from django.conf import settings

        base = os.path.join(settings.BASE_DIR, 'templates', 'distribution')
        paths = glob.glob(os.path.join(base, '*.html'))
        self.assertTrue(paths, 'No distribution templates found to scan.')

        offenders = []
        for path in paths:
            with open(path, encoding='utf-8') as f:
                for lineno, line in enumerate(f, 1):
                    idx = line.find('{#')
                    while idx != -1:
                        if line.find('#}', idx) == -1:
                            offenders.append(f'{os.path.basename(path)}:{lineno}')
                            break
                        idx = line.find('{#', line.find('#}', idx) + 2)

        self.assertEqual(
            offenders, [],
            f'Unclosed/multi-line {{# #}} comments found (use {{% comment %}}): {offenders}',
        )

    def test_move_modal_has_year_and_month_selectors(self):
        _make_po(self.dist, 2026, 5, status='projected')
        resp = self.client.get(reverse('distributor_list') + '?tab=distributor_pos')
        self.assertEqual(resp.status_code, 200)
        content = resp.content.decode()
        # Move modal offers a year + month selector (all 12 months built in JS).
        self.assertIn('id="move-target-year"', content)
        self.assertIn('id="move-target-month"', content)
        self.assertIn('id="move-month-empty"', content)

    def test_move_po_into_empty_month(self):
        # POs exist in May and July; June is empty.
        may = self._make_month(2026, 5, 2)
        july = self._make_month(2026, 7, 2)
        mover = may[1]  # position 2 of May
        resp = _ajax_post(self.client, self.move_url, {
            'po_pk': mover.pk, 'target_year': 2026, 'target_month': 6, 'target_position': 1,
        })
        self.assertEqual(resp.status_code, 200)
        mover.refresh_from_db()
        self.assertEqual((mover.year, mover.month), (2026, 6))
        self.assertEqual(mover.sort_position, 1)  # sole PO in June
        # Old month (May) renumbered to close the gap: remaining PO at position 1.
        posMay = self._positions(2026, 5)
        self.assertEqual(posMay[may[0].pk], 1)
        self.assertNotIn(mover.pk, posMay)
        # July untouched.
        posJuly = self._positions(2026, 7)
        self.assertEqual(posJuly[july[0].pk], 1)
        self.assertEqual(posJuly[july[1].pk], 2)

    # 10. move_modal_data_json present in context, structured month -> ordered list.
    def test_move_modal_data_in_context(self):
        p1 = _make_po(self.dist, 2026, 5, status='projected')
        p1.sort_position = 1
        p1.save(update_fields=['sort_position'])
        p2 = _make_po(self.dist, 2026, 5, status='projected')
        p2.sort_position = 2
        p2.save(update_fields=['sort_position'])
        resp = self.client.get(reverse('distributor_list') + '?tab=distributor_pos')
        self.assertEqual(resp.status_code, 200)
        data = json.loads(resp.context['move_modal_data_json'])
        self.assertIn('2026-05', data)
        month = data['2026-05']
        self.assertEqual([m['pk'] for m in month], [p1.pk, p2.pk])
        self.assertEqual([m['position'] for m in month], [1, 2])
        self.assertTrue(all('label' in m for m in month))


# ---------------------------------------------------------------------------
# Distributor area tweaks — tab scoping, header button, search/column removal,
# create redirect, forecast dropdown default + empty state, active-only dropdowns
# ---------------------------------------------------------------------------

class DistributorAreaTweaksTest(TestCase):

    def setUp(self):
        self.company = _make_company('Tweaks Co')
        self.admin = _make_inventory_user(self.company, 'tweaks_admin')
        self.dist = _make_distributor(self.company, 'Active Dist')
        self.brand = _make_brand(self.company)
        self.item = _make_item(self.brand, name='UniqueItemName', item_code='UNIQCODE')
        self.client = Client()
        self.client.login(username='tweaks_admin', password='testpass123')
        self.url = reverse('distributor_list')

    # --- PART 0: Filters button + PO listing strictly on the POs tab ---

    def test_filter_button_only_on_distributor_pos_tab(self):
        _make_po(self.dist, 2026, 5, status='projected')
        # Present on the POs tab
        resp = self.client.get(self.url + '?tab=distributor_pos')
        self.assertContains(resp, 'data-bs-target="#posFilterModal"')
        # Absent on every other tab
        for tab in ('distributors', 'inventory', 'forecast'):
            resp = self.client.get(self.url + f'?tab={tab}')
            self.assertNotContains(
                resp, 'data-bs-target="#posFilterModal"',
                msg_prefix=f'Filters button leaked onto {tab} tab',
            )

    def test_po_listing_only_on_distributor_pos_tab(self):
        _make_po(self.dist, 2026, 5, status='projected')
        # The PO listing table (po-table) only on the POs tab
        resp = self.client.get(self.url + '?tab=distributor_pos')
        self.assertContains(resp, 'po-table')
        for tab in ('distributors', 'inventory', 'forecast'):
            resp = self.client.get(self.url + f'?tab={tab}')
            self.assertNotContains(
                resp, 'po-table',
                msg_prefix=f'PO listing leaked onto {tab} tab',
            )

    # --- PART 1: Add Distributor button in header, scoped to Distributors tab ---

    def test_add_distributor_button_in_header(self):
        # Present on the Distributors tab (in the page header)
        resp = self.client.get(self.url + '?tab=distributors')
        self.assertContains(resp, 'Add Distributor')
        # Not present on other tabs (proves header scoping to the Distributors tab)
        for tab in ('inventory', 'forecast', 'distributor_pos'):
            resp = self.client.get(self.url + f'?tab={tab}')
            self.assertNotContains(
                resp, 'Add Distributor',
                msg_prefix=f'Add Distributor button leaked onto {tab} tab',
            )

    # --- PART 2: Search box removed ---

    def test_distributors_search_removed(self):
        resp = self.client.get(self.url + '?tab=distributors')
        self.assertEqual(resp.status_code, 200)
        self.assertNotContains(resp, 'name="q"')
        self.assertNotContains(resp, 'Distributor name…')

    # --- PART 3: PO delete UI disabled note (backend covered in DistributorPODeleteTest) ---

    def test_po_delete_note_present_for_non_projected_js(self):
        # The modal JS renders the "Only projected POs can be deleted." note for
        # non-projected POs. Assert the string is present in the page script.
        _make_po(self.dist, 2026, 5, status='projected')
        resp = self.client.get(self.url + '?tab=distributor_pos')
        self.assertContains(resp, 'Only projected POs can be deleted.')

    # --- PART 5: Inventory tab — Item name column removed (item code remains) ---

    def test_inventory_tab_no_item_name_column(self):
        _make_snapshot(self.dist, self.item, 2026, 5)
        resp = self.client.get(self.url + '?tab=inventory')
        self.assertEqual(resp.status_code, 200)
        # Item code column remains; the name column (and its sort link) is gone.
        self.assertContains(resp, 'UNIQCODE')
        self.assertNotContains(resp, 'sort=item&')
        self.assertNotContains(resp, 'UniqueItemName')

    # --- PART 6: Distributor create redirects to the listing ---

    def test_distributor_create_redirects_to_list(self):
        resp = self.client.post(
            reverse('distributor_create'),
            {'name': 'Brand New Dist'},
        )
        self.assertRedirects(resp, reverse('distributor_list'))
        self.assertTrue(
            Distributor.objects.filter(company=self.company, name='Brand New Dist').exists()
        )

    # --- PART 4: Forecast dropdown defaults to "Select a distributor" + empty state ---

    def test_forecast_dropdown_defaults_to_no_selection(self):
        _make_snapshot(self.dist, self.item, 2026, 1)
        resp = self.client.get(self.url + '?tab=forecast')
        self.assertEqual(resp.status_code, 200)
        # No distributor auto-selected; forecast not computed.
        self.assertIsNone(resp.context['forecast_distributor'])
        self.assertIsNone(resp.context['forecast_result'])
        # The default prompt option is present.
        self.assertContains(resp, 'Select a distributor')

    def test_forecast_empty_state_when_no_distributor(self):
        resp = self.client.get(self.url + '?tab=forecast')
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Select a distributor to view the forecast.')

    # --- PART 7: Active-only distributor dropdowns ---

    def test_forecast_dropdown_active_only(self):
        inactive = _make_distributor(self.company, 'Inactive Dist')
        inactive.is_active = False
        inactive.save(update_fields=['is_active'])
        resp = self.client.get(self.url + '?tab=forecast')
        self.assertEqual(resp.status_code, 200)
        avail = list(resp.context['available_distributors'])
        self.assertIn(self.dist, avail)
        self.assertNotIn(inactive, avail)

    def test_group_forecast_dropdown_active_only(self):
        # Audit: the group forecast (now in the Forecast tab via ?forecast_group=N)
        # uses the same "Forecast for" dropdown — active distributors only.
        member = _make_distributor(self.company, 'Member Dist')
        group = DistributorGroup.objects.create(
            company=self.company, name='Test Group', primary_distributor=member,
        )
        member.group = group
        member.save(update_fields=['group'])
        inactive = _make_distributor(self.company, 'Inactive Dist')
        inactive.is_active = False
        inactive.save(update_fields=['is_active'])
        resp = self.client.get(
            reverse('distributor_list') + f'?tab=forecast&forecast_group={group.pk}'
        )
        self.assertEqual(resp.status_code, 200)
        avail = list(resp.context['available_distributors'])
        self.assertNotIn(inactive, avail)
        self.assertIn(member, avail)


# ---------------------------------------------------------------------------
# 5. Unified PO modal — #7 (decimal pallets + total row) JS contract
# ---------------------------------------------------------------------------
#
# The modal's line table is built client-side (buildOrderForm in the inline IIFE
# of distributor_list.html), so there's no server-rendered DOM to assert on and
# no JS engine in the test client. These assert the inline JS *source* shipped to
# the browser carries the #7 behavior. The live arithmetic/sync is UI-verified.

class UnifiedPoModalJsContractTest(TestCase):

    def setUp(self):
        self.company = _make_company('Modal JS Co')
        self.admin = _make_inventory_user(self.company, 'modaljs_admin')
        self.dist = _make_distributor(self.company)
        self.brand = _make_brand(self.company)
        self.item = _make_item(self.brand, item_code='MJSIT')
        self.client = Client()
        self.client.login(username='modaljs_admin', password='testpass123')

    def _page(self):
        resp = self.client.get(reverse('distributor_list') + '?tab=distributor_pos')
        self.assertEqual(resp.status_code, 200)
        return resp.content.decode()

    # #7.1 — pallet display uses EXACT division via fmtQty, not Math.ceil.
    def test_pallet_display_shows_decimal(self):
        content = self._page()
        # Shared formatter present, and the pallet figure is computed by exact
        # division fed through fmtQty (decimal when fractional, whole otherwise).
        self.assertIn('function fmtQty', content)
        self.assertIn('fmtQty(casesVal / cpp)', content)
        # The old ceil-based pallet computation is gone (a historical mention may
        # remain in a comment — assert the actual call form is absent).
        self.assertNotIn('Math.ceil(casesVal / item.cases_per_pallet)', content)
        self.assertNotIn('Math.ceil(cases / cpp)', content)

    # #7.2 — the line table has a total row whose cases total is the line sum.
    def test_total_row_present(self):
        content = self._page()
        self.assertIn('<tfoot>', content)
        self.assertIn('po-total-cases', content)
        # Cases total = sum of per-line cases (computed in updateTotals).
        self.assertIn('function updateTotals', content)
        self.assertIn('totalCases += cases', content)

    # #7.3 — pallet total only in pallet mode; cases-mode shows cases only.
    def test_total_row_pallets_only_in_pallet_mode(self):
        content = self._page()
        # The pallets total cell is rendered only behind the isPallets guard.
        self.assertIn("if (isPallets) html += '<td class=\"small text-end po-total-pallets\">0</td>';", content)


# ---------------------------------------------------------------------------
# 6. Unified PO modal — #8 group save contract (cases stored whole)
# ---------------------------------------------------------------------------
#
# The bidirectional sync is shared JS, so it applies to the group modal too. The
# group save endpoint must store whole cases (same contract as the single one).

class GroupSaveCasesWholeTest(TestCase):

    def setUp(self):
        from apps.distribution.tests_group_forecast import _make_group
        self.company = _make_company('Group Whole Co')
        self.admin = _make_supplier_admin(self.company, 'gw_admin')
        self.primary = _make_distributor(self.company, 'GW Primary')
        self.member = _make_distributor(self.company, 'GW Member')
        self.group = _make_group(self.company, 'GW Group', self.primary,
                                 [self.primary, self.member])
        self.brand = _make_brand(self.company)
        self.item = _make_item(self.brand, item_code='GWIT')
        self.client = Client()
        self.client.login(username='gw_admin', password='testpass123')
        self.url = reverse('distributor_group_po_save', kwargs={'group_pk': self.group.pk})

    def test_group_save_stores_whole_cases(self):
        payload = {'year': 2026, 'month': 6, 'orders': [{
            'id': None, 'status': 'projected', 'external_po_number': '', 'notes': '',
            'lines': [{'item_id': self.item.pk, 'quantity_cases': 99.6}],
        }]}
        resp = _ajax_post(self.client, self.url, payload)
        self.assertEqual(resp.status_code, 200)
        po = DistributorPO.objects.get(distributor=self.primary, year=2026, month=6)
        stored = po.lines.first().quantity_cases
        self.assertEqual(stored, stored.to_integral_value())
        self.assertEqual(float(stored), 100.0)
