from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0001_initial'),
        ('core', '0004_remove_user_fields'),
        ('distribution', '0003_account_address_normalized_account_auto_created_and_more'),
        ('events', '0002_update_account_fk'),
        # imports.SalesRecord had a FK to distribution.Account; it must be
        # removed before we can drop the Account model.
        ('imports', '0003_remove_salesrecord'),
    ]

    operations = [
        migrations.DeleteModel(
            name='Account',
        ),
    ]
