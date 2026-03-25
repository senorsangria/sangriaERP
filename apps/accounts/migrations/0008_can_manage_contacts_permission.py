from django.db import migrations


def forwards(apps, schema_editor):
    Permission = apps.get_model('core', 'Permission')
    Role = apps.get_model('core', 'Role')

    perm, _ = Permission.objects.get_or_create(
        codename='can_manage_contacts',
        defaults={'description': 'Can manage account contacts'},
    )

    roles_to_assign = [
        'supplier_admin', 'sales_manager',
        'territory_manager', 'ambassador_manager',
    ]
    for codename in roles_to_assign:
        try:
            role = Role.objects.get(codename=codename)
            role.permissions.add(perm)
        except Role.DoesNotExist:
            pass


def backwards(apps, schema_editor):
    Permission = apps.get_model('core', 'Permission')
    try:
        Permission.objects.filter(codename='can_manage_contacts').delete()
    except Exception:
        pass


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0007_accountcontact'),
        ('core', '0008_remove_ambassador_manager_report_access'),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
