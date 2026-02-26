"""
Sales models: SalesRecord.
"""
from django.db import models
from apps.core.models import TimeStampedModel


class SalesRecord(TimeStampedModel):
    """
    One line of distributor sales data: what a retailer purchased on a given date.

    Quantity may be negative (returns/corrections).
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
        'accounts.Account',
        on_delete=models.PROTECT,
        related_name='sales_records',
    )
    item = models.ForeignKey(
        'catalog.Item',
        on_delete=models.PROTECT,
        related_name='sales_records',
    )
    sale_date = models.DateField()
    quantity = models.IntegerField(
        help_text='Quantity sold. May be negative for returns/corrections.',
    )

    class Meta:
        app_label = 'sales'
        verbose_name = 'Sales Record'
        verbose_name_plural = 'Sales Records'
        ordering = ['-sale_date']
        indexes = [
            models.Index(fields=['company', 'sale_date'], name='sales_sales_company_de19d4_idx'),
            models.Index(fields=['account', 'sale_date'], name='sales_sales_account_418ceb_idx'),
            models.Index(fields=['item', 'sale_date'], name='sales_sales_item_id_1ac2ba_idx'),
        ]

    def __str__(self):
        return (
            f'{self.account.name} — {self.item.item_code} '
            f'x{self.quantity} on {self.sale_date}'
        )
