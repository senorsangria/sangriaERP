"""
Tests for Phase D production PO modal endpoints:
  - production_po_modal_data (GET)
  - production_po_save (POST)
  - production_po_delete (POST)

Phase D2 additions:
  - COMPLETE status (model + save endpoint)
  - Production POs tab list view
  - production_po_modal_data_single (GET)
"""
import json
from decimal import Decimal

from django.test import Client, TestCase
from django.urls import reverse

from apps.catalog.models import Brand, CoPacker, Item
from apps.core.models import Company, User
from apps.core.rbac import Role
from apps.distribution.models import Distributor, DistributorPO
from apps.production.models import OwnInventorySnapshot, ProductionPO, ProductionPOLine


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_company(name='Test Co'):
    return Company.objects.create(name=name)


def make_supplier_admin(company, username='admin'):
    user = User.objects.create_user(username=username, password='pass', company=company)
    user.roles.set([Role.objects.get(codename='supplier_admin')])
    return user


def make_brand(company, name='Brand'):
    return Brand.objects.create(company=company, name=name)


def make_co_packer(company, name='Brotherhood Winery'):
    return CoPacker.objects.create(company=company, name=name)


def make_item(brand, co_packer=None, cases_per_batch=None, name='Item', item_code='CODE'):
    return Item.objects.create(
        brand=brand, name=name, item_code=item_code,
        co_packer=co_packer, cases_per_batch=cases_per_batch,
    )


def make_production_po(company, co_packer, year=2026, month=7, status='projected',
                       external_po_number='', generated_by_algorithm=False):
    return ProductionPO.objects.create(
        company=company,
        co_packer=co_packer,
        year=year,
        month=month,
        status=status,
        external_po_number=external_po_number,
        generated_by_algorithm=generated_by_algorithm,
    )


def make_po_line(po, item, batch_count=3):
    qty = Decimal(batch_count) * Decimal(item.cases_per_batch or 1)
    return ProductionPOLine.objects.create(
        po=po, item=item, batch_count=batch_count, quantity_cases=qty,
    )


def make_snapshot(company, item, year=2026, month=5, qty='100'):
    return OwnInventorySnapshot.objects.create(
        company=company, item=item, year=year, month=month, quantity_cases=Decimal(qty),
    )


def modal_data_url(year=2026, month=7):
    return reverse('production_po_modal_data', kwargs={'year': year, 'month': month})


def save_url():
    return reverse('production_po_save')


def delete_url(po_pk):
    return reverse('production_po_delete', kwargs={'po_pk': po_pk})


def post_save(client, payload):
    return client.post(
        save_url(),
        data=json.dumps(payload),
        content_type='application/json',
        HTTP_X_REQUESTED_WITH='XMLHttpRequest',
    )


# ---------------------------------------------------------------------------
# Base test setup
# ---------------------------------------------------------------------------

class POMixin(TestCase):

    def setUp(self):
        self.company = make_company()
        self.admin = make_supplier_admin(self.company)
        self.brand = make_brand(self.company)
        self.cp1 = make_co_packer(self.company, 'Brotherhood Winery')
        self.cp2 = make_co_packer(self.company, 'Nidra Packaging')
        self.item1 = make_item(self.brand, co_packer=self.cp1, cases_per_batch=280,
                               name='Classic Red 750ml', item_code='RED0750')
        self.item2 = make_item(self.brand, co_packer=self.cp2, cases_per_batch=200,
                               name='White 750ml', item_code='WHT0750')
        self.client = Client()
        self.client.login(username='admin', password='pass')


# ---------------------------------------------------------------------------
# Modal data endpoint (GET)
# ---------------------------------------------------------------------------

class ModalDataPermissionTest(POMixin):

    def test_modal_data_returns_403_without_permission(self):
        other_company = make_company('Other')
        sales_user = User.objects.create_user(username='sales', password='pass', company=self.company)
        sales_user.roles.set([Role.objects.get(codename='sales_manager')])
        c = Client()
        c.login(username='sales', password='pass')
        r = c.get(modal_data_url(), HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertEqual(r.status_code, 403)


class ModalDataContentTest(POMixin):

    def test_modal_data_returns_co_packers_for_company(self):
        r = self.client.get(modal_data_url())
        self.assertEqual(r.status_code, 200)
        data = r.json()
        cp_ids = [cp['id'] for cp in data['co_packers']]
        self.assertIn(self.cp1.pk, cp_ids)
        self.assertIn(self.cp2.pk, cp_ids)

    def test_modal_data_returns_items_by_co_packer(self):
        r = self.client.get(modal_data_url())
        data = r.json()
        cp1_items = data['items_by_co_packer'][str(self.cp1.pk)]
        self.assertEqual(len(cp1_items), 1)
        self.assertEqual(cp1_items[0]['id'], self.item1.pk)
        self.assertEqual(cp1_items[0]['cases_per_batch'], 280)

    def test_modal_data_excludes_items_without_cases_per_batch(self):
        item_no_batch = make_item(self.brand, co_packer=self.cp1, cases_per_batch=None,
                                  name='No Batch Item', item_code='NOBATCH')
        r = self.client.get(modal_data_url())
        data = r.json()
        cp1_ids = [i['id'] for i in data['items_by_co_packer'][str(self.cp1.pk)]]
        self.assertNotIn(item_no_batch.pk, cp1_ids)

    def test_modal_data_returns_saved_pos_with_lines(self):
        po = make_production_po(self.company, self.cp1, year=2026, month=7)
        make_po_line(po, self.item1, batch_count=5)
        r = self.client.get(modal_data_url(2026, 7))
        data = r.json()
        self.assertEqual(len(data['saved_pos']), 1)
        sp = data['saved_pos'][0]
        self.assertEqual(sp['po_id'], po.pk)
        self.assertEqual(sp['co_packer_id'], self.cp1.pk)
        self.assertEqual(len(sp['lines']), 1)
        self.assertEqual(sp['lines'][0]['batch_count'], 5)

    def test_modal_data_returns_empty_saved_pos_when_none(self):
        r = self.client.get(modal_data_url(2026, 7))
        data = r.json()
        self.assertEqual(data['saved_pos'], [])

    def test_modal_data_excludes_inactive_co_packers(self):
        inactive_cp = CoPacker.objects.create(company=self.company, name='Inactive CP', is_active=False)
        r = self.client.get(modal_data_url())
        data = r.json()
        cp_ids = [cp['id'] for cp in data['co_packers']]
        self.assertNotIn(inactive_cp.pk, cp_ids)


# ---------------------------------------------------------------------------
# Save endpoint (POST)
# ---------------------------------------------------------------------------

class SaveCreateTest(POMixin):

    def test_save_creates_new_po_with_lines(self):
        payload = {
            'year': 2026, 'month': 7,
            'pos': [{
                'po_id': None,
                'co_packer_id': self.cp1.pk,
                'status': 'projected',
                'external_po_number': '',
                'notes': '',
                'lines': [{'item_id': self.item1.pk, 'batch_count': 3}],
            }],
        }
        r = post_save(self.client, payload)
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json()['success'])
        po = ProductionPO.objects.get(company=self.company, year=2026, month=7)
        self.assertEqual(po.lines.count(), 1)

    def test_save_creates_quantity_cases_from_batch_count_times_cases_per_batch(self):
        payload = {
            'year': 2026, 'month': 7,
            'pos': [{
                'po_id': None,
                'co_packer_id': self.cp1.pk,
                'status': 'projected',
                'external_po_number': '',
                'notes': '',
                'lines': [{'item_id': self.item1.pk, 'batch_count': 4}],
            }],
        }
        post_save(self.client, payload)
        line = ProductionPOLine.objects.get(item=self.item1)
        self.assertEqual(line.batch_count, 4)
        self.assertEqual(line.quantity_cases, Decimal('1120'))  # 4 * 280

    def test_save_new_po_has_generated_by_algorithm_false(self):
        payload = {
            'year': 2026, 'month': 7,
            'pos': [{
                'po_id': None,
                'co_packer_id': self.cp1.pk,
                'status': 'projected',
                'external_po_number': '',
                'notes': '',
                'lines': [{'item_id': self.item1.pk, 'batch_count': 1}],
            }],
        }
        post_save(self.client, payload)
        po = ProductionPO.objects.get(company=self.company, year=2026, month=7)
        self.assertFalse(po.generated_by_algorithm)

    def test_save_allows_multiple_pos_per_co_packer_per_month(self):
        payload = {
            'year': 2026, 'month': 7,
            'pos': [
                {
                    'po_id': None,
                    'co_packer_id': self.cp1.pk,
                    'status': 'projected',
                    'external_po_number': '',
                    'notes': '',
                    'lines': [{'item_id': self.item1.pk, 'batch_count': 2}],
                },
                {
                    'po_id': None,
                    'co_packer_id': self.cp1.pk,
                    'status': 'projected',
                    'external_po_number': '',
                    'notes': '',
                    'lines': [{'item_id': self.item1.pk, 'batch_count': 1}],
                },
            ],
        }
        r = post_save(self.client, payload)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(ProductionPO.objects.filter(company=self.company, year=2026, month=7).count(), 2)

    def test_save_skips_new_po_with_all_zero_lines(self):
        payload = {
            'year': 2026, 'month': 7,
            'pos': [{
                'po_id': None,
                'co_packer_id': self.cp1.pk,
                'status': 'projected',
                'external_po_number': '',
                'notes': '',
                'lines': [{'item_id': self.item1.pk, 'batch_count': 0}],
            }],
        }
        r = post_save(self.client, payload)
        self.assertEqual(r.status_code, 200)
        self.assertFalse(ProductionPO.objects.filter(company=self.company).exists())


class SaveUpdateTest(POMixin):

    def setUp(self):
        super().setUp()
        self.po = make_production_po(self.company, self.cp1, year=2026, month=7)
        make_po_line(self.po, self.item1, batch_count=3)

    def test_save_updates_existing_po(self):
        payload = {
            'year': 2026, 'month': 7,
            'pos': [{
                'po_id': self.po.pk,
                'co_packer_id': self.cp1.pk,
                'status': 'actual',
                'external_po_number': 'PO-123',
                'notes': 'Updated',
                'lines': [{'item_id': self.item1.pk, 'batch_count': 5}],
            }],
        }
        r = post_save(self.client, payload)
        self.assertEqual(r.status_code, 200)
        self.po.refresh_from_db()
        self.assertEqual(self.po.status, 'actual')
        self.assertEqual(self.po.external_po_number, 'PO-123')
        line = self.po.lines.get()
        self.assertEqual(line.batch_count, 5)

    def test_save_flips_generated_by_algorithm_to_false_on_update(self):
        self.po.generated_by_algorithm = True
        self.po.save()
        payload = {
            'year': 2026, 'month': 7,
            'pos': [{
                'po_id': self.po.pk,
                'co_packer_id': self.cp1.pk,
                'status': 'projected',
                'external_po_number': '',
                'notes': '',
                'lines': [{'item_id': self.item1.pk, 'batch_count': 2}],
            }],
        }
        post_save(self.client, payload)
        self.po.refresh_from_db()
        self.assertFalse(self.po.generated_by_algorithm)

    def test_save_deletes_po_when_all_lines_zero(self):
        payload = {
            'year': 2026, 'month': 7,
            'pos': [{
                'po_id': self.po.pk,
                'co_packer_id': self.cp1.pk,
                'status': 'projected',
                'external_po_number': '',
                'notes': '',
                'lines': [{'item_id': self.item1.pk, 'batch_count': 0}],
            }],
        }
        r = post_save(self.client, payload)
        self.assertEqual(r.status_code, 200)
        self.assertFalse(ProductionPO.objects.filter(pk=self.po.pk).exists())


class SaveValidationTest(POMixin):

    def _base_payload(self, **overrides):
        po = {
            'po_id': None,
            'co_packer_id': self.cp1.pk,
            'status': 'projected',
            'external_po_number': '',
            'notes': '',
            'lines': [{'item_id': self.item1.pk, 'batch_count': 1}],
        }
        po.update(overrides)
        return {'year': 2026, 'month': 7, 'pos': [po]}

    def test_save_returns_400_for_actual_without_po_number(self):
        r = post_save(self.client, self._base_payload(status='actual', external_po_number=''))
        self.assertEqual(r.status_code, 400)
        self.assertIn('PO number', r.json()['error'])

    def test_save_returns_400_for_missing_co_packer(self):
        payload = {'year': 2026, 'month': 7, 'pos': [{
            'po_id': None,
            'co_packer_id': None,
            'status': 'projected',
            'external_po_number': '',
            'notes': '',
            'lines': [],
        }]}
        r = post_save(self.client, payload)
        self.assertEqual(r.status_code, 400)

    def test_save_returns_400_for_negative_batch_count(self):
        r = post_save(self.client, self._base_payload(
            lines=[{'item_id': self.item1.pk, 'batch_count': -1}]
        ))
        self.assertEqual(r.status_code, 400)

    def test_save_returns_400_for_non_integer_batch_count(self):
        r = post_save(self.client, self._base_payload(
            lines=[{'item_id': self.item1.pk, 'batch_count': 2.5}]
        ))
        self.assertEqual(r.status_code, 400)

    def test_save_returns_400_for_item_not_belonging_to_co_packer(self):
        # item2 belongs to cp2, not cp1
        r = post_save(self.client, self._base_payload(
            co_packer_id=self.cp1.pk,
            lines=[{'item_id': self.item2.pk, 'batch_count': 1}]
        ))
        self.assertEqual(r.status_code, 400)
        self.assertIn('co-packer', r.json()['error'])

    def test_save_returns_400_for_item_without_cases_per_batch(self):
        item_no_batch = make_item(self.brand, co_packer=self.cp1, cases_per_batch=None,
                                  name='No Batch', item_code='NB01')
        r = post_save(self.client, self._base_payload(
            co_packer_id=self.cp1.pk,
            lines=[{'item_id': item_no_batch.pk, 'batch_count': 2}]
        ))
        self.assertEqual(r.status_code, 400)
        self.assertIn('cases per batch', r.json()['error'])

    def test_save_returns_400_for_duplicate_item_in_lines(self):
        r = post_save(self.client, self._base_payload(
            lines=[
                {'item_id': self.item1.pk, 'batch_count': 1},
                {'item_id': self.item1.pk, 'batch_count': 2},
            ]
        ))
        self.assertEqual(r.status_code, 400)
        self.assertIn('Duplicate', r.json()['error'])

    def test_save_returns_400_for_cross_tenant_co_packer(self):
        other_company = make_company('Other Co')
        other_cp = make_co_packer(other_company, 'Other Packer')
        r = post_save(self.client, self._base_payload(co_packer_id=other_cp.pk, lines=[]))
        self.assertEqual(r.status_code, 400)

    def test_save_returns_400_for_cross_tenant_item(self):
        other_company = make_company('Other Co 2')
        other_brand = make_brand(other_company, 'OtherBrand')
        other_cp = make_co_packer(other_company, 'Other CP')
        other_item = make_item(other_brand, co_packer=other_cp, cases_per_batch=100,
                               name='Other Item', item_code='OTH01')
        r = post_save(self.client, self._base_payload(
            co_packer_id=self.cp1.pk,
            lines=[{'item_id': other_item.pk, 'batch_count': 1}]
        ))
        self.assertEqual(r.status_code, 400)

    def test_save_returns_403_without_permission(self):
        sales_user = User.objects.create_user(username='sales2', password='pass', company=self.company)
        sales_user.roles.set([Role.objects.get(codename='sales_manager')])
        c = Client()
        c.login(username='sales2', password='pass')
        r = c.post(save_url(), data='{}', content_type='application/json',
                   HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertEqual(r.status_code, 403)

    def test_save_atomic_all_or_nothing(self):
        """If second PO fails validation, first PO should not be committed."""
        payload = {
            'year': 2026, 'month': 7,
            'pos': [
                {
                    'po_id': None,
                    'co_packer_id': self.cp1.pk,
                    'status': 'projected',
                    'external_po_number': '',
                    'notes': '',
                    'lines': [{'item_id': self.item1.pk, 'batch_count': 2}],
                },
                {
                    # Missing co-packer — will trigger 400 before transaction even opens
                    'po_id': None,
                    'co_packer_id': None,
                    'status': 'projected',
                    'external_po_number': '',
                    'notes': '',
                    'lines': [],
                },
            ],
        }
        r = post_save(self.client, payload)
        self.assertEqual(r.status_code, 400)
        self.assertFalse(ProductionPO.objects.filter(company=self.company).exists())


# ---------------------------------------------------------------------------
# Delete endpoint (POST)
# ---------------------------------------------------------------------------

class DeleteTest(POMixin):

    def setUp(self):
        super().setUp()
        self.po = make_production_po(self.company, self.cp1)
        make_po_line(self.po, self.item1, batch_count=3)

    def test_delete_removes_po_and_lines(self):
        po_pk = self.po.pk
        r = self.client.post(delete_url(po_pk), HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json()['success'])
        self.assertFalse(ProductionPO.objects.filter(pk=po_pk).exists())
        self.assertFalse(ProductionPOLine.objects.filter(po_id=po_pk).exists())

    def test_delete_returns_403_without_permission(self):
        sales_user = User.objects.create_user(username='sales3', password='pass', company=self.company)
        sales_user.roles.set([Role.objects.get(codename='sales_manager')])
        c = Client()
        c.login(username='sales3', password='pass')
        r = c.post(delete_url(self.po.pk), HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertEqual(r.status_code, 403)

    def test_delete_returns_404_for_cross_tenant_po(self):
        other_company = make_company('Other Co 3')
        other_cp = make_co_packer(other_company, 'Other CP 2')
        other_po = make_production_po(other_company, other_cp)
        r = self.client.post(delete_url(other_po.pk), HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertEqual(r.status_code, 404)


# ---------------------------------------------------------------------------
# Phase D2 — COMPLETE status (model + save endpoint)
# ---------------------------------------------------------------------------

class CompleteStatusModelTest(POMixin):

    def test_complete_status_choice_exists(self):
        from apps.production.models import ProductionPO
        values = [choice[0] for choice in ProductionPO.Status.choices]
        self.assertIn('complete', values)

    def test_complete_status_requires_external_po_number(self):
        from django.core.exceptions import ValidationError
        po = ProductionPO(
            company=self.company,
            co_packer=self.cp1,
            year=2026, month=7,
            status='complete',
            external_po_number='',
            generated_by_algorithm=False,
        )
        with self.assertRaises(ValidationError) as ctx:
            po.full_clean()
        self.assertIn('external_po_number', ctx.exception.message_dict)

    def test_complete_status_valid_with_po_number(self):
        from django.core.exceptions import ValidationError
        po = ProductionPO(
            company=self.company,
            co_packer=self.cp1,
            year=2026, month=7,
            status='complete',
            external_po_number='PO-999',
            generated_by_algorithm=False,
        )
        try:
            po.full_clean()
        except ValidationError as e:
            if 'external_po_number' in e.message_dict:
                self.fail('full_clean() raised ValidationError for external_po_number unexpectedly')

    def test_actual_status_still_requires_external_po_number(self):
        from django.core.exceptions import ValidationError
        po = ProductionPO(
            company=self.company,
            co_packer=self.cp1,
            year=2026, month=7,
            status='actual',
            external_po_number='',
            generated_by_algorithm=False,
        )
        with self.assertRaises(ValidationError) as ctx:
            po.full_clean()
        self.assertIn('external_po_number', ctx.exception.message_dict)


class CompleteStatusSaveTest(POMixin):

    def _base_payload(self, **overrides):
        po = {
            'po_id': None,
            'co_packer_id': self.cp1.pk,
            'status': 'projected',
            'external_po_number': '',
            'notes': '',
            'lines': [{'item_id': self.item1.pk, 'batch_count': 1}],
        }
        po.update(overrides)
        return {'year': 2026, 'month': 7, 'pos': [po]}

    def test_save_endpoint_accepts_complete_status(self):
        r = post_save(self.client, self._base_payload(
            status='complete', external_po_number='PO-DONE'
        ))
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json()['success'])
        po = ProductionPO.objects.get(company=self.company, year=2026, month=7)
        self.assertEqual(po.status, 'complete')
        self.assertEqual(po.external_po_number, 'PO-DONE')

    def test_save_endpoint_rejects_complete_without_po_number(self):
        r = post_save(self.client, self._base_payload(
            status='complete', external_po_number=''
        ))
        self.assertEqual(r.status_code, 400)
        self.assertIn('PO number', r.json()['error'])

    def test_save_endpoint_rejects_invalid_status(self):
        r = post_save(self.client, self._base_payload(status='bogus'))
        self.assertEqual(r.status_code, 400)
        self.assertIn('Invalid status', r.json()['error'])


# ---------------------------------------------------------------------------
# Phase D2 — Production POs tab list view
# ---------------------------------------------------------------------------

class ProductionPOsTabTest(POMixin):

    def _get_tab(self, params=None):
        url = reverse('production_home') + '?tab=production_pos'
        if params:
            url += '&' + '&'.join(f'{k}={v}' for k, v in params.items())
        return self.client.get(url)

    def test_production_pos_tab_renders(self):
        r = self._get_tab()
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'Production POs')

    def test_production_pos_tab_requires_permission(self):
        sales_user = User.objects.create_user(username='sales_tab', password='pass', company=self.company)
        sales_user.roles.set([Role.objects.get(codename='sales_manager')])
        c = Client()
        c.login(username='sales_tab', password='pass')
        r = c.get(reverse('production_home') + '?tab=production_pos')
        self.assertEqual(r.status_code, 403)

    def test_production_pos_tab_default_active_filter_excludes_complete(self):
        make_production_po(self.company, self.cp1, year=2026, month=7, status='projected')
        make_production_po(self.company, self.cp1, year=2026, month=8, status='actual', external_po_number='PO-1')
        make_production_po(self.company, self.cp1, year=2026, month=9, status='complete', external_po_number='PO-2')
        r = self._get_tab()
        self.assertEqual(len(r.context['production_pos_list']), 2)
        statuses = [po.status for po in r.context['production_pos_list']]
        self.assertNotIn('complete', statuses)

    def test_production_pos_tab_complete_filter_shows_only_complete(self):
        make_production_po(self.company, self.cp1, year=2026, month=7, status='projected')
        make_production_po(self.company, self.cp1, year=2026, month=8, status='complete', external_po_number='PO-C')
        r = self._get_tab({'filter_pos_status': 'complete'})
        self.assertEqual(len(r.context['production_pos_list']), 1)
        self.assertEqual(r.context['production_pos_list'][0].status, 'complete')

    def test_production_pos_tab_all_filter_shows_all(self):
        make_production_po(self.company, self.cp1, year=2026, month=7, status='projected')
        make_production_po(self.company, self.cp1, year=2026, month=8, status='actual', external_po_number='PO-A')
        make_production_po(self.company, self.cp1, year=2026, month=9, status='complete', external_po_number='PO-C')
        r = self._get_tab({'filter_pos_status': 'all'})
        self.assertEqual(len(r.context['production_pos_list']), 3)

    def test_production_pos_tab_period_filter(self):
        make_production_po(self.company, self.cp1, year=2026, month=7, status='projected')
        make_production_po(self.company, self.cp1, year=2026, month=8, status='projected')
        r = self._get_tab({'filter_pos_period': '2026-08', 'filter_pos_status': 'all'})
        self.assertEqual(len(r.context['production_pos_list']), 1)
        self.assertEqual(r.context['production_pos_list'][0].month, 8)

    def test_production_pos_tab_co_packer_filter(self):
        make_production_po(self.company, self.cp1, year=2026, month=7, status='projected')
        make_production_po(self.company, self.cp2, year=2026, month=7, status='projected')
        r = self._get_tab({'filter_pos_co_packer': str(self.cp1.pk), 'filter_pos_status': 'all'})
        self.assertEqual(len(r.context['production_pos_list']), 1)
        self.assertEqual(r.context['production_pos_list'][0].co_packer_id, self.cp1.pk)

    def test_production_pos_tab_sort_order_date_asc(self):
        make_production_po(self.company, self.cp1, year=2026, month=9, status='projected')
        make_production_po(self.company, self.cp1, year=2026, month=7, status='projected')
        make_production_po(self.company, self.cp1, year=2026, month=8, status='projected')
        r = self._get_tab({'filter_pos_status': 'all'})
        months = [po.month for po in r.context['production_pos_list']]
        self.assertEqual(months, [7, 8, 9])

    def test_production_pos_tab_sort_empty_po_number_last(self):
        po_no_num = make_production_po(self.company, self.cp1, year=2026, month=7,
                                       status='projected', external_po_number='')
        po_with_num = make_production_po(self.company, self.cp1, year=2026, month=7,
                                         status='actual', external_po_number='PO-AAA')
        r = self._get_tab({'filter_pos_status': 'all'})
        result_pks = [po.pk for po in r.context['production_pos_list']]
        self.assertEqual(result_pks.index(po_with_num.pk), 0)
        self.assertEqual(result_pks.index(po_no_num.pk), 1)

    def test_production_pos_tab_empty_state_no_pos(self):
        r = self._get_tab()
        self.assertFalse(r.context['has_any_pos'])
        self.assertContains(r, 'No production POs yet')

    def test_production_pos_tab_empty_state_filters_match_nothing(self):
        make_production_po(self.company, self.cp1, year=2026, month=7, status='projected')
        r = self._get_tab({'filter_pos_period': '2025-01'})
        self.assertTrue(r.context['has_any_pos'])
        self.assertEqual(len(r.context['production_pos_list']), 0)

    def test_production_pos_tab_scoped_to_company(self):
        other_company = make_company('Other Co Tab')
        other_cp = make_co_packer(other_company, 'Other Packer Tab')
        make_production_po(other_company, other_cp, year=2026, month=7, status='projected')
        make_production_po(self.company, self.cp1, year=2026, month=7, status='projected')
        r = self._get_tab({'filter_pos_status': 'all'})
        self.assertEqual(len(r.context['production_pos_list']), 1)
        self.assertEqual(r.context['production_pos_list'][0].co_packer.company, self.company)


# ---------------------------------------------------------------------------
# Phase D2 — single-PO modal data endpoint
# ---------------------------------------------------------------------------

class SingleModalDataTest(POMixin):

    def setUp(self):
        super().setUp()
        self.po = make_production_po(self.company, self.cp1, year=2026, month=7,
                                     status='projected')
        make_po_line(self.po, self.item1, batch_count=4)

    def _single_url(self, po_pk=None):
        return reverse('production_po_modal_data_single', kwargs={'po_pk': po_pk or self.po.pk})

    def test_modal_data_single_returns_one_po(self):
        r = self.client.get(self._single_url())
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertEqual(len(data['saved_pos']), 1)
        self.assertEqual(data['saved_pos'][0]['po_id'], self.po.pk)

    def test_modal_data_single_returns_mode_single(self):
        r = self.client.get(self._single_url())
        self.assertEqual(r.json()['mode'], 'single')

    def test_modal_data_single_returns_year_month(self):
        r = self.client.get(self._single_url())
        data = r.json()
        self.assertEqual(data['year'], 2026)
        self.assertEqual(data['month'], 7)

    def test_modal_data_single_returns_lines(self):
        r = self.client.get(self._single_url())
        lines = r.json()['saved_pos'][0]['lines']
        self.assertEqual(len(lines), 1)
        self.assertEqual(lines[0]['batch_count'], 4)

    def test_modal_data_single_returns_co_packers(self):
        r = self.client.get(self._single_url())
        data = r.json()
        self.assertIn('co_packers', data)
        self.assertIn('items_by_co_packer', data)

    def test_modal_data_single_returns_403_without_permission(self):
        sales_user = User.objects.create_user(username='sales_s', password='pass', company=self.company)
        sales_user.roles.set([Role.objects.get(codename='sales_manager')])
        c = Client()
        c.login(username='sales_s', password='pass')
        r = c.get(self._single_url())
        self.assertEqual(r.status_code, 403)

    def test_modal_data_single_returns_404_for_wrong_company(self):
        other_company = make_company('Other Co Single')
        other_cp = make_co_packer(other_company, 'Other Packer Single')
        other_po = make_production_po(other_company, other_cp)
        r = self.client.get(self._single_url(po_pk=other_po.pk))
        self.assertEqual(r.status_code, 404)


# ---------------------------------------------------------------------------
# Post-save redirect target (Forecast grid, not Inventory)
# ---------------------------------------------------------------------------

class POCreationRedirectTest(POMixin):

    def test_production_po_creation_redirects_to_forecast(self):
        """A PO entered from the Forecast grid returns the user to the Forecast
        tab (refreshed), not the Inventory tab.

        The post-save navigation is client-side (the save endpoint returns
        JSON), so we assert the production page renders the forecast-tab
        redirect target that the save handler navigates to for forecast-grid
        (month-mode) entries.
        """
        payload = {
            'year': 2026, 'month': 7,
            'pos': [{
                'po_id': None,
                'co_packer_id': self.cp1.pk,
                'status': 'projected',
                'external_po_number': '',
                'notes': '',
                'lines': [{'item_id': self.item1.pk, 'batch_count': 2}],
            }],
        }
        resp = post_save(self.client, payload)
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json().get('success'))
        self.assertEqual(ProductionPO.objects.count(), 1)

        page = self.client.get(reverse('production_home'))
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, '?tab=forecast')
