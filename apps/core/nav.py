"""
Data-driven navigation menu definition.

NAV_SECTIONS and NAV_ITEMS are the single source of truth for menu structure.
Add new menu items by editing NAV_ITEMS only.

get_nav_for_user() is called by the navigation context processor and returns
a filtered, annotated list of sections ready for template rendering.
"""

NAV_SECTIONS = [
    {'key': 'main',        'label': None,          'collapsible': False},
    {'key': 'reports',     'label': 'Reports',      'collapsible': False},
    {'key': 'admin_tools', 'label': 'Admin Tools',  'collapsible': True},
]

NAV_ITEMS = [
    # ---- Main section (unlabeled — top of menu) ----
    {
        'label': 'Events',
        'url_name': 'event_list',
        'icon': 'bi-calendar-event',
        'permission': 'can_view_events',
        'section': 'main',
        'active_match': 'event',
    },
    {
        'label': 'Accounts',
        'url_name': 'account_list',
        'icon': 'bi-shop',
        'permission': 'can_view_accounts',
        'section': 'main',
        'active_match': 'account',
    },
    {
        'label': 'Distributors',
        'url_name': 'distributor_list',
        'icon': 'bi-truck',
        'permission': 'can_manage_distributors',
        'section': 'main',
        'active_match': 'distributor',
    },

    # ---- Reports section ----
    {
        'label': 'Account Sales by Year',
        'url_name': 'report_account_sales_by_year',
        'icon': 'bi-bar-chart-line',
        'permission': 'can_view_report_account_sales',
        'section': 'reports',
        'active_match': 'report_account_sales',
    },

    # ---- Admin Tools section (collapsible) ----
    {
        'label': 'Users',
        'url_name': 'user_list',
        'icon': 'bi-people',
        'permission': 'can_manage_users',
        'section': 'admin_tools',
        'active_match': 'user',
    },
    {
        'label': 'Brands',
        'url_name': 'brand_list',
        'icon': 'bi-bookmark',
        'permission': 'can_manage_brands',
        'section': 'admin_tools',
        'active_match': 'brand',
    },
    {
        'label': 'Sales Import',
        'url_name': 'import_upload',
        'icon': 'bi-cloud-upload',
        'permission': 'can_import_sales_data',
        'section': 'admin_tools',
        'active_match': 'import_upload',
    },
    {
        'label': 'Sales Import History',
        'url_name': 'batch_list',
        'icon': 'bi-clock-history',
        'permission': 'can_view_import_history',
        'section': 'admin_tools',
        'active_match': 'batch',
    },
    {
        'label': 'Account Import',
        'url_name': 'account_import_upload',
        'icon': 'bi-building-up',
        'permission': 'can_import_sales_data',
        'section': 'admin_tools',
        'active_match': 'account_import',
    },
    {
        'label': 'Item Mapping',
        'url_name': 'mapping_list',
        'icon': 'bi-arrow-left-right',
        'permission': 'can_manage_item_mapping',
        'section': 'admin_tools',
        'active_match': 'mapping',
    },
    {
        'label': 'Historical Event Import',
        'url_name': 'event_import_upload',
        'icon': 'bi-calendar2-plus',
        'permission': 'can_run_historical_event_import',
        'section': 'admin_tools',
        'active_match': 'event_import',
    },
]


def get_nav_for_user(user, request):
    """
    Returns a list of section dicts ready for template rendering.

    Each section dict has:
      key, label, collapsible, has_label, items (list of visible item dicts).

    Sections with no visible items are omitted entirely so empty section
    headers never appear. Items are annotated with is_active based on
    the current URL name.
    """
    if not user.is_authenticated:
        return []

    current_url_name = ''
    if request.resolver_match:
        current_url_name = request.resolver_match.url_name or ''

    # Resolve visible items, annotating each with is_active.
    # We copy each item dict so mutations don't affect the module-level list.
    visible_items = []
    for item in NAV_ITEMS:
        if 'permission' in item:
            if not user.has_permission(item['permission']):
                continue
        elif 'role_check' in item:
            if not getattr(user, item['role_check'], False):
                continue
        # Items with neither key are shown to all authenticated users.
        copy = dict(item)
        match = copy.get('active_match', '')
        copy['is_active'] = bool(match and match in current_url_name)
        visible_items.append(copy)

    # Group by section, preserving NAV_SECTIONS order.
    sections = []
    for section_def in NAV_SECTIONS:
        items_in_section = [i for i in visible_items if i['section'] == section_def['key']]
        if not items_in_section:
            continue
        sections.append({
            'key': section_def['key'],
            'label': section_def['label'],
            'collapsible': section_def['collapsible'],
            'has_label': section_def['label'] is not None,
            'items': items_in_section,
        })

    return sections
