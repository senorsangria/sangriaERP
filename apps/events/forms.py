"""
Event forms.
"""
from django import forms

from apps.catalog.models import Item
from apps.core.models import User

from .models import Event


class EventForm(forms.ModelForm):
    """
    Create and edit form for Event.

    Instantiate with `company=` and optional `user=` kwargs.
    Items queryset and people dropdowns are scoped to the company.
    """

    DURATION_HOURS_CHOICES = [(i, f'{i}h') for i in range(9)]  # 0–8

    duration_hours = forms.ChoiceField(
        choices=DURATION_HOURS_CHOICES,
        initial=0,
        label='Hours',
    )

    class Meta:
        model = Event
        fields = [
            'event_type',
            'account',
            'date',
            'start_time',
            'duration_hours',
            'duration_minutes',
            'ambassador',
            'event_manager',
            'items',
            'notes',
        ]
        widgets = {
            'date': forms.DateInput(attrs={'type': 'date', 'class': 'form-control'}),
            'start_time': forms.TimeInput(attrs={'type': 'time', 'class': 'form-control'}),
            'notes': forms.Textarea(attrs={'rows': 3, 'class': 'form-control'}),
        }

    def __init__(self, *args, company=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.company = company

        # Scope account dropdown to active company accounts
        from apps.accounts.models import Account
        self.fields['account'].queryset = (
            Account.active_accounts.filter(company=company).order_by('name')
            if company else Account.active_accounts.none()
        )
        self.fields['account'].required = False
        self.fields['account'].empty_label = '— Select account —'

        # Ambassador: all ambassadors and AMs in the company (refined via AJAX)
        self.fields['ambassador'].queryset = (
            User.objects.filter(
                company=company,
                is_active=True,
                role__in=[User.Role.AMBASSADOR, User.Role.AMBASSADOR_MANAGER],
            ).order_by('last_name', 'first_name')
            if company else User.objects.none()
        )
        self.fields['ambassador'].required = False
        self.fields['ambassador'].empty_label = '— Unassigned —'

        # Event Manager: all TMs and AMs in the company (refined via AJAX)
        self.fields['event_manager'].queryset = (
            User.objects.filter(
                company=company,
                is_active=True,
                role__in=[User.Role.TERRITORY_MANAGER, User.Role.AMBASSADOR_MANAGER],
            ).order_by('last_name', 'first_name')
            if company else User.objects.none()
        )
        self.fields['event_manager'].required = False
        self.fields['event_manager'].empty_label = '— Select event manager —'

        # Items: active items across all company brands
        from apps.catalog.models import Brand
        company_brand_pks = (
            Brand.objects.filter(company=company, is_active=True)
            .values_list('pk', flat=True)
            if company else []
        )
        self.fields['items'].queryset = (
            Item.objects.filter(
                brand__in=company_brand_pks, is_active=True
            ).select_related('brand').order_by('brand__name', 'name')
        )
        self.fields['items'].required = False
        self.fields['items'].widget.attrs.update({'class': 'form-select', 'size': '6'})

        # Coerce duration_hours initial from model instance if editing
        if self.instance and self.instance.pk:
            self.fields['duration_hours'].initial = self.instance.duration_hours

        # Apply Bootstrap classes
        for name, field in self.fields.items():
            if name in ('items',):
                continue
            if hasattr(field.widget, 'attrs'):
                if 'class' not in field.widget.attrs:
                    field.widget.attrs['class'] = 'form-control'
                if isinstance(field.widget, forms.Select):
                    field.widget.attrs['class'] = 'form-select'
                if isinstance(field.widget, forms.CheckboxSelectMultiple):
                    field.widget.attrs.pop('class', None)

    def clean_duration_hours(self):
        val = self.cleaned_data.get('duration_hours')
        try:
            return int(val)
        except (TypeError, ValueError):
            return 0
