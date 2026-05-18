"""
Tests for Distributor Groups — Phase G1.

Covers:
- DistributorGroup model: creation, str, unique_together, primary unique constraint, cascade behavior
- DistributorGroupForm: validation (unique name, primary not in members, empty members) and save sync
- Group views: list, create, edit, delete (permission gating, happy paths)
- Distributor list: grouped view by default, flat view when searching, primary badge
- Distributor edit: read-only group display
- Permission: supplier_admin has can_manage_distributor_groups
- Nav: active_match word-boundary fix
"""
from django.db import IntegrityError
from django.db.models import ProtectedError
from django.test import Client, TestCase
from django.urls import reverse

from apps.core.models import Company, User
from apps.core.rbac import Permission, Role
from apps.distribution.forms import DistributorGroupForm
from apps.distribution.models import Distributor, DistributorGroup


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_company(name='Test Co'):
    return Company.objects.create(name=name)


def make_supplier_admin(company, username='admin'):
    user = User.objects.create_user(username=username, password='testpass123', company=company)
    user.roles.set([Role.objects.get(codename='supplier_admin')])
    return user


def make_user_with_role(company, role_codename, username='limited'):
    user = User.objects.create_user(username=username, password='testpass123', company=company)
    user.roles.set([Role.objects.get(codename=role_codename)])
    return user


def make_distributor(company, name='Dist A'):
    return Distributor.objects.create(company=company, name=name)


def make_group(company, name='Group A', primary=None, members=None):
    if primary is None:
        primary = make_distributor(company, name=f'{name} Primary')
    group = DistributorGroup.objects.create(company=company, name=name, primary_distributor=primary)
    if members:
        for d in members:
            d.group = group
            d.save(update_fields=['group'])
    return group


# ---------------------------------------------------------------------------
# Model tests
# ---------------------------------------------------------------------------

class DistributorGroupModelTest(TestCase):

    def setUp(self):
        self.company = make_company()
        self.dist_a = make_distributor(self.company, 'Dist A')
        self.dist_b = make_distributor(self.company, 'Dist B')

    def test_distributor_group_create_and_str(self):
        group = DistributorGroup.objects.create(
            company=self.company,
            name='West Coast',
            primary_distributor=self.dist_a,
        )
        self.assertEqual(str(group), 'West Coast')
        self.assertEqual(group.company, self.company)

    def test_distributor_group_name_unique_per_company(self):
        DistributorGroup.objects.create(
            company=self.company, name='Alpha', primary_distributor=self.dist_a
        )
        with self.assertRaises(IntegrityError):
            DistributorGroup.objects.create(
                company=self.company, name='Alpha', primary_distributor=self.dist_b
            )

    def test_distributor_group_name_not_unique_across_companies(self):
        other_company = make_company('Other Co')
        other_dist = make_distributor(other_company, 'Other Dist')
        DistributorGroup.objects.create(company=self.company, name='Alpha', primary_distributor=self.dist_a)
        # Should not raise
        DistributorGroup.objects.create(company=other_company, name='Alpha', primary_distributor=other_dist)

    def test_distributor_group_primary_distributor_unique(self):
        DistributorGroup.objects.create(
            company=self.company, name='Group 1', primary_distributor=self.dist_a
        )
        with self.assertRaises(IntegrityError):
            DistributorGroup.objects.create(
                company=self.company, name='Group 2', primary_distributor=self.dist_a
            )

    def test_distributor_group_protect_on_primary_distributor_delete(self):
        DistributorGroup.objects.create(
            company=self.company, name='Group 1', primary_distributor=self.dist_a
        )
        with self.assertRaises(ProtectedError):
            self.dist_a.delete()

    def test_distributor_group_set_null_on_group_delete(self):
        group = DistributorGroup.objects.create(
            company=self.company, name='Group 1', primary_distributor=self.dist_a
        )
        self.dist_b.group = group
        self.dist_b.save(update_fields=['group'])
        group.delete()
        self.dist_b.refresh_from_db()
        self.assertIsNone(self.dist_b.group)

    def test_distributor_group_fk_set_null_when_group_deleted(self):
        group = DistributorGroup.objects.create(
            company=self.company, name='Group 1', primary_distributor=self.dist_a
        )
        self.dist_a.group = group
        self.dist_a.save(update_fields=['group'])
        # Cannot delete dist_a while it is primary (PROTECT), so use dist_b
        self.dist_b.group = group
        self.dist_b.save(update_fields=['group'])
        # Delete by unlinking primary first, then deleting group
        group.primary_distributor = self.dist_b
        group.save(update_fields=['primary_distributor'])
        group.delete()
        self.dist_a.refresh_from_db()
        self.assertIsNone(self.dist_a.group)


# ---------------------------------------------------------------------------
# Form tests
# ---------------------------------------------------------------------------

class DistributorGroupFormTest(TestCase):

    def setUp(self):
        self.company = make_company()
        self.dist_a = make_distributor(self.company, 'Dist A')
        self.dist_b = make_distributor(self.company, 'Dist B')
        self.dist_c = make_distributor(self.company, 'Dist C')

    def test_form_clean_name_unique_within_company(self):
        DistributorGroup.objects.create(
            company=self.company, name='Taken', primary_distributor=self.dist_a
        )
        form = DistributorGroupForm(
            data={'name': 'Taken', 'primary_distributor': self.dist_b.pk, 'members': [self.dist_b.pk], 'notes': ''},
            company=self.company,
        )
        self.assertFalse(form.is_valid())
        self.assertIn('name', form.errors)

    def test_form_allows_same_name_on_edit(self):
        group = DistributorGroup.objects.create(
            company=self.company, name='Existing', primary_distributor=self.dist_a
        )
        self.dist_a.group = group
        self.dist_a.save(update_fields=['group'])
        form = DistributorGroupForm(
            data={'name': 'Existing', 'primary_distributor': self.dist_a.pk, 'members': [self.dist_a.pk], 'notes': ''},
            instance=group,
            company=self.company,
        )
        self.assertTrue(form.is_valid(), form.errors)

    def test_form_rejects_primary_not_in_members(self):
        form = DistributorGroupForm(
            data={
                'name': 'New Group',
                'primary_distributor': self.dist_a.pk,
                'members': [self.dist_b.pk],
                'notes': '',
            },
            company=self.company,
        )
        self.assertFalse(form.is_valid())
        self.assertIn('primary_distributor', form.errors)

    def test_form_rejects_empty_members(self):
        form = DistributorGroupForm(
            data={
                'name': 'New Group',
                'primary_distributor': self.dist_a.pk,
                'members': [],
                'notes': '',
            },
            company=self.company,
        )
        self.assertFalse(form.is_valid())
        self.assertIn('members', form.errors)

    def test_form_save_syncs_member_group_fks(self):
        form = DistributorGroupForm(
            data={
                'name': 'West',
                'primary_distributor': self.dist_a.pk,
                'members': [self.dist_a.pk, self.dist_b.pk],
                'notes': '',
            },
            company=self.company,
        )
        self.assertTrue(form.is_valid(), form.errors)
        group = form.save()
        self.dist_a.refresh_from_db()
        self.dist_b.refresh_from_db()
        self.assertEqual(self.dist_a.group, group)
        self.assertEqual(self.dist_b.group, group)

    def test_form_remove_member_clears_their_group(self):
        group = DistributorGroup.objects.create(
            company=self.company, name='West', primary_distributor=self.dist_a
        )
        self.dist_a.group = group
        self.dist_a.save(update_fields=['group'])
        self.dist_b.group = group
        self.dist_b.save(update_fields=['group'])

        # Edit: remove dist_b
        form = DistributorGroupForm(
            data={
                'name': 'West',
                'primary_distributor': self.dist_a.pk,
                'members': [self.dist_a.pk],
                'notes': '',
            },
            instance=group,
            company=self.company,
        )
        self.assertTrue(form.is_valid(), form.errors)
        form.save()
        self.dist_b.refresh_from_db()
        self.assertIsNone(self.dist_b.group)


# ---------------------------------------------------------------------------
# View tests
# ---------------------------------------------------------------------------

class DistributorGroupViewTest(TestCase):

    def setUp(self):
        self.company = make_company()
        self.admin = make_supplier_admin(self.company, 'admin')
        self.limited = make_user_with_role(self.company, 'ambassador', 'limited')
        self.dist_a = make_distributor(self.company, 'Dist A')
        self.dist_b = make_distributor(self.company, 'Dist B')
        self.client = Client()

    def _login(self, user):
        self.client.login(username=user.username, password='testpass123')

    def test_group_list_requires_permission(self):
        self._login(self.limited)
        resp = self.client.get(reverse('distributor_group_list'))
        self.assertEqual(resp.status_code, 403)

    def test_group_list_accessible_to_supplier_admin(self):
        self._login(self.admin)
        resp = self.client.get(reverse('distributor_group_list'))
        self.assertEqual(resp.status_code, 200)

    def test_group_create_renders_and_saves(self):
        self._login(self.admin)
        resp = self.client.get(reverse('distributor_group_create'))
        self.assertEqual(resp.status_code, 200)

        resp = self.client.post(reverse('distributor_group_create'), {
            'name': 'New Group',
            'primary_distributor': self.dist_a.pk,
            'members': [self.dist_a.pk, self.dist_b.pk],
            'notes': '',
        })
        self.assertRedirects(resp, reverse('distributor_group_list'))
        group = DistributorGroup.objects.get(name='New Group', company=self.company)
        self.assertEqual(group.primary_distributor, self.dist_a)
        self.dist_a.refresh_from_db()
        self.assertEqual(self.dist_a.group, group)

    def test_group_edit_preserves_data(self):
        group = make_group(self.company, 'Edit Me', primary=self.dist_a, members=[self.dist_a])
        self._login(self.admin)
        resp = self.client.get(reverse('distributor_group_edit', kwargs={'pk': group.pk}))
        self.assertEqual(resp.status_code, 200)

        resp = self.client.post(reverse('distributor_group_edit', kwargs={'pk': group.pk}), {
            'name': 'Renamed',
            'primary_distributor': self.dist_a.pk,
            'members': [self.dist_a.pk, self.dist_b.pk],
            'notes': 'updated',
        })
        self.assertRedirects(resp, reverse('distributor_group_list'))
        group.refresh_from_db()
        self.assertEqual(group.name, 'Renamed')
        self.dist_b.refresh_from_db()
        self.assertEqual(self.dist_b.group, group)

    def test_group_delete_ungroups_members(self):
        group = make_group(self.company, 'Del Me', primary=self.dist_a, members=[self.dist_a, self.dist_b])
        group_pk = group.pk
        self._login(self.admin)

        # GET shows confirm page
        resp = self.client.get(reverse('distributor_group_delete', kwargs={'pk': group_pk}))
        self.assertEqual(resp.status_code, 200)

        resp = self.client.post(reverse('distributor_group_delete', kwargs={'pk': group_pk}))
        self.assertRedirects(resp, reverse('distributor_group_list'))
        self.assertFalse(DistributorGroup.objects.filter(pk=group_pk).exists())
        self.dist_b.refresh_from_db()
        self.assertIsNone(self.dist_b.group)

    def test_group_create_permission_denied_for_limited(self):
        self._login(self.limited)
        resp = self.client.post(reverse('distributor_group_create'), {
            'name': 'Bad', 'primary_distributor': self.dist_a.pk, 'members': [self.dist_a.pk], 'notes': '',
        })
        self.assertEqual(resp.status_code, 403)


# ---------------------------------------------------------------------------
# Distributor list view tests
# ---------------------------------------------------------------------------

class DistributorListGroupedViewTest(TestCase):

    def setUp(self):
        self.company = make_company()
        self.admin = make_supplier_admin(self.company, 'admin')
        self.dist_a = make_distributor(self.company, 'Alpha')
        self.dist_b = make_distributor(self.company, 'Beta')
        self.dist_c = make_distributor(self.company, 'Gamma')  # ungrouped
        self.group = make_group(self.company, 'West', primary=self.dist_a, members=[self.dist_a, self.dist_b])
        self.client = Client()
        self.client.login(username='admin', password='testpass123')

    def test_distributor_list_shows_grouped_view(self):
        resp = self.client.get(reverse('distributor_list'))
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.context['is_grouped_view'])
        self.assertIsNotNone(resp.context['grouped_data'])
        self.assertIsNotNone(resp.context['ungrouped_data'])

    def test_distributor_list_shows_flat_view_when_searching(self):
        resp = self.client.get(reverse('distributor_list') + '?q=Alpha')
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(resp.context['is_grouped_view'])
        self.assertIsNotNone(resp.context['distributors_flat'])

    def test_distributor_list_shows_primary_badge(self):
        resp = self.client.get(reverse('distributor_list'))
        self.assertContains(resp, 'Primary')

    def test_distributor_list_shows_ungrouped_section(self):
        resp = self.client.get(reverse('distributor_list'))
        ungrouped = resp.context['ungrouped_data']
        self.assertIn(self.dist_c, ungrouped)
        self.assertContains(resp, 'Ungrouped')

    def test_distributor_list_total_count(self):
        resp = self.client.get(reverse('distributor_list'))
        self.assertEqual(resp.context['total_count'], 3)


# ---------------------------------------------------------------------------
# Distributor edit: read-only group display
# ---------------------------------------------------------------------------

class DistributorEditGroupDisplayTest(TestCase):

    def setUp(self):
        self.company = make_company()
        self.admin = make_supplier_admin(self.company, 'admin')
        self.dist_a = make_distributor(self.company, 'Alpha')
        self.dist_b = make_distributor(self.company, 'Beta')
        self.group = make_group(self.company, 'West', primary=self.dist_a, members=[self.dist_a, self.dist_b])
        self.client = Client()
        self.client.login(username='admin', password='testpass123')

    def test_distributor_edit_shows_read_only_group_field(self):
        resp = self.client.get(reverse('distributor_edit', kwargs={'pk': self.dist_a.pk}))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'West')
        self.assertContains(resp, 'Distributor Groups admin page')

    def test_distributor_edit_shows_none_when_no_group(self):
        ungrouped = make_distributor(self.company, 'Ungrouped Dist')
        resp = self.client.get(reverse('distributor_edit', kwargs={'pk': ungrouped.pk}))
        self.assertContains(resp, '— None —')

    def test_distributor_edit_shows_primary_badge(self):
        resp = self.client.get(reverse('distributor_edit', kwargs={'pk': self.dist_a.pk}))
        self.assertContains(resp, 'Primary')


# ---------------------------------------------------------------------------
# Permission tests
# ---------------------------------------------------------------------------

class DistributorGroupsPermissionTest(TestCase):

    def test_supplier_admin_has_can_manage_distributor_groups(self):
        company = make_company()
        admin = make_supplier_admin(company, 'admin')
        self.assertTrue(admin.has_permission('can_manage_distributor_groups'))

    def test_other_roles_do_not_have_can_manage_distributor_groups(self):
        company = make_company()
        user = make_user_with_role(company, 'ambassador', 'amb')
        self.assertFalse(user.has_permission('can_manage_distributor_groups'))


# ---------------------------------------------------------------------------
# Nav active_match word-boundary test
# ---------------------------------------------------------------------------

class NavActiveMatchWordBoundaryTest(TestCase):

    def _nav_is_active(self, nav_url_name, current_url_name, user, company):
        """Return is_active for a specific nav item given a simulated current URL."""
        from unittest.mock import MagicMock
        from apps.core.nav import get_nav_for_user

        request = MagicMock()
        request.resolver_match = MagicMock()
        request.resolver_match.url_name = current_url_name

        sections = get_nav_for_user(user, request)
        for section in sections:
            for item in section['items']:
                if item.get('url_name') == nav_url_name:
                    return item['is_active']
        return None  # item not visible

    def setUp(self):
        self.company = make_company('Nav Test Co')
        self.admin = make_supplier_admin(self.company, 'nav_admin')

    def test_nav_active_match_word_boundary(self):
        # Distributors nav item should NOT be active on distributor_group_list
        # (most-specific match wins: distributor_group beats distributor)
        is_active = self._nav_is_active('distributor_list', 'distributor_group_list', self.admin, self.company)
        self.assertFalse(is_active, "'distributor' active_match should not win over 'distributor_group' for distributor_group_list")

        # Distributors nav item SHOULD be active on distributor_list
        is_active = self._nav_is_active('distributor_list', 'distributor_list', self.admin, self.company)
        self.assertTrue(is_active)

        # Distributors nav item SHOULD be active on distributor_edit
        is_active = self._nav_is_active('distributor_list', 'distributor_edit', self.admin, self.company)
        self.assertTrue(is_active)

    def test_distributor_group_nav_item_active_match(self):
        for url in ('distributor_group_list', 'distributor_group_create', 'distributor_group_edit', 'distributor_group_delete'):
            is_active = self._nav_is_active('distributor_group_list', url, self.admin, self.company)
            self.assertTrue(is_active, f"distributor_group_list nav item should be active for url '{url}'")
