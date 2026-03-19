from django.db import migrations, models


def delete_state_coverage_areas(apps, schema_editor):
    UserCoverageArea = apps.get_model('accounts', 'UserCoverageArea')
    UserCoverageArea.objects.filter(coverage_type='state').delete()


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0005_usercoveragearea_distributor_required'),
    ]

    operations = [
        migrations.RunPython(delete_state_coverage_areas, migrations.RunPython.noop),
        migrations.AlterField(
            model_name='usercoveragearea',
            name='coverage_type',
            field=models.CharField(
                choices=[
                    ('distributor', 'Distributor'),
                    ('county', 'County'),
                    ('city', 'City'),
                    ('account', 'Account'),
                ],
                max_length=20,
            ),
        ),
    ]
