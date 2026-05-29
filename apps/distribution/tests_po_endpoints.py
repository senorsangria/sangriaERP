"""
Tests for DistributorPO model, PO modal endpoints, and integration.

Phase 4-step-2b: 29 tests across 5 classes.
"""
import json
from datetime import date
from unittest.mock import patch

from django.test import Client, TestCase
from django.urls import reverse

from apps.catalog.models import Brand, Item
from apps.core.models import Company, User
from apps.core.rbac import Permission, Role
from apps.distribution.forecast import compute_distributor_forecast
from apps.distribution.models import (
    Distributor, DistributorItemProfile, DistributorPO, DistributorPOLine,
    InventorySnapshot,
)
from apps.distribution.tests_forecast import (
    _make_company, _make_supplier_admin, _make_distributor,
    _make_brand, _make_item, _make_account, _make_batch,
    _make_snapshot, _make_sale,
)


# ---------------------------------------------------------------------------
# Extra helpers
# ---------------------------------------------------------------------------

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
# 8. distributor_po_modal_data_v2 endpoint tests
# ---------------------------------------------------------------------------

class DistributorPOModalDataV2Test(TestCase):

    def setUp(self):
        self.company = _make_company('V2 Modal Co')
        self.admin = _make_inventory_user(self.company, 'v2_admin')
        self.dist = _make_distributor(self.company)
        self.brand = _make_brand(self.company)
        self.item = _make_item(self.brand, item_code='V2ITEM')
        self.client = Client()
        self.client.login(username='v2_admin', password='testpass123')
        self.url = reverse('distributor_po_modal_data_v2')

    # 9. Single PO mode returns one PO entry
    def test_modal_data_v2_single_po_mode(self):
        po = _make_po(self.dist, 2026, 5, status='projected')
        _make_po_line(po, self.item, 48)
        resp = self.client.get(self.url + f'?po_pk={po.pk}')
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn('pos', data)
        self.assertEqual(len(data['pos']), 1)
        self.assertEqual(data['pos'][0]['pk'], po.pk)
        self.assertEqual(data['pos'][0]['year'], 2026)
        self.assertEqual(data['pos'][0]['month'], 5)
        self.assertEqual(len(data['pos'][0]['lines']), 1)
        self.assertIn('items', data)
        self.assertIn('distributor', data)

    # 10. Multi-PO mode returns all POs for that distributor/month
    def test_modal_data_v2_multi_po_mode(self):
        po1 = _make_po(self.dist, 2026, 5)
        po2 = _make_po(self.dist, 2026, 5)
        resp = self.client.get(
            self.url + f'?distributor={self.dist.pk}&year=2026&month=5'
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(len(data['pos']), 2)
        pks = {p['pk'] for p in data['pos']}
        self.assertIn(po1.pk, pks)
        self.assertIn(po2.pk, pks)

    # 11. Requires can_manage_distributor_inventory
    def test_modal_data_v2_requires_permission(self):
        limited = _make_limited_user(self.company, 'v2_limited')
        c = Client()
        c.login(username='v2_limited', password='testpass123')
        po = _make_po(self.dist, 2026, 5)
        resp = c.get(self.url + f'?po_pk={po.pk}')
        self.assertEqual(resp.status_code, 403)

    # 12. Returns 404 for PO belonging to a different company
    def test_modal_data_v2_404_for_other_company_po(self):
        other_co = _make_company('Other V2 Co')
        other_dist = _make_distributor(other_co, 'Other V2 Dist')
        po = _make_po(other_dist, 2026, 5)
        resp = self.client.get(self.url + f'?po_pk={po.pk}')
        self.assertEqual(resp.status_code, 404)


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

    # 14. Distributor POs tab excludes Invoiced POs
    def test_distributor_pos_tab_excludes_invoiced(self):
        _make_po(self.dist, 2026, 5, status='projected')
        _make_po(self.dist, 2026, 6, status='invoiced')
        resp = self._get_tab()
        self.assertEqual(resp.status_code, 200)
        count = resp.context['pos_page_obj'].paginator.count
        self.assertEqual(count, 1)

    # 15. Invoiced POs tab shows only Invoiced
    def test_invoiced_pos_tab_only_invoiced(self):
        _make_po(self.dist, 2026, 5, status='projected')
        _make_po(self.dist, 2026, 6, status='invoiced')
        resp = self._get_tab(tab='invoiced_pos')
        self.assertEqual(resp.status_code, 200)
        count = resp.context['pos_page_obj'].paginator.count
        self.assertEqual(count, 1)
        self.assertTrue(resp.context['is_invoiced_tab'])

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

    # 18. Sort by so_number works
    def test_distributor_pos_tab_sort_by_so_number(self):
        po1 = _make_po(self.dist, 2026, 5, status='submitted')
        po1.so_number = 2010
        po1.save(update_fields=['so_number'])
        po2 = _make_po(self.dist, 2026, 6, status='submitted')
        po2.so_number = 2005
        po2.save(update_fields=['so_number'])
        resp = self._get_tab(sort='so_number')
        self.assertEqual(resp.status_code, 200)
        rows = resp.context['pos_rows']
        so_values = [r['po'].so_number for r in rows]
        self.assertEqual(so_values, sorted(so_values))

    # 19. Paginator at 50 per page
    def test_distributor_pos_tab_paginates_at_50(self):
        for i in range(55):
            _make_po(self.dist, 2025, (i % 12) + 1, status='projected')
        resp = self._get_tab()
        self.assertEqual(resp.status_code, 200)
        page_obj = resp.context['pos_page_obj']
        self.assertEqual(len(page_obj.object_list), 50)
        self.assertEqual(page_obj.paginator.num_pages, 2)


# ---------------------------------------------------------------------------
# 10. Distributor.code tests + new tab refinements
# ---------------------------------------------------------------------------

class DistributorCodeTest(TestCase):

    def setUp(self):
        self.company = _make_company('Code Co')

    # 1. Code auto-generated from name ("NJ" is one word → first letter 'N')
    def test_distributor_code_auto_generated_from_name(self):
        from apps.distribution.models import Distributor
        dist = Distributor.objects.create(
            company=self.company, name='Shore Point Dist Co, NJ'
        )
        # "Shore Point Dist Co NJ" → S, P, D, C, NJ(→N) = "SPDCN"
        self.assertEqual(dist.code, 'SPDCN')

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
        # "of" skipped
        self.assertNotIn('O', code.split('C')[0] if 'C' in code else '')
        self.assertEqual(code, 'CBNJ')

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
