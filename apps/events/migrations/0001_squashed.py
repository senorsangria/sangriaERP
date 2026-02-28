"""
Squash of events migrations 0001–0003.

The original events/0001_initial.py referenced distribution.account, which
no longer exists in the distribution app.  Django's schema editor cannot
resolve that historical FK when creating the test database from scratch.

This squashed migration replaces all three originals with a single
CreateModel that reflects the current schema (FK to accounts.Account).

On the production DB the three individual migrations are already applied,
so Django simply marks this squash as applied without running it again.
For fresh databases (CI, tests) this single migration is used instead.
"""
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    replaces = [
        ('events', '0001_initial'),
        ('events', '0002_update_account_fk'),
        ('events', '0003_phase_10_2_event_model'),
    ]

    initial = True

    dependencies = [
        ('accounts', '0002_usercoveragearea'),
        ('catalog', '0002_initial'),
        ('core', '0001_initial'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='Event',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True,
                                           serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('event_type', models.CharField(
                    choices=[('tasting', 'Tasting'), ('festival', 'Festival'), ('admin', 'Admin')],
                    default='tasting',
                    max_length=20,
                )),
                ('status', models.CharField(
                    choices=[
                        ('draft', 'Draft'),
                        ('scheduled', 'Scheduled'),
                        ('recap_submitted', 'Recap Submitted'),
                        ('revision_requested', 'Revision Requested'),
                        ('complete', 'Complete'),
                    ],
                    default='draft',
                    max_length=30,
                )),
                ('date', models.DateField(blank=True, null=True)),
                ('start_time', models.TimeField(blank=True, null=True)),
                ('duration_hours', models.PositiveSmallIntegerField(default=0)),
                ('duration_minutes', models.PositiveSmallIntegerField(
                    choices=[(0, '0 min'), (15, '15 min'), (30, '30 min'), (45, '45 min')],
                    default=0,
                )),
                ('notes', models.TextField(blank=True)),
                ('revision_note', models.TextField(blank=True)),
                ('company', models.ForeignKey(
                    on_delete=django.db.models.deletion.PROTECT,
                    related_name='events',
                    to='core.company',
                )),
                ('account', models.ForeignKey(
                    blank=True,
                    null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='events',
                    to='accounts.account',
                )),
                ('ambassador', models.ForeignKey(
                    blank=True,
                    null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='ambassador_events',
                    to=settings.AUTH_USER_MODEL,
                )),
                ('event_manager', models.ForeignKey(
                    blank=True,
                    null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='managed_events',
                    to=settings.AUTH_USER_MODEL,
                )),
                ('created_by', models.ForeignKey(
                    blank=True,
                    null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='created_events',
                    to=settings.AUTH_USER_MODEL,
                )),
                ('items', models.ManyToManyField(
                    blank=True,
                    related_name='events',
                    to='catalog.item',
                )),
            ],
            options={
                'verbose_name': 'Event',
                'verbose_name_plural': 'Events',
            },
        ),
    ]
