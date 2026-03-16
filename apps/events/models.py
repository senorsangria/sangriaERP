"""
Events models: Event scheduling and status workflow.
"""
from django.conf import settings
from django.db import models
from apps.core.models import TimeStampedModel


class Event(TimeStampedModel):
    """
    A field activity: in-store tasting, special event, or admin hours.

    Status workflow:
      Draft → Scheduled → Recap In Progress → Recap Submitted → Revision Requested → Complete → Ok to Pay
      Admin events: Draft → Scheduled → Complete → Ok to Pay (no recap step)

    Visibility rules enforced in views/utils — not at model level.
    """

    class EventType(models.TextChoices):
        TASTING       = 'tasting',       'Tasting'
        SPECIAL_EVENT = 'special_event', 'Special Event'
        ADMIN         = 'admin',         'Admin'

    class Status(models.TextChoices):
        DRAFT              = 'draft',              'Draft'
        SCHEDULED          = 'scheduled',          'Scheduled'
        RECAP_IN_PROGRESS  = 'recap_in_progress',  'Recap In Progress'
        RECAP_SUBMITTED    = 'recap_submitted',    'Recap Submitted'
        REVISION_REQUESTED = 'revision_requested', 'Revision Requested'
        COMPLETE           = 'complete',           'Complete'
        OK_TO_PAY          = 'ok_to_pay',          'Ok to Pay'
        PAID               = 'paid',               'Paid'

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

    # Historical import batch — set only on events created via historical import
    historical_batch = models.ForeignKey(
        'event_import.HistoricalImportBatch',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='events',
    )

    # Import flags — set only on events created via historical import
    is_imported = models.BooleanField(
        default=False,
        help_text='True for events created via historical import.',
    )
    legacy_ambassador_name = models.CharField(
        max_length=255,
        blank=True,
        default='',
        help_text='Ambassador name from historical import. '
                  'Only set on imported events.',
    )

    # Recap fields — populated by ambassador during recap submission
    # Tasting Part 1
    recap_samples_poured = models.IntegerField(null=True, blank=True)
    recap_qr_codes_scanned = models.IntegerField(null=True, blank=True)
    recap_notes = models.TextField(blank=True)
    # Festival
    recap_comment = models.TextField(blank=True)

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
        """Human-readable duration: '2hr 30m', '1hr', '45m', etc."""
        h = self.duration_hours or 0
        m = self.duration_minutes or 0
        if h and m:
            return f'{h}hr {m}m'
        elif h:
            return f'{h}hr'
        elif m:
            return f'{m}m'
        return '—'

    @property
    def status_badge_class(self):
        """Bootstrap badge class for the current status."""
        return {
            self.Status.DRAFT:              'secondary',
            self.Status.SCHEDULED:          'primary',
            self.Status.RECAP_IN_PROGRESS:  'warning',
            self.Status.RECAP_SUBMITTED:    'warning',
            self.Status.REVISION_REQUESTED: 'danger',
            self.Status.COMPLETE:           'success',
            self.Status.OK_TO_PAY:          'bg-success',
        }.get(self.status, 'secondary')


class EventPhoto(models.Model):
    """
    Photo uploaded during event recap.

    Photos are associated to both the Event and the Account at upload time.
    file_url stores the path returned by the photo storage backend (local
    media path in development, object storage URL in production).
    """

    event = models.ForeignKey(
        'events.Event',
        on_delete=models.CASCADE,
        related_name='photos',
    )
    account = models.ForeignKey(
        'accounts.Account',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='event_photos',
    )
    file_url = models.CharField(max_length=500)
    uploaded_at = models.DateTimeField(auto_now_add=True)
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='uploaded_event_photos',
    )

    class Meta:
        app_label = 'events'
        ordering = ['uploaded_at']

    def __str__(self):
        return f'Photo for {self.event} uploaded by {self.uploaded_by}'


class EventItemRecap(models.Model):
    """
    Per-item recap data captured during a Tasting event recap.

    One record per (event, item) pair. Created on first save; updated on
    subsequent saves. Shelf price is used to update AccountItem.current_price
    when the recap is submitted (not on save).
    """

    event = models.ForeignKey(
        'events.Event',
        on_delete=models.CASCADE,
        related_name='item_recaps',
    )
    item = models.ForeignKey(
        'catalog.Item',
        on_delete=models.CASCADE,
        related_name='event_item_recaps',
    )
    shelf_price = models.DecimalField(
        max_digits=6,
        decimal_places=2,
        null=True,
        blank=True,
    )
    bottles_sold = models.IntegerField(null=True, blank=True)
    bottles_used_for_samples = models.IntegerField(null=True, blank=True)

    class Meta:
        app_label = 'events'
        unique_together = [['event', 'item']]

    def __str__(self):
        return f'{self.item} recap for {self.event}'


class Expense(models.Model):
    """
    A single expense associated with an event recap.

    Captured during the recap (Tasting or Special Event, not Admin).
    A receipt photo is required to save an expense.
    """

    event = models.ForeignKey(
        'events.Event',
        on_delete=models.CASCADE,
        related_name='expenses',
    )
    amount = models.DecimalField(max_digits=8, decimal_places=2)
    description = models.CharField(max_length=200)
    receipt_photo_url = models.CharField(max_length=500)
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='created_expenses',
    )

    class Meta:
        app_label = 'events'
        ordering = ['created_at']

    def __str__(self):
        return f'{self.description} — ${self.amount} on {self.event}'
