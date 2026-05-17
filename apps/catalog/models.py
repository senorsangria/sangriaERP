"""
Catalog models: Brand, Item (SKU), and CoPacker.
"""
from django.db import models
from apps.core.models import TimeStampedModel


class CoPacker(TimeStampedModel):
    """
    A co-packer (contract manufacturer) that produces items on behalf of a company.

    Scoped to a Company (tenant). A co-packer is a production attribute — it lives
    in catalog to avoid a circular migration dependency with apps.production.
    """

    company = models.ForeignKey(
        'core.Company',
        on_delete=models.PROTECT,
        related_name='co_packers',
    )
    name = models.CharField(max_length=255)
    notes = models.TextField(blank=True, default='')
    is_active = models.BooleanField(default=True)

    class Meta:
        verbose_name = 'Co-Packer'
        verbose_name_plural = 'Co-Packers'
        unique_together = [['company', 'name']]
        ordering = ['name']

    def __str__(self):
        return f'{self.name} ({self.company.name})'


class Brand(TimeStampedModel):
    """
    A product brand belonging to a Company.
    Example: "Señor Sangria", "Backyard Barrel Co".
    """

    company = models.ForeignKey(
        'core.Company',
        on_delete=models.PROTECT,
        related_name='brands',
    )
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        verbose_name = 'Brand'
        verbose_name_plural = 'Brands'
        ordering = ['company', 'name']
        unique_together = [['company', 'name']]

    def __str__(self):
        return f'{self.name} ({self.company.name})'


class Item(TimeStampedModel):
    """
    A specific SKU belonging to a Brand.

    item_code   — internal system code (e.g. "Red0750").  Unique within a Brand.
    sku_number  — external SKU from a distributor/retailer system. Optional.
    """

    brand = models.ForeignKey(
        'catalog.Brand',
        on_delete=models.PROTECT,
        related_name='items',
    )
    name = models.CharField(max_length=255)
    item_code = models.CharField(
        max_length=100,
        help_text='Internal item code, e.g. Red0750. Unique within a Brand.',
    )
    sku_number = models.CharField(
        max_length=100,
        blank=True,
        help_text='External SKU number (e.g. from distributor system). Optional.',
    )
    description = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)
    sort_order = models.PositiveIntegerField(
        default=0,
        help_text='Display order within this brand. Lower values appear first.',
    )
    cases_per_pallet = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text='Number of cases that fit on one pallet for this item. Used by the distributor inventory and forecasting tools.',
    )
    co_packer = models.ForeignKey(
        'catalog.CoPacker',
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='items',
        help_text='The co-packer that produces this item.',
    )
    cases_per_batch = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text='Number of cases produced in one batch. Used by production projections.',
    )
    production_safety_stock_cases = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text='Minimum on-hand inventory to trigger production. Used by production projections.',
    )

    class Meta:
        verbose_name = 'Item'
        verbose_name_plural = 'Items'
        ordering = ['brand', 'sort_order', 'name']
        unique_together = [['brand', 'item_code']]

    def __str__(self):
        return f'{self.item_code} — {self.name} ({self.brand.name})'

    @property
    def company(self):
        """Convenience accessor: traverse brand → company."""
        return self.brand.company
