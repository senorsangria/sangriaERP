"""
Squash of core migrations 0002–0004.

core/0002_initial.py added a core.User.assigned_accounts M2M that pointed
to the now-removed distribution.Account model.  core/0004_remove_user_fields
removed that field (and others) in the same migration sequence.

On a fresh database Django cannot create the M2M junction table because
distribution.Account no longer exists in the current codebase, causing
"Related model 'distribution.account' cannot be resolved".

This squash represents the NET effect of 0002–0004:
  - Adds the fields that survived (company, groups, user_permissions,
    created_by, phone).
  - Removes the territory field that was present in 0001.
  - Skips the add-then-remove of assigned_accounts, assigned_distributors,
    managed_ambassadors (net effect: zero) so distribution is never touched.
"""
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    replaces = [
        ('core', '0002_initial'),
        ('core', '0003_add_phone_and_created_by_to_user'),
        ('core', '0004_remove_user_fields'),
    ]

    dependencies = [
        ('auth', '0012_alter_user_first_name_max_length'),
        ('core', '0001_initial'),
    ]

    operations = [
        # Fields that were added in 0002 and survive to the current model.
        migrations.AddField(
            model_name='user',
            name='company',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name='users',
                to='core.company',
            ),
        ),
        migrations.AddField(
            model_name='user',
            name='groups',
            field=models.ManyToManyField(
                blank=True,
                help_text='The groups this user belongs to. A user will get all permissions granted to each of their groups.',
                related_name='user_set',
                related_query_name='user',
                to='auth.group',
                verbose_name='groups',
            ),
        ),
        migrations.AddField(
            model_name='user',
            name='user_permissions',
            field=models.ManyToManyField(
                blank=True,
                help_text='Specific permissions for this user.',
                related_name='user_set',
                related_query_name='user',
                to='auth.permission',
                verbose_name='user permissions',
            ),
        ),
        # Fields added in 0003 that survive.
        migrations.AddField(
            model_name='user',
            name='created_by',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='created_users',
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name='user',
            name='phone',
            field=models.CharField(blank=True, max_length=50),
        ),
        # territory was added in 0001 and removed in 0004.
        migrations.RemoveField(
            model_name='user',
            name='territory',
        ),
        # Note: assigned_accounts, assigned_distributors, managed_ambassadors
        # were added in 0002 and removed in 0004 — net effect zero, omitted.
    ]
