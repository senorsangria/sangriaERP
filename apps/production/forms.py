"""
Forms for production: period selection for inventory snapshot entry.
"""
from datetime import date

from django import forms


MONTH_CHOICES = [
    (1, 'January'), (2, 'February'), (3, 'March'), (4, 'April'),
    (5, 'May'), (6, 'June'), (7, 'July'), (8, 'August'),
    (9, 'September'), (10, 'October'), (11, 'November'), (12, 'December'),
]


def _year_choices():
    current_year = date.today().year
    return [(y, str(y)) for y in range(current_year - 1, current_year + 3)]


class OwnInventorySnapshotPeriodForm(forms.Form):
    year = forms.TypedChoiceField(
        choices=_year_choices,
        coerce=int,
        label='Year',
        widget=forms.Select(attrs={'class': 'form-select'}),
    )
    month = forms.TypedChoiceField(
        choices=MONTH_CHOICES,
        coerce=int,
        label='Month',
        widget=forms.Select(attrs={'class': 'form-select'}),
    )
