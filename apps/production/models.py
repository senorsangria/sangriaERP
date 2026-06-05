"""
Production models: ProductionPO, ProductionPOLine, OwnInventorySnapshot.
"""
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models

from apps.core.models import TimeStampedModel


class ProductionPO(TimeStampedModel):
    """
    A projected, actual, or complete production purchase order to a co-packer for a given month.

    Mirrors DistributorPO structure but scoped by (company, co_packer, year, month)
    instead of distributor. Multiple POs per (co_packer, year, month) are allowed.

    Status ACTUAL or COMPLETE requires an external_po_number (enforced in clean()).
    """

    class Status(models.TextChoices):
        PROJECTED = 'projected', 'Projected'
        ACTUAL    = 'actual',    'Actual'
        COMPLETE  = 'complete',  'Complete'

    company = models.ForeignKey(
        'core.Company',
        on_delete=models.PROTECT,
        related_name='production_pos',
    )
    co_packer = models.ForeignKey(
        'catalog.CoPacker',
        on_delete=models.PROTECT,
        related_name='production_pos',
    )
    year = models.IntegerField()
    month = models.IntegerField()
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PROJECTED,
    )
    external_po_number = models.CharField(max_length=100, blank=True, default='')
    generated_by_algorithm = models.BooleanField(
        default=True,
        help_text='True when created by the production algorithm; False when manually entered.',
    )
    notes = models.TextField(blank=True, default='')
    created_by = models.ForeignKey(
        'core.User',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='production_pos_created',
    )

    class Meta:
        verbose_name = 'Production PO'
        verbose_name_plural = 'Production POs'
        ordering = ['-year', '-month', 'co_packer__name']

    def __str__(self):
        return f'{self.co_packer} / {self.year}-{self.month:02d} ({self.get_status_display()})'

    def clean(self):
        super().clean()
        if self.status in (self.Status.ACTUAL, self.Status.COMPLETE) and not self.external_po_number:
            raise ValidationError({
                'external_po_number': 'PO number is required when status is Actual or Complete.'
            })


class ProductionPOLine(TimeStampedModel):
    """
    One line item in a ProductionPO — a specific item, batch count, and case quantity.

    batch_count tracks whole batches ordered.
    quantity_cases stores the total case equivalent (batch_count × item.cases_per_batch).
    Stored as Decimal for consistency with DistributorPOLine and InventorySnapshot,
    even though production quantities are typically whole numbers.
    """

    po = models.ForeignKey(
        'production.ProductionPO',
        on_delete=models.CASCADE,
        related_name='lines',
    )
    item = models.ForeignKey(
        'catalog.Item',
        on_delete=models.PROTECT,
        related_name='production_po_lines',
    )
    batch_count = models.PositiveIntegerField()
    quantity_cases = models.DecimalField(max_digits=10, decimal_places=6)

    class Meta:
        verbose_name = 'Production PO Line'
        verbose_name_plural = 'Production PO Lines'
        unique_together = [['po', 'item']]
        ordering = ['item__brand__name', 'item__sort_order', 'item__name']

    def __str__(self):
        return f'{self.po} / {self.item}: {self.batch_count} batch(es), {self.quantity_cases} cases'


class OwnInventorySnapshot(TimeStampedModel):
    """
    Señor Sangria's own on-hand inventory for a specific (company, item) as of a given month.

    Entered manually via the Production UI (no CSV import flow).
    Unique per (company, item, year, month): one snapshot per SKU per month.
    year + month follow the integer-pair convention established in Phase 2a.
    """

    company = models.ForeignKey(
        'core.Company',
        on_delete=models.PROTECT,
        related_name='own_inventory_snapshots',
    )
    item = models.ForeignKey(
        'catalog.Item',
        on_delete=models.PROTECT,
        related_name='own_inventory_snapshots',
    )
    quantity_cases = models.DecimalField(
        max_digits=10,
        decimal_places=6,
        validators=[MinValueValidator(Decimal('0'))],
        help_text=(
            'Quantity of this item on hand, in cases. '
            'Fractional values allowed (e.g., partial cases).'
        ),
    )
    year = models.IntegerField()
    month = models.IntegerField()
    created_by = models.ForeignKey(
        'core.User',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='own_snapshots_created',
    )
    updated_by = models.ForeignKey(
        'core.User',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='own_snapshots_updated',
        help_text='User who most recently created or updated this snapshot value.',
    )

    class Meta:
        verbose_name = 'Own Inventory Snapshot'
        verbose_name_plural = 'Own Inventory Snapshots'
        unique_together = [['company', 'item', 'year', 'month']]
        ordering = ['-year', '-month', 'item__brand__name', 'item__name']

    def __str__(self):
        return f'{self.item} / {self.year}-{self.month:02d}: {self.quantity_cases} cases'
