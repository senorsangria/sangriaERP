import re

from django.db import migrations


def regenerate_codes(apps, schema_editor):
    Distributor = apps.get_model('distribution', 'Distributor')

    legal_suffixes = {'inc', 'corp', 'co', 'llc', 'ltd', 'lp', 'llp'}
    skip_words = {'a', 'an', 'the', 'of', 'and', '&'}

    def generate_code(name):
        if not name:
            return ''

        working = re.split(r'\s*-\s+', name, maxsplit=1)[0]
        if ',' in working:
            working = working.rsplit(',', 1)[0]

        words = re.findall(r'[A-Za-z0-9]+', working)

        code_chars = []
        for word in words:
            word_lower = word.lower()
            if word_lower in legal_suffixes:
                continue
            if word_lower in skip_words:
                continue
            code_chars.append(word[0].upper())

        return ''.join(code_chars)[:10]

    for d in Distributor.objects.all():
        new_code = generate_code(d.name)
        if d.code != new_code:
            d.code = new_code
            d.save(update_fields=['code'])


class Migration(migrations.Migration):

    dependencies = [
        ('distribution', '0014_backfill_distributor_codes'),
    ]

    operations = [
        migrations.RunPython(regenerate_codes, reverse_code=migrations.RunPython.noop),
    ]
