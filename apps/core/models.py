"""
Core models: Company (tenant), User, and role definitions.
"""
from django.contrib.auth.models import AbstractUser
from django.db import models
from django.utils.text import slugify


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
# User — extended with tenant assignment and role
# ---------------------------------------------------------------------------

class User(AbstractUser, TimeStampedModel):
    """
    Extended user model.  Inherits created_at / updated_at from
    TimeStampedModel; AbstractUser provides the standard auth fields.

    Roles
    -----
    SAAS_ADMIN          Platform-level; not scoped to any company.
    SUPPLIER_ADMIN      Superuser within their Company tenant.
    SALES_MANAGER       Sees all distributors/accounts in their Company.
    TERRITORY_MANAGER   Same as Sales Manager but scoped to assigned accounts.
    AMBASSADOR_MANAGER  Manages specific accounts and ambassadors.
    AMBASSADOR          Scoped to their own assigned events only.
    DISTRIBUTOR_CONTACT Read-only access scoped to their distributor.
    """

    class Role(models.TextChoices):
        SAAS_ADMIN = 'saas_admin', 'SaaS Admin'
        SUPPLIER_ADMIN = 'supplier_admin', 'Supplier Admin'
        SALES_MANAGER = 'sales_manager', 'Sales Manager'
        TERRITORY_MANAGER = 'territory_manager', 'Territory Manager'
        AMBASSADOR_MANAGER = 'ambassador_manager', 'Ambassador Manager'
        AMBASSADOR = 'ambassador', 'Ambassador'
        DISTRIBUTOR_CONTACT = 'distributor_contact', 'Distributor Contact'

    # Tenant link — null only for SAAS_ADMIN
    company = models.ForeignKey(
        'core.Company',
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='users',
    )

    role = models.CharField(
        max_length=30,
        choices=Role.choices,
        default=Role.AMBASSADOR,
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
    # Role convenience properties
    # ------------------------------------------------------------------

    @property
    def is_saas_admin(self):
        return self.role == self.Role.SAAS_ADMIN

    @property
    def is_supplier_admin(self):
        return self.role == self.Role.SUPPLIER_ADMIN

    @property
    def is_sales_manager(self):
        return self.role == self.Role.SALES_MANAGER

    @property
    def is_territory_manager(self):
        return self.role == self.Role.TERRITORY_MANAGER

    @property
    def is_ambassador_manager(self):
        return self.role == self.Role.AMBASSADOR_MANAGER

    @property
    def is_ambassador(self):
        return self.role == self.Role.AMBASSADOR

    @property
    def is_distributor_contact(self):
        return self.role == self.Role.DISTRIBUTOR_CONTACT
