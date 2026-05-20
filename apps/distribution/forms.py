"""
Forms for distribution: Distributor CRUD, inventory CSV upload, and Distributor Groups.
"""
from datetime import date

from django import forms

from .models import Distributor, DistributorGroup


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


class DistributorGroupForm(forms.ModelForm):
    members = forms.ModelMultipleChoiceField(
        queryset=Distributor.objects.none(),
        widget=forms.CheckboxSelectMultiple,
        required=False,
        help_text='Select distributors to include in this group.',
    )

    class Meta:
        model = DistributorGroup
        fields = ['name', 'primary_distributor', 'members', 'notes']
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control'}),
            'primary_distributor': forms.Select(attrs={'class': 'form-select'}),
            'notes': forms.Textarea(attrs={'class': 'form-control', 'rows': 3}),
        }

    def __init__(self, *args, company=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.company = company
        self.distributor_current_groups = {}
        if company is not None:
            distributors_qs = (
                Distributor.objects.filter(company=company, is_active=True)
                .order_by('name')
                .select_related('group')
            )
            self.fields['members'].queryset = distributors_qs
            self.fields['primary_distributor'].queryset = distributors_qs
            self.fields['primary_distributor'].empty_label = '— Select primary distributor —'
            self.fields['primary_distributor'].required = True
            for dist in distributors_qs:
                if dist.group_id and (not self.instance.pk or dist.group_id != self.instance.pk):
                    self.distributor_current_groups[str(dist.pk)] = dist.group.name
        if self.instance and self.instance.pk:
            self.fields['members'].initial = self.instance.members.all()

    def clean_name(self):
        name = self.cleaned_data['name']
        qs = DistributorGroup.objects.filter(company=self.company, name=name)
        if self.instance and self.instance.pk:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise forms.ValidationError('A group with this name already exists.')
        return name

    def clean(self):
        cleaned = super().clean()
        primary = cleaned.get('primary_distributor')
        members = cleaned.get('members') or []
        members_list = list(members)
        if not members_list:
            raise forms.ValidationError({'members': 'A group must have at least one member.'})
        if primary and primary not in members_list:
            raise forms.ValidationError({'primary_distributor': 'Primary distributor must be one of the selected members.'})

        # Block if any member is already in a different group
        conflicts = []
        current_group_pk = self.instance.pk if self.instance and self.instance.pk else None
        for member in members_list:
            if member.group_id and member.group_id != current_group_pk:
                conflicts.append({
                    'distributor_name': member.name,
                    'distributor_pk': member.pk,
                    'group_name': member.group.name,
                    'group_pk': member.group_id,
                })

        if conflicts:
            self._conflicts = conflicts
            raise forms.ValidationError(
                'Some selected distributors are already in another group. Remove them from their current group first.'
            )

        return cleaned

    def save(self, commit=True):
        instance = super().save(commit=False)
        if self.company is not None:
            instance.company = self.company
        if commit:
            instance.save()
            new_members = set(self.cleaned_data['members'])
            old_members = set(instance.members.all()) if instance.pk else set()

            for d in new_members - old_members:
                d.group = instance
                d.save(update_fields=['group'])

            for d in old_members - new_members:
                d.group = None
                d.save(update_fields=['group'])
        return instance


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
