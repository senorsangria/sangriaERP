"""
Data migration: remove can_view_report_item_sales and
can_view_report_account_distribution from the ambassador_manager role.

Follows the same pattern as 0008_remove_ambassador_manager_report_access,
which removed can_view_report_account_sales from this role. Ambassador
Managers now have no report-viewing permissions and see no Reports section.
"""
from django.db import migrations


def forwards(apps, schema_editor):
    Role = apps.get_model('core', 'Role')
    Permission = apps.get_model('core', 'Permission')
    role = Role.objects.get(codename='ambassador_manager')
    for codename in ('can_view_report_item_sales', 'can_view_report_account_distribution'):
        try:
            perm = Permission.objects.get(codename=codename)
            role.permissions.remove(perm)
        except Permission.DoesNotExist:
            pass


def backwards(apps, schema_editor):
    Role = apps.get_model('core', 'Role')
    Permission = apps.get_model('core', 'Permission')
    try:
        role = Role.objects.get(codename='ambassador_manager')
    except Role.DoesNotExist:
        return
    for codename in ('can_view_report_item_sales', 'can_view_report_account_distribution'):
        try:
            perm = Permission.objects.get(codename=codename)
            role.permissions.add(perm)
        except Permission.DoesNotExist:
            pass


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0011_report_account_distribution_permission'),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
