"""
Import models: ImportBatch, SalesRecord, ItemMapping.

Import data (account lists, sales data) comes from distributor CSV exports.
The actual CSV parsing logic will be built in a future feature phase.
"""
from django.db import models
from apps.core.models import TimeStampedModel


class ImportBatch(TimeStampedModel):
    """
    Tracks a single file import from a distributor.

    Status lifecycle:
        pending → complete | has_unmapped_items | failed
    """

    class ImportType(models.TextChoices):
        SALES_DATA = 'sales_data', 'Sales Data'
        INVENTORY_DATA = 'inventory_data', 'Inventory Data'

    class Status(models.TextChoices):
        PENDING = 'pending', 'Pending'
        COMPLETE = 'complete', 'Complete'
        HAS_UNMAPPED_ITEMS = 'has_unmapped_items', 'Has Unmapped Items'
        FAILED = 'failed', 'Failed'

    company = models.ForeignKey(
        'core.Company',
        on_delete=models.PROTECT,
        related_name='import_batches',
    )
    brand = models.ForeignKey(
        'catalog.Brand',
        on_delete=models.PROTECT,
        related_name='import_batches',
    )
    distributor = models.ForeignKey(
        'distribution.Distributor',
        on_delete=models.PROTECT,
        related_name='import_batches',
    )
    import_type = models.CharField(
        max_length=20,
        choices=ImportType.choices,
        default=ImportType.SALES_DATA,
    )
    import_date = models.DateField(auto_now_add=True)
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING,
    )
    filename = models.CharField(max_length=500)
    notes = models.TextField(blank=True)

    class Meta:
        verbose_name = 'Import Batch'
        verbose_name_plural = 'Import Batches'
        ordering = ['-import_date', '-created_at']

    def __str__(self):
        return f'{self.filename} ({self.get_status_display()}) — {self.import_date}'


class SalesRecord(TimeStampedModel):
    """
    One line of distributor sales data: what a retailer purchased on a given date.
    """

    company = models.ForeignKey(
        'core.Company',
        on_delete=models.PROTECT,
        related_name='sales_records',
    )
    import_batch = models.ForeignKey(
        'imports.ImportBatch',
        on_delete=models.CASCADE,
        related_name='sales_records',
    )
    account = models.ForeignKey(
        'distribution.Account',
        on_delete=models.PROTECT,
        related_name='sales_records',
    )
    item = models.ForeignKey(
        'catalog.Item',
        on_delete=models.PROTECT,
        related_name='sales_records',
    )
    sale_date = models.DateField()
    quantity = models.PositiveIntegerField()

    class Meta:
        verbose_name = 'Sales Record'
        verbose_name_plural = 'Sales Records'
        ordering = ['-sale_date']
        indexes = [
            models.Index(fields=['company', 'sale_date']),
            models.Index(fields=['account', 'sale_date']),
            models.Index(fields=['item', 'sale_date']),
        ]

    def __str__(self):
        return (
            f'{self.account.name} — {self.item.item_code} '
            f'x{self.quantity} on {self.sale_date}'
        )


class ItemMapping(TimeStampedModel):
    """
    Maps a raw item name (as it appeared in an import file) to a catalog Item.

    When an import contains an unrecognized item name, an ItemMapping record
    is created with status=unmapped so an admin can review and resolve it.
    """

    class Status(models.TextChoices):
        UNMAPPED = 'unmapped', 'Unmapped'
        MAPPED = 'mapped', 'Mapped'
        IGNORED = 'ignored', 'Ignored'

    company = models.ForeignKey(
        'core.Company',
        on_delete=models.PROTECT,
        related_name='item_mappings',
    )
    brand = models.ForeignKey(
        'catalog.Brand',
        on_delete=models.PROTECT,
        related_name='item_mappings',
        help_text='Item mapping is scoped to a Brand within a Company.',
    )
    raw_item_name = models.CharField(
        max_length=500,
        help_text='The item name exactly as it appeared in the import file.',
    )
    mapped_item = models.ForeignKey(
        'catalog.Item',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='item_mappings',
        help_text='The resolved catalog Item. Null while status is unmapped.',
    )
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.UNMAPPED,
    )

    class Meta:
        verbose_name = 'Item Mapping'
        verbose_name_plural = 'Item Mappings'
        ordering = ['status', 'brand', 'raw_item_name']
        unique_together = [['brand', 'raw_item_name']]

    def __str__(self):
        resolved = self.mapped_item.item_code if self.mapped_item else '(unmapped)'
        return f'"{self.raw_item_name}" → {resolved}'
