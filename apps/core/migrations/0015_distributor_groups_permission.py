"""
Data migration: add can_manage_distributor_groups permission, grant to supplier_admin.
"""
from django.db import migrations


def forwards(apps, schema_editor):
    Permission = apps.get_model('core', 'Permission')
    Role = apps.get_model('core', 'Role')
    perm, _ = Permission.objects.get_or_create(
        codename='can_manage_distributor_groups',
        defaults={'description': 'Can manage distributor groups'},
    )
    try:
        role = Role.objects.get(codename='supplier_admin')
        role.permissions.add(perm)
    except Role.DoesNotExist:
        pass


def backwards(apps, schema_editor):
    Permission = apps.get_model('core', 'Permission')
    Role = apps.get_model('core', 'Role')
    try:
        perm = Permission.objects.get(codename='can_manage_distributor_groups')
        try:
            role = Role.objects.get(codename='supplier_admin')
            role.permissions.remove(perm)
        except Role.DoesNotExist:
            pass
        perm.delete()
    except Permission.DoesNotExist:
        pass


class Migration(migrations.Migration):

    dependencies = [('core', '0014_production_permission')]

    operations = [migrations.RunPython(forwards, backwards)]
