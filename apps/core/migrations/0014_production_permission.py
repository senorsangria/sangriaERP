"""
Data migration: add can_manage_production permission, grant to supplier_admin only.
"""
from django.db import migrations


def forwards(apps, schema_editor):
    Permission = apps.get_model('core', 'Permission')
    Role = apps.get_model('core', 'Role')

    perm, _ = Permission.objects.get_or_create(
        codename='can_manage_production',
        defaults={'description': 'Can manage production POs and inventory'},
    )
    Role.objects.get(codename='supplier_admin').permissions.add(perm)


def backwards(apps, schema_editor):
    Permission = apps.get_model('core', 'Permission')
    Role = apps.get_model('core', 'Role')

    try:
        perm = Permission.objects.get(codename='can_manage_production')
    except Permission.DoesNotExist:
        return

    try:
        Role.objects.get(codename='supplier_admin').permissions.remove(perm)
    except Role.DoesNotExist:
        pass

    perm.delete()


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0013_distributor_inventory_permission'),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
