"""
Forms for distribution: Distributor CRUD and inventory CSV upload.
"""
from datetime import date

from django import forms

from .models import Distributor


MONTH_CHOICES = [
    (1, 'January'), (2, 'February'), (3, 'March'), (4, 'April'),
    (5, 'May'), (6, 'June'), (7, 'July'), (8, 'August'),
    (9, 'September'), (10, 'October'), (11, 'November'), (12, 'December'),
]

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


class DistributorForm(forms.ModelForm):
    state = forms.ChoiceField(
        choices=US_STATE_CHOICES,
        required=False,
        widget=forms.Select(attrs={'class': 'form-select'}),
    )

    class Meta:
        model = Distributor
        fields = ['name', 'address', 'city', 'state', 'notes', 'is_active']
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control'}),
            'address': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Optional'}),
            'city': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Optional'}),
            'notes': forms.Textarea(attrs={'class': 'form-control', 'rows': 3, 'placeholder': 'Optional'}),
            'is_active': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        }
        labels = {
            'is_active': 'Active',
        }

    def __init__(self, *args, company=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.company = company
        self.fields['name'].required = True
        self.fields['address'].required = False
        self.fields['city'].required = False
        self.fields['state'].required = False
        self.fields['notes'].required = False
        self.fields['is_active'].initial = True

    def clean_name(self):
        name = self.cleaned_data.get('name', '').strip()
        if not name:
            raise forms.ValidationError('Distributor name is required.')
        qs = Distributor.objects.filter(company=self.company, name__iexact=name)
        if self.instance and self.instance.pk:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise forms.ValidationError(
                'A distributor with this name already exists for your company.'
            )
        return name

    def save(self, commit=True):
        distributor = super().save(commit=False)
        if not self.company:
            raise ValueError("Cannot save Distributor without a company.")
        if not distributor.pk:
            distributor.company = self.company
        if commit:
            distributor.save()
        return distributor


class InventoryImportUploadForm(forms.Form):
    """Step 1 of inventory snapshot import: select period and upload CSV."""

    year = forms.TypedChoiceField(
        coerce=int,
        label='Year',
        widget=forms.Select(attrs={'class': 'form-select'}),
    )
    month = forms.TypedChoiceField(
        coerce=int,
        choices=MONTH_CHOICES,
        label='Month',
        widget=forms.Select(attrs={'class': 'form-select'}),
    )
    csv_file = forms.FileField(
        label='Inventory CSV',
        help_text='Upload the VIP inventory report CSV file (max 5 MB).',
        widget=forms.FileInput(attrs={
            'class': 'form-control',
            'accept': '.csv,text/csv',
        }),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        current_year = date.today().year
        # Offer current+1 down to current-3 (5 choices)
        year_choices = [(y, str(y)) for y in range(current_year + 1, current_year - 4, -1)]
        self.fields['year'].choices = year_choices

    def clean_csv_file(self):
        f = self.cleaned_data.get('csv_file')
        if f:
            if not f.name.lower().endswith('.csv'):
                raise forms.ValidationError('Please upload a .csv file.')
            if f.size > 5 * 1024 * 1024:
                raise forms.ValidationError('File size must be under 5 MB.')
        return f

    def clean_year(self):
        year = self.cleaned_data.get('year')
        if year is not None and not (2000 <= year <= 2100):
            raise forms.ValidationError('Year must be between 2000 and 2100.')
        return year

    def clean_month(self):
        month = self.cleaned_data.get('month')
        if month is not None and not (1 <= month <= 12):
            raise forms.ValidationError('Month must be between 1 and 12.')
        return month
