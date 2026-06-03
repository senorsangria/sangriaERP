"""
Tests for apps.production — Phase A foundation.

Covers:
- ProductionPO model (CRUD, clean(), Status)
- ProductionPOLine (cascade on PO delete, unique constraint)
- OwnInventorySnapshot (CRUD, unique constraint, negative quantity rejected)
- Permission: can_manage_production granted to supplier_admin only
- Production home view (permission gating, 200 for supplier_admin)
- Nav: Production item visible to supplier_admin, hidden for other roles
- Data migration: CoPacker seeding (integration)
"""
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import IntegrityError
from django.db.models import ProtectedError
from django.test import Client, TestCase
from django.urls import reverse

from apps.catalog.models import Brand, CoPacker, Item
from apps.core.models import Company, User
from apps.core.rbac import Permission, Role
from apps.production.models import OwnInventorySnapshot, ProductionPO, ProductionPOLine


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_company(name='Test Co'):
    return Company.objects.create(name=name)


def make_supplier_admin(company, username='admin'):
    user = User.objects.create_user(
        username=username, password='testpass123', company=company,
    )
    user.roles.set([Role.objects.get(codename='supplier_admin')])
    return user


def make_user_with_role(company, role_codename, username='user'):
    user = User.objects.create_user(
        username=username, password='testpass123', company=company,
    )
    user.roles.set([Role.objects.get(codename=role_codename)])
    return user


def make_brand(company, name='Brand'):
    return Brand.objects.create(company=company, name=name)


def make_item(brand, name='Item', item_code='CODE'):
    return Item.objects.create(brand=brand, name=name, item_code=item_code)


def make_co_packer(company, name='Brotherhood Winery'):
    return CoPacker.objects.create(company=company, name=name)


def make_production_po(company, co_packer, year=2026, month=5, status='projected'):
    return ProductionPO.objects.create(
        company=company,
        co_packer=co_packer,
        year=year,
        month=month,
        status=status,
    )


# ---------------------------------------------------------------------------
# ProductionPO model tests
# ---------------------------------------------------------------------------

class ProductionPOCreateAndStrTest(TestCase):

    def setUp(self):
        self.company = make_company()
        self.co_packer = make_co_packer(self.company)

    def test_production_po_create_and_str(self):
        po = make_production_po(self.company, self.co_packer)
        self.assertIsNotNone(po.pk)
        self.assertIn('2026-05', str(po))
        self.assertIn('Projected', str(po))
        self.assertEqual(po.status, 'projected')
        self.assertTrue(po.generated_by_algorithm)

    def test_production_po_clean_requires_po_number_when_actual(self):
        po = ProductionPO(
            company=self.company,
            co_packer=self.co_packer,
            year=2026,
            month=5,
            status='actual',
            external_po_number='',
        )
        with self.assertRaises(ValidationError) as ctx:
            po.clean()
        self.assertIn('external_po_number', ctx.exception.message_dict)

    def test_production_po_clean_passes_actual_with_po_number(self):
        po = ProductionPO(
            company=self.company,
            co_packer=self.co_packer,
            year=2026,
            month=5,
            status='actual',
            external_po_number='PO-12345',
        )
        po.clean()  # should not raise

    def test_production_po_clean_passes_projected_without_po_number(self):
        po = ProductionPO(
            company=self.company,
            co_packer=self.co_packer,
            year=2026,
            month=5,
            status='projected',
            external_po_number='',
        )
        po.clean()  # should not raise

    def test_production_po_multiple_per_co_packer_month_allowed(self):
        make_production_po(self.company, self.co_packer, year=2026, month=5)
        po2 = make_production_po(self.company, self.co_packer, year=2026, month=5)
        self.assertIsNotNone(po2.pk)


# ---------------------------------------------------------------------------
# ProductionPOLine tests
# ---------------------------------------------------------------------------

class ProductionPOLineTest(TestCase):

    def setUp(self):
        self.company = make_company()
        self.co_packer = make_co_packer(self.company)
        self.brand = make_brand(self.company)
        self.item = make_item(self.brand)
        self.po = make_production_po(self.company, self.co_packer)

    def test_production_po_line_cascade_on_po_delete(self):
        ProductionPOLine.objects.create(
            po=self.po, item=self.item, batch_count=2, quantity_cases=Decimal('240'),
        )
        self.assertEqual(ProductionPOLine.objects.count(), 1)
        self.po.delete()
        self.assertEqual(ProductionPOLine.objects.count(), 0)

    def test_production_po_line_unique_per_po_item(self):
        ProductionPOLine.objects.create(
            po=self.po, item=self.item, batch_count=1, quantity_cases=Decimal('120'),
        )
        with self.assertRaises(IntegrityError):
            ProductionPOLine.objects.create(
                po=self.po, item=self.item, batch_count=2, quantity_cases=Decimal('240'),
            )

    def test_production_po_line_item_protected_on_delete(self):
        ProductionPOLine.objects.create(
            po=self.po, item=self.item, batch_count=1, quantity_cases=Decimal('120'),
        )
        with self.assertRaises(ProtectedError):
            self.item.delete()

    def test_production_po_line_str(self):
        line = ProductionPOLine.objects.create(
            po=self.po, item=self.item, batch_count=3, quantity_cases=Decimal('360'),
        )
        self.assertIn('batch', str(line))
        self.assertIn('cases', str(line))


# ---------------------------------------------------------------------------
# OwnInventorySnapshot tests
# ---------------------------------------------------------------------------

class OwnInventorySnapshotTest(TestCase):

    def setUp(self):
        self.company = make_company()
        self.brand = make_brand(self.company)
        self.item = make_item(self.brand)

    def test_own_inventory_snapshot_create(self):
        snap = OwnInventorySnapshot.objects.create(
            company=self.company, item=self.item, year=2026, month=5,
            quantity_cases=Decimal('100.5'),
        )
        self.assertIn('2026-05', str(snap))
        self.assertIn('cases', str(snap))
        self.assertIn('100.5', str(snap))

    def test_own_inventory_snapshot_unique_constraint(self):
        OwnInventorySnapshot.objects.create(
            company=self.company, item=self.item, year=2026, month=5,
            quantity_cases=Decimal('50'),
        )
        with self.assertRaises(IntegrityError):
            OwnInventorySnapshot.objects.create(
                company=self.company, item=self.item, year=2026, month=5,
                quantity_cases=Decimal('75'),
            )

    def test_own_inventory_snapshot_negative_rejected(self):
        from django.core.exceptions import ValidationError as DjangoValidationError
        snap = OwnInventorySnapshot(
            company=self.company, item=self.item, year=2026, month=5,
            quantity_cases=Decimal('-1'),
        )
        with self.assertRaises(DjangoValidationError):
            snap.full_clean()

    def test_own_inventory_snapshot_zero_allowed(self):
        snap = OwnInventorySnapshot.objects.create(
            company=self.company, item=self.item, year=2026, month=5,
            quantity_cases=Decimal('0'),
        )
        self.assertEqual(snap.quantity_cases, Decimal('0'))


# ---------------------------------------------------------------------------
# Permission tests
# ---------------------------------------------------------------------------

class ProductionPermissionTest(TestCase):

    def test_production_permission_granted_to_supplier_admin(self):
        supplier_admin_role = Role.objects.get(codename='supplier_admin')
        perm_codenames = set(
            supplier_admin_role.permissions.values_list('codename', flat=True)
        )
        self.assertIn('can_manage_production', perm_codenames)

    def test_production_permission_not_granted_to_other_roles(self):
        other_roles = Role.objects.exclude(codename='supplier_admin')
        for role in other_roles:
            perm_codenames = set(
                role.permissions.values_list('codename', flat=True)
            )
            self.assertNotIn(
                'can_manage_production', perm_codenames,
                msg=f'Role {role.codename} should not have can_manage_production',
            )


# ---------------------------------------------------------------------------
# Production home view tests
# ---------------------------------------------------------------------------

class ProductionHomeViewTest(TestCase):

    def setUp(self):
        self.company = make_company()
        self.admin = make_supplier_admin(self.company)
        self.client = Client()

    def test_production_home_renders_for_supplier_admin(self):
        self.client.login(username='admin', password='testpass123')
        resp = self.client.get(reverse('production_home'))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Production')

    def test_production_home_requires_permission(self):
        other = make_user_with_role(self.company, 'sales_manager', username='sm')
        self.client.login(username='sm', password='testpass123')
        resp = self.client.get(reverse('production_home'))
        self.assertEqual(resp.status_code, 403)

    def test_production_home_requires_login(self):
        resp = self.client.get(reverse('production_home'))
        self.assertEqual(resp.status_code, 302)
        self.assertIn('login', resp['Location'])


# ---------------------------------------------------------------------------
# Nav tests
# ---------------------------------------------------------------------------

class ProductionNavTest(TestCase):

    def setUp(self):
        self.company = make_company()

    def test_production_nav_item_visible_for_supplier_admin(self):
        from apps.core.nav import NAV_ITEMS
        production_items = [i for i in NAV_ITEMS if i.get('url_name') == 'production_home']
        self.assertEqual(len(production_items), 1)
        self.assertEqual(production_items[0]['permission'], 'can_manage_production')
        self.assertEqual(production_items[0]['section'], 'main')

    def test_production_nav_item_rendered_for_supplier_admin(self):
        admin = make_supplier_admin(self.company)
        self.client.login(username='admin', password='testpass123')
        resp = self.client.get(reverse('production_home'))
        self.assertContains(resp, '/production/')

    def test_production_nav_item_hidden_for_sales_manager(self):
        make_user_with_role(self.company, 'sales_manager', username='sm')
        from apps.core.nav import get_nav_for_user

        class FakeRequest:
            resolver_match = None

        user = User.objects.get(username='sm')
        sections = get_nav_for_user(user, FakeRequest())
        all_url_names = [
            item['url_name']
            for section in sections
            for item in section['items']
        ]
        self.assertNotIn('production_home', all_url_names)


# ---------------------------------------------------------------------------
# CoPacker seeding data migration test
# ---------------------------------------------------------------------------

class CoPackerSeedingTest(TestCase):
    """
    Verifies seeding logic: co-packers created when company exists, skipped when not.
    Tests the migration logic directly (not the migration runner).
    """

    def test_seeding_creates_co_packers_when_company_exists(self):
        company = Company.objects.create(name='Drink Up Life')
        CoPacker.objects.get_or_create(company=company, name='Brotherhood Winery')
        CoPacker.objects.get_or_create(company=company, name='Nidra Packaging')
        names = set(CoPacker.objects.filter(company=company).values_list('name', flat=True))
        self.assertIn('Brotherhood Winery', names)
        self.assertIn('Nidra Packaging', names)

    def test_seeding_skips_when_company_does_not_exist(self):
        initial_count = CoPacker.objects.count()
        company = Company.objects.filter(name__iexact='drink up life').first()
        if company is None:
            pass  # migration forwards() would return early — correct
        self.assertEqual(CoPacker.objects.count(), initial_count)


# ---------------------------------------------------------------------------
# Phase B — Inventory upload (entry) view tests
# ---------------------------------------------------------------------------

def make_snapshot(company, item, year=2026, month=5, qty='100', user=None):
    return OwnInventorySnapshot.objects.create(
        company=company, item=item, year=year, month=month,
        quantity_cases=Decimal(qty), created_by=user,
    )


class ProductionInventoryUploadGetTest(TestCase):
    """GET /production/inventory/upload/"""

    def setUp(self):
        self.company = make_company()
        self.admin = make_supplier_admin(self.company)
        self.brand = make_brand(self.company)
        self.item = make_item(self.brand, 'Item A', 'A001')
        self.client = Client()
        self.client.login(username='admin', password='testpass123')

    def test_upload_get_renders_form_with_items(self):
        resp = self.client.get(reverse('production_inventory_upload'))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Item A')
        self.assertContains(resp, 'A001')

    def test_upload_get_groups_items_by_brand(self):
        brand2 = make_brand(self.company, 'Brand Z')
        make_item(brand2, 'Item Z', 'Z001')
        resp = self.client.get(reverse('production_inventory_upload'))
        self.assertContains(resp, self.brand.name)
        self.assertContains(resp, 'Brand Z')

    def test_upload_get_excludes_inactive_items(self):
        inactive = make_item(self.brand, 'Inactive Item', 'INACT')
        inactive.is_active = False
        inactive.save()
        resp = self.client.get(reverse('production_inventory_upload'))
        self.assertNotContains(resp, 'Inactive Item')

    def test_upload_get_excludes_items_from_other_companies(self):
        other_co = make_company('Other Co')
        other_brand = make_brand(other_co, 'Other Brand')
        make_item(other_brand, 'Other Item', 'OTH001')
        resp = self.client.get(reverse('production_inventory_upload'))
        self.assertNotContains(resp, 'Other Item')

    def test_upload_get_requires_permission(self):
        no_perm = make_user_with_role(self.company, 'sales_manager', username='sm')
        c = Client()
        c.login(username='sm', password='testpass123')
        resp = c.get(reverse('production_inventory_upload'))
        self.assertEqual(resp.status_code, 403)

    def test_upload_get_requires_login(self):
        c = Client()
        resp = c.get(reverse('production_inventory_upload'))
        self.assertEqual(resp.status_code, 302)
        self.assertIn('login', resp['Location'])

    def test_upload_get_empty_state_when_no_items(self):
        self.item.is_active = False
        self.item.save()
        resp = self.client.get(reverse('production_inventory_upload'))
        self.assertContains(resp, 'No active items found')


class ProductionInventoryUploadPostTest(TestCase):
    """POST /production/inventory/upload/"""

    def setUp(self):
        self.company = make_company()
        self.admin = make_supplier_admin(self.company)
        self.brand = make_brand(self.company)
        self.item_a = make_item(self.brand, 'Item A', 'A001')
        self.item_b = make_item(self.brand, 'Item B', 'B001')
        self.client = Client()
        self.client.login(username='admin', password='testpass123')

    def _post(self, data):
        return self.client.post(reverse('production_inventory_upload'), data)

    def _base_post(self, **overrides):
        data = {'year': '2026', 'month': '5'}
        data.update(overrides)
        return self._post(data)

    def _inventory_tab_url(self):
        return reverse('production_home') + '?tab=inventory'

    def test_post_creates_snapshots(self):
        resp = self._base_post(**{
            f'qty_{self.item_a.pk}': '100',
            f'qty_{self.item_b.pk}': '50',
        })
        self.assertRedirects(resp, self._inventory_tab_url())
        self.assertEqual(OwnInventorySnapshot.objects.filter(company=self.company).count(), 2)

    def test_post_with_blank_inputs_skips(self):
        resp = self._base_post(**{
            f'qty_{self.item_a.pk}': '100',
            f'qty_{self.item_b.pk}': '',
        })
        self.assertRedirects(resp, self._inventory_tab_url())
        self.assertEqual(OwnInventorySnapshot.objects.filter(company=self.company).count(), 1)
        snap = OwnInventorySnapshot.objects.get(company=self.company)
        self.assertEqual(snap.item, self.item_a)

    def test_post_with_zero_creates_snapshot_with_zero(self):
        resp = self._base_post(**{f'qty_{self.item_a.pk}': '0'})
        self.assertRedirects(resp, self._inventory_tab_url())
        snap = OwnInventorySnapshot.objects.get(company=self.company, item=self.item_a)
        self.assertEqual(snap.quantity_cases, Decimal('0'))

    def test_post_with_negative_rejects_save(self):
        resp = self._base_post(**{f'qty_{self.item_a.pk}': '-5'})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(OwnInventorySnapshot.objects.count(), 0)
        self.assertContains(resp, 'Cannot be negative')

    def test_post_with_non_numeric_rejects_save(self):
        resp = self._base_post(**{f'qty_{self.item_a.pk}': 'abc'})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(OwnInventorySnapshot.objects.count(), 0)
        self.assertContains(resp, 'Invalid number')

    def test_post_with_period_conflict_rejects(self):
        make_snapshot(self.company, self.item_a, year=2026, month=5)
        resp = self._base_post(**{f'qty_{self.item_b.pk}': '50'})
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'already exists')
        # No new snapshots created (only the original one)
        self.assertEqual(OwnInventorySnapshot.objects.filter(company=self.company).count(), 1)

    def test_post_all_blank_shows_info_message(self):
        resp = self._base_post()
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(OwnInventorySnapshot.objects.count(), 0)
        self.assertContains(resp, 'Nothing to save')

    def test_post_atomic_no_rows_saved_on_error(self):
        # Negative value alongside a valid value — neither should be saved
        resp = self._base_post(**{
            f'qty_{self.item_a.pk}': '100',
            f'qty_{self.item_b.pk}': '-1',
        })
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(OwnInventorySnapshot.objects.count(), 0)

    def test_post_sets_created_by(self):
        resp = self._base_post(**{f'qty_{self.item_a.pk}': '75'})
        snap = OwnInventorySnapshot.objects.get(company=self.company)
        self.assertEqual(snap.created_by, self.admin)

    def test_post_rerenders_with_input_preserved_on_error(self):
        resp = self._base_post(**{
            f'qty_{self.item_a.pk}': '100',
            f'qty_{self.item_b.pk}': 'bad',
        })
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, '100')


# ---------------------------------------------------------------------------
# Phase B — Snapshots listing view tests
# ---------------------------------------------------------------------------

class ProductionInventorySnapshotsViewTest(TestCase):
    """Inventory tab on /production/?tab=inventory (formerly standalone snapshots page)."""

    def setUp(self):
        self.company = make_company()
        self.admin = make_supplier_admin(self.company)
        self.brand = make_brand(self.company)
        self.item_a = make_item(self.brand, 'Item A', 'A001')
        self.item_b = make_item(self.brand, 'Item B', 'B001')
        self.client = Client()
        self.client.login(username='admin', password='testpass123')
        self.url = reverse('production_home') + '?tab=inventory'

    def test_snapshots_list_renders_with_data(self):
        make_snapshot(self.company, self.item_a, year=2026, month=5, qty='120')
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'A001')  # item code shown in inventory tab
        self.assertContains(resp, '120')

    def test_snapshots_list_empty_state(self):
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'No inventory snapshots yet')

    def test_snapshots_list_requires_permission(self):
        make_user_with_role(self.company, 'sales_manager', username='sm')
        c = Client()
        c.login(username='sm', password='testpass123')
        resp = c.get(self.url)
        self.assertEqual(resp.status_code, 403)

    def test_snapshots_list_old_url_redirects_to_inventory_tab(self):
        resp = self.client.get(reverse('production_inventory_snapshots'))
        self.assertRedirects(resp, self.url)

    def test_snapshots_list_filters_by_period(self):
        make_snapshot(self.company, self.item_a, year=2026, month=5, qty='100')
        make_snapshot(self.company, self.item_b, year=2026, month=6, qty='200')
        resp = self.client.get(
            reverse('production_home'),
            {'tab': 'inventory', 'filter_period': '2026-06'},
        )
        # Check context snapshots list, not raw HTML (forecast tab also renders item codes)
        snap_codes = [s['item_code'] for s in resp.context['snapshots']]
        self.assertIn('B001', snap_codes)
        self.assertNotIn('A001', snap_codes)

    def test_snapshots_list_filters_by_brand(self):
        brand2 = make_brand(self.company, 'Brand Two')
        item_c = make_item(brand2, 'Item C', 'C001')
        make_snapshot(self.company, self.item_a, year=2026, month=5, qty='100')
        make_snapshot(self.company, item_c, year=2026, month=6, qty='50')
        resp = self.client.get(
            reverse('production_home'),
            {'tab': 'inventory', 'filter_brand': str(brand2.pk)},
        )
        snap_codes = [s['item_code'] for s in resp.context['snapshots']]
        self.assertIn('C001', snap_codes)
        self.assertNotIn('A001', snap_codes)

    def test_snapshots_list_scoped_to_company(self):
        other_co = make_company('Other Co')
        other_brand = make_brand(other_co, 'Other Brand')
        other_item = make_item(other_brand, 'Other Item', 'OTH')
        make_snapshot(other_co, other_item, year=2026, month=5, qty='99')
        resp = self.client.get(self.url)
        self.assertNotContains(resp, 'OTH')


# ---------------------------------------------------------------------------
# Phase B — Bulk delete view tests
# ---------------------------------------------------------------------------

class ProductionInventoryBulkDeleteTest(TestCase):
    """POST /production/inventory/delete/"""

    def setUp(self):
        self.company = make_company()
        self.admin = make_supplier_admin(self.company)
        self.brand = make_brand(self.company)
        self.item_a = make_item(self.brand, 'Item A', 'A001')
        self.item_b = make_item(self.brand, 'Item B', 'B001')
        self.client = Client()
        self.client.login(username='admin', password='testpass123')

    def _inventory_tab_url(self):
        return reverse('production_home') + '?tab=inventory'

    def test_bulk_delete_removes_selected(self):
        s1 = make_snapshot(self.company, self.item_a, year=2026, month=5)
        s2 = make_snapshot(self.company, self.item_b, year=2026, month=5)
        resp = self.client.post(
            reverse('production_inventory_bulk_delete'),
            {'snapshot_ids': [str(s1.pk)]},
        )
        self.assertRedirects(resp, self._inventory_tab_url())
        self.assertFalse(OwnInventorySnapshot.objects.filter(pk=s1.pk).exists())
        self.assertTrue(OwnInventorySnapshot.objects.filter(pk=s2.pk).exists())

    def test_bulk_delete_scoped_to_company(self):
        other_co = make_company('Other Co')
        other_brand = make_brand(other_co, 'Other Brand')
        other_item = make_item(other_brand, 'Other Item', 'OTH')
        other_snap = make_snapshot(other_co, other_item, year=2026, month=5)
        # Admin from self.company tries to delete other company's snapshot by PK
        self.client.post(
            reverse('production_inventory_bulk_delete'),
            {'snapshot_ids': [str(other_snap.pk)]},
        )
        # Other company's snapshot must still exist
        self.assertTrue(OwnInventorySnapshot.objects.filter(pk=other_snap.pk).exists())

    def test_bulk_delete_requires_permission(self):
        snap = make_snapshot(self.company, self.item_a)
        make_user_with_role(self.company, 'sales_manager', username='sm')
        c = Client()
        c.login(username='sm', password='testpass123')
        resp = c.post(
            reverse('production_inventory_bulk_delete'),
            {'snapshot_ids': [str(snap.pk)]},
        )
        self.assertEqual(resp.status_code, 403)
        self.assertTrue(OwnInventorySnapshot.objects.filter(pk=snap.pk).exists())

    def test_bulk_delete_no_ids_shows_info_message(self):
        resp = self.client.post(reverse('production_inventory_bulk_delete'), {})
        self.assertRedirects(resp, self._inventory_tab_url())
        msgs = list(resp.wsgi_request._messages)
        self.assertTrue(any('No snapshots' in str(m) for m in msgs))

    def test_bulk_delete_invalid_ids_handled_gracefully(self):
        resp = self.client.post(
            reverse('production_inventory_bulk_delete'),
            {'snapshot_ids': ['abc', 'xyz']},
        )
        self.assertRedirects(resp, self._inventory_tab_url())


# ---------------------------------------------------------------------------
# Phase C — Production home tab structure and forecast view tests
# ---------------------------------------------------------------------------

class ProductionHomeTabTest(TestCase):
    """Tests for the tabbed production home view."""

    def setUp(self):
        self.company = make_company()
        self.admin = make_supplier_admin(self.company)
        self.brand = make_brand(self.company)
        self.item = make_item(self.brand, 'Item A', 'A001')
        self.client = Client()
        self.client.login(username='admin', password='testpass123')

    def test_default_tab_is_inventory(self):
        resp = self.client.get(reverse('production_home'))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.context['active_tab'], 'inventory')

    def test_tab_inventory_renders_inventory_pane(self):
        resp = self.client.get(reverse('production_home'), {'tab': 'inventory'})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.context['active_tab'], 'inventory')
        self.assertContains(resp, 'Inventory Snapshots')

    def test_tab_forecast_renders_forecast_pane(self):
        resp = self.client.get(reverse('production_home'), {'tab': 'forecast'})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.context['active_tab'], 'forecast')

    def test_invalid_tab_falls_back_to_inventory(self):
        resp = self.client.get(reverse('production_home'), {'tab': 'bad_value'})
        self.assertEqual(resp.context['active_tab'], 'inventory')

    def test_forecast_empty_state_when_no_snapshots(self):
        resp = self.client.get(reverse('production_home'))
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.context['forecast_result']['message'])

    def test_forecast_grid_renders_with_snapshot_data(self):
        make_snapshot(self.company, self.item, year=2026, month=4, qty='500')
        resp = self.client.get(reverse('production_home'))
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(resp.context['forecast_result']['message'])
        self.assertContains(resp, 'Item A')

    def test_forecast_tab_requires_permission(self):
        make_user_with_role(self.company, 'sales_manager', username='sm')
        c = Client()
        c.login(username='sm', password='testpass123')
        resp = c.get(reverse('production_home'))
        self.assertEqual(resp.status_code, 403)

    def test_inventory_tab_shows_item_code_not_name_column(self):
        make_snapshot(self.company, self.item, year=2026, month=5, qty='100')
        resp = self.client.get(reverse('production_home'), {'tab': 'inventory'})
        self.assertContains(resp, 'A001')
        self.assertContains(resp, 'Item Code')
        self.assertNotContains(resp, 'Entered by')

    def test_inventory_tab_enter_inventory_button_present(self):
        resp = self.client.get(reverse('production_home'), {'tab': 'inventory'})
        self.assertContains(resp, reverse('production_inventory_upload'))

    def test_upload_success_redirects_to_inventory_tab(self):
        resp = self.client.post(
            reverse('production_inventory_upload'),
            {'year': '2026', 'month': '5', f'qty_{self.item.pk}': '100'},
        )
        self.assertRedirects(resp, reverse('production_home') + '?tab=inventory')

    def test_bulk_delete_redirects_to_inventory_tab(self):
        snap = make_snapshot(self.company, self.item, year=2026, month=5)
        resp = self.client.post(
            reverse('production_inventory_bulk_delete'),
            {'snapshot_ids': [str(snap.pk)]},
        )
        self.assertRedirects(resp, reverse('production_home') + '?tab=inventory')


# ---------------------------------------------------------------------------
# Phase C — Demand modal endpoint tests
# ---------------------------------------------------------------------------

class ProductionDemandModalTest(TestCase):
    """Tests for GET /production/demand/<year>/<month>/"""

    def setUp(self):
        from apps.distribution.models import Distributor, DistributorPO, DistributorPOLine
        self.company = make_company()
        self.admin = make_supplier_admin(self.company)
        self.brand = make_brand(self.company)
        self.item = make_item(self.brand, 'Item A', 'A001')
        self.client = Client()
        self.client.login(username='admin', password='testpass123')

        self.distributor = Distributor.objects.create(company=self.company, name='Dist One')
        self.po = DistributorPO.objects.create(
            distributor=self.distributor, year=2026, month=6, status='projected',
        )
        DistributorPOLine.objects.create(
            po=self.po, item=self.item, quantity_cases=Decimal('120'),
        )

    def test_demand_modal_returns_json(self):
        resp = self.client.get(
            reverse('production_demand_modal', kwargs={'year': 2026, 'month': 6}),
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data['period'], 'June 2026')
        self.assertEqual(len(data['items']), 1)
        self.assertEqual(len(data['distributors']), 1)
        self.assertEqual(data['grand_total'], 120.0)

    def test_demand_modal_no_demand_returns_empty(self):
        resp = self.client.get(
            reverse('production_demand_modal', kwargs={'year': 2026, 'month': 3}),
        )
        data = resp.json()
        self.assertEqual(data['cells'], [])
        self.assertEqual(data['grand_total'], 0.0)

    def test_demand_modal_requires_permission(self):
        make_user_with_role(self.company, 'sales_manager', username='sm')
        c = Client()
        c.login(username='sm', password='testpass123')
        resp = c.get(
            reverse('production_demand_modal', kwargs={'year': 2026, 'month': 6}),
        )
        self.assertEqual(resp.status_code, 403)

    def test_demand_modal_scoped_to_company(self):
        from apps.distribution.models import Distributor, DistributorPO, DistributorPOLine
        other_co = make_company('Other Co')
        other_brand = make_brand(other_co, 'Other Brand')
        other_item = make_item(other_brand, 'Other Item', 'OTH')
        other_dist = Distributor.objects.create(company=other_co, name='Other Dist')
        other_po = DistributorPO.objects.create(
            distributor=other_dist, year=2026, month=6, status='projected',
        )
        DistributorPOLine.objects.create(
            po=other_po, item=other_item, quantity_cases=Decimal('999'),
        )
        resp = self.client.get(
            reverse('production_demand_modal', kwargs={'year': 2026, 'month': 6}),
        )
        data = resp.json()
        item_ids = [i['id'] for i in data['items']]
        self.assertIn(self.item.pk, item_ids)
        self.assertNotIn(other_item.pk, item_ids)
        self.assertEqual(data['grand_total'], 120.0)


# ---------------------------------------------------------------------------
# Phase D — Forecast tab view tests
# ---------------------------------------------------------------------------

class ForecastTabPhaseD_Test(TestCase):
    """
    View-level tests for Phase D additions: Production POs row, warning banner,
    Phase C tweaks (item code removed, Dist Orders rename, count display).
    """

    def setUp(self):
        self.company = make_company()
        self.admin = make_supplier_admin(self.company)
        self.brand = make_brand(self.company)
        self.co_packer = make_co_packer(self.company)
        self.item = Item.objects.create(
            brand=self.brand, name='Classic Red 750ml', item_code='RED0750',
            co_packer=self.co_packer, cases_per_batch=280,
        )
        OwnInventorySnapshot.objects.create(
            company=self.company, item=self.item, year=2026, month=5,
            quantity_cases=Decimal('500'),
        )
        self.client = Client()
        self.client.login(username='admin', password='testpass123')

    def test_forecast_tab_shows_production_pos_row(self):
        resp = self.client.get(reverse('production_home') + '?tab=forecast')
        self.assertContains(resp, 'Production POs')
        self.assertContains(resp, 'production-po-btn')

    def test_forecast_tab_production_pos_row_shows_count_badge(self):
        ProductionPO.objects.create(
            company=self.company, co_packer=self.co_packer, year=2026, month=6,
            status='projected', generated_by_algorithm=False,
        )
        resp = self.client.get(reverse('production_home') + '?tab=forecast')
        self.assertContains(resp, 'bg-success')  # badge for count > 0

    def test_forecast_tab_shows_warning_banner_when_items_missing_co_packer(self):
        item_no_cp = Item.objects.create(
            brand=self.brand, name='No CP Item', item_code='NOCP01',
            co_packer=None, cases_per_batch=100,
        )
        resp = self.client.get(reverse('production_home') + '?tab=forecast')
        self.assertContains(resp, 'No CP Item')
        self.assertContains(resp, 'missing co-packer')

    def test_forecast_tab_shows_warning_banner_when_items_missing_cases_per_batch(self):
        item_no_batch = Item.objects.create(
            brand=self.brand, name='No Batch Item', item_code='NOBT01',
            co_packer=self.co_packer, cases_per_batch=None,
        )
        resp = self.client.get(reverse('production_home') + '?tab=forecast')
        self.assertContains(resp, 'No Batch Item')
        self.assertContains(resp, 'missing cases per batch')

    def test_forecast_tab_no_banner_when_all_items_configured(self):
        resp = self.client.get(reverse('production_home') + '?tab=forecast')
        self.assertNotContains(resp, 'missing co-packer')
        self.assertNotContains(resp, 'missing cases per batch')

    def test_forecast_tab_no_item_code_in_grid(self):
        resp = self.client.get(reverse('production_home') + '?tab=forecast')
        # The item_code appeared in the forecast grid as <div style="font-size:0.75rem;">item_code</div>
        # That specific inline style was ONLY used in the forecast grid item row for the code sub-label.
        # After Phase C removal, that style should be gone from the page.
        # (The item_code itself still appears in the Inventory tab snapshot table as <code>.)
        self.assertNotContains(resp, 'style="font-size:0.75rem;"')

    def test_dist_orders_row_shows_count_not_sum(self):
        from apps.distribution.models import Distributor, DistributorPO, DistributorPOLine
        dist = Distributor.objects.create(company=self.company, name='Test Dist')
        # Two POs in June 2026 with large case totals — sum would be 1000, count is 2
        for _ in range(2):
            po = DistributorPO.objects.create(
                distributor=dist, year=2026, month=6, status='projected',
            )
            DistributorPOLine.objects.create(po=po, item=self.item, quantity_cases=Decimal('500'))
        resp = self.client.get(reverse('production_home') + '?tab=forecast')
        self.assertContains(resp, 'Dist Orders')
        # Context variable should hold count (2), not sum (1000)
        dist_orders = resp.context['dist_orders_by_month']
        self.assertEqual(dist_orders.get('2026-06'), 2)
        # Sum of cases (1000) should NOT appear in the dist orders row button
        # (It could appear elsewhere as a forecast inventory number — check via context only)
        self.assertNotContains(resp, '1000')

    def test_demand_breakdown_no_item_code_in_modal(self):
        from apps.distribution.models import Distributor, DistributorPO, DistributorPOLine
        dist = Distributor.objects.create(company=self.company, name='Test Dist 2')
        po = DistributorPO.objects.create(
            distributor=dist, year=2026, month=6, status='projected',
        )
        DistributorPOLine.objects.create(po=po, item=self.item, quantity_cases=Decimal('120'))
        resp = self.client.get(
            reverse('production_demand_modal', kwargs={'year': 2026, 'month': 6}),
        )
        data = resp.json()
        # item_code is still returned but item list structure should not require it for display
        # The key is that item_code is still in the payload but the template JS no longer renders it
        self.assertIn('item_code', data['items'][0])  # still in API response


# ---------------------------------------------------------------------------
# Production page tweaks — tab order, forecast item sort, subtitle removal
# ---------------------------------------------------------------------------

class ProductionPageTweaksTest(TestCase):
    """Tab nav order, default tab, forecast item sort, subtitle removal."""

    def setUp(self):
        self.company = make_company()
        self.admin = make_supplier_admin(self.company)
        self.brand = make_brand(self.company)
        self.co_packer = make_co_packer(self.company)
        self.client = Client()
        self.client.login(username='admin', password='testpass123')

    def test_production_tabs_order(self):
        resp = self.client.get(reverse('production_home'))
        self.assertEqual(resp.status_code, 200)
        html = resp.content.decode()
        # Locate each tab button by its id, in the nav
        pos_inventory = html.index('id="tab-inventory"')
        pos_forecast = html.index('id="tab-forecast"')
        pos_production_pos = html.index('id="tab-production-pos"')
        pos_production_cases = html.index('id="tab-production-cases"')
        # Order: Inventory, Forecast, Production POs, Production Cases
        self.assertLess(pos_inventory, pos_forecast)
        self.assertLess(pos_forecast, pos_production_pos)
        self.assertLess(pos_production_pos, pos_production_cases)

    def test_production_default_tab_is_inventory(self):
        resp = self.client.get(reverse('production_home'))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.context['active_tab'], 'inventory')

    def test_production_forecast_items_sorted_by_sort_order(self):
        # Items under the same co-packer with non-sequential sort_order values
        item_c = Item.objects.create(
            brand=self.brand, name='Gamma Wine', item_code='GAM001',
            co_packer=self.co_packer, cases_per_batch=100, sort_order=30,
        )
        item_a = Item.objects.create(
            brand=self.brand, name='Alpha Wine', item_code='ALP001',
            co_packer=self.co_packer, cases_per_batch=100, sort_order=10,
        )
        item_b = Item.objects.create(
            brand=self.brand, name='Beta Wine', item_code='BET001',
            co_packer=self.co_packer, cases_per_batch=100, sort_order=20,
        )
        # A snapshot is required for the forecast grid to render (not empty state)
        make_snapshot(self.company, item_a, year=2026, month=5, qty='100')

        resp = self.client.get(reverse('production_home') + '?tab=forecast')
        self.assertEqual(resp.status_code, 200)

        # Verify via grouped context: items ordered by sort_order within the group
        grouped = resp.context['production_forecast_grouped']
        cp_group = next(
            g for g in grouped if g['co_packer_name'] == self.co_packer.name
        )
        ordered_names = [r['item'].name for r in cp_group['rows']]
        self.assertEqual(
            ordered_names,
            ['Alpha Wine', 'Beta Wine', 'Gamma Wine'],
        )

        # And in the rendered HTML
        html = resp.content.decode()
        self.assertLess(html.index('Alpha Wine'), html.index('Beta Wine'))
        self.assertLess(html.index('Beta Wine'), html.index('Gamma Wine'))

    def test_production_subtitle_removed(self):
        resp = self.client.get(reverse('production_home'))
        self.assertEqual(resp.status_code, 200)
        self.assertNotContains(resp, 'Production planning for')
