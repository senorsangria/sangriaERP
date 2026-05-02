from django.db import models

from apps.core.models import TimeStampedModel


class Route(TimeStampedModel):
    company = models.ForeignKey(
        'core.Company',
        on_delete=models.CASCADE,
        related_name='routes',
    )
    distributor = models.ForeignKey(
        'distribution.Distributor',
        on_delete=models.PROTECT,
        related_name='routes',
    )
    created_by = models.ForeignKey(
        'core.User',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='routes',
    )
    name = models.CharField(max_length=100)

    class Meta:
        ordering = ['name']
        # created_by is nullable — PostgreSQL treats NULLs as distinct in unique
        # constraints, so multiple orphaned routes (created_by=NULL) with the
        # same distributor+name are allowed. This is acceptable; orphaned routes
        # from deleted users are cleaned up manually.
        unique_together = [['created_by', 'distributor', 'name']]

    def __str__(self):
        return f'{self.name} ({self.distributor.name})'


class RouteAccount(models.Model):
    route = models.ForeignKey(
        Route,
        on_delete=models.CASCADE,
        related_name='route_accounts',
    )
    account = models.ForeignKey(
        'accounts.Account',
        on_delete=models.CASCADE,
        related_name='route_accounts',
    )
    position = models.PositiveIntegerField(
        default=0,
        help_text='Display order within this route. Lower values appear first.',
    )

    class Meta:
        ordering = ['position', 'id']
        unique_together = [['route', 'account']]

    def __str__(self):
        return f'{self.route.name} — {self.account.name}'
