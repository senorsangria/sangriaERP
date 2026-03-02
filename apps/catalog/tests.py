"""
Tests for apps.catalog — Item sort order and AJAX reorder endpoints.

Phase 10.3.3
"""
from django.test import Client, TestCase
from django.urls import reverse

from apps.catalog.models import Brand, Item
from apps.core.models import Company, User


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_company(name="Test Co"):
    return Company.objects.create(name=name)


def make_supplier_admin(company, username="admin"):
    return User.objects.create_user(
        username=username,
        password="testpass123",
        company=company,
        role=User.Role.SUPPLIER_ADMIN,
    )


def make_brand(company, name="Test Brand"):
    return Brand.objects.create(company=company, name=name)


def make_item(brand, name="Item", item_code="Code", sort_order=0):
    return Item.objects.create(
        brand=brand,
        name=name,
        item_code=item_code,
        sort_order=sort_order,
    )


# ---------------------------------------------------------------------------
# Sort order field defaults
# ---------------------------------------------------------------------------

class ItemSortOrderDefaultTest(TestCase):
    """New items default to sort_order=0."""

    def setUp(self):
        self.company = make_company()
        self.brand = make_brand(self.company)

    def test_sort_order_defaults_to_zero(self):
        item = Item.objects.create(brand=self.brand, name="A", item_code="A001")
        self.assertEqual(item.sort_order, 0)

    def test_sort_order_can_be_set_explicitly(self):
        item = Item.objects.create(brand=self.brand, name="B", item_code="B001", sort_order=5)
        self.assertEqual(item.sort_order, 5)

    def test_items_ordered_by_sort_order_within_brand(self):
        item_b = make_item(self.brand, "B Item", "B001", sort_order=1)
        item_a = make_item(self.brand, "A Item", "A001", sort_order=0)
        items = list(self.brand.items.order_by('sort_order', 'name'))
        self.assertEqual(items[0], item_a)
        self.assertEqual(items[1], item_b)


# ---------------------------------------------------------------------------
# AJAX sort order endpoints
# ---------------------------------------------------------------------------

class ItemMoveUpTest(TestCase):
    """item_move_up: move an item one position earlier in sort order."""

    def setUp(self):
        self.company = make_company()
        self.admin = make_supplier_admin(self.company)
        self.brand = make_brand(self.company)
        self.item_a = make_item(self.brand, "Item A", "A001", sort_order=0)
        self.item_b = make_item(self.brand, "Item B", "B001", sort_order=1)
        self.client = Client()
        self.client.login(username="admin", password="testpass123")

    def test_move_up_swaps_positions(self):
        resp = self.client.post(
            reverse("item_move_up", args=[self.brand.pk, self.item_b.pk])
        )
        self.assertEqual(resp.status_code, 200)
        self.item_a.refresh_from_db()
        self.item_b.refresh_from_db()
        self.assertGreater(self.item_a.sort_order, self.item_b.sort_order)

    def test_move_up_returns_items_json(self):
        resp = self.client.post(
            reverse("item_move_up", args=[self.brand.pk, self.item_b.pk])
        )
        data = resp.json()
        self.assertIn("items", data)
        self.assertEqual(len(data["items"]), 2)
        self.assertEqual(data["items"][0]["id"], self.item_b.pk)

    def test_move_up_first_item_returns_400(self):
        resp = self.client.post(
            reverse("item_move_up", args=[self.brand.pk, self.item_a.pk])
        )
        self.assertEqual(resp.status_code, 400)

    def test_move_up_requires_post(self):
        resp = self.client.get(
            reverse("item_move_up", args=[self.brand.pk, self.item_b.pk])
        )
        self.assertEqual(resp.status_code, 405)

    def test_move_up_requires_supplier_admin(self):
        other = User.objects.create_user(
            username="other", password="testpass123",
            company=self.company, role=User.Role.AMBASSADOR,
        )
        c = Client()
        c.login(username="other", password="testpass123")
        resp = c.post(reverse("item_move_up", args=[self.brand.pk, self.item_b.pk]))
        self.assertEqual(resp.status_code, 403)


class ItemMoveDownTest(TestCase):
    """item_move_down: move an item one position later in sort order."""

    def setUp(self):
        self.company = make_company()
        self.admin = make_supplier_admin(self.company)
        self.brand = make_brand(self.company)
        self.item_a = make_item(self.brand, "Item A", "A001", sort_order=0)
        self.item_b = make_item(self.brand, "Item B", "B001", sort_order=1)
        self.client = Client()
        self.client.login(username="admin", password="testpass123")

    def test_move_down_swaps_positions(self):
        resp = self.client.post(
            reverse("item_move_down", args=[self.brand.pk, self.item_a.pk])
        )
        self.assertEqual(resp.status_code, 200)
        self.item_a.refresh_from_db()
        self.item_b.refresh_from_db()
        self.assertGreater(self.item_a.sort_order, self.item_b.sort_order)

    def test_move_down_returns_items_json(self):
        resp = self.client.post(
            reverse("item_move_down", args=[self.brand.pk, self.item_a.pk])
        )
        data = resp.json()
        self.assertIn("items", data)
        self.assertEqual(data["items"][0]["id"], self.item_b.pk)

    def test_move_down_last_item_returns_400(self):
        resp = self.client.post(
            reverse("item_move_down", args=[self.brand.pk, self.item_b.pk])
        )
        self.assertEqual(resp.status_code, 400)

    def test_move_down_requires_post(self):
        resp = self.client.get(
            reverse("item_move_down", args=[self.brand.pk, self.item_a.pk])
        )
        self.assertEqual(resp.status_code, 405)


# ---------------------------------------------------------------------------
# Sort order normalization — duplicate detection and fix
# ---------------------------------------------------------------------------

class SortOrderNormalizationTest(TestCase):
    """
    Verify that duplicate sort_order values are handled correctly:
    - Page load normalizes duplicates
    - A single up/down click with all-zero sort_orders moves exactly one position
    """

    def setUp(self):
        self.company = make_company()
        self.admin = make_supplier_admin(self.company)
        self.brand = make_brand(self.company)
        self.client = Client()
        self.client.login(username="admin", password="testpass123")

    def test_move_up_with_all_zero_sort_orders_moves_exactly_one_position(self):
        """With all items at sort_order=0, one click moves exactly one step."""
        a = make_item(self.brand, "Alpha", "AA", sort_order=0)
        b = make_item(self.brand, "Beta",  "BB", sort_order=0)
        c = make_item(self.brand, "Gamma", "GG", sort_order=0)

        # Items displayed order by (sort_order, name): Alpha, Beta, Gamma
        # Move Gamma up once — it should land at position 1 (before Beta), not position 0
        resp = self.client.post(
            reverse("item_move_up", args=[self.brand.pk, c.pk])
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        ids = [item["id"] for item in data["items"]]
        # Expected order after one move: Alpha, Gamma, Beta
        self.assertEqual(ids, [a.pk, c.pk, b.pk])

    def test_move_down_with_all_zero_sort_orders_moves_exactly_one_position(self):
        """With all items at sort_order=0, one down-click moves exactly one step."""
        a = make_item(self.brand, "Alpha", "AA", sort_order=0)
        b = make_item(self.brand, "Beta",  "BB", sort_order=0)
        c = make_item(self.brand, "Gamma", "GG", sort_order=0)

        # Items displayed order by (sort_order, name): Alpha, Beta, Gamma
        # Move Alpha down once — it should be at position 1 (after Beta stays first)
        resp = self.client.post(
            reverse("item_move_down", args=[self.brand.pk, a.pk])
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        ids = [item["id"] for item in data["items"]]
        # Expected order: Beta, Alpha, Gamma
        self.assertEqual(ids, [b.pk, a.pk, c.pk])

    def test_normalize_assigns_unique_sequential_sort_orders(self):
        """After any move, every item in the brand has a unique sort_order."""
        a = make_item(self.brand, "Alpha", "AA", sort_order=0)
        b = make_item(self.brand, "Beta",  "BB", sort_order=0)
        c = make_item(self.brand, "Gamma", "GG", sort_order=0)

        self.client.post(
            reverse("item_move_up", args=[self.brand.pk, c.pk])
        )
        a.refresh_from_db()
        b.refresh_from_db()
        c.refresh_from_db()
        orders = [a.sort_order, b.sort_order, c.sort_order]
        self.assertEqual(len(orders), len(set(orders)), "sort_order values must be unique after move")

    def test_brand_detail_page_normalizes_duplicate_sort_orders(self):
        """Brand detail page load fixes duplicate sort_order values."""
        a = make_item(self.brand, "Alpha", "AA", sort_order=0)
        b = make_item(self.brand, "Beta",  "BB", sort_order=0)
        c = make_item(self.brand, "Gamma", "GG", sort_order=0)

        self.client.get(reverse("brand_detail", args=[self.brand.pk]))

        a.refresh_from_db()
        b.refresh_from_db()
        c.refresh_from_db()
        orders = [a.sort_order, b.sort_order, c.sort_order]
        self.assertEqual(len(orders), len(set(orders)), "page load must normalize duplicate sort_orders")
