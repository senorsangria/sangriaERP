from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0004_add_third_party_id_account_type_distributor_route'),
        ('distribution', '0001_initial'),
    ]

    operations = [
        migrations.AlterField(
            model_name='usercoveragearea',
            name='distributor',
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name='coverage_areas',
                to='distribution.distributor',
            ),
        ),
    ]
