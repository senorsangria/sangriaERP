from django.conf import settings
from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0003_add_phone_and_created_by_to_user'),
        ('distribution', '0001_initial'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='user',
            name='territory',
        ),
        migrations.RemoveField(
            model_name='user',
            name='assigned_distributors',
        ),
        migrations.RemoveField(
            model_name='user',
            name='assigned_accounts',
        ),
        migrations.RemoveField(
            model_name='user',
            name='managed_ambassadors',
        ),
    ]
