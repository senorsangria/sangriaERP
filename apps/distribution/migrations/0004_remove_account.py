from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0001_initial'),
        ('core', '0004_remove_user_fields'),
        ('distribution', '0003_account_address_normalized_account_auto_created_and_more'),
        ('events', '0002_update_account_fk'),
    ]

    operations = [
        migrations.DeleteModel(
            name='Account',
        ),
    ]
