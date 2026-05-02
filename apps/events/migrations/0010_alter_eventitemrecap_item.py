import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('catalog', '0001_initial'),
        ('events', '0009_add_paid_status'),
    ]

    operations = [
        migrations.AlterField(
            model_name='eventitemrecap',
            name='item',
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name='event_item_recaps',
                to='catalog.item',
            ),
        ),
    ]
