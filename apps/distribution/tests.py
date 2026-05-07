"""
Tests for Phases 1 and 2a of the distributor inventory and order forecasting tool.

Phase 1 covers:
- Model fields (Item.cases_per_pallet, Distributor order quantity, DistributorItemProfile)
- Permission: can_manage_distributor_inventory granted to supplier_admin only
- Distributor edit page 3-tab rendering and permission gating
- Order profile save endpoint
- Safety stock save endpoint (create, update, delete, invalid values, brand grouping)

Phase 2a covers:
- DistributorItemProfile.is_active field
- InventorySnapshot model
- Updated safety stock save (three-path logic: active+value, active+blank, inactive)
- Distributor list page 3-tab structure (Distributors, Inventory, Snapshots)
- Active checkbox column on Safety Stock tab
"""
from django.db import IntegrityError
from django.db.utils import IntegrityError as DBIntegrityError
from django.test import Client, TestCase
from django.urls import reverse
from django.db.models import ProtectedError

from apps.catalog.models import Brand, Item
from apps.core.models import Company, User
from apps.core.rbac import Permission, Role
from apps.distribution.models import Distributor, DistributorItemProfile, InventorySnapshot


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_company(name='Test Co'):
    return Company.objects.create(name=name)


def make_supplier_admin(company, username='admin'):
    user = User.objects.create_user(
        username=username,
        password='testpass123',
        company=company,
    )
    user.roles.set([Role.objects.get(codename='supplier_admin')])
    return user


def make_user_with_roles(company, role_codenames, username='limited'):
    user = User.objects.create_user(
        username=username,
        password='testpass123',
        company=company,
    )
    roles = Role.objects.filter(codename__in=role_codenames)
    user.roles.set(roles)
    return user


def make_distributor(company, name='Test Distributor'):
    return Distributor.objects.create(company=company, name=name)


def make_brand(company, name='Test Brand'):
    return Brand.objects.create(company=company, name=name)


def make_item(brand, name='Item', item_code='CODE001', sort_order=1):
    return Item.objects.create(brand=brand, name=name, item_code=item_code, sort_order=sort_order)


# ---------------------------------------------------------------------------
# 1. Item.cases_per_pallet field
# ---------------------------------------------------------------------------

class ItemCasesPerPalletFieldTest(TestCase):

    def setUp(self):
        self.company = make_company()
        self.brand = make_brand(self.company)

    def test_cases_per_pallet_field_added_to_item_model(self):
        item = Item.objects.create(brand=self.brand, name='Red 750ml', item_code='RED750')
        self.assertTrue(hasattr(item, 'cases_per_pallet'))
        self.assertIsNone(item.cases_per_pallet)

    def test_cases_per_pallet_can_be_set(self):
        item = Item.objects.create(
            brand=self.brand, name='Red 750ml', item_code='RED750', cases_per_pallet=56
        )
        item.refresh_from_db()
        self.assertEqual(item.cases_per_pallet, 56)

    def test_cases_per_pallet_is_nullable(self):
        item = Item.objects.create(brand=self.brand, name='White 750ml', item_code='WHT750')
        item.refresh_from_db()
        self.assertIsNone(item.cases_per_pallet)


# ---------------------------------------------------------------------------
# 2. Distributor order quantity fields
# ---------------------------------------------------------------------------

class DistributorOrderQuantityFieldsTest(TestCase):

    def setUp(self):
        self.company = make_company()

    def test_distributor_order_quantity_fields_added(self):
        d = make_distributor(self.company)
        self.assertTrue(hasattr(d, 'order_quantity_value'))
        self.assertTrue(hasattr(d, 'order_quantity_unit'))
        self.assertIsNone(d.order_quantity_value)
        self.assertIsNone(d.order_quantity_unit)

    def test_order_quantity_value_can_be_set(self):
        d = make_distributor(self.company)
        d.order_quantity_value = 10
        d.order_quantity_unit = Distributor.OrderQuantityUnit.PALLETS
        d.save(update_fields=['order_quantity_value', 'order_quantity_unit'])
        d.refresh_from_db()
        self.assertEqual(d.order_quantity_value, 10)
        self.assertEqual(d.order_quantity_unit, 'pallets')

    def test_order_quantity_unit_choices(self):
        choices = [c[0] for c in Distributor.OrderQuantityUnit.choices]
        self.assertIn('pallets', choices)
        self.assertIn('cases', choices)


# ---------------------------------------------------------------------------
# 3. DistributorItemProfile model
# ---------------------------------------------------------------------------

class DistributorItemProfileModelTest(TestCase):

    def setUp(self):
        self.company = make_company()
        self.distributor = make_distributor(self.company)
        self.brand = make_brand(self.company)
        self.item = make_item(self.brand)

    def test_distributor_item_profile_model_created(self):
        profile = DistributorItemProfile.objects.create(
            distributor=self.distributor,
            item=self.item,
            safety_stock_cases=100,
        )
        fetched = DistributorItemProfile.objects.get(pk=profile.pk)
        self.assertEqual(fetched.safety_stock_cases, 100)
        self.assertEqual(fetched.distributor, self.distributor)
        self.assertEqual(fetched.item, self.item)

    def test_distributor_item_profile_nullable_safety_stock(self):
        profile = DistributorItemProfile.objects.create(
            distributor=self.distributor,
            item=self.item,
        )
        self.assertIsNone(profile.safety_stock_cases)

    def test_distributor_item_profile_unique_constraint(self):
        DistributorItemProfile.objects.create(
            distributor=self.distributor,
            item=self.item,
            safety_stock_cases=50,
        )
        with self.assertRaises(Exception):
            DistributorItemProfile.objects.create(
                distributor=self.distributor,
                item=self.item,
                safety_stock_cases=75,
            )

    def test_distributor_item_profile_protect_on_delete(self):
        DistributorItemProfile.objects.create(
            distributor=self.distributor,
            item=self.item,
            safety_stock_cases=50,
        )
        with self.assertRaises(ProtectedError):
            self.item.delete()

    def test_distributor_item_profile_str(self):
        profile = DistributorItemProfile.objects.create(
            distributor=self.distributor,
            item=self.item,
            safety_stock_cases=42,
        )
        self.assertIn('42', str(profile))
        self.assertIn(self.distributor.name, str(profile))


# ---------------------------------------------------------------------------
# 4. Permission: can_manage_distributor_inventory
# ---------------------------------------------------------------------------

class DistributorInventoryPermissionTest(TestCase):

    def test_inventory_permission_granted_to_supplier_admin_only(self):
        company = make_company()
        supplier_admin = make_supplier_admin(company)
        self.assertTrue(supplier_admin.has_permission('can_manage_distributor_inventory'))

    def test_inventory_permission_not_granted_to_ambassador_manager(self):
        company = make_company()
        user = make_user_with_roles(company, ['ambassador_manager'])
        self.assertFalse(user.has_permission('can_manage_distributor_inventory'))

    def test_inventory_permission_not_granted_to_sales_manager(self):
        company = make_company()
        user = make_user_with_roles(company, ['sales_manager'])
        self.assertFalse(user.has_permission('can_manage_distributor_inventory'))

    def test_inventory_permission_not_granted_to_territory_manager(self):
        company = make_company()
        user = make_user_with_roles(company, ['territory_manager'])
        self.assertFalse(user.has_permission('can_manage_distributor_inventory'))

    def test_inventory_permission_not_granted_to_ambassador(self):
        company = make_company()
        user = make_user_with_roles(company, ['ambassador'])
        self.assertFalse(user.has_permission('can_manage_distributor_inventory'))


# ---------------------------------------------------------------------------
# 5. Distributor edit page — tab rendering
# ---------------------------------------------------------------------------

class DistributorEditTabRenderingTest(TestCase):

    def setUp(self):
        self.company = make_company()
        self.admin = make_supplier_admin(self.company)
        self.distributor = make_distributor(self.company)
        self.client = Client()
        self.client.login(username='admin', password='testpass123')

    def test_distributor_edit_basic_tab_renders(self):
        url = reverse('distributor_edit', args=[self.distributor.pk])
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Basic Info')
        self.assertContains(resp, 'Distributor Name')

    def test_distributor_edit_order_profile_tab_renders_with_permission(self):
        url = reverse('distributor_edit', args=[self.distributor.pk])
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Order Profile')
        self.assertContains(resp, 'order-profile')

    def test_distributor_edit_safety_stock_tab_renders_with_permission(self):
        url = reverse('distributor_edit', args=[self.distributor.pk])
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Safety Stock')
        self.assertContains(resp, 'safety-stock')

    def test_inventory_tabs_hidden_without_permission(self):
        # Create a role with only can_manage_distributors
        limited_role, _ = Role.objects.get_or_create(
            codename='test_dist_only',
            defaults={'name': 'Test Dist Only'},
        )
        perm = Permission.objects.get(codename='can_manage_distributors')
        limited_role.permissions.set([perm])

        limited_user = User.objects.create_user(
            username='limited_user',
            password='testpass123',
            company=self.company,
        )
        limited_user.roles.set([limited_role])

        c = Client()
        c.login(username='limited_user', password='testpass123')
        url = reverse('distributor_edit', args=[self.distributor.pk])
        resp = c.get(url)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Basic Info')
        self.assertNotContains(resp, 'Order Profile')
        self.assertNotContains(resp, 'Safety Stock')


# ---------------------------------------------------------------------------
# 6. Order profile save endpoint
# ---------------------------------------------------------------------------

class DistributorOrderProfileSaveTest(TestCase):

    def setUp(self):
        self.company = make_company()
        self.admin = make_supplier_admin(self.company)
        self.distributor = make_distributor(self.company)
        self.client = Client()
        self.client.login(username='admin', password='testpass123')
        self.url = reverse('distributor_order_profile_save', args=[self.distributor.pk])

    def test_order_profile_save_updates_distributor(self):
        self.client.post(self.url, {
            'order_quantity_value': '8',
            'order_quantity_unit': 'pallets',
        })
        self.distributor.refresh_from_db()
        self.assertEqual(self.distributor.order_quantity_value, 8)
        self.assertEqual(self.distributor.order_quantity_unit, 'pallets')

    def test_order_profile_save_requires_permission(self):
        limited_role, _ = Role.objects.get_or_create(
            codename='test_dist_only2',
            defaults={'name': 'Test Dist Only 2'},
        )
        perm = Permission.objects.get(codename='can_manage_distributors')
        limited_role.permissions.set([perm])
        limited_user = User.objects.create_user(
            username='limited2', password='testpass123', company=self.company,
        )
        limited_user.roles.set([limited_role])

        c = Client()
        c.login(username='limited2', password='testpass123')
        resp = c.post(self.url, {
            'order_quantity_value': '8',
            'order_quantity_unit': 'pallets',
        })
        self.assertEqual(resp.status_code, 403)

    def test_order_profile_save_blank_clears_values(self):
        self.distributor.order_quantity_value = 5
        self.distributor.order_quantity_unit = 'pallets'
        self.distributor.save()

        self.client.post(self.url, {
            'order_quantity_value': '',
            'order_quantity_unit': '',
        })
        self.distributor.refresh_from_db()
        self.assertIsNone(self.distributor.order_quantity_value)
        self.assertIsNone(self.distributor.order_quantity_unit)

    def test_order_profile_save_redirects_to_order_profile_tab(self):
        resp = self.client.post(self.url, {
            'order_quantity_value': '3',
            'order_quantity_unit': 'cases',
        })
        self.assertRedirects(
            resp,
            reverse('distributor_edit', args=[self.distributor.pk]) + '?tab=order-profile',
            fetch_redirect_response=False,
        )


# ---------------------------------------------------------------------------
# 7. Safety stock save endpoint
# ---------------------------------------------------------------------------

class DistributorSafetyStockSaveTest(TestCase):

    def setUp(self):
        self.company = make_company()
        self.admin = make_supplier_admin(self.company)
        self.distributor = make_distributor(self.company)
        self.brand = make_brand(self.company)
        self.item1 = make_item(self.brand, name='Item A', item_code='A001', sort_order=1)
        self.item2 = make_item(self.brand, name='Item B', item_code='B001', sort_order=2)
        self.client = Client()
        self.client.login(username='admin', password='testpass123')
        self.url = reverse('distributor_safety_stock_save', args=[self.distributor.pk])

    def test_safety_stock_save_creates_profiles(self):
        # Both items active (checkbox posted) with valid values.
        self.client.post(self.url, {
            f'is_active_{self.item1.pk}': 'on',
            f'safety_stock_{self.item1.pk}': '50',
            f'is_active_{self.item2.pk}': 'on',
            f'safety_stock_{self.item2.pk}': '75',
        })
        self.assertEqual(DistributorItemProfile.objects.count(), 2)
        p1 = DistributorItemProfile.objects.get(distributor=self.distributor, item=self.item1)
        p2 = DistributorItemProfile.objects.get(distributor=self.distributor, item=self.item2)
        self.assertEqual(p1.safety_stock_cases, 50)
        self.assertEqual(p2.safety_stock_cases, 75)

    def test_safety_stock_save_updates_existing_profiles(self):
        DistributorItemProfile.objects.create(
            distributor=self.distributor, item=self.item1, safety_stock_cases=50
        )
        # Post item1 active+value, item2 also active+blank (no-op: no profile).
        self.client.post(self.url, {
            f'is_active_{self.item1.pk}': 'on',
            f'safety_stock_{self.item1.pk}': '120',
            f'is_active_{self.item2.pk}': 'on',
            f'safety_stock_{self.item2.pk}': '',
        })
        # Only one profile should exist (item1's updated value, item2 has no profile).
        self.assertEqual(DistributorItemProfile.objects.count(), 1)
        p = DistributorItemProfile.objects.get(distributor=self.distributor, item=self.item1)
        self.assertEqual(p.safety_stock_cases, 120)

    def test_safety_stock_save_blank_value_deletes_profile(self):
        DistributorItemProfile.objects.create(
            distributor=self.distributor, item=self.item1, safety_stock_cases=50
        )
        # Active + blank → delete profile (Path 2).
        self.client.post(self.url, {
            f'is_active_{self.item1.pk}': 'on',
            f'safety_stock_{self.item1.pk}': '',
        })
        self.assertFalse(
            DistributorItemProfile.objects.filter(
                distributor=self.distributor, item=self.item1
            ).exists()
        )

    def test_safety_stock_save_zero_value_deletes_profile(self):
        DistributorItemProfile.objects.create(
            distributor=self.distributor, item=self.item1, safety_stock_cases=50
        )
        # Active + zero → delete profile (Path 2).
        self.client.post(self.url, {
            f'is_active_{self.item1.pk}': 'on',
            f'safety_stock_{self.item1.pk}': '0',
        })
        self.assertFalse(
            DistributorItemProfile.objects.filter(
                distributor=self.distributor, item=self.item1
            ).exists()
        )

    def test_safety_stock_save_invalid_value_warning(self):
        resp = self.client.post(self.url, {
            f'is_active_{self.item1.pk}': 'on',
            f'safety_stock_{self.item1.pk}': 'abc',
            f'is_active_{self.item2.pk}': 'on',
            f'safety_stock_{self.item2.pk}': '60',
        }, follow=True)
        # Item2's valid value should still be saved.
        self.assertTrue(
            DistributorItemProfile.objects.filter(
                distributor=self.distributor, item=self.item2, safety_stock_cases=60
            ).exists()
        )
        # Item1's invalid value should be skipped; no profile created.
        self.assertFalse(
            DistributorItemProfile.objects.filter(
                distributor=self.distributor, item=self.item1
            ).exists()
        )
        messages_list = list(resp.context['messages'])
        warning_texts = [str(m) for m in messages_list if m.level_tag == 'warning']
        self.assertTrue(any('Item A' in t for t in warning_texts))

    def test_safety_stock_save_requires_permission(self):
        limited_role, _ = Role.objects.get_or_create(
            codename='test_dist_only3',
            defaults={'name': 'Test Dist Only 3'},
        )
        perm = Permission.objects.get(codename='can_manage_distributors')
        limited_role.permissions.set([perm])
        limited_user = User.objects.create_user(
            username='limited3', password='testpass123', company=self.company,
        )
        limited_user.roles.set([limited_role])

        c = Client()
        c.login(username='limited3', password='testpass123')
        resp = c.post(self.url, {f'safety_stock_{self.item1.pk}': '50'})
        self.assertEqual(resp.status_code, 403)

    def test_safety_stock_save_redirects_to_safety_stock_tab(self):
        resp = self.client.post(self.url, {
            f'safety_stock_{self.item1.pk}': '50',
        })
        self.assertRedirects(
            resp,
            reverse('distributor_edit', args=[self.distributor.pk]) + '?tab=safety-stock',
            fetch_redirect_response=False,
        )

    def test_safety_stock_table_shows_items_grouped_by_brand(self):
        brand2 = make_brand(self.company, name='Second Brand')
        item3 = make_item(brand2, name='Item C', item_code='C001', sort_order=1)

        url = reverse('distributor_edit', args=[self.distributor.pk]) + '?tab=safety-stock'
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        content = resp.content.decode()

        # Both brand names appear as headers
        self.assertIn('Test Brand', content)
        self.assertIn('Second Brand', content)

        # Each brand name as a brand-header appears exactly once
        self.assertEqual(content.count('data-brand="Test Brand"'), 1)
        self.assertEqual(content.count('data-brand="Second Brand"'), 1)

        # Items appear in the table
        self.assertIn('Item A', content)
        self.assertIn('Item B', content)
        self.assertIn('Item C', content)

    def test_safety_stock_table_no_cases_per_pallet_column(self):
        url = reverse('distributor_edit', args=[self.distributor.pk]) + '?tab=safety-stock'
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        self.assertNotContains(resp, 'Cases / Pallet')


# ---------------------------------------------------------------------------
# 8. Order Profile tab — field order
# ---------------------------------------------------------------------------

class DistributorOrderProfileFieldOrderTest(TestCase):

    def setUp(self):
        self.company = make_company()
        self.admin = make_supplier_admin(self.company)
        self.distributor = make_distributor(self.company)
        self.client = Client()
        self.client.login(username='admin', password='testpass123')

    def test_order_profile_tab_unit_field_appears_before_quantity(self):
        url = reverse('distributor_edit', args=[self.distributor.pk]) + '?tab=order-profile'
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        content = resp.content.decode()
        unit_pos = content.find('id_order_quantity_unit')
        value_pos = content.find('id_order_quantity_value')
        self.assertGreater(unit_pos, -1, 'Unit field not found in response')
        self.assertGreater(value_pos, -1, 'Value field not found in response')
        self.assertLess(unit_pos, value_pos, 'Unit field must appear before quantity field in HTML')


# ===========================================================================
# PHASE 2a TESTS
# ===========================================================================

# ---------------------------------------------------------------------------
# 9. DistributorItemProfile.is_active
# ---------------------------------------------------------------------------

class DistributorItemProfileIsActiveTest(TestCase):

    def setUp(self):
        self.company = make_company()
        self.distributor = make_distributor(self.company)
        self.brand = make_brand(self.company)
        self.item = make_item(self.brand)

    def test_distributor_item_profile_is_active_default_true(self):
        profile = DistributorItemProfile.objects.create(
            distributor=self.distributor,
            item=self.item,
        )
        self.assertTrue(profile.is_active)

    def test_distributor_item_profile_is_active_can_be_set_false(self):
        profile = DistributorItemProfile.objects.create(
            distributor=self.distributor,
            item=self.item,
            is_active=False,
        )
        profile.refresh_from_db()
        self.assertFalse(profile.is_active)


# ---------------------------------------------------------------------------
# 10. InventorySnapshot model
# ---------------------------------------------------------------------------

class InventorySnapshotModelTest(TestCase):

    def setUp(self):
        self.company = make_company()
        self.distributor = make_distributor(self.company)
        self.brand = make_brand(self.company)
        self.item = make_item(self.brand)

    def test_inventory_snapshot_model_create(self):
        snapshot = InventorySnapshot.objects.create(
            distributor=self.distributor,
            item=self.item,
            quantity_cases=100,
            year=2025,
            month=3,
        )
        fetched = InventorySnapshot.objects.get(pk=snapshot.pk)
        self.assertEqual(fetched.distributor, self.distributor)
        self.assertEqual(fetched.item, self.item)
        self.assertEqual(fetched.quantity_cases, 100)
        self.assertEqual(fetched.year, 2025)
        self.assertEqual(fetched.month, 3)

    def test_inventory_snapshot_zero_quantity_allowed(self):
        snapshot = InventorySnapshot.objects.create(
            distributor=self.distributor,
            item=self.item,
            quantity_cases=0,
            year=2025,
            month=4,
        )
        self.assertEqual(snapshot.quantity_cases, 0)

    def test_inventory_snapshot_unique_constraint(self):
        InventorySnapshot.objects.create(
            distributor=self.distributor,
            item=self.item,
            quantity_cases=50,
            year=2025,
            month=1,
        )
        with self.assertRaises(Exception):
            InventorySnapshot.objects.create(
                distributor=self.distributor,
                item=self.item,
                quantity_cases=75,
                year=2025,
                month=1,
            )

    def test_inventory_snapshot_protect_on_delete_distributor(self):
        InventorySnapshot.objects.create(
            distributor=self.distributor,
            item=self.item,
            quantity_cases=10,
            year=2025,
            month=2,
        )
        with self.assertRaises(ProtectedError):
            self.distributor.delete()

    def test_inventory_snapshot_protect_on_delete_item(self):
        InventorySnapshot.objects.create(
            distributor=self.distributor,
            item=self.item,
            quantity_cases=10,
            year=2025,
            month=2,
        )
        with self.assertRaises(ProtectedError):
            self.item.delete()

    def test_inventory_snapshot_created_by_set_null(self):
        creator = User.objects.create_user(
            username='snapshot_creator',
            password='testpass123',
            company=self.company,
        )
        snapshot = InventorySnapshot.objects.create(
            distributor=self.distributor,
            item=self.item,
            quantity_cases=20,
            year=2025,
            month=5,
            created_by=creator,
        )
        creator.delete()
        snapshot.refresh_from_db()
        self.assertIsNone(snapshot.created_by)

    def test_inventory_snapshot_ordering(self):
        # Create snapshots out of order; expect newest first, then dist/item name.
        InventorySnapshot.objects.create(
            distributor=self.distributor, item=self.item,
            quantity_cases=10, year=2024, month=3,
        )
        InventorySnapshot.objects.create(
            distributor=self.distributor, item=self.item,
            quantity_cases=20, year=2025, month=1,
        )
        InventorySnapshot.objects.create(
            distributor=self.distributor, item=self.item,
            quantity_cases=30, year=2024, month=11,
        )
        snapshots = list(InventorySnapshot.objects.all())
        self.assertEqual(snapshots[0].year, 2025)
        self.assertEqual(snapshots[0].month, 1)
        self.assertEqual(snapshots[1].year, 2024)
        self.assertEqual(snapshots[1].month, 11)
        self.assertEqual(snapshots[2].year, 2024)
        self.assertEqual(snapshots[2].month, 3)


# ---------------------------------------------------------------------------
# 11. Safety stock save — Phase 2a three-path logic
# ---------------------------------------------------------------------------

class SafetyStockSavePhase2aTest(TestCase):

    def setUp(self):
        self.company = make_company()
        self.admin = make_supplier_admin(self.company)
        self.distributor = make_distributor(self.company)
        self.brand = make_brand(self.company)
        self.item = make_item(self.brand, name='Item A', item_code='A001', sort_order=1)
        self.item2 = make_item(self.brand, name='Item B', item_code='B001', sort_order=2)
        self.client = Client()
        self.client.login(username='admin', password='testpass123')
        self.url = reverse('distributor_safety_stock_save', args=[self.distributor.pk])

    def _post(self, data):
        return self.client.post(self.url, data, follow=True)

    def test_safety_stock_save_active_with_value(self):
        """Path 1: active + valid value → create profile."""
        self._post({f'is_active_{self.item.pk}': 'on', f'safety_stock_{self.item.pk}': '50'})
        profile = DistributorItemProfile.objects.get(distributor=self.distributor, item=self.item)
        self.assertTrue(profile.is_active)
        self.assertEqual(profile.safety_stock_cases, 50)

    def test_safety_stock_save_active_blank_value_no_existing_profile(self):
        """Path 2: active + blank, no profile → no profile created."""
        self._post({f'is_active_{self.item.pk}': 'on', f'safety_stock_{self.item.pk}': ''})
        self.assertFalse(
            DistributorItemProfile.objects.filter(
                distributor=self.distributor, item=self.item
            ).exists()
        )

    def test_safety_stock_save_active_blank_value_existing_profile_with_safety_stock(self):
        """Path 2: active + blank, profile exists → profile deleted."""
        DistributorItemProfile.objects.create(
            distributor=self.distributor, item=self.item,
            is_active=True, safety_stock_cases=50,
        )
        self._post({f'is_active_{self.item.pk}': 'on', f'safety_stock_{self.item.pk}': ''})
        self.assertFalse(
            DistributorItemProfile.objects.filter(
                distributor=self.distributor, item=self.item
            ).exists()
        )

    def test_safety_stock_save_active_blank_value_existing_inactive_profile(self):
        """Path 2: active + blank, inactive profile exists → profile deleted."""
        DistributorItemProfile.objects.create(
            distributor=self.distributor, item=self.item,
            is_active=False, safety_stock_cases=None,
        )
        self._post({f'is_active_{self.item.pk}': 'on', f'safety_stock_{self.item.pk}': ''})
        self.assertFalse(
            DistributorItemProfile.objects.filter(
                distributor=self.distributor, item=self.item
            ).exists()
        )

    def test_safety_stock_save_inactive_no_existing_profile(self):
        """Path 3: inactive, no profile → profile created with is_active=False, no safety stock."""
        self._post({f'safety_stock_{self.item.pk}': ''})  # checkbox absent = inactive
        profile = DistributorItemProfile.objects.get(distributor=self.distributor, item=self.item)
        self.assertFalse(profile.is_active)
        self.assertIsNone(profile.safety_stock_cases)

    def test_safety_stock_save_inactive_with_safety_stock_value_ignored(self):
        """Path 3: inactive + value posted → profile created, value ignored."""
        self._post({f'safety_stock_{self.item.pk}': '50'})  # checkbox absent = inactive
        profile = DistributorItemProfile.objects.get(distributor=self.distributor, item=self.item)
        self.assertFalse(profile.is_active)
        self.assertIsNone(profile.safety_stock_cases)

    def test_safety_stock_save_inactive_existing_active_profile_deactivates(self):
        """Path 3: inactive, active profile exists → profile updated to inactive, safety stock cleared."""
        DistributorItemProfile.objects.create(
            distributor=self.distributor, item=self.item,
            is_active=True, safety_stock_cases=50,
        )
        self._post({f'safety_stock_{self.item.pk}': '50'})  # checkbox absent = inactive
        profile = DistributorItemProfile.objects.get(distributor=self.distributor, item=self.item)
        self.assertFalse(profile.is_active)
        self.assertIsNone(profile.safety_stock_cases)

    def test_safety_stock_save_invalid_value_when_active_warns(self):
        """Active + non-numeric value → warning shown, no profile created/changed."""
        resp = self._post({
            f'is_active_{self.item.pk}': 'on',
            f'safety_stock_{self.item.pk}': 'abc',
        })
        self.assertFalse(
            DistributorItemProfile.objects.filter(
                distributor=self.distributor, item=self.item
            ).exists()
        )
        messages_list = list(resp.context['messages'])
        warning_texts = [str(m) for m in messages_list if m.level_tag == 'warning']
        self.assertTrue(any('Item A' in t for t in warning_texts))

    def test_safety_stock_save_only_one_profile_created_on_duplicate_post(self):
        """Posting the same item twice in sequence creates only one profile."""
        self._post({f'is_active_{self.item.pk}': 'on', f'safety_stock_{self.item.pk}': '30'})
        self._post({f'is_active_{self.item.pk}': 'on', f'safety_stock_{self.item.pk}': '60'})
        self.assertEqual(
            DistributorItemProfile.objects.filter(
                distributor=self.distributor, item=self.item
            ).count(), 1
        )
        profile = DistributorItemProfile.objects.get(distributor=self.distributor, item=self.item)
        self.assertEqual(profile.safety_stock_cases, 60)


# ---------------------------------------------------------------------------
# 12. Distributor list page — 3-tab structure
# ---------------------------------------------------------------------------

class DistributorListTabsTest(TestCase):

    def setUp(self):
        self.company = make_company()
        self.admin = make_supplier_admin(self.company)
        self.distributor = make_distributor(self.company)
        self.client = Client()
        self.client.login(username='admin', password='testpass123')
        self.url = reverse('distributor_list')

    def test_distributor_list_default_tab_distributors(self):
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.context['active_tab'], 'distributors')
        self.assertContains(resp, 'pane-distributors')

    def test_distributor_list_inventory_tab_renders_with_permission(self):
        resp = self.client.get(self.url + '?tab=inventory')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.context['active_tab'], 'inventory')
        self.assertContains(resp, 'No inventory data yet')

    def test_distributor_list_snapshots_tab_renders_with_permission(self):
        resp = self.client.get(self.url + '?tab=snapshots')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.context['active_tab'], 'snapshots')
        self.assertContains(resp, 'No snapshots uploaded yet')

    def test_distributor_list_invalid_tab_falls_back_to_distributors(self):
        resp = self.client.get(self.url + '?tab=garbage')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.context['active_tab'], 'distributors')

    def test_distributor_list_inventory_tab_hidden_without_inventory_permission(self):
        limited_role, _ = Role.objects.get_or_create(
            codename='test_dist_list_only',
            defaults={'name': 'Test Dist List Only'},
        )
        perm = Permission.objects.get(codename='can_manage_distributors')
        limited_role.permissions.set([perm])
        limited_user = User.objects.create_user(
            username='limited_list', password='testpass123', company=self.company,
        )
        limited_user.roles.set([limited_role])

        c = Client()
        c.login(username='limited_list', password='testpass123')
        resp = c.get(self.url)
        self.assertEqual(resp.status_code, 200)
        self.assertNotContains(resp, 'tab-inventory')
        self.assertNotContains(resp, 'tab-snapshots')
        # tab-distributors is always visible
        self.assertContains(resp, 'tab-distributors')

    def test_distributor_list_inventory_tab_forced_to_distributors_without_permission(self):
        """Even with ?tab=inventory, users without inventory permission get distributors tab."""
        limited_role, _ = Role.objects.get_or_create(
            codename='test_dist_list_only2',
            defaults={'name': 'Test Dist List Only 2'},
        )
        perm = Permission.objects.get(codename='can_manage_distributors')
        limited_role.permissions.set([perm])
        limited_user = User.objects.create_user(
            username='limited_list2', password='testpass123', company=self.company,
        )
        limited_user.roles.set([limited_role])

        c = Client()
        c.login(username='limited_list2', password='testpass123')
        resp = c.get(self.url + '?tab=inventory')
        self.assertEqual(resp.context['active_tab'], 'distributors')


# ---------------------------------------------------------------------------
# 13. Safety Stock tab — Active column UI
# ---------------------------------------------------------------------------

class SafetyStockActiveColumnUITest(TestCase):

    def setUp(self):
        self.company = make_company()
        self.admin = make_supplier_admin(self.company)
        self.distributor = make_distributor(self.company)
        self.brand = make_brand(self.company)
        self.item = make_item(self.brand, name='Item A', item_code='A001', sort_order=1)
        self.client = Client()
        self.client.login(username='admin', password='testpass123')

    def _get_safety_stock_tab(self):
        url = reverse('distributor_edit', args=[self.distributor.pk]) + '?tab=safety-stock'
        return self.client.get(url)

    def test_safety_stock_table_has_active_column(self):
        resp = self._get_safety_stock_tab()
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Active')
        self.assertContains(resp, 'safety-stock-active-cb')

    def test_safety_stock_table_active_column_default_checked_for_no_profile(self):
        resp = self._get_safety_stock_tab()
        content = resp.content.decode()
        # The checkbox for this item should be checked (no profile = active by default)
        cb_marker = f'is_active_{self.item.pk}'
        self.assertIn(cb_marker, content)
        # Find the checkbox block and assert it has 'checked'
        cb_idx = content.find(f'name="is_active_{self.item.pk}"')
        self.assertGreater(cb_idx, -1)
        snippet = content[cb_idx:cb_idx + 200]
        self.assertIn('checked', snippet)

    def test_safety_stock_table_active_column_unchecked_for_inactive_profile(self):
        DistributorItemProfile.objects.create(
            distributor=self.distributor,
            item=self.item,
            is_active=False,
        )
        resp = self._get_safety_stock_tab()
        content = resp.content.decode()
        cb_idx = content.find(f'name="is_active_{self.item.pk}"')
        self.assertGreater(cb_idx, -1)
        snippet = content[cb_idx:cb_idx + 200]
        self.assertNotIn('checked', snippet)
