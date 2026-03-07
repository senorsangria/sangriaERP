"""
Forms for accounts: Account CRUD.
"""
from django import forms

from apps.distribution.models import Distributor
from .models import Account


US_STATE_CHOICES = [
    ('', '— Select State —'),
    ('AL', 'AL'), ('AK', 'AK'), ('AZ', 'AZ'), ('AR', 'AR'), ('CA', 'CA'),
    ('CO', 'CO'), ('CT', 'CT'), ('DE', 'DE'), ('FL', 'FL'), ('GA', 'GA'),
    ('HI', 'HI'), ('ID', 'ID'), ('IL', 'IL'), ('IN', 'IN'), ('IA', 'IA'),
    ('KS', 'KS'), ('KY', 'KY'), ('LA', 'LA'), ('ME', 'ME'), ('MD', 'MD'),
    ('MA', 'MA'), ('MI', 'MI'), ('MN', 'MN'), ('MS', 'MS'), ('MO', 'MO'),
    ('MT', 'MT'), ('NE', 'NE'), ('NV', 'NV'), ('NH', 'NH'), ('NJ', 'NJ'),
    ('NM', 'NM'), ('NY', 'NY'), ('NC', 'NC'), ('ND', 'ND'), ('OH', 'OH'),
    ('OK', 'OK'), ('OR', 'OR'), ('PA', 'PA'), ('RI', 'RI'), ('SC', 'SC'),
    ('SD', 'SD'), ('TN', 'TN'), ('TX', 'TX'), ('UT', 'UT'), ('VT', 'VT'),
    ('VA', 'VA'), ('WA', 'WA'), ('WV', 'WV'), ('WI', 'WI'), ('WY', 'WY'),
    ('DC', 'DC'),
]

ON_OFF_CHOICES = [
    ('Unknown', 'Unknown'),
    ('ON', 'ON'),
    ('OFF', 'OFF'),
]


class AccountForm(forms.ModelForm):
    state = forms.ChoiceField(
        choices=US_STATE_CHOICES,
        required=False,
        widget=forms.Select(attrs={'class': 'form-select'}),
    )
    on_off_premise = forms.ChoiceField(
        choices=ON_OFF_CHOICES,
        required=False,
        widget=forms.Select(attrs={'class': 'form-select'}),
    )

    class Meta:
        model = Account
        fields = [
            'name', 'street', 'city', 'state', 'zip_code', 'phone',
            'county', 'on_off_premise', 'account_type', 'distributor', 'is_active',
        ]
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control'}),
            'street': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Optional'}),
            'city': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Optional'}),
            'zip_code': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Optional'}),
            'phone': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Optional'}),
            'county': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Optional'}),
            'account_type': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Optional'}),
            'distributor': forms.Select(attrs={'class': 'form-select'}),
            'is_active': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        }
        labels = {
            'zip_code': 'Zip Code',
            'on_off_premise': 'On / Off Premise',
            'account_type': 'Account Type',
            'is_active': 'Active',
        }

    def __init__(self, *args, company=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.company = company

        # Distributor choices scoped to company's active distributors
        qs = (
            Distributor.objects.filter(company=company, is_active=True).order_by('name')
            if company else Distributor.objects.none()
        )
        self.fields['distributor'].queryset = qs
        self.fields['distributor'].required = False
        self.fields['distributor'].empty_label = 'No distributor assigned'

        for field_name in ['street', 'city', 'zip_code', 'phone', 'county']:
            self.fields[field_name].required = False

        # Set defaults for new records
        if not self.instance.pk:
            self.fields['on_off_premise'].initial = 'Unknown'
            self.fields['account_type'].initial = ''
            self.fields['is_active'].initial = True

    def clean_name(self):
        name = self.cleaned_data.get('name', '').strip()
        if not name:
            raise forms.ValidationError('Account name is required.')
        return name

    def save(self, commit=True):
        account = super().save(commit=False)
        if self.company and not account.pk:
            account.company = self.company
        if commit:
            account.save()
        return account
