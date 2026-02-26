import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ('accounts', '0001_initial'),
        ('catalog', '0002_initial'),
        ('core', '0003_add_phone_and_created_by_to_user'),
        ('imports', '0001_initial'),
    ]

    operations = [
        migrations.CreateModel(
            name='SalesRecord',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('sale_date', models.DateField()),
                ('quantity', models.IntegerField(help_text='Quantity sold. May be negative for returns/corrections.')),
                ('account', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='sales_records', to='accounts.account')),
                ('company', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='sales_records', to='core.company')),
                ('import_batch', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='sales_records', to='imports.importbatch')),
                ('item', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='sales_records', to='catalog.item')),
            ],
            options={
                'verbose_name': 'Sales Record',
                'verbose_name_plural': 'Sales Records',
                'ordering': ['-sale_date'],
            },
        ),
        migrations.AddIndex(
            model_name='salesrecord',
            index=models.Index(fields=['company', 'sale_date'], name='sales_sales_company_de19d4_idx'),
        ),
        migrations.AddIndex(
            model_name='salesrecord',
            index=models.Index(fields=['account', 'sale_date'], name='sales_sales_account_418ceb_idx'),
        ),
        migrations.AddIndex(
            model_name='salesrecord',
            index=models.Index(fields=['item', 'sale_date'], name='sales_sales_item_id_1ac2ba_idx'),
        ),
    ]
