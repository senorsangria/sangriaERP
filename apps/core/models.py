"""
Core models: Company (tenant), User, and role definitions.
"""
from django.contrib.auth.models import AbstractUser
from django.db import models
from django.utils.text import slugify

from apps.core.rbac import Permission, Role  # noqa: F401 — register models with this app


# ---------------------------------------------------------------------------
# Abstract base — timestamps on every model
# ---------------------------------------------------------------------------

class TimeStampedModel(models.Model):
    """Abstract base class that adds created_at / updated_at to every model."""

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


# ---------------------------------------------------------------------------
# Company — top-level tenant
# ---------------------------------------------------------------------------

class Company(TimeStampedModel):
    """
    Top-level tenant.  Every piece of data in the system belongs to a Company.
    """

    name = models.CharField(max_length=255)
    slug = models.SlugField(max_length=120, unique=True, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        verbose_name = 'Company'
        verbose_name_plural = 'Companies'
        ordering = ['name']

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.name)
        super().save(*args, **kwargs)

    def __str__(self):
        return self.name


# ---------------------------------------------------------------------------
# User — extended with tenant assignment and RBAC roles
# ---------------------------------------------------------------------------

class User(AbstractUser, TimeStampedModel):
    """
    Extended user model.  Inherits created_at / updated_at from
    TimeStampedModel; AbstractUser provides the standard auth fields.

    Roles are assigned via the M2M `roles` field (core.Role).
    Each Role carries a set of Permissions (core.Permission).
    Use has_role() and has_permission() for all access checks.
    """

    # Tenant link — null only for saas_admin
    company = models.ForeignKey(
        'core.Company',
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='users',
    )

    # RBAC role assignments
    roles = models.ManyToManyField(
        'core.Role',
        blank=True,
        related_name='users',
    )

    # Contact phone number (optional for all roles)
    phone = models.CharField(max_length=50, blank=True)

    # Tracks who created this user — enables delegated management chain
    created_by = models.ForeignKey(
        'self',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='created_users',
    )

    class Meta:
        verbose_name = 'User'
        verbose_name_plural = 'Users'
        ordering = ['last_name', 'first_name']

    def __str__(self):
        full = self.get_full_name()
        return full if full.strip() else self.username

    # ------------------------------------------------------------------
    # RBAC helpers
    # ------------------------------------------------------------------

    def get_role_codenames(self) -> set:
        """
        Returns a set of all role codenames assigned to this user.
        Cached on the instance.
        """
        if not hasattr(self, '_role_cache'):
            self._role_cache = set(
                self.roles.values_list('codename', flat=True)
            )
        return self._role_cache

    def get_permission_codenames(self) -> set:
        """
        Returns a set of all permission codenames available to this user
        across all their roles. Cached on the instance.
        """
        if not hasattr(self, '_perm_cache'):
            self._perm_cache = {
                c
                for c in self.roles.values_list('permissions__codename', flat=True)
                if c is not None
            }
        return self._perm_cache

    def has_role(self, codename: str) -> bool:
        """
        Returns True if the user has the role with the given codename.
        Caches the role set on the instance on first call.
        """
        return codename in self.get_role_codenames()

    def has_permission(self, codename: str) -> bool:
        """
        Returns True if any of the user's roles has the given permission.
        Caches the full permission set on the instance on first call.
        """
        return codename in self.get_permission_codenames()

    # ------------------------------------------------------------------
    # Role convenience properties — implemented via has_role() so
    # existing template and view checks continue to work unchanged.
    # ------------------------------------------------------------------

    @property
    def is_saas_admin(self):
        return self.has_role('saas_admin')

    @property
    def is_supplier_admin(self):
        return self.has_role('supplier_admin')

    @property
    def is_sales_manager(self):
        return self.has_role('sales_manager')

    @property
    def is_territory_manager(self):
        return self.has_role('territory_manager')

    @property
    def is_ambassador_manager(self):
        return self.has_role('ambassador_manager')

    @property
    def is_ambassador(self):
        return self.has_role('ambassador')

    @property
    def is_distributor_contact(self):
        return self.has_role('distributor_contact')

    @property
    def is_payroll_reviewer(self):
        return self.has_role('payroll_reviewer')
