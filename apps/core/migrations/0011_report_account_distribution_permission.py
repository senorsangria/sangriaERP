"""
Data migration: add can_view_report_account_distribution permission and assign to roles.
"""
from django.db import migrations


def forwards(apps, schema_editor):
    Permission = apps.get_model('core', 'Permission')
    Role = apps.get_model('core', 'Role')

    perm, _ = Permission.objects.get_or_create(
        codename='can_view_report_account_distribution',
        defaults={'description': 'Can view the Account Distribution by Volume report'},
    )

    for codename in ('supplier_admin', 'sales_manager', 'territory_manager', 'ambassador_manager'):
        role = Role.objects.get(codename=codename)
        role.permissions.add(perm)


def backwards(apps, schema_editor):
    Permission = apps.get_model('core', 'Permission')
    Role = apps.get_model('core', 'Role')

    try:
        perm = Permission.objects.get(codename='can_view_report_account_distribution')
    except Permission.DoesNotExist:
        return

    for codename in ('supplier_admin', 'sales_manager', 'territory_manager', 'ambassador_manager'):
        try:
            Role.objects.get(codename=codename).permissions.remove(perm)
        except Role.DoesNotExist:
            pass

    perm.delete()


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0010_report_item_sales_permission'),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
