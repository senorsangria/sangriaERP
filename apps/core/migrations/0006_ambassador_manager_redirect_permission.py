"""
Data migration: add can_redirect_to_events_on_login permission to Ambassador Manager role.
"""
from django.db import migrations


def forwards(apps, schema_editor):
    Role = apps.get_model('core', 'Role')
    Permission = apps.get_model('core', 'Permission')
    role = Role.objects.get(codename='ambassador_manager')
    perm = Permission.objects.get(codename='can_redirect_to_events_on_login')
    role.permissions.add(perm)


def backwards(apps, schema_editor):
    Role = apps.get_model('core', 'Role')
    Permission = apps.get_model('core', 'Permission')
    role = Role.objects.get(codename='ambassador_manager')
    perm = Permission.objects.get(codename='can_redirect_to_events_on_login')
    role.permissions.remove(perm)


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0005_user_roles_field'),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
