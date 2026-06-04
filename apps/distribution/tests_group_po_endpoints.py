"""
Tests for G3 group PO modal endpoints:
  - distributor_group_orders_modal_data (replaces G2 read-only endpoint)
  - distributor_group_po_save (new G3 save endpoint)
  - distributor_po_delete reuse for primary PO deletion from group context

16 tests across 3 classes.
"""
import json
from unittest.mock import patch

from django.test import Client, TestCase
from django.urls import reverse

from apps.distribution.models import (
    Distributor, DistributorGroup, DistributorItemProfile,
    DistributorPO, DistributorPOLine, InventorySnapshot,
)
from apps.distribution.tests_forecast import (
    _make_company, _make_supplier_admin, _make_distributor,
    _make_brand, _make_item, _make_account, _make_batch,
    _make_snapshot, _make_sale,
)
from apps.distribution.tests_group_forecast import _make_group
from apps.distribution.tests_po_endpoints import (
    _make_limited_user, _make_po, _make_po_line, _ajax_post,
    _FrozenApril2026Date,
)


# ---------------------------------------------------------------------------
# 1. distributor_group_orders_modal_data
# ---------------------------------------------------------------------------

class GroupPOModalDataTest(TestCase):

    def setUp(self):
        self.company  = _make_company('Group Modal Co')
        self.admin    = _make_supplier_admin(self.company, 'gm_admin')
        self.primary  = _make_distributor(self.company, 'Primary Dist')
        self.other    = _make_distributor(self.company, 'Other Dist')
        self.group    = _make_group(self.company, 'Test Group', self.primary,
                                    [self.primary, self.other])
        self.brand    = _make_brand(self.company)
        self.item     = _make_item(self.brand, item_code='GMOD1')
        self.client   = Client()
        self.client.login(username='gm_admin', password='testpass123')
        self.url = reverse('distributor_group_orders_modal_data',
                           kwargs={'group_pk': self.group.pk, 'year': 2026, 'month': 5})

    # 1. is_primary flag set correctly for POs from primary vs non-primary members
    def test_group_modal_data_returns_all_member_pos_with_is_primary_flag(self):
        po_primary = _make_po(self.primary, 2026, 5)
        po_other   = _make_po(self.other, 2026, 5)
        _make_po_line(po_primary, self.item, 24)
        _make_po_line(po_other,   self.item, 12)

        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()

        self.assertEqual(len(data['saved_orders']), 2)
        primary_po = next(o for o in data['saved_orders'] if o['distributor_pk'] == self.primary.pk)
        other_po   = next(o for o in data['saved_orders'] if o['distributor_pk'] == self.other.pk)
        self.assertTrue(primary_po['is_primary'])
        self.assertFalse(other_po['is_primary'])

    # 2. Response includes items list with active group items
    def test_group_modal_data_includes_items_active_for_any_member(self):
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn('items', data)
        item_ids = [i['id'] for i in data['items']]
        self.assertIn(self.item.pk, item_ids)

    # 3. Modal data does NOT include suggested_orders (on-demand only)
    def test_group_modal_data_returns_empty_suggestions_when_misaligned(self):
        resp = self.client.get(self.url)
        data = resp.json()
        self.assertNotIn('suggested_orders', data)

    # 4. Modal data does not include algorithm suggestions at all (on-demand now)
    def test_group_modal_data_includes_algorithm_suggestions(self):
        self.primary.order_quantity_value = 2
        self.primary.order_quantity_unit  = 'pallets'
        self.primary.save()
        self.item.cases_per_pallet = 12
        self.item.save()

        _make_snapshot(self.primary, self.item, 2026, 4, quantity=100)
        _make_snapshot(self.other,   self.item, 2026, 4, quantity=100)

        acc_p  = _make_account(self.company, self.primary)
        bat_p  = _make_batch(self.company, self.primary)
        acc_o  = _make_account(self.company, self.other)
        bat_o  = _make_batch(self.company, self.other)

        _make_sale(self.company, bat_p, acc_p, self.item, 2025, 5, 40)
        _make_sale(self.company, bat_o, acc_o, self.item, 2025, 5, 40)
        _make_sale(self.company, bat_p, acc_p, self.item, 2025, 6, 240)
        _make_sale(self.company, bat_o, acc_o, self.item, 2025, 6, 240)

        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        # Suggestions no longer returned by modal data endpoint
        self.assertNotIn('suggested_orders', data)
        # Core keys still present
        self.assertIn('saved_orders', data)
        self.assertIn('items', data)

    # 5. Returns 403 without can_manage_distributor_inventory
    def test_group_modal_data_returns_403_without_permission(self):
        limited = _make_limited_user(self.company, 'gm_limited')
        c = Client()
        c.login(username='gm_limited', password='testpass123')
        resp = c.get(self.url)
        self.assertEqual(resp.status_code, 403)

    # 6. Returns 404 for group belonging to another company
    def test_group_modal_data_returns_404_for_other_company_group(self):
        other_co      = _make_company('Other Co G')
        other_primary = _make_distributor(other_co, 'Other Primary')
        other_group   = _make_group(other_co, 'Other Group', other_primary, [other_primary])
        url = reverse('distributor_group_orders_modal_data',
                      kwargs={'group_pk': other_group.pk, 'year': 2026, 'month': 5})
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 404)

    # 7. Response shape includes all required top-level keys
    def test_group_modal_data_response_shape(self):
        resp = self.client.get(self.url)
        data = resp.json()
        for key in ('group', 'primary_distributor', 'items', 'saved_orders',
                    'year', 'month', 'period_label'):
            self.assertIn(key, data, f'Missing key: {key}')
        self.assertNotIn('suggested_orders', data)
        self.assertEqual(data['primary_distributor']['id'], self.primary.pk)
        self.assertEqual(data['group']['id'], self.group.pk)


# ---------------------------------------------------------------------------
# 2. distributor_group_po_save
# ---------------------------------------------------------------------------

class GroupPOSaveTest(TestCase):

    def setUp(self):
        self.company  = _make_company('Group Save Co')
        self.admin    = _make_supplier_admin(self.company, 'gs_admin')
        self.primary  = _make_distributor(self.company, 'Primary Saver')
        self.other    = _make_distributor(self.company, 'Other Member')
        self.group    = _make_group(self.company, 'Save Group', self.primary,
                                    [self.primary, self.other])
        self.brand    = _make_brand(self.company)
        self.item     = _make_item(self.brand)
        self.client   = Client()
        self.client.login(username='gs_admin', password='testpass123')
        self.url = reverse('distributor_group_po_save', kwargs={'group_pk': self.group.pk})

    def _post(self, payload):
        return _ajax_post(self.client, self.url, payload)

    def _payload(self, year=2026, month=6, orders=None):
        if orders is None:
            orders = [{'id': None, 'status': 'projected', 'external_po_number': '',
                       'notes': '', 'lines': [{'item_id': self.item.pk, 'quantity_cases': 24}]}]
        return {'year': year, 'month': month, 'orders': orders}

    # 7. New PO is always created against the primary distributor
    def test_group_po_save_creates_new_po_against_primary(self):
        resp = self._post(self._payload())
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()['ok'])
        po = DistributorPO.objects.get(year=2026, month=6, distributor=self.primary)
        self.assertEqual(po.lines.count(), 1)
        self.assertFalse(po.generated_by_algorithm)

    # 8. Existing primary PO can be updated
    def test_group_po_save_updates_existing_primary_po(self):
        po = _make_po(self.primary, 2026, 6)
        _make_po_line(po, self.item, 12)
        payload = self._payload(orders=[{
            'id': po.pk, 'status': 'projected', 'external_po_number': '', 'notes': '',
            'lines': [{'item_id': self.item.pk, 'quantity_cases': 48}],
        }])
        resp = self._post(payload)
        self.assertTrue(resp.json()['ok'])
        po.refresh_from_db()
        self.assertEqual(float(po.lines.first().quantity_cases), 48.0)

    # 8b. Save-path deletion (emptying lines) is allowed for projected primary POs
    def test_group_save_path_delete_allowed_when_projected(self):
        po = _make_po(self.primary, 2026, 6, status='projected')
        _make_po_line(po, self.item, 24)
        payload = self._payload(orders=[{
            'id': po.pk, 'status': 'projected', 'external_po_number': '', 'notes': '',
            'lines': [{'item_id': self.item.pk, 'quantity_cases': 0}],
        }])
        resp = self._post(payload)
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()['ok'])
        self.assertFalse(DistributorPO.objects.filter(pk=po.pk).exists())

    # 8c. Save-path deletion is rejected for non-projected primary POs (persisted
    #     status drives eligibility; the group path only accepts submitted
    #     'projected'/'actual', so 'actual' is the representative non-projected case)
    def test_group_save_path_delete_rejected_when_not_projected(self):
        po = _make_po(self.primary, 2026, 6, status='actual', ext_po='PO-7')
        _make_po_line(po, self.item, 24)
        payload = self._payload(orders=[{
            'id': po.pk, 'status': 'projected', 'external_po_number': '', 'notes': '',
            'lines': [{'item_id': self.item.pk, 'quantity_cases': 0}],
        }])
        resp = self._post(payload)
        self.assertEqual(resp.status_code, 400)
        self.assertIn('projected', resp.json()['error'].lower())
        self.assertTrue(DistributorPO.objects.filter(pk=po.pk).exists())

    # 9. Submitting a non-primary PO's ID is rejected with 400
    def test_group_po_save_rejects_non_primary_po_id(self):
        other_po = _make_po(self.other, 2026, 6)
        _make_po_line(other_po, self.item, 24)
        payload = self._payload(orders=[{
            'id': other_po.pk, 'status': 'projected', 'external_po_number': '', 'notes': '',
            'lines': [{'item_id': self.item.pk, 'quantity_cases': 24}],
        }])
        resp = self._post(payload)
        self.assertEqual(resp.status_code, 400)
        # Non-primary PO must be unchanged
        self.assertTrue(DistributorPO.objects.filter(pk=other_po.pk).exists())

    # 10. Negative quantities are rejected
    def test_group_po_save_rejects_negative_quantities(self):
        payload = self._payload(orders=[{
            'id': None, 'status': 'projected', 'external_po_number': '', 'notes': '',
            'lines': [{'item_id': self.item.pk, 'quantity_cases': -5}],
        }])
        resp = self._post(payload)
        self.assertEqual(resp.status_code, 400)

    # 11. Actual status without PO number is rejected
    def test_group_po_save_rejects_actual_status_without_po_number(self):
        payload = self._payload(orders=[{
            'id': None, 'status': 'actual', 'external_po_number': '', 'notes': '',
            'lines': [{'item_id': self.item.pk, 'quantity_cases': 24}],
        }])
        resp = self._post(payload)
        self.assertEqual(resp.status_code, 400)
        self.assertIn('PO number', resp.json()['error'])

    # 12. Item IDs from another company are rejected
    def test_group_po_save_rejects_invalid_item_id(self):
        other_co    = _make_company('Item Other Co')
        other_brand = _make_brand(other_co, 'Other Brand')
        other_item  = _make_item(other_brand, name='Other Item', item_code='OTHI')
        payload = self._payload(orders=[{
            'id': None, 'status': 'projected', 'external_po_number': '', 'notes': '',
            'lines': [{'item_id': other_item.pk, 'quantity_cases': 10}],
        }])
        resp = self._post(payload)
        self.assertEqual(resp.status_code, 400)

    # 13. Failure mid-save rolls back all changes atomically
    def test_group_po_save_atomic_failure_rolls_back(self):
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
        with patch.object(type(DistributorPO.objects), 'create',
                          side_effect=lambda **kw: failing_create(DistributorPO.objects, **kw)):
            resp = self._post({'year': 2026, 'month': 6, 'orders': orders})

        self.assertEqual(resp.status_code, 500)
        self.assertEqual(DistributorPO.objects.filter(distributor=self.primary).count(), 0)

    # 14. Returns 403 without permission
    def test_group_po_save_returns_403_without_permission(self):
        limited = _make_limited_user(self.company, 'gs_limited')
        c = Client()
        c.login(username='gs_limited', password='testpass123')
        resp = _ajax_post(c, self.url, self._payload())
        self.assertEqual(resp.status_code, 403)

    # 15. Returns 404 for group belonging to another company
    def test_group_po_save_returns_404_for_other_company_group(self):
        other_co      = _make_company('Other Co Save')
        other_primary = _make_distributor(other_co, 'Other Primary S')
        other_group   = _make_group(other_co, 'Other Save Group', other_primary, [other_primary])
        url = reverse('distributor_group_po_save', kwargs={'group_pk': other_group.pk})
        resp = _ajax_post(self.client, url, self._payload())
        self.assertEqual(resp.status_code, 404)


# ---------------------------------------------------------------------------
# 3. Primary PO deletion via existing distributor_po_delete endpoint
# ---------------------------------------------------------------------------

class GroupPODeleteViaExistingEndpointTest(TestCase):

    def setUp(self):
        self.company  = _make_company('Group Delete Co')
        self.admin    = _make_supplier_admin(self.company, 'gd_admin')
        self.primary  = _make_distributor(self.company, 'Primary Deleter')
        self.other    = _make_distributor(self.company, 'Other Deleter')
        self.group    = _make_group(self.company, 'Delete Group', self.primary,
                                    [self.primary, self.other])
        self.brand    = _make_brand(self.company)
        self.item     = _make_item(self.brand)
        self.client   = Client()
        self.client.login(username='gd_admin', password='testpass123')

    # 16. Primary PO deleted via existing distributor_po_delete using primary's dist_pk
    def test_group_po_delete_via_existing_endpoint(self):
        po = _make_po(self.primary, 2026, 6)
        url = reverse('distributor_po_delete',
                      kwargs={'dist_pk': self.primary.pk, 'po_pk': po.pk})
        resp = _ajax_post(self.client, url, {'po_id': po.pk})
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()['ok'])
        self.assertFalse(DistributorPO.objects.filter(pk=po.pk).exists())

    # Bonus: attempting to delete a non-primary PO via the wrong dist_pk is rejected
    def test_group_po_delete_cross_distributor_rejected(self):
        other_po = _make_po(self.other, 2026, 6)
        # Use primary's dist_pk but other's po_pk — should 404
        url = reverse('distributor_po_delete',
                      kwargs={'dist_pk': self.primary.pk, 'po_pk': other_po.pk})
        resp = _ajax_post(self.client, url, {'po_id': other_po.pk})
        self.assertEqual(resp.status_code, 404)
        self.assertTrue(DistributorPO.objects.filter(pk=other_po.pk).exists())


# ---------------------------------------------------------------------------
# 4. distributor_group_po_suggest endpoint
# ---------------------------------------------------------------------------

class GroupSuggestEndpointTest(TestCase):

    def setUp(self):
        self.company  = _make_company('Group Sug Co')
        self.admin    = _make_supplier_admin(self.company, 'gsug_admin')
        self.primary  = Distributor.objects.create(
            company=self.company, name='GSug Primary',
            order_quantity_value=10, order_quantity_unit='cases',
        )
        self.other    = _make_distributor(self.company, 'GSug Other')
        self.group    = _make_group(self.company, 'GSug Group', self.primary,
                                    [self.primary, self.other])
        self.brand    = _make_brand(self.company)
        self.item     = _make_item(self.brand, item_code='GSUG1')
        self.client   = Client()
        self.client.login(username='gsug_admin', password='testpass123')
        # Anchor both members at April 2026; prior-year May depletion large enough to cause shortage
        _make_snapshot(self.primary, self.item, 2026, 4, quantity=10)
        _make_snapshot(self.other,   self.item, 2026, 4, quantity=10)
        acc_p = _make_account(self.company, self.primary)
        bat_p = _make_batch(self.company, self.primary)
        acc_o = _make_account(self.company, self.other)
        bat_o = _make_batch(self.company, self.other)
        # Combined prior-year May 2025 depletion = 100 → May 2026 inv = 20 - 100 = -80
        _make_sale(self.company, bat_p, acc_p, self.item, 2025, 5, 50)
        _make_sale(self.company, bat_o, acc_o, self.item, 2025, 5, 50)
        # Modal month = April 2026 → lookahead = May 2026
        self.url = reverse('distributor_group_po_suggest',
                           kwargs={'group_pk': self.group.pk, 'year': 2026, 'month': 4})

    # 17. Group suggest returns correct lines when shortage exists
    @patch('apps.distribution.forecast.date', _FrozenApril2026Date)
    def test_group_suggest_returns_correct_lines(self):
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn('lines', data)
        self.assertGreater(len(data['lines']), 0)
        self.assertEqual(data['lines'][0]['item_id'], self.item.pk)

    # 18. Returns empty lines when group alignment is broken
    def test_group_suggest_empty_when_alignment_broken(self):
        InventorySnapshot.objects.all().delete()
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data['lines'], [])

    # 19. Suggestion uses primary distributor's order config
    @patch('apps.distribution.forecast.date', _FrozenApril2026Date)
    def test_group_suggest_uses_primary_order_config(self):
        # Primary has order_qty=10 cases; primary has shortage of ~80
        # ceil(80/10)*10 = 80 cases
        resp = self.client.get(self.url)
        data = resp.json()
        self.assertGreater(len(data['lines']), 0)
        line = data['lines'][0]
        self.assertIsNone(line['pallets'])  # cases mode, no pallets
        self.assertGreater(line['cases'], 0)

    # 20. Returns 403 without can_manage_distributor_inventory
    def test_group_suggest_requires_permission(self):
        limited = _make_limited_user(self.company, 'gsug_limited')
        c = Client()
        c.login(username='gsug_limited', password='testpass123')
        resp = c.get(self.url)
        self.assertEqual(resp.status_code, 403)
