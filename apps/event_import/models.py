"""
Event Import models: HistoricalImportBatch.
"""
from django.db import models
from apps.core.models import TimeStampedModel


class HistoricalImportBatch(TimeStampedModel):
    """
    Tracks a single historical event import run.

    Created at the start of Stage 3 (event_import_execute).
    event_count is updated after all events are created.
    """

    company = models.ForeignKey(
        'core.Company',
        on_delete=models.CASCADE,
        related_name='historical_import_batches',
    )
    imported_by = models.ForeignKey(
        'core.User',
        on_delete=models.SET_NULL,
        null=True,
        related_name='historical_import_batches',
    )
    imported_at = models.DateTimeField(auto_now_add=True)
    event_count = models.IntegerField(default=0)
    csv_filename = models.CharField(max_length=255, blank=True)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ['-imported_at']
        verbose_name = 'Historical Import Batch'
        verbose_name_plural = 'Historical Import Batches'

    def __str__(self):
        return f'Historical Import {self.imported_at:%Y-%m-%d} ({self.event_count} events)'
