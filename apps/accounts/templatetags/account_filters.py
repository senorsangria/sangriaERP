import re
from django import template

register = template.Library()


@register.filter(name='smart_title')
def smart_title(value):
    """
    Title-case a string ONLY if it's currently all uppercase.
    Mixed-case input is returned unchanged (assumed already curated).
    """
    if not value or not isinstance(value, str):
        return value

    has_letters = any(c.isalpha() for c in value)
    if not has_letters:
        return value

    is_all_caps = all(c.isupper() or not c.isalpha() for c in value)

    if is_all_caps:
        result = value.title()
        # Python's title() incorrectly capitalizes possessive 'S:
        # "JOHN'S BAR" → "John'S Bar". Fix by lowercasing only when
        # the char before the apostrophe is lowercase (mid-word possessive),
        # not when it is uppercase (name prefixes like O'Connor).
        result = re.sub(r"([a-z])'([A-Z])", lambda m: m.group(1) + "'" + m.group(2).lower(), result)
        return result

    return value
