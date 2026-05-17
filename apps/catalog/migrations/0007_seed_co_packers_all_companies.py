"""
Data migration: seed Brotherhood Winery and Nidra Packaging for every company.

0006 filtered for a specific company name that didn't match the dev tenant
('Drink Up Life V2'). This migration seeds for all companies using get_or_create,
so it is safe to apply on any environment regardless of company names.
"""
from django.db import migrations


def forwards(apps, schema_editor):
    Company = apps.get_model('core', 'Company')
    CoPacker = apps.get_model('catalog', 'CoPacker')

    for company in Company.objects.all():
        for name in ('Brotherhood Winery', 'Nidra Packaging'):
            CoPacker.objects.get_or_create(company=company, name=name)


def backwards(apps, schema_editor):
    # Don't delete in reverse — co-packers may have been assigned to items by then.
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('catalog', '0006_seed_co_packers'),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
