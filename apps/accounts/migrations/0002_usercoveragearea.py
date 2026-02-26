import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0001_initial'),
        ('core', '0003_add_phone_and_created_by_to_user'),
        ('distribution', '0003_account_address_normalized_account_auto_created_and_more'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='UserCoverageArea',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('coverage_type', models.CharField(choices=[('distributor', 'Distributor'), ('state', 'State'), ('county', 'County'), ('city', 'City'), ('account', 'Account')], max_length=20)),
                ('state', models.CharField(blank=True, max_length=100)),
                ('county', models.CharField(blank=True, max_length=100)),
                ('city', models.CharField(blank=True, max_length=100)),
                ('account', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='coverage_areas', to='accounts.account')),
                ('company', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='user_coverage_areas', to='core.company')),
                ('distributor', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='coverage_areas', to='distribution.distributor')),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='coverage_areas', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'verbose_name': 'User Coverage Area',
                'verbose_name_plural': 'User Coverage Areas',
                'ordering': ['company', 'user', 'coverage_type'],
            },
        ),
    ]
