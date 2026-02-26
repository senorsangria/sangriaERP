from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('imports', '0002_alter_itemmapping_options_and_more'),
        ('sales', '0001_initial'),
    ]

    operations = [
        migrations.DeleteModel(
            name='SalesRecord',
        ),
    ]
