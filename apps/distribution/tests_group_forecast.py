"""
Tests for Phase G2 — Group forecast view and compute_group_forecast().
"""
from datetime import date

from django.test import Client, TestCase
from django.urls import reverse

from apps.catalog.models import Brand, Item
from apps.core.models import Company, User
from apps.core.rbac import Permission, Role
from apps.distribution.forecast import compute_group_forecast
from apps.distribution.models import (
    Distributor, DistributorGroup, DistributorItemProfile,
    DistributorPO, DistributorPOLine, InventorySnapshot,
)
from apps.distribution.tests_forecast import (
    _make_company, _make_supplier_admin, _make_distributor,
    _make_brand, _make_item, _make_account, _make_batch,
    _make_snapshot, _make_sale,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_group(company, name, primary, members):
    """Create a DistributorGroup and assign member group FKs."""
    group = DistributorGroup.objects.create(
        company=company, name=name, primary_distributor=primary
    )
    for m in members:
        m.group = group
        m.save(update_fields=['group'])
    return group


def _make_po(distributor, year, month, status='projected'):
    return DistributorPO.objects.create(
        distributor=distributor, year=year, month=month, status=status,
        generated_by_algorithm=True,
    )


def _make_po_line(po, item, quantity_cases):
    return DistributorPOLine.objects.create(
        po=po, item=item, quantity_cases=quantity_cases
    )


# ---------------------------------------------------------------------------
# Algorithm tests
# ---------------------------------------------------------------------------

class GroupForecastComputeTest(TestCase):

    def setUp(self):
        self.company = _make_company('Group Test Co')
        self.brand = _make_brand(self.company)
        self.item_a = _make_item(self.brand, name='Item A', item_code='ITMA', sort_order=1)
        self.item_b = _make_item(self.brand, name='Item B', item_code='ITMB', sort_order=2)

        self.acme = _make_distributor(self.company, name='Acme Dist')
        self.bayside = _make_distributor(self.company, name='Bayside Dist')

        self.group = _make_group(
            self.company, 'Test Group', self.acme, [self.acme, self.bayside]
        )

        self.acme_account = _make_account(self.company, self.acme, name='Acme Retailer')
        self.bayside_account = _make_account(self.company, self.bayside, name='Bayside Retailer')
        self.acme_batch = _make_batch(self.company, self.acme)
        self.bayside_batch = _make_batch(self.company, self.bayside)

    # -----------------------------------------------------------------------
    # 1. Basic aggregation — inventory sums across members at anchor
    # -----------------------------------------------------------------------
    def test_compute_group_forecast_basic_aggregation(self):
        _make_snapshot(self.acme, self.item_a, 2026, 3, quantity=100)
        _make_snapshot(self.bayside, self.item_a, 2026, 3, quantity=50)
        _make_snapshot(self.acme, self.item_b, 2026, 3, quantity=200)
        _make_snapshot(self.bayside, self.item_b, 2026, 3, quantity=80)

        result = compute_group_forecast(self.group, today=date(2026, 5, 1))

        self.assertEqual(result['alignment_status'], 'ok')
        rows_by_item = {r['item'].pk: r for r in result['rows']}

        # Item A anchor: 100 + 50 = 150
        anchor_a = rows_by_item[self.item_a.pk]['monthly_data'][0]
        self.assertEqual(anchor_a['inventory'], 150.0)
        self.assertEqual(anchor_a['status'], 'snapshot')

        # Item B anchor: 200 + 80 = 280
        anchor_b = rows_by_item[self.item_b.pk]['monthly_data'][0]
        self.assertEqual(anchor_b['inventory'], 280.0)

    # -----------------------------------------------------------------------
    # 2. Safety stock comes from primary only
    # -----------------------------------------------------------------------
    def test_compute_group_forecast_uses_primary_safety_stock(self):
        _make_snapshot(self.acme, self.item_a, 2026, 3, quantity=100)
        _make_snapshot(self.bayside, self.item_a, 2026, 3, quantity=50)
        _make_snapshot(self.acme, self.item_b, 2026, 3, quantity=100)
        _make_snapshot(self.bayside, self.item_b, 2026, 3, quantity=100)

        # Primary (acme) has safety stock 80 for item_a
        DistributorItemProfile.objects.create(
            distributor=self.acme, item=self.item_a, safety_stock_cases=80
        )
        # Non-primary (bayside) has safety stock 30 — should be ignored
        DistributorItemProfile.objects.create(
            distributor=self.bayside, item=self.item_a, safety_stock_cases=30
        )

        result = compute_group_forecast(self.group, today=date(2026, 5, 1))

        self.assertEqual(result['alignment_status'], 'ok')
        self.assertEqual(result['safety_stock_map'].get(self.item_a.pk), 80)
        self.assertNotEqual(result['safety_stock_map'].get(self.item_a.pk), 30)

    # -----------------------------------------------------------------------
    # 3. Sales aggregated across members
    # -----------------------------------------------------------------------
    def test_compute_group_forecast_aggregates_sales_across_members(self):
        _make_snapshot(self.acme, self.item_a, 2026, 3, quantity=300)
        _make_snapshot(self.bayside, self.item_a, 2026, 3, quantity=200)
        _make_snapshot(self.acme, self.item_b, 2026, 3, quantity=100)
        _make_snapshot(self.bayside, self.item_b, 2026, 3, quantity=100)

        # Prior-year sales (used for future projection months)
        _make_sale(self.company, self.acme_batch, self.acme_account, self.item_a, 2025, 4, 10)
        _make_sale(self.company, self.bayside_batch, self.bayside_account, self.item_a, 2025, 4, 20)

        result = compute_group_forecast(self.group, today=date(2026, 4, 1))

        self.assertEqual(result['alignment_status'], 'ok')
        rows_by_item = {r['item'].pk: r for r in result['rows']}
        row_a = rows_by_item[self.item_a.pk]

        # Anchor = (2026,3), horizon[1] = Apr 2026 (future, uses prior-year 2025 Apr)
        # Combined sales in Apr 2025 = 10 + 20 = 30; starting = 500; projected = 500 - 30 = 470
        apr_cell = row_a['monthly_data'][1]
        self.assertEqual(apr_cell['year'], 2026)
        self.assertEqual(apr_cell['month'], 4)
        self.assertEqual(apr_cell['depletion'], 30.0)
        self.assertEqual(apr_cell['inventory'], 470.0)

    # -----------------------------------------------------------------------
    # 4. PO additions aggregated across members
    # -----------------------------------------------------------------------
    def test_compute_group_forecast_aggregates_pos_across_members(self):
        _make_snapshot(self.acme, self.item_a, 2026, 3, quantity=10)
        _make_snapshot(self.bayside, self.item_a, 2026, 3, quantity=5)
        _make_snapshot(self.acme, self.item_b, 2026, 3, quantity=100)
        _make_snapshot(self.bayside, self.item_b, 2026, 3, quantity=100)

        _make_sale(self.company, self.acme_batch, self.acme_account, self.item_a, 2025, 4, 5)

        # POs: acme has 24 cases in Apr 2026, bayside has 12 cases in Apr 2026
        po_additions = {
            (self.item_a.pk, 2026, 4): 36.0,  # 24 + 12
        }

        result = compute_group_forecast(
            self.group, po_additions=po_additions, today=date(2026, 4, 1)
        )

        self.assertEqual(result['alignment_status'], 'ok')
        rows_by_item = {r['item'].pk: r for r in result['rows']}
        # starting = 15, apr depletion=5, po=36 → 15 + 36 - 5 = 46
        apr_cell = rows_by_item[self.item_a.pk]['monthly_data'][1]
        self.assertEqual(apr_cell['inventory'], 46.0)

    # -----------------------------------------------------------------------
    # 5. Block when members have snapshots at different periods
    # -----------------------------------------------------------------------
    def test_compute_group_forecast_blocks_when_misaligned_periods(self):
        # Acme at March, Bayside at April — no shared period
        _make_snapshot(self.acme, self.item_a, 2026, 3, quantity=100)
        _make_snapshot(self.acme, self.item_b, 2026, 3, quantity=100)
        _make_snapshot(self.bayside, self.item_a, 2026, 4, quantity=80)
        _make_snapshot(self.bayside, self.item_b, 2026, 4, quantity=80)

        result = compute_group_forecast(self.group, today=date(2026, 5, 1))

        self.assertEqual(result['alignment_status'], 'misaligned')
        self.assertIn('alignment_errors', result)
        self.assertEqual(result['rows'], [])

    # -----------------------------------------------------------------------
    # 6. Block when members have same period but one is missing a required item
    # -----------------------------------------------------------------------
    def test_compute_group_forecast_blocks_when_misaligned_items(self):
        # Bayside has an explicit is_active=True profile for item_b — it is required.
        DistributorItemProfile.objects.create(
            distributor=self.bayside, item=self.item_b, is_active=True
        )
        _make_snapshot(self.acme, self.item_a, 2026, 3, quantity=100)
        _make_snapshot(self.acme, self.item_b, 2026, 3, quantity=100)
        _make_snapshot(self.bayside, self.item_a, 2026, 3, quantity=80)
        # Bayside is missing item_b snapshot despite having an active profile for it.

        result = compute_group_forecast(self.group, today=date(2026, 5, 1))

        self.assertEqual(result['alignment_status'], 'misaligned')
        # Bayside should appear in alignment_errors with item_b listed
        bayside_err = next(
            e for e in result['alignment_errors']
            if e['distributor'] == self.bayside.name
        )
        self.assertIn(self.item_b.name, bayside_err['missing_items'])

    # -----------------------------------------------------------------------
    # 7. Alignment passes when member has explicit inactive profile for an item
    # -----------------------------------------------------------------------
    def test_compute_group_forecast_alignment_passes_with_per_member_active_items(self):
        item_c = _make_item(self.brand, name='Item C', item_code='ITMC', sort_order=3)

        # Acme: carries all 3 items
        _make_snapshot(self.acme, self.item_a, 2026, 3, quantity=100)
        _make_snapshot(self.acme, self.item_b, 2026, 3, quantity=100)
        _make_snapshot(self.acme, item_c, 2026, 3, quantity=100)

        # Bayside: item_c explicitly inactive (no snapshot needed for item_c)
        DistributorItemProfile.objects.create(
            distributor=self.bayside, item=item_c, is_active=False
        )
        _make_snapshot(self.bayside, self.item_a, 2026, 3, quantity=80)
        _make_snapshot(self.bayside, self.item_b, 2026, 3, quantity=80)

        result = compute_group_forecast(self.group, today=date(2026, 5, 1))

        self.assertEqual(result['alignment_status'], 'ok')
        # item_c should be in rows (active for acme, union of active items)
        item_ids = [r['item'].pk for r in result['rows']]
        self.assertIn(item_c.pk, item_ids)

    # -----------------------------------------------------------------------
    # 8. Uses most recent aligned period when multiple exist
    # -----------------------------------------------------------------------
    def test_compute_group_forecast_uses_most_recent_aligned_period(self):
        # Both periods are aligned; most recent should win
        for year, month in [(2026, 2), (2026, 3)]:
            _make_snapshot(self.acme, self.item_a, year, month, quantity=100)
            _make_snapshot(self.acme, self.item_b, year, month, quantity=100)
            _make_snapshot(self.bayside, self.item_a, year, month, quantity=80)
            _make_snapshot(self.bayside, self.item_b, year, month, quantity=80)

        result = compute_group_forecast(self.group, today=date(2026, 5, 1))

        self.assertEqual(result['alignment_status'], 'ok')
        self.assertEqual(result['anchor_period'], (2026, 3))
        self.assertEqual(result['horizon'][0]['year'], 2026)
        self.assertEqual(result['horizon'][0]['month'], 3)

    # -----------------------------------------------------------------------
    # 9. Group with no members returns no_data
    # -----------------------------------------------------------------------
    def test_compute_group_forecast_returns_no_data_for_empty_group_members(self):
        # Create a standalone distributor not already primary of any group,
        # then create a group with it as primary but don't set any group FKs.
        solo_dist = _make_distributor(self.company, name='Solo For Empty')
        empty_group = DistributorGroup.objects.create(
            company=self.company, name='Empty Group', primary_distributor=solo_dist
        )
        # Deliberately NOT setting solo_dist.group = empty_group

        result = compute_group_forecast(empty_group, today=date(2026, 5, 1))

        self.assertEqual(result['alignment_status'], 'no_data')
        self.assertIn('no members', result['message'].lower())

    # -----------------------------------------------------------------------
    # 10. Alignment errors list missing items per member
    # -----------------------------------------------------------------------
    def test_compute_group_forecast_alignment_errors_list_missing_items_per_member(self):
        # Bayside has an explicit is_active=True profile for item_b — it is required.
        DistributorItemProfile.objects.create(
            distributor=self.bayside, item=self.item_b, is_active=True
        )
        # Acme: both items at Mar 2026
        _make_snapshot(self.acme, self.item_a, 2026, 3, quantity=100)
        _make_snapshot(self.acme, self.item_b, 2026, 3, quantity=100)
        # Bayside: only item_a at Mar 2026; item_b is missing despite active profile
        _make_snapshot(self.bayside, self.item_a, 2026, 3, quantity=80)

        result = compute_group_forecast(self.group, today=date(2026, 5, 1))

        self.assertEqual(result['alignment_status'], 'misaligned')
        errors_by_dist = {e['distributor']: e for e in result['alignment_errors']}

        self.assertIn(self.bayside.name, errors_by_dist)
        self.assertIn(self.item_b.name, errors_by_dist[self.bayside.name]['missing_items'])

        # Acme has no explicit is_active=True profiles — nothing required, no missing items.
        self.assertEqual(errors_by_dist[self.acme.name]['missing_items'], [])

    # -----------------------------------------------------------------------
    # 11. Regression: alignment must not require items inactive for a member
    # -----------------------------------------------------------------------
    def test_alignment_does_not_require_items_inactive_for_member(self):
        """
        Regression test for the group-forecast alignment bug.

        Members should only need snapshots for items where THEY have an explicit
        is_active=True DistributorItemProfile. Items active for other members, or
        items with no profile at all, must not be required from a given member.
        """
        item_c = _make_item(self.brand, name='Item C', item_code='ITMC', sort_order=3)

        # Acme carries items A and B (explicit active profiles)
        DistributorItemProfile.objects.create(distributor=self.acme, item=self.item_a, is_active=True)
        DistributorItemProfile.objects.create(distributor=self.acme, item=self.item_b, is_active=True)

        # Bayside carries items A and C — NOT item B (different set from Acme)
        DistributorItemProfile.objects.create(distributor=self.bayside, item=self.item_a, is_active=True)
        DistributorItemProfile.objects.create(distributor=self.bayside, item=item_c, is_active=True)

        # Each member snapshots only their own required items at the same period
        _make_snapshot(self.acme, self.item_a, 2026, 3, quantity=100)
        _make_snapshot(self.acme, self.item_b, 2026, 3, quantity=100)
        _make_snapshot(self.bayside, self.item_a, 2026, 3, quantity=80)
        _make_snapshot(self.bayside, item_c, 2026, 3, quantity=60)
        # Notably: Bayside has NO snapshot for item_b, and that must not block alignment.

        result = compute_group_forecast(self.group, today=date(2026, 5, 1))

        self.assertEqual(result['alignment_status'], 'ok',
                         msg='Alignment should pass when each member has all their own active items.')
        self.assertEqual(result['anchor_period'], (2026, 3))

    # -----------------------------------------------------------------------
    # 12 (renumbered). Walker refactor: individual forecast results unchanged
    # -----------------------------------------------------------------------
    def test_walker_extracted_correctly(self):
        """Confirm individual forecast result matches expected value after refactor."""
        from apps.distribution.forecast import compute_distributor_forecast

        _make_snapshot(self.acme, self.item_a, 2026, 1, quantity=100)
        _make_sale(
            self.company, self.acme_batch, self.acme_account, self.item_a, 2025, 2, 20
        )

        result = compute_distributor_forecast(self.acme, today=date(2026, 1, 20))

        self.assertEqual(result['rows'][0]['monthly_data'][0]['inventory'], 100.0)
        self.assertEqual(result['rows'][0]['monthly_data'][0]['status'], 'snapshot')
        # [1] = Feb 2026, prior-year depletion 20 → 100 - 20 = 80
        self.assertEqual(result['rows'][0]['monthly_data'][1]['inventory'], 80.0)


# ---------------------------------------------------------------------------
# View tests
# ---------------------------------------------------------------------------

class GroupForecastViewTest(TestCase):

    def setUp(self):
        self.company = _make_company('View Test Co')
        self.admin = _make_supplier_admin(self.company, 'view_admin')
        self.brand = _make_brand(self.company)
        self.item_a = _make_item(self.brand, name='Item A', item_code='ITMA', sort_order=1)
        self.item_b = _make_item(self.brand, name='Item B', item_code='ITMB', sort_order=2)

        self.acme = _make_distributor(self.company, name='Acme Dist')
        self.bayside = _make_distributor(self.company, name='Bayside Dist')

        self.group = _make_group(
            self.company, 'MA Group', self.acme, [self.acme, self.bayside]
        )

        self.client = Client()
        self.client.login(username='view_admin', password='testpass123')
        self.url = reverse('distributor_group_forecast', args=[self.group.pk])

    def _add_aligned_snapshots(self, year=2026, month=3):
        for dist in [self.acme, self.bayside]:
            _make_snapshot(dist, self.item_a, year, month, quantity=100)
            _make_snapshot(dist, self.item_b, year, month, quantity=100)

    # -----------------------------------------------------------------------
    # 12. Aligned group renders 200
    # -----------------------------------------------------------------------
    def test_group_forecast_view_renders_aligned(self):
        self._add_aligned_snapshots()
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'MA Group')
        # Should render the forecast grid (not the error block)
        self.assertNotContains(resp, 'snapshots not aligned')

    # -----------------------------------------------------------------------
    # 13. Misaligned group shows error block
    # -----------------------------------------------------------------------
    def test_group_forecast_view_renders_misaligned_with_errors(self):
        # Acme only
        _make_snapshot(self.acme, self.item_a, 2026, 3, quantity=100)
        _make_snapshot(self.acme, self.item_b, 2026, 3, quantity=100)

        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'snapshots not aligned')
        self.assertContains(resp, self.bayside.name)

    # -----------------------------------------------------------------------
    # 14. Requires can_manage_distributor_inventory
    # -----------------------------------------------------------------------
    def test_group_forecast_view_requires_permission(self):
        role, _ = Role.objects.get_or_create(
            codename='test_no_inv_gf', defaults={'name': 'No Inv GF'}
        )
        perm = Permission.objects.get(codename='can_manage_distributors')
        role.permissions.set([perm])
        limited = User.objects.create_user(
            username='limited_gf', password='testpass123', company=self.company
        )
        limited.roles.set([role])

        c = Client()
        c.login(username='limited_gf', password='testpass123')
        resp = c.get(self.url)
        self.assertEqual(resp.status_code, 403)

    # -----------------------------------------------------------------------
    # 15. 404 for group belonging to another company
    # -----------------------------------------------------------------------
    def test_group_forecast_view_404_for_other_company_group(self):
        other_company = _make_company('Other Co')
        other_dist = _make_distributor(other_company, name='Other Dist')
        other_group = _make_group(other_company, 'Other Group', other_dist, [other_dist])

        resp = self.client.get(
            reverse('distributor_group_forecast', args=[other_group.pk])
        )
        self.assertEqual(resp.status_code, 404)

    # -----------------------------------------------------------------------
    # 16. Member list with primary marked
    # -----------------------------------------------------------------------
    def test_group_forecast_view_displays_member_list_with_primary(self):
        self._add_aligned_snapshots()
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, self.acme.name)
        self.assertContains(resp, self.bayside.name)
        self.assertContains(resp, 'Primary')

    # -----------------------------------------------------------------------
    # 17. Banner on individual forecast for a grouped distributor
    # -----------------------------------------------------------------------
    def test_individual_forecast_shows_group_banner_for_grouped_distributor(self):
        _make_snapshot(self.acme, self.item_a, 2026, 3, quantity=100)
        url = reverse('distributor_list') + f'?tab=forecast&forecast_distributor={self.acme.pk}'
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'MA Group')
        self.assertContains(resp, 'View Group Forecast')

    # -----------------------------------------------------------------------
    # 18. No banner for ungrouped distributor
    # -----------------------------------------------------------------------
    def test_individual_forecast_no_banner_for_ungrouped_distributor(self):
        solo = _make_distributor(self.company, name='Solo Dist')
        _make_snapshot(solo, self.item_a, 2026, 3, quantity=100)
        url = reverse('distributor_list') + f'?tab=forecast&forecast_distributor={solo.pk}'
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        self.assertNotContains(resp, 'View Group Forecast')

    # -----------------------------------------------------------------------
    # 19. Dropdown on distributor_list shows Groups optgroup
    # -----------------------------------------------------------------------
    def test_dropdown_shows_groups_optgroup(self):
        resp = self.client.get(reverse('distributor_list') + '?tab=forecast')
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'optgroup')
        self.assertContains(resp, 'Groups')

    # -----------------------------------------------------------------------
    # 20. Dropdown distributor labels show group membership
    # -----------------------------------------------------------------------
    def test_dropdown_distributor_labels_show_group_membership(self):
        resp = self.client.get(reverse('distributor_list') + '?tab=forecast')
        self.assertEqual(resp.status_code, 200)
        # Acme is in MA Group as primary
        self.assertContains(resp, 'MA Group')


# ---------------------------------------------------------------------------
# Modal endpoint tests
# ---------------------------------------------------------------------------

class GroupOrdersModalTest(TestCase):

    def setUp(self):
        self.company = _make_company('Modal Test Co')
        self.admin = _make_supplier_admin(self.company, 'modal_admin')
        self.brand = _make_brand(self.company)
        self.item_a = _make_item(self.brand, name='Item A', item_code='ITMA', sort_order=1)

        self.acme = _make_distributor(self.company, name='Acme Dist')
        self.bayside = _make_distributor(self.company, name='Bayside Dist')

        self.group = _make_group(
            self.company, 'Modal Group', self.acme, [self.acme, self.bayside]
        )

        self.client = Client()
        self.client.login(username='modal_admin', password='testpass123')

    def _url(self, year=2026, month=3):
        return reverse(
            'distributor_group_orders_modal_data',
            kwargs={'group_pk': self.group.pk, 'year': year, 'month': month},
        )

    # -----------------------------------------------------------------------
    # 21. Returns all member POs for the month
    # -----------------------------------------------------------------------
    def test_group_orders_modal_returns_all_member_pos_for_month(self):
        po_acme = _make_po(self.acme, 2026, 3)
        _make_po_line(po_acme, self.item_a, 24)
        po_bayside = _make_po(self.bayside, 2026, 3)
        _make_po_line(po_bayside, self.item_a, 12)

        resp = self.client.get(self._url())
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(len(data['saved_orders']), 2)

    # -----------------------------------------------------------------------
    # 22. Primary flag set correctly
    # -----------------------------------------------------------------------
    def test_group_orders_modal_marks_primary_pos(self):
        po_acme = _make_po(self.acme, 2026, 3)
        _make_po_line(po_acme, self.item_a, 24)
        po_bayside = _make_po(self.bayside, 2026, 3)
        _make_po_line(po_bayside, self.item_a, 12)

        resp = self.client.get(self._url())
        data = resp.json()
        pos_by_dist = {p['distributor_name']: p for p in data['saved_orders']}

        self.assertTrue(pos_by_dist[self.acme.name]['is_primary'])
        self.assertFalse(pos_by_dist[self.bayside.name]['is_primary'])

    # -----------------------------------------------------------------------
    # 23. POs ordered by distributor name
    # -----------------------------------------------------------------------
    def test_group_orders_modal_orders_pos_by_distributor_name(self):
        _make_po(self.bayside, 2026, 3)
        _make_po(self.acme, 2026, 3)

        resp = self.client.get(self._url())
        data = resp.json()
        names = [p['distributor_name'] for p in data['saved_orders']]
        self.assertEqual(names, sorted(names))

    # -----------------------------------------------------------------------
    # 24. Requires permission
    # -----------------------------------------------------------------------
    def test_group_orders_modal_requires_permission(self):
        role, _ = Role.objects.get_or_create(
            codename='test_no_inv_mo', defaults={'name': 'No Inv MO'}
        )
        perm = Permission.objects.get(codename='can_manage_distributors')
        role.permissions.set([perm])
        limited = User.objects.create_user(
            username='limited_mo', password='testpass123', company=self.company
        )
        limited.roles.set([role])

        c = Client()
        c.login(username='limited_mo', password='testpass123')
        resp = c.get(self._url())
        self.assertEqual(resp.status_code, 403)

    # -----------------------------------------------------------------------
    # 25. Scoped to company
    # -----------------------------------------------------------------------
    def test_group_orders_modal_scoped_to_company(self):
        other_company = _make_company('Other Modal Co')
        other_dist = _make_distributor(other_company, name='Other Dist')
        other_group = _make_group(other_company, 'Other Group', other_dist, [other_dist])

        resp = self.client.get(
            reverse(
                'distributor_group_orders_modal_data',
                kwargs={'group_pk': other_group.pk, 'year': 2026, 'month': 3},
            )
        )
        self.assertEqual(resp.status_code, 404)
