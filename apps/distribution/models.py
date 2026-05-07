"""
Distribution models: Distributor, DistributorItemProfile.
"""
from django.db import models
from apps.core.models import TimeStampedModel


class Distributor(TimeStampedModel):
    """
    A distribution company that services Accounts on behalf of a Brand.

    A Distributor belongs to a Company (tenant).
    Cross-tenant distributor sharing is a future feature.
    """

    class OrderQuantityUnit(models.TextChoices):
        PALLETS = 'pallets', 'Pallets'
        CASES   = 'cases',   'Cases'

    company = models.ForeignKey(
        'core.Company',
        on_delete=models.PROTECT,
        related_name='distributors',
    )
    name = models.CharField(max_length=255)
    address = models.CharField(max_length=500, blank=True)
    city = models.CharField(max_length=100, blank=True)
    state = models.CharField(max_length=50, blank=True)
    notes = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)
    order_quantity_value = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text='Typical order size in pallets or cases. Leave blank if not yet configured.',
    )
    order_quantity_unit = models.CharField(
        max_length=10,
        choices=OrderQuantityUnit.choices,
        null=True,
        blank=True,
        help_text='Whether the order quantity is in pallets or cases.',
    )

    class Meta:
        verbose_name = 'Distributor'
        verbose_name_plural = 'Distributors'
        ordering = ['company', 'name']

    def __str__(self):
        return f'{self.name} ({self.company.name})'


class DistributorItemProfile(TimeStampedModel):
    """
    Per-distributor per-item safety stock target.

    A null safety_stock_cases means the value has not yet been configured.
    A missing record is equivalent to null (no profile set).
    """

    distributor = models.ForeignKey(
        'distribution.Distributor',
        on_delete=models.PROTECT,
        related_name='item_profiles',
    )
    item = models.ForeignKey(
        'catalog.Item',
        on_delete=models.PROTECT,
        related_name='distributor_profiles',
    )
    safety_stock_cases = models.PositiveIntegerField(
        null=True,
        blank=True,
    )

    class Meta:
        verbose_name = 'Distributor Item Profile'
        verbose_name_plural = 'Distributor Item Profiles'
        unique_together = [['distributor', 'item']]

    def __str__(self):
        return (
            f'Safety stock for {self.item} at {self.distributor}: '
            f'{self.safety_stock_cases} cases'
        )
