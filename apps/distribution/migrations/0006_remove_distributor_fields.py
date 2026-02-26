from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('distribution', '0005_remove_masteraccount'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='distributor',
            name='brands',
        ),
        migrations.RemoveField(
            model_name='distributor',
            name='email',
        ),
        migrations.RemoveField(
            model_name='distributor',
            name='phone',
        ),
    ]
