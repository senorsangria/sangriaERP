"""
Events views: list, detail, create, edit, status transitions, AJAX endpoints.

Access rules:
  - Event list/detail: all roles except Distributor Contact
  - Create/edit:       Supplier Admin, Sales Manager, Territory Manager,
                       Ambassador Manager
  - Status actions:    Event Manager, Sales Manager, Supplier Admin
  - Ambassador:        sees only their assigned events (no Drafts)
"""
from datetime import date as date_type

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render

from apps.accounts.models import Account
from apps.accounts.utils import get_accounts_for_user, get_users_covering_account
from apps.core.models import User
from apps.distribution.models import Distributor

from .forms import EventForm
from .models import Event


# ---------------------------------------------------------------------------
# Role constants
# ---------------------------------------------------------------------------

_VIEWER_ROLES = {
    User.Role.SUPPLIER_ADMIN,
    User.Role.SALES_MANAGER,
    User.Role.TERRITORY_MANAGER,
    User.Role.AMBASSADOR_MANAGER,
    User.Role.AMBASSADOR,
}

_CREATOR_ROLES = {
    User.Role.SUPPLIER_ADMIN,
    User.Role.SALES_MANAGER,
    User.Role.TERRITORY_MANAGER,
    User.Role.AMBASSADOR_MANAGER,
}

_MANAGER_ROLES = {
    User.Role.SUPPLIER_ADMIN,
    User.Role.SALES_MANAGER,
    User.Role.TERRITORY_MANAGER,
    User.Role.AMBASSADOR_MANAGER,
}

_ACTION_ROLES = {
    User.Role.SUPPLIER_ADMIN,
    User.Role.SALES_MANAGER,
    User.Role.AMBASSADOR_MANAGER,
    User.Role.TERRITORY_MANAGER,
}


# ---------------------------------------------------------------------------
# Visibility helpers
# ---------------------------------------------------------------------------

def _get_visible_events(user):
    """
    Return a queryset of events visible to the given user.

    Visibility rules by role:
      Supplier Admin      — all company events
      Sales Manager       — all events at company accounts + admin events by
                            TM / AM / Ambassador below them
      Territory Manager   — events at accounts in their coverage area +
                            admin events they created
      Ambassador Manager  — events they created or are event_manager on
      Ambassador          — events they are assigned to (no Drafts)
    """
    company = user.company
    if not company:
        return Event.objects.none()

    role = user.role
    qs = Event.objects.filter(company=company).select_related(
        'account', 'ambassador', 'event_manager', 'created_by',
        'account__distributor',
    )

    if role == User.Role.SUPPLIER_ADMIN:
        return qs

    if role == User.Role.SALES_MANAGER:
        below_roles = [
            User.Role.TERRITORY_MANAGER,
            User.Role.AMBASSADOR_MANAGER,
            User.Role.AMBASSADOR,
        ]
        return qs.filter(
            Q(account__isnull=False)
            | Q(event_type=Event.EventType.ADMIN, created_by__role__in=below_roles)
        )

    if role == User.Role.TERRITORY_MANAGER:
        visible_accounts = get_accounts_for_user(user)
        return qs.filter(
            Q(account__in=visible_accounts)
            | Q(event_type=Event.EventType.ADMIN, created_by=user)
        )

    if role == User.Role.AMBASSADOR_MANAGER:
        return qs.filter(
            Q(created_by=user) | Q(event_manager=user)
        )

    if role == User.Role.AMBASSADOR:
        return qs.filter(
            ambassador=user
        ).exclude(status=Event.Status.DRAFT)

    return Event.objects.none()


def _can_view_drafts(user):
    """True if this user should see Draft events."""
    return user.role in (
        User.Role.SUPPLIER_ADMIN,
        User.Role.SALES_MANAGER,
        User.Role.TERRITORY_MANAGER,
        User.Role.AMBASSADOR_MANAGER,
    )


def _sort_events(events_qs):
    """
    Group and sort events per the required sort order:
      1. Revision Requested (date asc)
      2. Draft (no-date first, then date asc)
      3. Recap Submitted (date asc)
      4. Scheduled (date asc)
      5. Complete (date desc)

    Returns a list of (group_label, events_list) tuples.
    """
    MAX_DATE = date_type.max

    def date_asc_key(e):
        return (e.date is None, e.date or MAX_DATE)

    def date_asc_no_date_first_key(e):
        # None dates come first, then ascending
        return (e.date is not None, e.date or MAX_DATE)

    def date_desc_key(e):
        # Negate for descending; put no-dates at end
        if e.date is None:
            return (1, date_type.min)
        return (0, date_type(9999 - e.date.year, 12 - e.date.month, 28 - min(e.date.day, 28)))

    # Materialize and split by status
    events = list(events_qs)

    revision  = sorted([e for e in events if e.status == Event.Status.REVISION_REQUESTED], key=date_asc_key)
    drafts    = sorted([e for e in events if e.status == Event.Status.DRAFT],              key=date_asc_no_date_first_key)
    recap     = sorted([e for e in events if e.status == Event.Status.RECAP_SUBMITTED],    key=date_asc_key)
    scheduled = sorted([e for e in events if e.status == Event.Status.SCHEDULED],         key=date_asc_key)
    complete  = sorted([e for e in events if e.status == Event.Status.COMPLETE],           key=date_desc_key)

    groups = []
    if revision:
        groups.append(('Revision Requested', 'revision_requested', revision))
    if drafts:
        groups.append(('Drafts', 'draft', drafts))
    if recap:
        groups.append(('Recap Submitted', 'recap_submitted', recap))
    if scheduled:
        groups.append(('Scheduled', 'scheduled', scheduled))
    if complete:
        groups.append(('Complete', 'complete', complete))

    return groups


# ---------------------------------------------------------------------------
# Event List
# ---------------------------------------------------------------------------

@login_required
def event_list(request):
    if request.user.role == User.Role.DISTRIBUTOR_CONTACT:
        return render(request, '403.html', status=403)
    if request.user.role not in _VIEWER_ROLES:
        return render(request, '403.html', status=403)

    company = request.user.company

    # ---- Restore / save filters in session ----
    SESSION_KEY = 'event_list_filters'

    if request.GET.get('clear_filters'):
        request.session.pop(SESSION_KEY, None)
        return redirect('event_list')

    if request.method == 'GET' and any(k in request.GET for k in (
        'status', 'year', 'month', 'event_type', 'creator',
        'distributor', 'account_name', 'city',
    )):
        # User submitted filters — save to session
        filters = {
            'status':       request.GET.getlist('status'),
            'year':         request.GET.get('year', ''),
            'month':        request.GET.get('month', ''),
            'event_type':   request.GET.get('event_type', ''),
            'creator':      request.GET.get('creator', ''),
            'distributor':  request.GET.get('distributor', ''),
            'account_name': request.GET.get('account_name', ''),
            'city':         request.GET.get('city', ''),
        }
        request.session[SESSION_KEY] = filters
    else:
        # Restore from session
        filters = request.session.get(SESSION_KEY, {
            'status': [], 'year': '', 'month': '', 'event_type': '',
            'creator': '', 'distributor': '', 'account_name': '', 'city': '',
        })

    # ---- Base queryset ----
    qs = _get_visible_events(request.user)

    # Hide drafts from ambassadors (already handled in _get_visible_events,
    # but belt-and-suspenders here)
    if not _can_view_drafts(request.user):
        qs = qs.exclude(status=Event.Status.DRAFT)

    # ---- Apply filters ----
    if filters.get('status'):
        qs = qs.filter(status__in=filters['status'])

    if filters.get('year'):
        try:
            qs = qs.filter(date__year=int(filters['year']))
        except (ValueError, TypeError):
            pass

    if filters.get('month'):
        try:
            qs = qs.filter(date__month=int(filters['month']))
        except (ValueError, TypeError):
            pass

    if filters.get('event_type'):
        qs = qs.filter(event_type=filters['event_type'])

    if filters.get('creator'):
        try:
            qs = qs.filter(created_by_id=int(filters['creator']))
        except (ValueError, TypeError):
            pass

    if filters.get('distributor'):
        try:
            qs = qs.filter(account__distributor_id=int(filters['distributor']))
        except (ValueError, TypeError):
            pass

    if filters.get('account_name'):
        qs = qs.filter(account__name__icontains=filters['account_name'])

    if filters.get('city'):
        qs = qs.filter(account__city__icontains=filters['city'])

    # ---- Build filter sidebar data ----
    # Years from event dates (from full visible set, not filtered)
    all_events = _get_visible_events(request.user)
    if not _can_view_drafts(request.user):
        all_events = all_events.exclude(status=Event.Status.DRAFT)

    distinct_years = (
        all_events.exclude(date__isnull=True)
        .dates('date', 'year', order='DESC')
    )
    years = [d.year for d in distinct_years]

    # Creators from visible events
    creator_pks = (
        all_events.exclude(created_by__isnull=True)
        .values_list('created_by_id', flat=True)
        .distinct()
    )
    creators = User.objects.filter(pk__in=creator_pks).order_by('last_name', 'first_name')

    distributors = Distributor.objects.filter(company=company, is_active=True).order_by('name')

    # ---- Group and sort ----
    event_groups = _sort_events(qs)

    filters_active = bool(
        filters.get('status') or filters.get('year') or filters.get('month')
        or filters.get('event_type') or filters.get('creator')
        or filters.get('distributor') or filters.get('account_name')
        or filters.get('city')
    )

    return render(request, 'events/event_list.html', {
        'event_groups':     event_groups,
        'filters':          filters,
        'filters_active':   filters_active,
        'years':            years,
        'creators':         creators,
        'distributors':     distributors,
        'event_type_choices': Event.EventType.choices,
        'status_choices':     Event.Status.choices,
        'months': [
            (1,'January'),(2,'February'),(3,'March'),(4,'April'),
            (5,'May'),(6,'June'),(7,'July'),(8,'August'),
            (9,'September'),(10,'October'),(11,'November'),(12,'December'),
        ],
    })


# ---------------------------------------------------------------------------
# Event Detail
# ---------------------------------------------------------------------------

@login_required
def event_detail(request, pk):
    if request.user.role == User.Role.DISTRIBUTOR_CONTACT:
        return render(request, '403.html', status=403)

    company = request.user.company
    visible = _get_visible_events(request.user)
    event = get_object_or_404(visible, pk=pk, company=company)

    can_edit = request.user.role in _CREATOR_ROLES
    can_action = request.user.role in _ACTION_ROLES

    return render(request, 'events/event_detail.html', {
        'event':      event,
        'can_edit':   can_edit,
        'can_action': can_action,
    })


# ---------------------------------------------------------------------------
# Event Create
# ---------------------------------------------------------------------------

@login_required
def event_create(request):
    if request.user.role not in _CREATOR_ROLES:
        return render(request, '403.html', status=403)

    company = request.user.company

    if request.method == 'POST':
        form = EventForm(request.POST, company=company, user=request.user)
        if form.is_valid():
            event = form.save(commit=False)
            event.company = company
            event.created_by = request.user
            event.status = Event.Status.DRAFT
            # Default event_manager to creator if not set
            if not event.event_manager_id:
                if request.user.role in (User.Role.TERRITORY_MANAGER, User.Role.AMBASSADOR_MANAGER):
                    event.event_manager = request.user
            event.duration_hours = int(form.cleaned_data.get('duration_hours', 0))
            event.save()
            form.save_m2m()
            messages.success(request, 'Event created successfully.')
            return redirect('event_detail', pk=event.pk)
    else:
        form = EventForm(company=company, user=request.user)

    return render(request, 'events/event_form.html', {
        'form':       form,
        'form_title': 'Create Event',
        'is_create':  True,
    })


# ---------------------------------------------------------------------------
# Event Edit
# ---------------------------------------------------------------------------

@login_required
def event_edit(request, pk):
    if request.user.role not in _CREATOR_ROLES:
        return render(request, '403.html', status=403)

    company = request.user.company
    visible = _get_visible_events(request.user)
    event = get_object_or_404(visible, pk=pk, company=company)

    if request.method == 'POST':
        form = EventForm(request.POST, instance=event, company=company, user=request.user)
        if form.is_valid():
            event = form.save(commit=False)
            event.duration_hours = int(form.cleaned_data.get('duration_hours', 0))
            event.save()
            form.save_m2m()
            messages.success(request, 'Event updated successfully.')
            return redirect('event_detail', pk=event.pk)
    else:
        form = EventForm(instance=event, company=company, user=request.user)
        form.fields['duration_hours'].initial = event.duration_hours

    return render(request, 'events/event_form.html', {
        'form':       form,
        'event':      event,
        'form_title': f'Edit Event',
        'is_create':  False,
    })


# ---------------------------------------------------------------------------
# Status transitions
# ---------------------------------------------------------------------------

@login_required
def event_release(request, pk):
    """POST: Draft → Scheduled"""
    if request.user.role not in _ACTION_ROLES:
        return render(request, '403.html', status=403)
    if request.method != 'POST':
        return redirect('event_detail', pk=pk)

    company = request.user.company
    visible = _get_visible_events(request.user)
    event = get_object_or_404(visible, pk=pk, company=company)

    if event.status != Event.Status.DRAFT:
        messages.error(request, 'Event is not in Draft status.')
        return redirect('event_detail', pk=pk)

    # Validate: must have date, ambassador, and account (except Admin)
    errors = []
    if not event.date:
        errors.append('Event must have a date before it can be released.')
    if not event.ambassador:
        errors.append('Event must have an assigned ambassador before it can be released.')
    if event.event_type != Event.EventType.ADMIN and not event.account:
        errors.append('Tasting and Festival events must have an account assigned.')

    if errors:
        for err in errors:
            messages.error(request, err)
        return redirect('event_detail', pk=pk)

    event.status = Event.Status.SCHEDULED
    event.save(update_fields=['status', 'updated_at'])
    messages.success(request, 'Event released and is now Scheduled.')
    return redirect('event_detail', pk=pk)


@login_required
def event_request_revision(request, pk):
    """POST: Recap Submitted → Revision Requested"""
    if request.user.role not in _ACTION_ROLES:
        return render(request, '403.html', status=403)
    if request.method != 'POST':
        return redirect('event_detail', pk=pk)

    company = request.user.company
    visible = _get_visible_events(request.user)
    event = get_object_or_404(visible, pk=pk, company=company)

    if event.status != Event.Status.RECAP_SUBMITTED:
        messages.error(request, 'Event is not in Recap Submitted status.')
        return redirect('event_detail', pk=pk)

    revision_note = request.POST.get('revision_note', '').strip()
    if not revision_note:
        messages.error(request, 'A revision note explaining what needs to be fixed is required.')
        return redirect('event_detail', pk=pk)

    event.status = Event.Status.REVISION_REQUESTED
    event.revision_note = revision_note
    event.save(update_fields=['status', 'revision_note', 'updated_at'])
    messages.success(request, 'Revision requested. The ambassador has been notified.')
    return redirect('event_detail', pk=pk)


@login_required
def event_approve(request, pk):
    """POST: Recap Submitted → Complete"""
    if request.user.role not in _ACTION_ROLES:
        return render(request, '403.html', status=403)
    if request.method != 'POST':
        return redirect('event_detail', pk=pk)

    company = request.user.company
    visible = _get_visible_events(request.user)
    event = get_object_or_404(visible, pk=pk, company=company)

    if event.status != Event.Status.RECAP_SUBMITTED:
        messages.error(request, 'Event is not in Recap Submitted status.')
        return redirect('event_detail', pk=pk)

    event.status = Event.Status.COMPLETE
    event.save(update_fields=['status', 'updated_at'])
    messages.success(request, 'Event approved and marked as Complete.')
    return redirect('event_detail', pk=pk)


# ---------------------------------------------------------------------------
# AJAX endpoints
# ---------------------------------------------------------------------------

def _ambassador_list_response(users):
    return [{'id': u.pk, 'name': u.get_full_name() or u.username} for u in users]


@login_required
def ajax_ambassadors(request):
    """
    GET /events/ajax/ambassadors/?account_id=X
    Returns ambassadors and ambassador managers covering the given account.
    For Admin events (no account_id), returns all company ambassadors/AMs.
    """
    if not request.user.is_authenticated:
        return JsonResponse({'error': 'Authentication required.'}, status=403)

    company = request.user.company
    account_id = request.GET.get('account_id', '').strip()

    roles = [User.Role.AMBASSADOR, User.Role.AMBASSADOR_MANAGER]

    if account_id:
        try:
            account = Account.active_accounts.get(pk=account_id, company=company)
        except Account.DoesNotExist:
            return JsonResponse({'ambassadors': []})
        users = get_users_covering_account(account, roles)
    else:
        users = User.objects.filter(
            company=company, role__in=roles, is_active=True
        ).order_by('last_name', 'first_name')

    return JsonResponse({'ambassadors': _ambassador_list_response(users)})


@login_required
def ajax_event_managers(request):
    """
    GET /events/ajax/event_managers/?account_id=X
    Returns TMs and AMs covering the given account.
    For Admin events (no account_id), returns all company TMs and AMs.
    """
    if not request.user.is_authenticated:
        return JsonResponse({'error': 'Authentication required.'}, status=403)

    company = request.user.company
    account_id = request.GET.get('account_id', '').strip()

    roles = [User.Role.TERRITORY_MANAGER, User.Role.AMBASSADOR_MANAGER]

    if account_id:
        try:
            account = Account.active_accounts.get(pk=account_id, company=company)
        except Account.DoesNotExist:
            return JsonResponse({'event_managers': []})
        users = get_users_covering_account(account, roles)
    else:
        users = User.objects.filter(
            company=company, role__in=roles, is_active=True
        ).order_by('last_name', 'first_name')

    return JsonResponse({'event_managers': _ambassador_list_response(users)})
