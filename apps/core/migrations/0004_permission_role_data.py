"""
Data migration: populate all Permission and Role records.

This is the authoritative source of truth for the permission system.
Phase 10.5 Step 3.
"""
from django.db import migrations


PERMISSIONS = [
    # Authentication & Navigation
    ('can_access_dashboard',            'Can access the dashboard'),
    ('can_redirect_to_events_on_login', 'Redirect to events list on login instead of dashboard'),
    # User Management
    ('can_manage_users',            'Can access user management'),
    ('can_create_users',            'Can create new users'),
    ('can_reset_user_password',     "Can reset another user's password"),
    ('can_manage_user',             'Can edit and manage individual users'),
    ('can_view_coverage_areas_tab', 'Can view coverage areas tab on user profile'),
    ('can_assign_coverage_areas',   'Can add/remove coverage area assignments'),
    # Catalog
    ('can_manage_brands', 'Can create and edit brands'),
    ('can_manage_items',  'Can create and edit items'),
    ('can_reorder_items', 'Can change item sort order'),
    # Distributors
    ('can_manage_distributors', 'Can create and edit distributors'),
    # Imports
    ('can_import_sales_data', 'Can import sales data files'),
    # Accounts
    ('can_view_accounts',        'Can view the account list and detail pages'),
    ('can_create_accounts',      'Can create new accounts'),
    ('can_edit_accounts',        'Can edit account details'),
    ('can_toggle_account_status', 'Can activate and deactivate accounts'),
    ('can_delete_accounts',      'Can delete manually created accounts'),
    ('can_view_all_accounts',    'Can view all accounts regardless of coverage area'),
    # Events
    ('can_view_events',       'Can view the event list and detail pages'),
    ('can_export_events_csv', 'Can export the event list as CSV'),
    ('can_view_draft_events', 'Can see events in Draft status'),
    ('can_create_events',     'Can create new events'),
    ('can_edit_events',       'Can edit event setup fields'),
    ('can_release_event',     'Can release a Draft event to Scheduled'),
    ('can_request_revision',  'Can request revision on a submitted recap'),
    ('can_approve_event',     'Can approve and complete a submitted recap'),
    ('can_delete_event',      'Can permanently delete Draft events'),
    ('can_fill_recap',        'Can fill out and submit event recap'),
    ('can_view_all_events',   'Can view all events regardless of coverage area'),
    # Platform
    ('can_view_saas_admin_ui', 'Can access the SaaS admin UI'),
    ('can_mark_ok_to_pay',     'Can mark events as OK to pay'),
]

ROLES = [
    {
        'name': 'SaaS Admin',
        'codename': 'saas_admin',
        'permissions': [
            'can_access_dashboard', 'can_manage_users', 'can_create_users',
            'can_reset_user_password', 'can_manage_user', 'can_view_coverage_areas_tab',
            'can_assign_coverage_areas', 'can_manage_brands', 'can_manage_items',
            'can_reorder_items', 'can_manage_distributors', 'can_import_sales_data',
            'can_view_accounts', 'can_create_accounts', 'can_edit_accounts',
            'can_toggle_account_status', 'can_delete_accounts', 'can_view_all_accounts',
            'can_view_events', 'can_export_events_csv', 'can_view_draft_events',
            'can_create_events', 'can_edit_events', 'can_release_event',
            'can_request_revision', 'can_approve_event', 'can_delete_event',
            'can_fill_recap', 'can_view_all_events', 'can_view_saas_admin_ui',
            'can_mark_ok_to_pay',
        ],
    },
    {
        'name': 'Supplier Admin',
        'codename': 'supplier_admin',
        'permissions': [
            'can_access_dashboard', 'can_manage_users', 'can_create_users',
            'can_reset_user_password', 'can_manage_user', 'can_view_coverage_areas_tab',
            'can_assign_coverage_areas', 'can_manage_brands', 'can_manage_items',
            'can_reorder_items', 'can_manage_distributors', 'can_import_sales_data',
            'can_view_accounts', 'can_create_accounts', 'can_edit_accounts',
            'can_toggle_account_status', 'can_delete_accounts', 'can_view_all_accounts',
            'can_view_events', 'can_export_events_csv', 'can_view_draft_events',
            'can_create_events', 'can_edit_events', 'can_release_event',
            'can_request_revision', 'can_approve_event', 'can_delete_event',
            'can_fill_recap', 'can_view_all_events', 'can_mark_ok_to_pay',
        ],
    },
    {
        'name': 'Sales Manager',
        'codename': 'sales_manager',
        'permissions': [
            'can_access_dashboard', 'can_reset_user_password', 'can_manage_user',
            'can_view_accounts', 'can_create_accounts', 'can_edit_accounts',
            'can_toggle_account_status', 'can_delete_accounts',
            'can_view_events', 'can_export_events_csv', 'can_view_draft_events',
            'can_create_events', 'can_edit_events', 'can_release_event',
            'can_request_revision', 'can_approve_event', 'can_delete_event',
            'can_fill_recap',
        ],
    },
    {
        'name': 'Territory Manager',
        'codename': 'territory_manager',
        'permissions': [
            'can_access_dashboard', 'can_manage_user',
            'can_view_accounts', 'can_create_accounts', 'can_edit_accounts',
            'can_toggle_account_status', 'can_delete_accounts',
            'can_view_events', 'can_export_events_csv', 'can_view_draft_events',
            'can_create_events', 'can_edit_events', 'can_release_event',
            'can_request_revision', 'can_approve_event', 'can_delete_event',
            'can_fill_recap',
        ],
    },
    {
        'name': 'Ambassador Manager',
        'codename': 'ambassador_manager',
        'permissions': [
            'can_access_dashboard', 'can_manage_user',
            'can_view_accounts', 'can_create_accounts', 'can_edit_accounts',
            'can_toggle_account_status', 'can_delete_accounts',
            'can_view_events', 'can_export_events_csv', 'can_view_draft_events',
            'can_create_events', 'can_edit_events', 'can_release_event',
            'can_request_revision', 'can_approve_event', 'can_delete_event',
            'can_fill_recap',
        ],
    },
    {
        'name': 'Ambassador',
        'codename': 'ambassador',
        'permissions': [
            'can_redirect_to_events_on_login',
            'can_view_events', 'can_export_events_csv', 'can_fill_recap',
        ],
    },
    {
        'name': 'Distributor Contact',
        'codename': 'distributor_contact',
        'permissions': [],  # read-only placeholder, no permissions yet
    },
    {
        'name': 'Payroll Reviewer',
        'codename': 'payroll_reviewer',
        'permissions': [
            'can_access_dashboard', 'can_view_events', 'can_export_events_csv',
            'can_view_draft_events', 'can_mark_ok_to_pay',
        ],
    },
]


def populate_permissions_and_roles(apps, schema_editor):
    Permission = apps.get_model('core', 'Permission')
    Role = apps.get_model('core', 'Role')

    perm_map = {}
    for codename, description in PERMISSIONS:
        perm, _ = Permission.objects.get_or_create(
            codename=codename,
            defaults={'description': description},
        )
        perm_map[codename] = perm

    for role_data in ROLES:
        role, _ = Role.objects.get_or_create(
            codename=role_data['codename'],
            defaults={'name': role_data['name']},
        )
        role.permissions.set([perm_map[c] for c in role_data['permissions']])


def remove_permissions_and_roles(apps, schema_editor):
    Role = apps.get_model('core', 'Role')
    Permission = apps.get_model('core', 'Permission')
    Role.objects.all().delete()
    Permission.objects.all().delete()


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0003_permission_role'),
    ]

    operations = [
        migrations.RunPython(
            populate_permissions_and_roles,
            remove_permissions_and_roles,
        ),
    ]
