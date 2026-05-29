import re

from django.db import migrations


def backfill_distributor_codes(apps, schema_editor):
    Distributor = apps.get_model('distribution', 'Distributor')

    skip_words = {'a', 'an', 'the', 'of', 'and', '&'}

    def generate_code(name):
        if not name:
            return ''
        words = re.findall(r'[A-Za-z0-9]+', name)
        code_chars = [w[0].upper() for w in words if w.lower() not in skip_words]
        return ''.join(code_chars)[:10]

    for d in Distributor.objects.filter(code=''):
        d.code = generate_code(d.name)
        d.save(update_fields=['code'])


class Migration(migrations.Migration):

    dependencies = [
        ('distribution', '0013_distributor_code'),
    ]

    operations = [
        migrations.RunPython(
            backfill_distributor_codes,
            reverse_code=migrations.RunPython.noop,
        ),
    ]
