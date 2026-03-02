"""
Import models: ImportBatch, ItemMapping.

Import data (account lists, sales data) comes from distributor CSV exports.
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
        null=True,
        blank=True,
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

    # Date range of the data contained in this import
    date_range_start = models.DateField(null=True, blank=True)
    date_range_end = models.DateField(null=True, blank=True)

    # Import statistics
    records_imported = models.IntegerField(default=0)
    accounts_created = models.IntegerField(default=0)
    accounts_reactivated = models.IntegerField(default=0)
    records_skipped = models.IntegerField(default=0)
    account_items_created = models.IntegerField(default=0)

    class Meta:
        verbose_name = 'Import Batch'
        verbose_name_plural = 'Import Batches'
        ordering = ['-import_date', '-created_at']

    def __str__(self):
        return f'{self.filename} ({self.get_status_display()}) — {self.import_date}'


class ItemMapping(TimeStampedModel):
    """
    Maps a raw item code (as it appeared in an import file) to a catalog Item.

    Scoped to company + distributor + raw_item_name (unique together).
    Mappings are per distributor — the same item code from different distributors
    may map to different catalog items.

    Statuses:
    - unmapped: code seen but not yet resolved
    - mapped: code resolved to a catalog Item
    - ignored: code intentionally excluded from imports
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
    distributor = models.ForeignKey(
        'distribution.Distributor',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='item_mappings',
        help_text='The distributor this mapping applies to.',
    )
    brand = models.ForeignKey(
        'catalog.Brand',
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='item_mappings',
        help_text='Brand context for filtering the mapped item dropdown.',
    )
    raw_item_name = models.CharField(
        max_length=500,
        help_text='The item code exactly as it appeared in the import file.',
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
        ordering = ['status', 'distributor', 'raw_item_name']
        unique_together = [['company', 'distributor', 'raw_item_name']]

    def __str__(self):
        resolved = self.mapped_item.item_code if self.mapped_item else '(unmapped)'
        return f'"{self.raw_item_name}" → {resolved}'
