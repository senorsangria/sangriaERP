"""
Core views: authentication, dashboard, user management, profile.
"""
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth import login, logout, update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import AuthenticationForm
from django.contrib import messages
from django.db.models import Q

from .models import User
from .forms import (
    UserCreateForm,
    UserEditForm,
    PasswordChangeForm,
    AdminPasswordResetForm,
    ProfileEditForm,
    CREATABLE_ROLES,
    ROLE_CHOICES,
    _allowed_codenames_for,
)
from apps.distribution.models import Distributor
from apps.accounts.constants import US_STATES, US_STATES_DICT
from apps.accounts.views import _build_enhanced_coverage_areas


# ---------------------------------------------------------------------------
# Permission helpers
# ---------------------------------------------------------------------------

def _can_manage_user(manager, target):
    """Return True if manager has permission to edit/deactivate target."""
    if manager.pk == target.pk:
        return True  # anyone can manage themselves
    if manager.is_saas_admin:
        return True
    if manager.is_supplier_admin:
        return target.company_id == manager.company_id
    if manager.is_sales_manager:
        return (
            target.company_id == manager.company_id
            and not (target.has_role('saas_admin') or target.has_role('supplier_admin'))
        )
    if manager.is_territory_manager:
        return (
            target.company_id == manager.company_id
            and (target.has_role('ambassador_manager') or target.has_role('ambassador'))
        )
    if manager.is_ambassador_manager:
        return (
            target.has_role('ambassador')
            and target.created_by_id == manager.pk
        )
    return False


def _get_visible_users(requesting_user):
    """Return queryset of users this user can see and manage."""
    u = requesting_user
    if u.is_saas_admin:
        return User.objects.select_related('company')

    base_qs = User.objects.filter(company=u.company).select_related('company')

    if u.is_supplier_admin:
        return base_qs

    if u.is_sales_manager:
        return base_qs.filter(roles__codename__in=[
            'territory_manager', 'ambassador_manager', 'ambassador', 'distributor_contact',
        ]).distinct()

    if u.is_territory_manager:
        return base_qs.filter(roles__codename__in=[
            'ambassador_manager', 'ambassador',
        ]).distinct()

    if u.is_ambassador_manager:
        return base_qs.filter(
            roles__codename='ambassador', created_by=u
        ).distinct()

    return User.objects.none()


# ---------------------------------------------------------------------------
# Authentication views
# ---------------------------------------------------------------------------

def login_view(request):
    if request.user.is_authenticated:
        return redirect('dashboard')

    error = None
    if request.method == 'POST':
        data = request.POST.copy()
        data['username'] = data.get('username', '').lower()
        form = AuthenticationForm(request, data=data)
        if form.is_valid():
            user = form.get_user()
            if not user.is_active:
                error = 'Your account has been deactivated. Please contact your administrator.'
            else:
                login(request, user)
                next_url = request.GET.get('next', '')
                if next_url:
                    return redirect(next_url)
                if user.has_permission('can_redirect_to_events_on_login'):
                    return redirect('event_list')
                return redirect('dashboard')
        else:
            error = 'Invalid username or password. Please try again.'
    else:
        form = AuthenticationForm(request)

    return render(request, 'registration/login.html', {'form': form, 'error': error})


def logout_view(request):
    logout(request)
    return redirect('login')


def password_reset_stub(request):
    """Placeholder: email-based password reset is not yet configured."""
    return render(request, 'registration/password_reset_stub.html')


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------

PHASE_ROADMAP = [
    {'label': 'Foundation — Data models & admin', 'status': 'done'},
    {'label': 'Phase 1 — Login & User Management', 'status': 'active'},
    {'label': 'Phase 2 — Distributors & VIP Import', 'status': 'pending'},
    {'label': 'Phase 3 — Sales Views', 'status': 'pending'},
    {'label': 'Phase 4 — Saving Sales Views', 'status': 'pending'},
    {'label': 'Phase 5 — CRM / Accounts', 'status': 'pending'},
    {'label': 'Phase 6 — Sales Reports', 'status': 'pending'},
    {'label': 'Phase 7 — Sales Orders', 'status': 'pending'},
    {'label': 'Phase 8 — Production Ordering', 'status': 'pending'},
    {'label': 'Phase 9 — Projection Planning', 'status': 'pending'},
    {'label': 'Phase 10 — Event Management', 'status': 'pending'},
]


@login_required
def dashboard(request):
    if request.user.has_permission('can_redirect_to_events_on_login'):
        return redirect('event_list')
    return render(request, 'core/dashboard.html', {'phases': PHASE_ROADMAP})


# ---------------------------------------------------------------------------
# User management
# ---------------------------------------------------------------------------

@login_required
def user_list(request):
    if not request.user.has_permission('can_manage_users'):
        return render(request, '403.html', status=403)

    users = _get_visible_users(request.user)

    search = request.GET.get('q', '').strip()
    role_filter = request.GET.get('role', '').strip()

    if search:
        users = users.filter(
            Q(first_name__icontains=search)
            | Q(last_name__icontains=search)
            | Q(email__icontains=search)
            | Q(username__icontains=search)
        )
    if role_filter:
        users = users.filter(roles__codename=role_filter).distinct()

    users = users.order_by('last_name', 'first_name')
    can_create = request.user.has_permission('can_create_users')

    return render(request, 'core/user_list.html', {
        'users': users,
        'search': search,
        'role_filter': role_filter,
        'role_choices': ROLE_CHOICES,
        'can_create': can_create,
    })


@login_required
def user_create(request):
    if not request.user.has_permission('can_manage_users'):
        return render(request, '403.html', status=403)
    if not request.user.has_permission('can_create_users'):
        return render(request, '403.html', status=403)

    if request.method == 'POST':
        form = UserCreateForm(request.POST, creator=request.user)
        if form.is_valid():
            user = form.save()
            name = user.get_full_name() or user.username
            messages.success(request, f'{name} has been created successfully.')
            return redirect('user_list')
    else:
        form = UserCreateForm(creator=request.user)

    return render(request, 'core/user_create.html', {'form': form})


@login_required
def user_edit(request, pk):
    if not request.user.has_permission('can_manage_users'):
        return render(request, '403.html', status=403)

    target = get_object_or_404(User, pk=pk)

    if not _can_manage_user(request.user, target):
        return render(request, '403.html', status=403)

    if request.method == 'POST':
        form = UserEditForm(request.POST, instance=target, editor=request.user)
        if form.is_valid():
            form.save()
            name = target.get_full_name() or target.username
            messages.success(request, f'{name} has been updated.')
            return redirect('user_edit', pk=pk)
    else:
        form = UserEditForm(instance=target, editor=request.user)

    # Coverage Areas tab — visible to Supplier Admins only
    show_coverage_tab = request.user.is_supplier_admin
    enhanced_coverage_areas = []
    distributors = []

    if show_coverage_tab:
        enhanced_coverage_areas = _build_enhanced_coverage_areas(
            target, request.user.company
        )
        distributors = list(
            Distributor.objects.filter(
                company=request.user.company, is_active=True
            ).order_by('name')
        )

    return render(request, 'core/user_edit.html', {
        'form': form,
        'target': target,
        'show_coverage_tab': show_coverage_tab,
        'enhanced_coverage_areas': enhanced_coverage_areas,
        'distributors': distributors,
        'us_states': US_STATES,
    })


@login_required
def user_deactivate(request, pk):
    if not request.user.has_permission('can_manage_users'):
        return render(request, '403.html', status=403)

    target = get_object_or_404(User, pk=pk)

    if not _can_manage_user(request.user, target):
        return render(request, '403.html', status=403)

    if request.user.pk == target.pk:
        messages.error(request, 'You cannot deactivate your own account.')
        return redirect('user_list')

    if request.method == 'POST':
        target.is_active = not target.is_active
        target.save(update_fields=['is_active'])
        action = 'activated' if target.is_active else 'deactivated'
        name = target.get_full_name() or target.username
        messages.success(request, f'{name} has been {action}.')
        return redirect('user_list')

    return render(request, 'core/user_deactivate_confirm.html', {'target': target})


@login_required
def user_password_reset(request, pk):
    if not request.user.has_permission('can_reset_user_password'):
        return render(request, '403.html', status=403)

    target = get_object_or_404(User, pk=pk)

    if not _can_manage_user(request.user, target):
        return render(request, '403.html', status=403)

    if request.method == 'POST':
        form = AdminPasswordResetForm(request.POST)
        if form.is_valid():
            target.set_password(form.cleaned_data['new_password'])
            target.save(update_fields=['password'])
            name = target.get_full_name() or target.username
            messages.success(request, f'Password for {name} has been reset.')
            return redirect('user_list')
    else:
        form = AdminPasswordResetForm()

    return render(request, 'core/user_password_reset.html', {'form': form, 'target': target})


# ---------------------------------------------------------------------------
# My Profile
# ---------------------------------------------------------------------------

@login_required
def profile(request):
    return render(request, 'core/profile.html')


@login_required
def profile_edit(request):
    if request.method == 'POST':
        form = ProfileEditForm(request.POST, instance=request.user)
        if form.is_valid():
            form.save()
            messages.success(request, 'Your profile has been updated.')
            return redirect('profile')
    else:
        form = ProfileEditForm(instance=request.user)

    return render(request, 'core/profile_edit.html', {'form': form})


@login_required
def password_change(request):
    if request.method == 'POST':
        form = PasswordChangeForm(request.POST, user=request.user)
        if form.is_valid():
            request.user.set_password(form.cleaned_data['new_password'])
            request.user.save(update_fields=['password'])
            update_session_auth_hash(request, request.user)
            messages.success(request, 'Your password has been changed successfully.')
            return redirect('profile')
    else:
        form = PasswordChangeForm(user=request.user)

    return render(request, 'core/password_change.html', {'form': form})


# ---------------------------------------------------------------------------
# Access denied (friendly 403)
# ---------------------------------------------------------------------------

def access_denied(request):
    return render(request, '403.html', status=403)
