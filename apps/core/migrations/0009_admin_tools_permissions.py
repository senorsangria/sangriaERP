"""
Data migration: add Admin Tools permissions and grant to supplier_admin only.

Three menu items previously gated by is_supplier_admin now have proper permissions:
- can_view_import_history
- can_manage_item_mapping
- can_run_historical_event_import
"""
from django.db import migrations


NEW_PERMISSIONS = [
    ('can_view_import_history',        'Can view sales import history'),
    ('can_manage_item_mapping',        'Can view and manage item mappings'),
    ('can_run_historical_event_import', 'Can run the historical event import tool'),
]


def forwards(apps, schema_editor):
    Permission = apps.get_model('core', 'Permission')
    Role = apps.get_model('core', 'Role')

    supplier_admin = Role.objects.get(codename='supplier_admin')

    for codename, description in NEW_PERMISSIONS:
        perm, _ = Permission.objects.get_or_create(
            codename=codename,
            defaults={'description': description},
        )
        supplier_admin.permissions.add(perm)


def backwards(apps, schema_editor):
    Permission = apps.get_model('core', 'Permission')
    Role = apps.get_model('core', 'Role')

    try:
        supplier_admin = Role.objects.get(codename='supplier_admin')
    except Role.DoesNotExist:
        supplier_admin = None

    for codename, _ in NEW_PERMISSIONS:
        try:
            perm = Permission.objects.get(codename=codename)
        except Permission.DoesNotExist:
            continue
        if supplier_admin:
            supplier_admin.permissions.remove(perm)
        perm.delete()


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0008_remove_ambassador_manager_report_access'),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
