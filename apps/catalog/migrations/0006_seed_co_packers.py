"""
Data migration: seed initial CoPacker records for Drink Up Life.
Creates Brotherhood Winery and Nidra Packaging if the company exists.
Silently skips on dev environments where Drink Up Life has not been created.
"""
from django.db import migrations


def forwards(apps, schema_editor):
    Company = apps.get_model('core', 'Company')
    CoPacker = apps.get_model('catalog', 'CoPacker')

    company = Company.objects.filter(name__iexact='drink up life').first()
    if company is None:
        return

    for name in ('Brotherhood Winery', 'Nidra Packaging'):
        CoPacker.objects.get_or_create(company=company, name=name)


def backwards(apps, schema_editor):
    CoPacker = apps.get_model('catalog', 'CoPacker')
    CoPacker.objects.filter(name__in=('Brotherhood Winery', 'Nidra Packaging')).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('catalog', '0005_item_cases_per_batch_and_more'),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
