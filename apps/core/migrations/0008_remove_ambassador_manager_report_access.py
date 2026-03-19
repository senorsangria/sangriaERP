"""
Data migration: remove can_view_report_account_sales permission from
ambassador_manager role.
"""
from django.db import migrations


def forwards(apps, schema_editor):
    Role = apps.get_model('core', 'Role')
    Permission = apps.get_model('core', 'Permission')
    role = Role.objects.get(codename='ambassador_manager')
    perm = Permission.objects.get(codename='can_view_report_account_sales')
    role.permissions.remove(perm)


def backwards(apps, schema_editor):
    Role = apps.get_model('core', 'Role')
    Permission = apps.get_model('core', 'Permission')
    try:
        role = Role.objects.get(codename='ambassador_manager')
        perm = Permission.objects.get(codename='can_view_report_account_sales')
        role.permissions.add(perm)
    except (Role.DoesNotExist, Permission.DoesNotExist):
        pass


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0007_report_account_sales_permission'),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
