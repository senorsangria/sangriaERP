"""
Forms for distribution: Distributor CRUD.
"""
from django import forms
from .models import Distributor


class DistributorForm(forms.ModelForm):
    class Meta:
        model = Distributor
        fields = ['name', 'address', 'city', 'state', 'notes', 'is_active']
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control'}),
            'address': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Optional'}),
            'city': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Optional'}),
            'state': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Optional'}),
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
        if self.company and not distributor.pk:
            distributor.company = self.company
        if commit:
            distributor.save()
        return distributor
