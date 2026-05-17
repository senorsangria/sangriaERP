"""
Custom template filters for RBAC permission checks.

Usage in templates:
    {% load rbac %}
    {% if user|has_perm:'can_reset_user_password' %}...{% endif %}
"""
from django import template

register = template.Library()


@register.filter
def has_perm(user, codename):
    """Return True if the user has the named permission."""
    return user.has_permission(codename)


@register.filter
def get_item(dictionary, key):
    """Look up a dictionary value by a variable key. Usage: {{ dict|get_item:key }}"""
    return dictionary.get(key)
