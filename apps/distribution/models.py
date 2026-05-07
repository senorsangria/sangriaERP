"""
Distribution models: Distributor, DistributorItemProfile, InventorySnapshot.
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
    Per-distributor per-item configuration: safety stock target and active flag.

    A profile only exists when it holds non-default data:
    - is_active=False (distributor does not carry this item), OR
    - safety_stock_cases is set (a target has been configured).

    A missing profile is equivalent to is_active=True with no safety stock target.
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
    is_active = models.BooleanField(
        default=True,
        help_text=(
            'Whether this distributor carries this item. When inactive, '
            'the item is hidden from inventory views and excluded from forecasts.'
        ),
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


class InventorySnapshot(TimeStampedModel):
    """
    On-hand inventory for a specific (distributor, item) as of a given month.

    quantity_cases may be zero — some snapshots legitimately record zero on hand.
    year + month use the integer-pair convention established in Phase 2a.
    Unique per (distributor, item, year, month): one snapshot per SKU per month.
    """

    distributor = models.ForeignKey(
        'distribution.Distributor',
        on_delete=models.PROTECT,
        related_name='inventory_snapshots',
    )
    item = models.ForeignKey(
        'catalog.Item',
        on_delete=models.PROTECT,
        related_name='inventory_snapshots',
    )
    quantity_cases = models.PositiveIntegerField()
    year = models.IntegerField()
    month = models.IntegerField()
    created_by = models.ForeignKey(
        'core.User',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='inventory_snapshots_created',
    )

    class Meta:
        verbose_name = 'Inventory Snapshot'
        verbose_name_plural = 'Inventory Snapshots'
        unique_together = [['distributor', 'item', 'year', 'month']]
        ordering = ['-year', '-month', 'distributor__name', 'item__name']

    def __str__(self):
        return (
            f'{self.distributor} / {self.item} / '
            f'{self.year}-{self.month:02d}: {self.quantity_cases} cases'
        )
