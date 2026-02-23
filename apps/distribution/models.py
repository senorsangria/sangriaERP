"""
Distribution models: Distributor, MasterAccount, Account.
"""
from django.db import models
from apps.core.models import TimeStampedModel


class Distributor(TimeStampedModel):
    """
    A distribution company that services Accounts on behalf of a Brand.

    A Distributor belongs to a Company (tenant) and can be associated with
    multiple Brands.  Cross-tenant distributor sharing is a future feature.
    """

    company = models.ForeignKey(
        'core.Company',
        on_delete=models.PROTECT,
        related_name='distributors',
    )
    brands = models.ManyToManyField(
        'catalog.Brand',
        blank=True,
        related_name='distributors',
    )
    name = models.CharField(max_length=255)
    address = models.CharField(max_length=500, blank=True)
    city = models.CharField(max_length=100, blank=True)
    state = models.CharField(max_length=50, blank=True)
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=50, blank=True)
    notes = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        verbose_name = 'Distributor'
        verbose_name_plural = 'Distributors'
        ordering = ['company', 'name']

    def __str__(self):
        return f'{self.name} ({self.company.name})'


class MasterAccount(TimeStampedModel):
    """
    Golden-record for a retail location.

    Account records will be matched and linked to a MasterAccount in a future
    deduplication phase.  This stub is here so the schema supports it from day 1.
    """

    company = models.ForeignKey(
        'core.Company',
        on_delete=models.PROTECT,
        related_name='master_accounts',
    )
    name = models.CharField(max_length=255, help_text='Canonical name for this retail location.')
    street = models.CharField(max_length=255, blank=True)
    city = models.CharField(max_length=100, blank=True)
    state = models.CharField(max_length=50, blank=True)
    zip_code = models.CharField(max_length=20, blank=True)
    phone = models.CharField(max_length=50, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        verbose_name = 'Master Account'
        verbose_name_plural = 'Master Accounts'
        ordering = ['company', 'name']

    def __str__(self):
        return f'{self.name} (master)'


# ---------------------------------------------------------------------------
# Custom managers for Account
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
    Optionally linked to a MasterAccount golden record.

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
    master_account = models.ForeignKey(
        'distribution.MasterAccount',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='accounts',
        help_text='Golden-record link. Null until deduplication is run.',
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
        verbose_name = 'Account'
        verbose_name_plural = 'Accounts'
        ordering = ['company', 'name']

    def __str__(self):
        return f'{self.name} — {self.city}, {self.state}'
