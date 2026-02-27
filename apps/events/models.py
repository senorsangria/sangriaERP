"""
Events models: Event scheduling and status workflow.
"""
from django.db import models
from apps.core.models import TimeStampedModel


class Event(TimeStampedModel):
    """
    A field activity: in-store tasting, festival, or admin hours.

    Status workflow:
      Draft → Scheduled → Recap Submitted → Revision Requested → Complete
      Admin events: Draft → Scheduled → Complete (no recap step)

    Visibility rules enforced in views/utils — not at model level.
    """

    class EventType(models.TextChoices):
        TASTING  = 'tasting',  'Tasting'
        FESTIVAL = 'festival', 'Festival'
        ADMIN    = 'admin',    'Admin'

    class Status(models.TextChoices):
        DRAFT              = 'draft',              'Draft'
        SCHEDULED          = 'scheduled',          'Scheduled'
        RECAP_SUBMITTED    = 'recap_submitted',    'Recap Submitted'
        REVISION_REQUESTED = 'revision_requested', 'Revision Requested'
        COMPLETE           = 'complete',           'Complete'

    # Core fields
    company = models.ForeignKey(
        'core.Company',
        on_delete=models.PROTECT,
        related_name='events',
    )
    event_type = models.CharField(
        max_length=20,
        choices=EventType.choices,
        default=EventType.TASTING,
    )
    status = models.CharField(
        max_length=30,
        choices=Status.choices,
        default=Status.DRAFT,
    )

    # Location — required for Tasting and Festival, not required for Admin
    account = models.ForeignKey(
        'accounts.Account',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='events',
    )

    # Scheduling
    date = models.DateField(null=True, blank=True)
    start_time = models.TimeField(null=True, blank=True)
    duration_hours = models.PositiveSmallIntegerField(default=0)
    duration_minutes = models.PositiveSmallIntegerField(
        default=0,
        choices=[(0, '0 min'), (15, '15 min'), (30, '30 min'), (45, '45 min')],
    )

    # People
    ambassador = models.ForeignKey(
        'core.User',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='ambassador_events',
    )
    event_manager = models.ForeignKey(
        'core.User',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='managed_events',
    )
    created_by = models.ForeignKey(
        'core.User',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='created_events',
    )

    # Items to be sampled (Tasting only)
    items = models.ManyToManyField(
        'catalog.Item',
        blank=True,
        related_name='events',
    )

    # Notes
    notes = models.TextField(blank=True)

    # Populated when Event Manager requests revision on a Recap Submitted event
    revision_note = models.TextField(blank=True)

    class Meta:
        app_label = 'events'
        verbose_name = 'Event'
        verbose_name_plural = 'Events'

    def __str__(self):
        if self.account:
            return f'{self.get_event_type_display()} — {self.account.name} on {self.date}'
        return f'{self.get_event_type_display()} — Admin on {self.date}'

    @property
    def duration_display(self):
        """Human-readable duration: '2h 30m', '1h', '45m', etc."""
        h = self.duration_hours or 0
        m = self.duration_minutes or 0
        if h and m:
            return f'{h}h {m}m'
        elif h:
            return f'{h}h'
        elif m:
            return f'{m}m'
        return '—'

    @property
    def status_badge_class(self):
        """Bootstrap badge class for the current status."""
        return {
            self.Status.DRAFT:              'secondary',
            self.Status.SCHEDULED:          'primary',
            self.Status.RECAP_SUBMITTED:    'warning',
            self.Status.REVISION_REQUESTED: 'danger',
            self.Status.COMPLETE:           'success',
        }.get(self.status, 'secondary')
