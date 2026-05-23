"""
Shared filter utilities for session-backed list views.

Standard pattern:
  - Session-stored filters with save/restore/clear
  - Active filter count for badge display
  - Multi-value getlist support

Reference implementation: apps/accounts/views.py account_list
See: ARCHITECTURE.md — Filter Pattern section
"""


def apply_session_filters(request, session_key, default_filters=None):
    """
    Standard session filter handling for list views.

    Returns (active_filters: dict, was_just_set: bool).

    Behavior:
    - If request has filter params matching default_filters keys: save to session
    - If ?clear_filters=1: pop session, return defaults
    - Otherwise: restore from session if present, else return defaults

    Caller is responsible for redirecting after ?clear_filters=1 if needed.
    Multi-value params (list defaults) are read via request.GET.getlist().
    """
    default_filters = default_filters or {}

    if request.GET.get('clear_filters') == '1':
        request.session.pop(session_key, None)
        return default_filters.copy(), False

    filter_params_in_get = any(key in request.GET for key in default_filters.keys())

    if filter_params_in_get:
        active_filters = {}
        for key, default_value in default_filters.items():
            if isinstance(default_value, list):
                active_filters[key] = request.GET.getlist(key)
            else:
                active_filters[key] = request.GET.get(key, default_value)
        request.session[session_key] = active_filters
        return active_filters, True

    # Restore from session or use defaults
    if session_key in request.session:
        restored = dict(request.session[session_key])
        # Ensure list-type values are lists (backward compat with old session data)
        for key, default_value in default_filters.items():
            if isinstance(default_value, list) and not isinstance(restored.get(key), list):
                restored[key] = [restored[key]] if restored.get(key) else []
        return restored, False

    return default_filters.copy(), False


def compute_active_filter_count(active_filters, default_filters):
    """
    Count filter dimensions that differ from their default value.

    For list filters: count as 1 if list is non-empty.
    For scalar filters: count as 1 if value is truthy and differs from default.
    """
    count = 0
    for key, default_value in default_filters.items():
        current = active_filters.get(key, default_value)
        if isinstance(default_value, list):
            if current:
                count += 1
        else:
            if current and current != default_value:
                count += 1
    return count


def is_filter_active(active_filters, default_filters):
    """Return True if any filter dimension has a non-default value."""
    return compute_active_filter_count(active_filters, default_filters) > 0
