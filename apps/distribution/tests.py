"""
Tests for Phase 1 of the distributor inventory and order forecasting tool.

Covers:
- Model fields (Item.cases_per_pallet, Distributor order quantity, DistributorItemProfile)
- Permission: can_manage_distributor_inventory granted to supplier_admin only
- Distributor edit page 3-tab rendering and permission gating
- Order profile save endpoint
- Safety stock save endpoint (create, update, delete, invalid values, brand grouping)
"""
from django.db import IntegrityError
from django.db.utils import IntegrityError as DBIntegrityError
from django.test import Client, TestCase
from django.urls import reverse
from django.db.models import ProtectedError

from apps.catalog.models import Brand, Item
from apps.core.models import Company, User
from apps.core.rbac import Permission, Role
from apps.distribution.models import Distributor, DistributorItemProfile


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
        self.client.post(self.url, {
            f'safety_stock_{self.item1.pk}': '50',
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
        self.client.post(self.url, {
            f'safety_stock_{self.item1.pk}': '120',
        })
        self.assertEqual(DistributorItemProfile.objects.count(), 1)
        p = DistributorItemProfile.objects.get(distributor=self.distributor, item=self.item1)
        self.assertEqual(p.safety_stock_cases, 120)

    def test_safety_stock_save_blank_value_deletes_profile(self):
        DistributorItemProfile.objects.create(
            distributor=self.distributor, item=self.item1, safety_stock_cases=50
        )
        self.client.post(self.url, {
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
        self.client.post(self.url, {
            f'safety_stock_{self.item1.pk}': '0',
        })
        self.assertFalse(
            DistributorItemProfile.objects.filter(
                distributor=self.distributor, item=self.item1
            ).exists()
        )

    def test_safety_stock_save_invalid_value_warning(self):
        resp = self.client.post(self.url, {
            f'safety_stock_{self.item1.pk}': 'abc',
            f'safety_stock_{self.item2.pk}': '60',
        }, follow=True)
        # Item2's valid value should still be saved
        self.assertTrue(
            DistributorItemProfile.objects.filter(
                distributor=self.distributor, item=self.item2, safety_stock_cases=60
            ).exists()
        )
        # Item1's invalid value should be skipped; no profile created
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
