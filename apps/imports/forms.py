"""
Forms for the imports app: sales data upload and item mapping.
"""
from django import forms
from apps.distribution.models import Distributor
from apps.catalog.models import Brand, Item
from apps.imports.models import ItemMapping


class ImportUploadForm(forms.Form):
    """Step 1 of sales data import: select distributor and upload CSV."""

    distributor = forms.ModelChoiceField(
        queryset=Distributor.objects.none(),
        label='Distributor',
        empty_label='Select a distributor...',
        widget=forms.Select(attrs={'class': 'form-select'}),
    )
    csv_file = forms.FileField(
        label='CSV File',
        help_text='Upload a VIP sales data export CSV file.',
        widget=forms.ClearableFileInput(attrs={'class': 'form-control', 'accept': '.csv'}),
    )

    def __init__(self, *args, company=None, **kwargs):
        super().__init__(*args, **kwargs)
        if company:
            self.fields['distributor'].queryset = (
                Distributor.objects.filter(company=company, is_active=True)
                .order_by('name')
            )

    def clean_csv_file(self):
        f = self.cleaned_data.get('csv_file')
        if f:
            name = f.name.lower()
            if not name.endswith('.csv'):
                raise forms.ValidationError('Only CSV files are accepted.')
        return f


class ItemMappingForm(forms.ModelForm):
    """Create or edit an item mapping."""

    class Meta:
        model = ItemMapping
        fields = ['raw_item_name', 'distributor', 'brand', 'mapped_item', 'status']
        widgets = {
            'raw_item_name': forms.TextInput(attrs={'class': 'form-control'}),
            'distributor': forms.Select(attrs={'class': 'form-select'}),
            'brand': forms.Select(attrs={'class': 'form-select'}),
            'mapped_item': forms.Select(attrs={'class': 'form-select'}),
            'status': forms.Select(attrs={'class': 'form-select'}),
        }
        labels = {
            'raw_item_name': 'Raw Item Code',
            'mapped_item': 'Map To Item',
        }

    def __init__(self, *args, company=None, **kwargs):
        super().__init__(*args, **kwargs)
        if company:
            self.fields['distributor'].queryset = (
                Distributor.objects.filter(company=company, is_active=True)
                .order_by('name')
            )
            self.fields['brand'].queryset = (
                Brand.objects.filter(company=company, is_active=True)
                .order_by('name')
            )
            self.fields['mapped_item'].queryset = (
                Item.objects.filter(brand__company=company, is_active=True)
                .select_related('brand')
                .order_by('brand__name', 'item_code')
            )

        self.fields['distributor'].required = False
        self.fields['brand'].required = False
        self.fields['mapped_item'].required = False

        # Label items with brand + code for clarity
        self.fields['mapped_item'].label_from_instance = (
            lambda obj: f'{obj.brand.name} — {obj.item_code} ({obj.name})'
        )
