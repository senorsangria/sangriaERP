from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('distribution', '0004_remove_account'),
    ]

    operations = [
        migrations.DeleteModel(
            name='MasterAccount',
        ),
    ]
