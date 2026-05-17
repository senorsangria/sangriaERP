"""
Data migration: add can_manage_distributor_inventory permission, grant to supplier_admin only.
"""
from django.db import migrations


def forwards(apps, schema_editor):
    Permission = apps.get_model('core', 'Permission')
    Role = apps.get_model('core', 'Role')

    perm, _ = Permission.objects.get_or_create(
        codename='can_manage_distributor_inventory',
        defaults={'description': 'Can manage distributor inventory profiles and safety stock'},
    )
    Role.objects.get(codename='supplier_admin').permissions.add(perm)


def backwards(apps, schema_editor):
    Permission = apps.get_model('core', 'Permission')
    Role = apps.get_model('core', 'Role')

    try:
        perm = Permission.objects.get(codename='can_manage_distributor_inventory')
    except Permission.DoesNotExist:
        return

    try:
        Role.objects.get(codename='supplier_admin').permissions.remove(perm)
    except Role.DoesNotExist:
        pass

    perm.delete()


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0012_revoke_report_permissions_from_ambassador_manager'),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
