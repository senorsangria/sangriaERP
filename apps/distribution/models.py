"""
Distribution models: Distributor.
"""
from django.db import models
from apps.core.models import TimeStampedModel


class Distributor(TimeStampedModel):
    """
    A distribution company that services Accounts on behalf of a Brand.

    A Distributor belongs to a Company (tenant).
    Cross-tenant distributor sharing is a future feature.
    """

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

    class Meta:
        verbose_name = 'Distributor'
        verbose_name_plural = 'Distributors'
        ordering = ['company', 'name']

    def __str__(self):
        return f'{self.name} ({self.company.name})'
