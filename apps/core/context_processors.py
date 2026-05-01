"""
Custom context processors for productERP.
"""


def navigation(request):
    """Expose nav_sections and admin_tools_collapsed to all templates."""
    if not hasattr(request, 'user'):
        return {}
    from apps.core.nav import get_nav_for_user
    return {
        'nav_sections': get_nav_for_user(request.user, request),
        'admin_tools_collapsed': request.session.get('admin_tools_collapsed', True),
    }
