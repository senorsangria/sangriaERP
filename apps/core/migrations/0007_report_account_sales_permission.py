"""
Data migration: add can_view_report_account_sales permission and assign to roles.
"""
from django.db import migrations


def forwards(apps, schema_editor):
    Permission = apps.get_model('core', 'Permission')
    Role = apps.get_model('core', 'Role')

    perm, _ = Permission.objects.get_or_create(
        codename='can_view_report_account_sales',
        defaults={'description': 'Reports: Account Sales by Year'},
    )

    for codename in ('supplier_admin', 'sales_manager', 'territory_manager', 'ambassador_manager'):
        role = Role.objects.get(codename=codename)
        role.permissions.add(perm)


def backwards(apps, schema_editor):
    Permission = apps.get_model('core', 'Permission')
    Role = apps.get_model('core', 'Role')

    try:
        perm = Permission.objects.get(codename='can_view_report_account_sales')
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
        ('core', '0006_ambassador_manager_redirect_permission'),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
