"""
Distribution models: Distributor, DistributorItemProfile, InventoryImportBatch, InventorySnapshot.
"""
import re
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models

from apps.core.models import TimeStampedModel


class DistributorGroup(TimeStampedModel):
    """
    A named group of distributors scoped to a Company.

    primary_distributor is unique — a distributor can be primary of at most one group.
    Members are tracked via the group FK on Distributor (SET_NULL on group delete).
    """

    company = models.ForeignKey(
        'core.Company',
        on_delete=models.PROTECT,
        related_name='distributor_groups',
    )
    name = models.CharField(max_length=255)
    primary_distributor = models.ForeignKey(
        'distribution.Distributor',
        on_delete=models.PROTECT,
        related_name='primary_for_groups',
        unique=True,
    )
    notes = models.TextField(blank=True, default='')

    class Meta:
        unique_together = [['company', 'name']]
        ordering = ['name']

    def __str__(self):
        return self.name


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
    code = models.CharField(
        max_length=10,
        blank=True,
        db_index=True,
        help_text='Short identifier for this distributor (e.g., SPDC). Auto-generated from name if left blank.',
    )
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
    group = models.ForeignKey(
        'distribution.DistributorGroup',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='members',
    )

    class Meta:
        verbose_name = 'Distributor'
        verbose_name_plural = 'Distributors'
        ordering = ['company', 'name']

    def __str__(self):
        return f'{self.name} ({self.company.name})'

    @staticmethod
    def _generate_code_from_name(name):
        """
        Generate a default code from a distributor name.

        Algorithm:
        1. Drop everything from " - " or "- " onward (strips city after hyphen)
        2. Drop everything after the LAST comma (strips state/city after comma)
        3. Strip legal suffixes: Inc, Corp, Co, LLC, Ltd, LP, LLP (case-insensitive)
        4. First letter of each remaining significant word (skip a/an/the/of/and/&)
        5. Uppercase, max 10 chars

        Examples:
          "Shore Point Dist Co, NJ"              → "SPD"
          "Burke Distributing Corp.- Randolph, MA" → "BD"
          "Colonial Beverage Wholesaler, MA"      → "CBW"
          "Peerless Beverage, NJ"                → "PB"
          "Atlas Distributing Inc., MA"           → "AD"
        """
        if not name:
            return ''

        # Step 1: Drop everything from "- " or " - " onward
        working = re.split(r'\s*-\s+', name, maxsplit=1)[0]

        # Step 2: Drop everything after the LAST comma
        if ',' in working:
            working = working.rsplit(',', 1)[0]

        # Step 3: Tokenize and filter
        words = re.findall(r'[A-Za-z0-9]+', working)

        legal_suffixes = {'inc', 'corp', 'co', 'llc', 'ltd', 'lp', 'llp'}
        skip_words = {'a', 'an', 'the', 'of', 'and', '&'}

        code_chars = []
        for word in words:
            word_lower = word.lower()
            if word_lower in legal_suffixes:
                continue
            if word_lower in skip_words:
                continue
            code_chars.append(word[0].upper())

        return ''.join(code_chars)[:10]

    @property
    def display_code(self):
        """Returns code prefixed with state, e.g. 'NJ-SPD'. Falls back gracefully."""
        if self.state and self.code:
            return f"{self.state}-{self.code}"
        return self.code or self.name

    def save(self, *args, **kwargs):
        if not self.code and self.name:
            self.code = self._generate_code_from_name(self.name)
        super().save(*args, **kwargs)


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


class InventoryImportBatch(TimeStampedModel):
    """
    Tracks a single inventory snapshot CSV upload event.

    Uploads are atomic — either fully committed or fully rolled back.
    No status field is needed since there are no partial states.
    """

    company = models.ForeignKey(
        'core.Company',
        on_delete=models.PROTECT,
        related_name='inventory_import_batches',
    )
    year = models.IntegerField()
    month = models.IntegerField()
    uploaded_by = models.ForeignKey(
        'core.User',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='inventory_uploads',
    )
    filename = models.CharField(max_length=500)
    distributor_count = models.PositiveIntegerField()
    snapshots_created = models.PositiveIntegerField()

    class Meta:
        verbose_name = 'Inventory Import Batch'
        verbose_name_plural = 'Inventory Import Batches'
        ordering = ['-created_at']

    def __str__(self):
        return (
            f'Inventory upload {self.year}-{self.month:02d} '
            f'({self.distributor_count} distributors, {self.snapshots_created} items)'
        )


class InventorySnapshot(TimeStampedModel):
    """
    On-hand inventory for a specific (distributor, item) as of a given month.

    quantity_cases may be zero — some snapshots legitimately record zero on hand.
    Fractional values are supported for partial cases / loose bottles.
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
    quantity_cases = models.DecimalField(
        max_digits=10,
        decimal_places=6,
        validators=[MinValueValidator(Decimal('0'))],
        help_text=(
            'Quantity of this item on hand at the distributor, in cases. '
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
        related_name='inventory_snapshots_created',
    )
    import_batch = models.ForeignKey(
        'distribution.InventoryImportBatch',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='snapshots',
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


class DistributorPO(TimeStampedModel):
    """
    A projected or actual purchase order for a distributor in a given month.

    Projected POs are generated by the order-generation algorithm (Phase 4-step-2a)
    and carry status=PROJECTED. When confirmed and submitted to the distributor,
    status is changed to ACTUAL and an external_po_number is required.

    Multiple POs in the same (distributor, year, month) are allowed —
    some distributors place more than one order per month.
    """

    class Status(models.TextChoices):
        PROJECTED  = 'projected',  'Projected'
        ACTUAL     = 'actual',     'Actual'
        SUBMITTED  = 'submitted',  'Submitted'
        IN_TRANSIT = 'in_transit', 'In Transit'
        DELIVERED  = 'delivered',  'Delivered'
        INVOICED   = 'invoiced',   'Invoiced'
        CANCELLED  = 'cancelled',  'Cancelled'

    distributor = models.ForeignKey(
        'distribution.Distributor',
        on_delete=models.PROTECT,
        related_name='purchase_orders',
    )
    year = models.IntegerField()
    month = models.IntegerField()
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PROJECTED,
    )
    external_po_number = models.CharField(max_length=100, blank=True, default='')
    so_number = models.IntegerField(
        null=True,
        blank=True,
        db_index=True,
        help_text='Auto-assigned when PO is Submitted',
    )
    generated_by_algorithm = models.BooleanField(
        default=True,
        help_text='True when created by the order-generation algorithm; False when manually entered.',
    )
    notes = models.TextField(blank=True, default='')
    selected_for_projection = models.BooleanField(
        default=False,
        help_text='Whether this PO is selected in the inventory projection tool on the Distributor POs tab.',
    )
    created_by = models.ForeignKey(
        'core.User',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='distributor_pos_created',
    )

    class Meta:
        verbose_name = 'Distributor PO'
        verbose_name_plural = 'Distributor POs'
        ordering = ['-year', '-month', 'distributor__name']

    def __str__(self):
        return f'{self.distributor} / {self.year}-{self.month:02d} ({self.get_status_display()})'

    def clean(self):
        super().clean()
        if self.status == self.Status.ACTUAL and not self.external_po_number:
            raise ValidationError({
                'external_po_number': 'PO number is required when status is Actual.'
            })
        if self.status == self.Status.SUBMITTED and not self.so_number:
            raise ValidationError({
                'so_number': 'SO number is required when status is Submitted. It should be auto-assigned by the system.'
            })


class DistributorPOLine(TimeStampedModel):
    """
    One line item in a DistributorPO — a specific item and quantity.

    quantity_cases is always stored in cases (same convention as InventorySnapshot).
    Display in pallets is derived at render time from the distributor's order_quantity_unit.
    """

    po = models.ForeignKey(
        'distribution.DistributorPO',
        on_delete=models.CASCADE,
        related_name='lines',
    )
    item = models.ForeignKey(
        'catalog.Item',
        on_delete=models.PROTECT,
        related_name='po_lines',
    )
    quantity_cases = models.DecimalField(max_digits=10, decimal_places=6)

    class Meta:
        verbose_name = 'Distributor PO Line'
        verbose_name_plural = 'Distributor PO Lines'
        unique_together = [['po', 'item']]
        ordering = ['item__brand__name', 'item__sort_order', 'item__name']

    def __str__(self):
        return f'{self.po} / {self.item}: {self.quantity_cases} cases'


def assign_so_number(distributor_po):
    """
    Assign next SO# for the company. Idempotent — if so_number already set, do nothing.

    Returns the assigned so_number.
    """
    if distributor_po.so_number is not None:
        return distributor_po.so_number

    company = distributor_po.distributor.company

    from django.db.models import Max
    max_so = DistributorPO.objects.filter(
        distributor__company=company,
        so_number__isnull=False,
    ).aggregate(Max('so_number'))['so_number__max']

    if max_so is None:
        next_so = company.so_sequence_start
    else:
        next_so = max_so + 1

    distributor_po.so_number = next_so
    return next_so
