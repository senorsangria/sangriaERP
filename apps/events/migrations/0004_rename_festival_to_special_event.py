"""
Migration: rename festival → special_event in Event.event_type choices.

This migration:
1. Alters the Event.event_type field to use 'special_event' instead of 'festival'
   in the choices list (schema change only — CharField stores raw values).
2. Runs a data migration to update any existing rows with event_type='festival'
   to event_type='special_event'.
"""
from django.db import migrations, models


def festival_to_special_event(apps, schema_editor):
    Event = apps.get_model('events', 'Event')
    Event.objects.filter(event_type='festival').update(event_type='special_event')


def special_event_to_festival(apps, schema_editor):
    Event = apps.get_model('events', 'Event')
    Event.objects.filter(event_type='special_event').update(event_type='festival')


class Migration(migrations.Migration):

    dependencies = [
        ('events', '0003_event_recap_fields_and_models'),
    ]

    operations = [
        # Data migration first — update existing rows before changing choices
        migrations.RunPython(
            festival_to_special_event,
            reverse_code=special_event_to_festival,
        ),
        # Then update the field choices metadata
        migrations.AlterField(
            model_name='event',
            name='event_type',
            field=models.CharField(
                choices=[
                    ('tasting',       'Tasting'),
                    ('special_event', 'Special Event'),
                    ('admin',         'Admin'),
                ],
                default='tasting',
                max_length=20,
            ),
        ),
    ]
