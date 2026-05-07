"""
Forms for catalog: Brand and Item CRUD.
"""
from django import forms
from .models import Brand, Item


class BrandForm(forms.ModelForm):
    class Meta:
        model = Brand
        fields = ['name', 'description', 'is_active']
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control'}),
            'description': forms.Textarea(attrs={'class': 'form-control', 'rows': 3}),
            'is_active': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        }
        labels = {
            'is_active': 'Active',
        }

    def __init__(self, *args, company=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.company = company
        self.fields['name'].required = True
        self.fields['description'].required = False
        self.fields['is_active'].initial = True

    def clean_name(self):
        name = self.cleaned_data.get('name', '').strip()
        if not name:
            raise forms.ValidationError('Brand name is required.')
        qs = Brand.objects.filter(company=self.company, name__iexact=name)
        if self.instance and self.instance.pk:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise forms.ValidationError(
                'A brand with this name already exists for your company.'
            )
        return name

    def save(self, commit=True):
        brand = super().save(commit=False)
        if self.company and not brand.pk:
            brand.company = self.company
        if commit:
            brand.save()
        return brand


class ItemForm(forms.ModelForm):
    class Meta:
        model = Item
        fields = ['name', 'item_code', 'sku_number', 'cases_per_pallet', 'description', 'is_active']
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control'}),
            'item_code': forms.TextInput(attrs={'class': 'form-control'}),
            'sku_number': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Optional'}),
            'cases_per_pallet': forms.NumberInput(attrs={'class': 'form-control', 'placeholder': 'Optional'}),
            'description': forms.Textarea(attrs={'class': 'form-control', 'rows': 3}),
            'is_active': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        }
        labels = {
            'item_code': 'Item Code',
            'sku_number': 'SKU Number',
            'cases_per_pallet': 'Cases per Pallet',
            'is_active': 'Active',
        }

    def __init__(self, *args, brand=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.brand = brand
        self.fields['name'].required = True
        self.fields['item_code'].required = True
        self.fields['sku_number'].required = False
        self.fields['cases_per_pallet'].required = False
        self.fields['description'].required = False
        self.fields['is_active'].initial = True

    def clean_item_code(self):
        item_code = self.cleaned_data.get('item_code', '').strip()
        if not item_code:
            raise forms.ValidationError('Item code is required.')
        qs = Item.objects.filter(brand=self.brand, item_code=item_code)
        if self.instance and self.instance.pk:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise forms.ValidationError(
                'An item with this code already exists for this brand.'
            )
        return item_code

    def save(self, commit=True):
        item = super().save(commit=False)
        if self.brand and not item.pk:
            item.brand = self.brand
        if commit:
            item.save()
        return item
