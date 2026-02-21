"""
Events model: tasting events and field activities.

This model is intentionally lean for the foundation phase.
Recap and photo-upload support will be added in a later feature build.
"""
from django.conf import settings
from django.db import models
from apps.core.models import TimeStampedModel


class Event(TimeStampedModel):
    """
    A tasting event or other field activity at a retail Account.

    Future additions (not built yet):
      - EventRecap: notes, outcome, attendance
      - EventPhoto: uploaded photos linked to a recap
    """

    class EventType(models.TextChoices):
        IN_STORE_TASTING = 'in_store_tasting', 'In-Store Tasting'
        FESTIVAL = 'festival', 'Festival'
        SPECIAL_EVENT = 'special_event', 'Special Event'
        ADMIN_HOURS = 'admin_hours', 'Admin Hours'

    class Status(models.TextChoices):
        SCHEDULED = 'scheduled', 'Scheduled'
        COMPLETED = 'completed', 'Completed'
        CANCELLED = 'cancelled', 'Cancelled'

    company = models.ForeignKey(
        'core.Company',
        on_delete=models.PROTECT,
        related_name='events',
    )
    account = models.ForeignKey(
        'distribution.Account',
        on_delete=models.PROTECT,
        related_name='events',
    )
    brand = models.ForeignKey(
        'catalog.Brand',
        on_delete=models.PROTECT,
        related_name='events',
    )
    event_type = models.CharField(
        max_length=30,
        choices=EventType.choices,
        default=EventType.IN_STORE_TASTING,
    )

    # Staff assignments — nullable; not every event has both roles filled at creation
    ambassador = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='ambassador_events',
        limit_choices_to={'role': 'ambassador'},
    )
    ambassador_manager = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='managed_events',
        limit_choices_to={'role': 'ambassador_manager'},
    )

    scheduled_date = models.DateField()
    scheduled_time = models.TimeField()
    # Duration stored in minutes for flexibility; can render as h:mm in UI
    duration_minutes = models.PositiveIntegerField(
        default=120,
        help_text='Duration of the event in minutes.',
    )

    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.SCHEDULED,
    )

    # Stub fields for future recap / photo support (not built yet)
    # recap: OneToOneField(EventRecap) — to be added
    # photos: ManyToManyField(EventPhoto) — to be added

    class Meta:
        verbose_name = 'Event'
        verbose_name_plural = 'Events'
        ordering = ['-scheduled_date', '-scheduled_time']

    def __str__(self):
        return (
            f'{self.get_event_type_display()} — '
            f'{self.account.name} on {self.scheduled_date}'
        )
