import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ('core', '0003_add_phone_and_created_by_to_user'),
        ('distribution', '0003_account_address_normalized_account_auto_created_and_more'),
    ]

    operations = [
        migrations.CreateModel(
            name='Account',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('merge_note', models.TextField(blank=True, help_text='Reason or notes captured when this account was merged.')),
                ('name', models.CharField(max_length=255)),
                ('street', models.CharField(blank=True, max_length=255)),
                ('city', models.CharField(blank=True, max_length=100)),
                ('state', models.CharField(blank=True, max_length=50)),
                ('zip_code', models.CharField(blank=True, max_length=20)),
                ('phone', models.CharField(blank=True, max_length=50)),
                ('address_normalized', models.CharField(blank=True, max_length=255)),
                ('city_normalized', models.CharField(blank=True, max_length=100)),
                ('state_normalized', models.CharField(blank=True, max_length=50)),
                ('vip_outlet_id', models.CharField(blank=True, help_text='VIP Outlet ID from distributor export. Reference only, not used as unique key.', max_length=100)),
                ('county', models.CharField(blank=True, default='Unknown', max_length=100)),
                ('on_off_premise', models.CharField(blank=True, default='Unknown', help_text='ON, OFF, or Unknown.', max_length=10)),
                ('account_type', models.CharField(choices=[('liquor_store', 'Liquor Store'), ('restaurant', 'Restaurant'), ('festival', 'Festival'), ('other', 'Other')], default='other', max_length=20)),
                ('is_active', models.BooleanField(default=True)),
                ('auto_created', models.BooleanField(default=False, help_text='True if this account was created automatically by a sales data import.')),
                ('company', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='accounts', to='core.company')),
                ('distributor', models.ForeignKey(blank=True, help_text='The distributor that services this account.', null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='accounts', to='distribution.distributor')),
                ('merged_into', models.ForeignKey(blank=True, help_text='If set, this account has been merged into the referenced account.', null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='merged_accounts', to='accounts.account')),
            ],
            options={
                'verbose_name': 'Account',
                'verbose_name_plural': 'Accounts',
                'ordering': ['company', 'name'],
            },
        ),
    ]
