import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    """
    Change ItemMapping.distributor from SET_NULL (nullable) to PROTECT (non-nullable).

    No data migration needed: no code path creates ItemMapping with distributor=None,
    so there are no NULL rows to backfill.
    """

    dependencies = [
        ('distribution', '0001_initial'),
        ('imports', '0005_add_accounts_reactivated'),
    ]

    operations = [
        migrations.AlterField(
            model_name='itemmapping',
            name='distributor',
            field=models.ForeignKey(
                help_text='The distributor this mapping applies to.',
                on_delete=django.db.models.deletion.PROTECT,
                related_name='item_mappings',
                to='distribution.distributor',
            ),
        ),
    ]
