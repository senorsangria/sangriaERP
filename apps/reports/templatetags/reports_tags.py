"""Custom template filters for the reports app."""
from django import template

register = template.Library()


@register.filter
def get_item(dictionary, key):
    """Return dictionary[key], or None if missing. Usage: {{ my_dict|get_item:key }}"""
    if not isinstance(dictionary, dict):
        return None
    return dictionary.get(key)
