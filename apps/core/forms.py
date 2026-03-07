"""
Forms for Phase 1: login, user management, profile, and password changes.
"""
from django import forms
from django.contrib.auth import get_user_model

from apps.core.rbac import Role

User = get_user_model()

# ---------------------------------------------------------------------------
# Role choices list — used by the user list filter dropdown.
# User create/edit forms pull roles directly from the Role model.
# ---------------------------------------------------------------------------

ROLE_CHOICES = [
    ('saas_admin',          'SaaS Admin'),
    ('supplier_admin',      'Supplier Admin'),
    ('sales_manager',       'Sales Manager'),
    ('territory_manager',   'Territory Manager'),
    ('ambassador_manager',  'Ambassador Manager'),
    ('ambassador',          'Ambassador'),
    ('distributor_contact', 'Distributor Contact'),
    ('payroll_reviewer',    'Payroll Reviewer'),
]


# ---------------------------------------------------------------------------
# User create form
# ---------------------------------------------------------------------------

class UserCreateForm(forms.ModelForm):
    password = forms.CharField(
        widget=forms.PasswordInput(attrs={'class': 'form-control', 'autocomplete': 'new-password'}),
        label='Password',
        min_length=8,
        help_text='Minimum 8 characters.',
    )
    password_confirm = forms.CharField(
        widget=forms.PasswordInput(attrs={'class': 'form-control', 'autocomplete': 'new-password'}),
        label='Confirm Password',
    )
    roles = forms.ModelMultipleChoiceField(
        queryset=Role.objects.all().order_by('name'),
        widget=forms.CheckboxSelectMultiple(),
        label='Roles',
        required=True,
    )

    class Meta:
        model = User
        fields = ['first_name', 'last_name', 'email', 'phone', 'username', 'is_active']
        widgets = {
            'first_name': forms.TextInput(attrs={'class': 'form-control'}),
            'last_name': forms.TextInput(attrs={'class': 'form-control'}),
            'email': forms.EmailInput(attrs={'class': 'form-control'}),
            'phone': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Optional'}),
            'username': forms.TextInput(attrs={'class': 'form-control'}),
            'is_active': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        }

    def __init__(self, *args, creator=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.creator = creator

        if creator and creator.is_saas_admin:
            from apps.core.models import Company
            self.fields['company'] = forms.ModelChoiceField(
                queryset=Company.objects.filter(is_active=True).order_by('name'),
                widget=forms.Select(attrs={'class': 'form-select'}),
                label='Company',
                required=True,
            )

        for f in ['first_name', 'last_name', 'email', 'username']:
            self.fields[f].required = True
        self.fields['phone'].required = False
        self.fields['is_active'].initial = True

    def clean_username(self):
        username = self.cleaned_data.get('username', '').lower()
        if User.objects.filter(username=username).exists():
            raise forms.ValidationError('This username is already taken.')
        return username

    def clean(self):
        cleaned = super().clean()
        p1 = cleaned.get('password')
        p2 = cleaned.get('password_confirm')
        if p1 and p2 and p1 != p2:
            self.add_error('password_confirm', 'Passwords do not match.')
        return cleaned

    def save(self, commit=True):
        user = super().save(commit=False)
        user.set_password(self.cleaned_data['password'])
        if self.creator:
            if self.creator.is_saas_admin:
                user.company = self.cleaned_data.get('company')
            else:
                user.company = self.creator.company
            user.created_by = self.creator
        if commit:
            user.save()
            roles = self.cleaned_data.get('roles')
            if roles:
                user.roles.set(roles)
        return user


# ---------------------------------------------------------------------------
# User edit form (no password fields)
# ---------------------------------------------------------------------------

class UserEditForm(forms.ModelForm):
    roles = forms.ModelMultipleChoiceField(
        queryset=Role.objects.all().order_by('name'),
        widget=forms.CheckboxSelectMultiple(),
        label='Roles',
        required=True,
    )

    class Meta:
        model = User
        fields = ['first_name', 'last_name', 'email', 'phone', 'username', 'is_active']
        widgets = {
            'first_name': forms.TextInput(attrs={'class': 'form-control'}),
            'last_name': forms.TextInput(attrs={'class': 'form-control'}),
            'email': forms.EmailInput(attrs={'class': 'form-control'}),
            'phone': forms.TextInput(attrs={'class': 'form-control'}),
            'username': forms.TextInput(attrs={'class': 'form-control'}),
            'is_active': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        }

    def __init__(self, *args, editor=None, **kwargs):
        super().__init__(*args, **kwargs)

        if self.instance and self.instance.pk:
            self.fields['roles'].initial = self.instance.roles.all()

        for f in ['first_name', 'last_name', 'email', 'username']:
            self.fields[f].required = True
        self.fields['phone'].required = False

    def clean_username(self):
        username = self.cleaned_data.get('username', '').lower()
        qs = User.objects.filter(username=username)
        if self.instance and self.instance.pk:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise forms.ValidationError('This username is already taken.')
        return username

    def save(self, commit=True):
        user = super().save(commit=commit)
        if commit:
            roles = self.cleaned_data.get('roles')
            if roles is not None:
                user.roles.set(roles)
        return user


# ---------------------------------------------------------------------------
# Password change (own account)
# ---------------------------------------------------------------------------

class PasswordChangeForm(forms.Form):
    current_password = forms.CharField(
        widget=forms.PasswordInput(attrs={'class': 'form-control'}),
        label='Current Password',
    )
    new_password = forms.CharField(
        widget=forms.PasswordInput(attrs={'class': 'form-control', 'autocomplete': 'new-password'}),
        label='New Password',
        min_length=8,
        help_text='Minimum 8 characters.',
    )
    new_password_confirm = forms.CharField(
        widget=forms.PasswordInput(attrs={'class': 'form-control', 'autocomplete': 'new-password'}),
        label='Confirm New Password',
    )

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user

    def clean_current_password(self):
        current = self.cleaned_data.get('current_password')
        if self.user and not self.user.check_password(current):
            raise forms.ValidationError('Your current password is incorrect.')
        return current

    def clean(self):
        cleaned = super().clean()
        p1 = cleaned.get('new_password')
        p2 = cleaned.get('new_password_confirm')
        if p1 and p2 and p1 != p2:
            self.add_error('new_password_confirm', 'New passwords do not match.')
        return cleaned


# ---------------------------------------------------------------------------
# Admin password reset (for another user)
# ---------------------------------------------------------------------------

class AdminPasswordResetForm(forms.Form):
    new_password = forms.CharField(
        widget=forms.PasswordInput(attrs={'class': 'form-control', 'autocomplete': 'new-password'}),
        label='New Password',
        min_length=8,
        help_text='Minimum 8 characters.',
    )
    new_password_confirm = forms.CharField(
        widget=forms.PasswordInput(attrs={'class': 'form-control', 'autocomplete': 'new-password'}),
        label='Confirm New Password',
    )

    def clean(self):
        cleaned = super().clean()
        p1 = cleaned.get('new_password')
        p2 = cleaned.get('new_password_confirm')
        if p1 and p2 and p1 != p2:
            self.add_error('new_password_confirm', 'New passwords do not match.')
        return cleaned


# ---------------------------------------------------------------------------
# Profile edit (own account — no username/role/company changes)
# ---------------------------------------------------------------------------

class ProfileEditForm(forms.ModelForm):
    class Meta:
        model = User
        fields = ['first_name', 'last_name', 'email', 'phone']
        widgets = {
            'first_name': forms.TextInput(attrs={'class': 'form-control'}),
            'last_name': forms.TextInput(attrs={'class': 'form-control'}),
            'email': forms.EmailInput(attrs={'class': 'form-control'}),
            'phone': forms.TextInput(attrs={'class': 'form-control'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for f in ['first_name', 'last_name', 'email']:
            self.fields[f].required = True
        self.fields['phone'].required = False
