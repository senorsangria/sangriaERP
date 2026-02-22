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


class Account(TimeStampedModel):
    """
    A physical retail location (liquor store, restaurant, festival, etc.).

    Belongs to a Company.
    Serviced by a Distributor (the distributor services the account; it does
    not own it).
    Optionally linked to a MasterAccount golden record.
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
    name = models.CharField(max_length=255)
    street = models.CharField(max_length=255, blank=True)
    city = models.CharField(max_length=100, blank=True)
    state = models.CharField(max_length=50, blank=True)
    zip_code = models.CharField(max_length=20, blank=True)
    phone = models.CharField(max_length=50, blank=True)
    account_type = models.CharField(
        max_length=20,
        choices=AccountType.choices,
        default=AccountType.OTHER,
    )
    is_active = models.BooleanField(default=True)

    class Meta:
        verbose_name = 'Account'
        verbose_name_plural = 'Accounts'
        ordering = ['company', 'name']

    def __str__(self):
        return f'{self.name} — {self.city}, {self.state}'
