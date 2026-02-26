"""
Accounts models: Account and UserCoverageArea.
"""
from django.db import models
from apps.core.models import TimeStampedModel


# ---------------------------------------------------------------------------
# Custom managers
# ---------------------------------------------------------------------------

class ActiveAccountManager(models.Manager):
    """
    Automatically filters to accounts that are active and not merged.

    Filters applied:
    - is_active=True
    - merged_into__isnull=True

    Use Account.active_accounts.filter(...) in all queries to automatically
    exclude inactive and merged accounts without manual filtering.
    """

    def get_queryset(self):
        return super().get_queryset().filter(
            is_active=True,
            merged_into__isnull=True,
        )


class Account(TimeStampedModel):
    """
    A physical retail location (liquor store, restaurant, festival, etc.).

    Belongs to a Company.
    Serviced by a Distributor (the distributor services the account; it does
    not own it).

    Address normalization:
    - street / city / state: original values, preserved exactly as received
    - address_normalized / city_normalized / state_normalized: uppercase,
      trimmed, abbreviations standardized — used for matching and deduplication
    """

    class AccountType(models.TextChoices):
        LIQUOR_STORE = 'liquor_store', 'Liquor Store'
        RESTAURANT = 'restaurant', 'Restaurant'
        FESTIVAL = 'festival', 'Festival'
        OTHER = 'other', 'Other'

    company = models.ForeignKey(
        'core.Company',
        on_delete=models.PROTECT,
        related_name='accounts',
    )
    distributor = models.ForeignKey(
        'distribution.Distributor',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='accounts',
        help_text='The distributor that services this account.',
    )

    # --- Merge support (Phase 2.3) ---
    merged_into = models.ForeignKey(
        'self',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='merged_accounts',
        help_text='If set, this account has been merged into the referenced account.',
    )
    merge_note = models.TextField(
        blank=True,
        help_text='Reason or notes captured when this account was merged.',
    )

    name = models.CharField(max_length=255)

    # Original address values — preserved exactly as received for display
    street = models.CharField(max_length=255, blank=True)
    city = models.CharField(max_length=100, blank=True)
    state = models.CharField(max_length=50, blank=True)
    zip_code = models.CharField(max_length=20, blank=True)
    phone = models.CharField(max_length=50, blank=True)

    # Normalized address values — used for matching and conflict detection only
    address_normalized = models.CharField(max_length=255, blank=True)
    city_normalized = models.CharField(max_length=100, blank=True)
    state_normalized = models.CharField(max_length=50, blank=True)

    # Additional fields from VIP/distributor exports
    vip_outlet_id = models.CharField(
        max_length=100,
        blank=True,
        help_text='VIP Outlet ID from distributor export. Reference only, not used as unique key.',
    )
    county = models.CharField(max_length=100, blank=True, default='Unknown')
    on_off_premise = models.CharField(
        max_length=10,
        blank=True,
        default='Unknown',
        help_text='ON, OFF, or Unknown.',
    )

    account_type = models.CharField(
        max_length=20,
        choices=AccountType.choices,
        default=AccountType.OTHER,
    )
    is_active = models.BooleanField(default=True)
    auto_created = models.BooleanField(
        default=False,
        help_text='True if this account was created automatically by a sales data import.',
    )

    # Default manager — returns all accounts (needed for admin and merge tool)
    objects = models.Manager()

    # Active accounts manager — excludes inactive and merged accounts.
    # Use this in all report and display queries.
    active_accounts = ActiveAccountManager()

    class Meta:
        app_label = 'accounts'
        verbose_name = 'Account'
        verbose_name_plural = 'Accounts'
        ordering = ['company', 'name']

    def __str__(self):
        return f'{self.name} — {self.city}, {self.state}'


class UserCoverageArea(TimeStampedModel):
    """
    Defines the geographic or organizational scope of a user's coverage.

    Replaces the removed M2M fields (assigned_distributors, assigned_accounts,
    territory) on User with a flexible, typed coverage model.
    """

    class CoverageType(models.TextChoices):
        DISTRIBUTOR = 'distributor', 'Distributor'
        STATE = 'state', 'State'
        COUNTY = 'county', 'County'
        CITY = 'city', 'City'
        ACCOUNT = 'account', 'Account'

    company = models.ForeignKey(
        'core.Company',
        on_delete=models.PROTECT,
        related_name='user_coverage_areas',
    )
    user = models.ForeignKey(
        'core.User',
        on_delete=models.PROTECT,
        related_name='coverage_areas',
    )
    coverage_type = models.CharField(
        max_length=20,
        choices=CoverageType.choices,
    )
    distributor = models.ForeignKey(
        'distribution.Distributor',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='coverage_areas',
    )
    account = models.ForeignKey(
        'accounts.Account',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='coverage_areas',
    )
    state = models.CharField(max_length=100, blank=True)
    county = models.CharField(max_length=100, blank=True)
    city = models.CharField(max_length=100, blank=True)

    class Meta:
        app_label = 'accounts'
        verbose_name = 'User Coverage Area'
        verbose_name_plural = 'User Coverage Areas'
        ordering = ['company', 'user', 'coverage_type']

    def __str__(self):
        return f'{self.user} — {self.get_coverage_type_display()}'
