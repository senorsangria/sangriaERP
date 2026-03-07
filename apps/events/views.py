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
from itertools import groupby

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render

from apps.accounts.models import Account
from apps.accounts.utils import get_accounts_for_user, get_users_covering_account
from apps.catalog.models import Brand, Item
from apps.core.models import User
from apps.distribution.models import Distributor

from .forms import EventForm
from .models import Event, EventItemRecap, EventPhoto, Expense
from .storage import delete_event_photo, save_event_photo


# ---------------------------------------------------------------------------
# Role constants
# ---------------------------------------------------------------------------

_VIEWER_ROLES = {
    'supplier_admin', 'sales_manager', 'territory_manager',
    'ambassador_manager', 'ambassador',
}

_CREATOR_ROLES = {
    'supplier_admin', 'sales_manager', 'territory_manager', 'ambassador_manager',
}

_MANAGER_ROLES = {
    'supplier_admin', 'sales_manager', 'territory_manager', 'ambassador_manager',
}

_ACTION_ROLES = {
    'supplier_admin', 'sales_manager', 'ambassador_manager', 'territory_manager',
}

# Statuses where the recap form is editable
_RECAP_ACTIVE_STATUSES = {
    Event.Status.SCHEDULED,
    Event.Status.RECAP_IN_PROGRESS,
    Event.Status.REVISION_REQUESTED,
}


def _can_recap(user, event):
    """
    Return True if the user can access the recap form for this event.

    Eligible users:
      - The assigned ambassador
      - The assigned event manager
      - Any user whose coverage areas include the event account
        (Supplier Admin always qualifies since they see all accounts)
    """
    if event.event_type == Event.EventType.ADMIN:
        return False
    if event.account_id is None:
        return False
    if event.ambassador_id and event.ambassador_id == user.pk:
        return True
    if event.event_manager_id and event.event_manager_id == user.pk:
        return True
    return get_accounts_for_user(user).filter(pk=event.account_id).exists()


# ---------------------------------------------------------------------------
# Item grouping helper
# ---------------------------------------------------------------------------

def _get_items_by_brand(company):
    """
    Return a list of (brand_name, items_list) tuples for all active items
    in the given company, ordered by brand name then item name.
    """
    if not company:
        return []
    brand_pks = Brand.objects.filter(
        company=company, is_active=True
    ).values_list('pk', flat=True)
    items_qs = (
        Item.objects.filter(brand__in=brand_pks, is_active=True)
        .select_related('brand')
        .order_by('brand__name', 'sort_order', 'name')
    )
    result = []
    for brand_name, brand_items in groupby(items_qs, key=lambda x: x.brand.name):
        result.append((brand_name, list(brand_items)))
    return result


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

    qs = Event.objects.filter(company=company).select_related(
        'account', 'ambassador', 'event_manager', 'created_by',
        'account__distributor',
    )

    if user.has_role('supplier_admin'):
        return qs

    if user.has_role('sales_manager'):
        # Sales Managers see all events with accounts, plus all admin events
        # (admin events have no account scoping per the product spec)
        return qs.filter(
            Q(account__isnull=False)
            | Q(event_type=Event.EventType.ADMIN)
        )

    if user.has_role('territory_manager'):
        visible_accounts = get_accounts_for_user(user)
        return qs.filter(
            Q(account__in=visible_accounts)
            | Q(event_type=Event.EventType.ADMIN, created_by=user)
        )

    if user.has_role('ambassador_manager'):
        return qs.filter(
            Q(created_by=user) | Q(event_manager=user)
        )

    if user.has_role('ambassador'):
        return qs.filter(
            ambassador=user
        ).exclude(status=Event.Status.DRAFT)

    if user.has_role('payroll_reviewer'):
        visible_accounts = get_accounts_for_user(user)
        return qs.filter(account__in=visible_accounts)

    return Event.objects.none()


def _can_view_drafts(user):
    """True if this user should see Draft events."""
    return user.has_permission('can_view_draft_events')


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

    revision       = sorted([e for e in events if e.status == Event.Status.REVISION_REQUESTED], key=date_asc_key)
    drafts         = sorted([e for e in events if e.status == Event.Status.DRAFT],              key=date_asc_no_date_first_key)
    recap_in_prog  = sorted([e for e in events if e.status == Event.Status.RECAP_IN_PROGRESS], key=date_asc_key)
    recap          = sorted([e for e in events if e.status == Event.Status.RECAP_SUBMITTED],    key=date_asc_key)
    scheduled      = sorted([e for e in events if e.status == Event.Status.SCHEDULED],         key=date_asc_key)
    complete       = sorted([e for e in events if e.status == Event.Status.COMPLETE],           key=date_desc_key)
    ok_to_pay      = sorted([e for e in events if e.status == Event.Status.OK_TO_PAY],         key=date_desc_key)

    groups = []
    if revision:
        groups.append(('Revision Requested', 'revision_requested', revision))
    if drafts:
        groups.append(('Drafts', 'draft', drafts))
    if recap_in_prog:
        groups.append(('Recap In Progress', 'recap_in_progress', recap_in_prog))
    if recap:
        groups.append(('Recap Submitted', 'recap_submitted', recap))
    if scheduled:
        groups.append(('Scheduled', 'scheduled', scheduled))
    if complete:
        groups.append(('Complete', 'complete', complete))
    if ok_to_pay:
        groups.append(('Ok to Pay', 'ok_to_pay', ok_to_pay))

    return groups


# ---------------------------------------------------------------------------
# Event List helpers
# ---------------------------------------------------------------------------

def _apply_event_filters(qs, filters):
    """
    Apply a filter dict to an event queryset.

    Used by both event_list (which saves filters to session) and
    event_export_csv (which reads filters from GET parameters).
    """
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

    return qs


# ---------------------------------------------------------------------------
# Event List
# ---------------------------------------------------------------------------

@login_required
def event_list(request):
    if not request.user.has_permission('can_view_events'):
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
    qs = _apply_event_filters(qs, filters)

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
# Event CSV Export
# ---------------------------------------------------------------------------

@login_required
def event_export_csv(request):
    """
    GET: Export the current filtered event list as a CSV download.

    Accepts the same filter parameters as the event list view.  The filter
    form on the event list page passes its current values via query parameters
    when the user clicks "Export CSV", so the export always matches what is
    visible on screen.

    CSV columns:
      Event Type, Event Status, Event Date, Event Duration, Account Name,
      City, Ambassador, Event Manager, Samples Poured, QR Codes Scanned,
      [one column per distinct item sorted by brand name then item sort_order
      within each brand — bottles sold], Recap Note
    """
    import csv
    from datetime import date as _date
    from django.http import HttpResponse

    if not request.user.has_permission('can_view_events'):
        return render(request, '403.html', status=403)

    # Build filter dict from GET parameters (same keys as event_list session)
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

    qs = _get_visible_events(request.user)
    if not _can_view_drafts(request.user):
        qs = qs.exclude(status=Event.Status.DRAFT)
    qs = _apply_event_filters(qs, filters)

    # Fetch all events with related data in a single pass
    events = list(
        qs.select_related('account', 'ambassador', 'event_manager')
        .prefetch_related('items__brand', 'item_recaps', 'expenses')
        .order_by('date', 'pk')
    )

    # Collect all distinct items across all events in this export,
    # sorted by brand name then item sort_order then name.
    seen_item_pks = set()
    all_items = []
    for event in events:
        for item in event.items.all():
            if item.pk not in seen_item_pks:
                seen_item_pks.add(item.pk)
                all_items.append(item)
    all_items.sort(key=lambda x: (x.brand.name, x.sort_order, x.name))

    # Build recap lookup: {event_pk: {item_pk: EventItemRecap}}
    recap_lookup = {}
    for event in events:
        recap_lookup[event.pk] = {r.item_id: r for r in event.item_recaps.all()}

    today_str = _date.today().strftime('%Y-%m-%d')
    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = (
        f'attachment; filename="events_export_{today_str}.csv"'
    )
    writer = csv.writer(response)

    # Header
    writer.writerow(
        ['Event Type', 'Event Status', 'Event Date', 'Event Duration',
         'Account Name', 'City', 'Ambassador', 'Event Manager',
         'Samples Poured', 'QR Codes Scanned',
         'Total Expenses', 'Expense Notes']
        + [item.name for item in all_items]
        + ['Recap Note']
    )

    # Data rows
    for event in events:
        if event.account:
            acct_name = event.account.name
            city      = event.account.city or ''
        else:
            acct_name = 'Admin Hours'
            city      = ''

        date_val = event.date.strftime('%m/%d/%y') if event.date else ''

        # Duration as decimal hours for Excel summing (e.g. 2h30m → 2.5)
        h = event.duration_hours or 0
        m = event.duration_minutes or 0
        total_minutes = h * 60 + m
        duration_decimal = total_minutes / 60 if total_minutes else ''

        ambassador_name = event.ambassador.get_full_name() if event.ambassador else ''
        event_mgr_name  = event.event_manager.get_full_name() if event.event_manager else ''

        # Recap Note: use recap_notes for Tasting, recap_comment for Special Event
        if event.event_type == Event.EventType.TASTING:
            recap_note = event.recap_notes or ''
        elif event.event_type == Event.EventType.SPECIAL_EVENT:
            recap_note = event.recap_comment or ''
        else:
            recap_note = ''

        # Expense columns
        event_expenses = list(event.expenses.all())
        if event_expenses:
            from decimal import Decimal
            total_expenses = sum(e.amount for e in event_expenses)
            expense_notes  = ' | '.join(e.description for e in event_expenses)
        else:
            total_expenses = ''
            expense_notes  = ''

        row = [
            event.get_event_type_display(),
            event.get_status_display(),
            date_val,
            duration_decimal,
            acct_name,
            city,
            ambassador_name,
            event_mgr_name,
            event.recap_samples_poured if event.recap_samples_poured is not None else '',
            event.recap_qr_codes_scanned if event.recap_qr_codes_scanned is not None else '',
            total_expenses,
            expense_notes,
        ]

        event_recaps    = recap_lookup.get(event.pk, {})
        event_item_pks  = {item.pk for item in event.items.all()}
        for item in all_items:
            if item.pk not in event_item_pks:
                row.append('')
            else:
                recap = event_recaps.get(item.pk)
                if recap is None or recap.bottles_sold is None:
                    row.append('')
                else:
                    row.append(recap.bottles_sold)

        row.append(recap_note)
        writer.writerow(row)

    return response


# ---------------------------------------------------------------------------
# Event Detail
# ---------------------------------------------------------------------------

@login_required
def event_detail(request, pk):
    if not request.user.has_permission('can_view_events'):
        return render(request, '403.html', status=403)

    company = request.user.company
    visible = _get_visible_events(request.user)
    event = get_object_or_404(visible, pk=pk, company=company)

    can_edit = bool(request.user.get_role_codenames() & _CREATOR_ROLES)
    can_action = bool(request.user.get_role_codenames() & _ACTION_ROLES)
    can_recap = _can_recap(request.user, event)

    # Revert Complete → Recap Submitted: can_approve_event OR this event's manager
    user = request.user
    _has_revert_role = user.has_permission('can_approve_event')
    _is_event_manager = event.event_manager_id and event.event_manager_id == user.pk
    can_revert = _has_revert_role or _is_event_manager
    can_unrelease = _has_revert_role or _is_event_manager
    can_revert_to_scheduled = _has_revert_role or _is_event_manager
    can_revert_revision_requested = _has_revert_role or _is_event_manager
    can_mark_ok_to_pay = user.has_permission('can_mark_ok_to_pay')

    tasting_items_by_brand = None
    if event.event_type == Event.EventType.TASTING:
        items_qs = event.items.select_related('brand').order_by('brand__name', 'sort_order', 'name')
        tasting_items_by_brand = [
            (brand_name, list(brand_items))
            for brand_name, brand_items in groupby(items_qs, key=lambda x: x.brand.name)
        ]

    # Recap context
    recap_active = event.status in _RECAP_ACTIVE_STATUSES
    show_recap = (
        event.event_type != Event.EventType.ADMIN
        and event.status not in (Event.Status.DRAFT,)
    )

    # Build (item, recap_or_None) list for tasting recap form
    items_with_recaps = []
    if event.event_type == Event.EventType.TASTING:
        existing_recaps = {
            r.item_id: r
            for r in EventItemRecap.objects.filter(event=event)
        }
        for item in event.items.select_related('brand').order_by('brand__name', 'sort_order', 'name'):
            items_with_recaps.append((item, existing_recaps.get(item.pk)))

    photos = event.photos.all() if show_recap else []
    expenses = list(event.expenses.all()) if show_recap else []

    return render(request, 'events/event_detail.html', {
        'event':                  event,
        'can_edit':               can_edit,
        'can_action':             can_action,
        'can_recap':              can_recap,
        'can_revert':             can_revert,
        'can_unrelease':          can_unrelease,
        'can_revert_to_scheduled': can_revert_to_scheduled,
        'can_revert_revision_requested': can_revert_revision_requested,
        'can_mark_ok_to_pay':     can_mark_ok_to_pay,
        'recap_active':           recap_active,
        'show_recap':             show_recap,
        'tasting_items_by_brand': tasting_items_by_brand,
        'items_with_recaps':      items_with_recaps,
        'photos':                 photos,
        'expenses':               expenses,
    })


# ---------------------------------------------------------------------------
# Event Create
# ---------------------------------------------------------------------------

def _account_search_disabled(user):
    """
    Return True if the account live search should be disabled for this user
    (they have no accounts available and no privileged access).
    """
    from apps.accounts.models import UserCoverageArea
    if user.has_permission('can_view_all_accounts'):
        return False
    # All other roles need coverage areas
    return not UserCoverageArea.objects.filter(
        user=user, company=user.company
    ).exists()


_VALID_EVENT_TYPES = {'tasting', 'special_event', 'admin'}


@login_required
def event_create(request):
    if not request.user.has_permission('can_create_events'):
        return render(request, '403.html', status=403)

    company = request.user.company

    if request.method == 'POST':
        locked_event_type = request.POST.get('event_type', '').strip().lower()
        if locked_event_type not in _VALID_EVENT_TYPES:
            return redirect('event_list')

        form = EventForm(request.POST, company=company, user=request.user)
        if form.is_valid():
            event = form.save(commit=False)
            event.company = company
            event.created_by = request.user
            event.status = Event.Status.DRAFT

            if event.event_type == Event.EventType.ADMIN:
                event.event_manager = request.user
                event.start_time = None
            else:
                if not event.event_manager_id:
                    event.event_manager = request.user

            event.duration_hours = int(form.cleaned_data.get('duration_hours', 0))
            event.save()
            form.save_m2m()
            messages.success(request, 'Event created successfully.')
            return redirect('event_detail', pk=event.pk)

        selected_item_pks = set(int(x) for x in request.POST.getlist('items') if x.isdigit())
    else:
        locked_event_type = request.GET.get('type', '').strip().lower()
        if locked_event_type not in _VALID_EVENT_TYPES:
            return redirect('event_list')

        form = EventForm(company=company, user=request.user)
        selected_item_pks = set()

    locked_event_type_display = dict(Event.EventType.choices).get(locked_event_type, locked_event_type.title())
    items_by_brand = _get_items_by_brand(company)

    return render(request, 'events/event_form.html', {
        'form':                    form,
        'form_title':              'Create Event',
        'is_create':               True,
        'account_search_disabled': _account_search_disabled(request.user),
        'selected_account_name':   '',
        'locked_event_type':         locked_event_type,
        'locked_event_type_display': locked_event_type_display,
        'items_by_brand':            items_by_brand,
        'selected_item_pks':         selected_item_pks,
    })


# ---------------------------------------------------------------------------
# Event Edit
# ---------------------------------------------------------------------------

@login_required
def event_edit(request, pk):
    if not request.user.has_permission('can_edit_events'):
        return render(request, '403.html', status=403)

    company = request.user.company
    visible = _get_visible_events(request.user)
    event = get_object_or_404(visible, pk=pk, company=company)

    if request.method == 'POST':
        form = EventForm(request.POST, instance=event, company=company, user=request.user)
        if form.is_valid():
            event = form.save(commit=False)
            event.duration_hours = int(form.cleaned_data.get('duration_hours', 0))
            if event.event_type == Event.EventType.ADMIN:
                event.event_manager = event.created_by or request.user
                event.start_time = None
            event.save()
            form.save_m2m()
            messages.success(request, 'Event updated successfully.')
            return redirect('event_detail', pk=event.pk)

        selected_item_pks = set(int(x) for x in request.POST.getlist('items') if x.isdigit())
    else:
        form = EventForm(instance=event, company=company, user=request.user)
        form.fields['duration_hours'].initial = event.duration_hours
        selected_item_pks = set(event.items.values_list('pk', flat=True))

    # For the live search display: resolve the currently-selected account name
    selected_account_name = ''
    if event.account_id:
        try:
            acc = Account.objects.get(pk=event.account_id)
            selected_account_name = acc.name
        except Exception:
            pass

    locked_event_type = event.event_type
    locked_event_type_display = event.get_event_type_display()
    items_by_brand = _get_items_by_brand(company)

    return render(request, 'events/event_form.html', {
        'form':                    form,
        'event':                   event,
        'form_title':              'Edit Event',
        'is_create':               False,
        'account_search_disabled': _account_search_disabled(request.user),
        'selected_account_name':   selected_account_name,
        'locked_event_type':         locked_event_type,
        'locked_event_type_display': locked_event_type_display,
        'items_by_brand':            items_by_brand,
        'selected_item_pks':         selected_item_pks,
    })


# ---------------------------------------------------------------------------
# Status transitions
# ---------------------------------------------------------------------------

@login_required
def event_release(request, pk):
    """
    POST: Release a Draft event.

    - Tasting / Special Event: Draft → Scheduled
    - Admin:                   Draft → Recap Submitted
      (Admin events have no recap step, so they go straight to awaiting approval.)
    """
    if not request.user.has_permission('can_release_event'):
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
        errors.append('Tasting and Special Event events must have an account assigned.')
    if event.event_type == Event.EventType.TASTING and not event.items.exists():
        errors.append('A Tasting event must have at least one item selected before it can be released.')

    if errors:
        for err in errors:
            messages.error(request, err)
        return redirect('event_detail', pk=pk)

    if event.event_type == Event.EventType.ADMIN:
        event.status = Event.Status.RECAP_SUBMITTED
        event.save(update_fields=['status', 'updated_at'])
        messages.success(request, 'Admin event released and is ready for approval.')
    else:
        event.status = Event.Status.SCHEDULED
        event.save(update_fields=['status', 'updated_at'])
        messages.success(request, 'Event released and is now Scheduled.')

    return redirect('event_detail', pk=pk)


@login_required
def event_request_revision(request, pk):
    """POST: Recap Submitted → Revision Requested (Tasting / Special Event only)."""
    if not request.user.has_permission('can_request_revision'):
        return render(request, '403.html', status=403)
    if request.method != 'POST':
        return redirect('event_detail', pk=pk)

    company = request.user.company
    visible = _get_visible_events(request.user)
    event = get_object_or_404(visible, pk=pk, company=company)

    # Admin events have no recap — revision requests are not applicable
    if event.event_type == Event.EventType.ADMIN:
        messages.error(request, 'Admin events do not have a recap to revise.')
        return redirect('event_detail', pk=pk)

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
def event_unrelease(request, pk):
    """
    POST: Scheduled → Draft.

    Access: Supplier Admin, Sales Manager, or the assigned Event Manager
    on this specific event.
    """
    if request.method != 'POST':
        return redirect('event_detail', pk=pk)

    company = request.user.company
    visible = _get_visible_events(request.user)
    event = get_object_or_404(visible, pk=pk, company=company)

    user = request.user
    can_unrelease = (
        user.has_permission('can_approve_event')
        or (event.event_manager_id and event.event_manager_id == user.pk)
    )
    if not can_unrelease:
        return render(request, '403.html', status=403)

    if event.status != Event.Status.SCHEDULED:
        messages.error(request, 'Only Scheduled events can be moved back to Draft.')
        return redirect('event_detail', pk=pk)

    event.status = Event.Status.DRAFT
    event.save(update_fields=['status', 'updated_at'])
    messages.success(request, 'Event moved back to Draft.')
    return redirect('event_detail', pk=pk)


@login_required
def event_approve(request, pk):
    """POST: Recap Submitted → Complete (with race condition guard)."""
    if not request.user.has_permission('can_approve_event'):
        return render(request, '403.html', status=403)
    if request.method != 'POST':
        return redirect('event_detail', pk=pk)

    company = request.user.company
    visible = _get_visible_events(request.user)
    event = get_object_or_404(visible, pk=pk, company=company)

    # Race condition guard: re-check status at moment of approval
    if event.status != Event.Status.RECAP_SUBMITTED:
        messages.error(request, 'Event is not in Recap Submitted status.')
        return redirect('event_detail', pk=pk)

    updated = Event.objects.filter(
        pk=event.pk, status=Event.Status.RECAP_SUBMITTED
    ).update(status=Event.Status.COMPLETE)
    if updated:
        messages.success(request, 'Event approved and marked as Complete.')
    else:
        messages.error(request, 'Event status changed before approval could be saved. Please try again.')
    return redirect('event_detail', pk=pk)


# ---------------------------------------------------------------------------
# Recap: save, submit, unlock
# ---------------------------------------------------------------------------

def _save_recap_data(request, event):
    """
    Parse POST data and persist recap fields, per-item recap records,
    and any uploaded photos.

    Called by both save_recap and submit_recap. Does NOT update event status
    or AccountItem prices — those are handled by the calling view.
    """
    from decimal import Decimal, InvalidOperation

    update_fields = ['updated_at']

    if event.event_type == Event.EventType.TASTING:
        # Part 1 — Overall event fields
        samples_str = request.POST.get('samples_poured', '').strip()
        qr_str = request.POST.get('qr_codes_scanned', '').strip()
        notes = request.POST.get('recap_notes', '').strip()

        try:
            event.recap_samples_poured = int(samples_str) if samples_str else None
        except ValueError:
            event.recap_samples_poured = None
        update_fields.append('recap_samples_poured')

        try:
            event.recap_qr_codes_scanned = int(qr_str) if qr_str else None
        except ValueError:
            event.recap_qr_codes_scanned = None
        update_fields.append('recap_qr_codes_scanned')

        event.recap_notes = notes
        update_fields.append('recap_notes')

        event.save(update_fields=update_fields)

        # Part 2 — Per item recap
        for item in event.items.select_related('brand').order_by('brand__name', 'sort_order', 'name'):
            price_str = request.POST.get(f'shelf_price_{item.pk}', '').strip()
            sold_str = request.POST.get(f'bottles_sold_{item.pk}', '').strip()
            samples_str = request.POST.get(f'bottles_samples_{item.pk}', '').strip()

            shelf_price = None
            bottles_sold = None
            bottles_used_for_samples = None

            if price_str:
                try:
                    shelf_price = Decimal(price_str)
                except InvalidOperation:
                    pass
            if sold_str:
                try:
                    bottles_sold = int(sold_str)
                except ValueError:
                    pass
            if samples_str:
                try:
                    bottles_used_for_samples = int(samples_str)
                except ValueError:
                    pass

            recap, _ = EventItemRecap.objects.get_or_create(event=event, item=item)
            recap.shelf_price = shelf_price
            recap.bottles_sold = bottles_sold
            recap.bottles_used_for_samples = bottles_used_for_samples
            recap.save(update_fields=['shelf_price', 'bottles_sold', 'bottles_used_for_samples'])

    elif event.event_type == Event.EventType.SPECIAL_EVENT:
        comment = request.POST.get('recap_comment', '').strip()
        event.recap_comment = comment
        update_fields.append('recap_comment')
        event.save(update_fields=update_fields)

    # Photos (both Tasting and Festival)
    for photo_file in request.FILES.getlist('photos'):
        file_url = save_event_photo(photo_file, event.pk)
        EventPhoto.objects.create(
            event=event,
            account=event.account,
            file_url=file_url,
            uploaded_by=request.user,
        )


def _apply_price_updates(event, user):
    """
    On recap submission, update AccountItem.current_price for each item in the
    event. Archives the old price to AccountItemPriceHistory if it changed.
    """
    from apps.accounts.models import AccountItem, AccountItemPriceHistory

    if event.event_type != Event.EventType.TASTING or event.account_id is None:
        return

    recaps = EventItemRecap.objects.filter(event=event).select_related('item')
    for recap in recaps:
        if recap.shelf_price is None:
            continue
        try:
            account_item = AccountItem.objects.get(
                account_id=event.account_id, item=recap.item
            )
        except AccountItem.DoesNotExist:
            continue

        if account_item.current_price is None:
            account_item.current_price = recap.shelf_price
            account_item.save(update_fields=['current_price'])
        elif account_item.current_price != recap.shelf_price:
            AccountItemPriceHistory.objects.create(
                account_item=account_item,
                price=account_item.current_price,
                recorded_by=user,
            )
            account_item.current_price = recap.shelf_price
            account_item.save(update_fields=['current_price'])
        # If price unchanged, do nothing


@login_required
def event_save_recap(request, pk):
    """POST: Save recap data. Scheduled → Recap In Progress on first save."""
    if request.method != 'POST':
        return redirect('event_detail', pk=pk)

    visible = _get_visible_events(request.user)
    event = get_object_or_404(visible, pk=pk)

    if not _can_recap(request.user, event):
        return render(request, '403.html', status=403)

    if event.status not in _RECAP_ACTIVE_STATUSES:
        messages.error(request, 'Recap cannot be edited in the current event status.')
        return redirect('event_detail', pk=pk)

    _save_recap_data(request, event)

    if event.status == Event.Status.SCHEDULED:
        Event.objects.filter(pk=event.pk).update(status=Event.Status.RECAP_IN_PROGRESS)

    messages.success(request, 'Recap saved.')
    return redirect('event_detail', pk=pk)


@login_required
def event_submit_recap(request, pk):
    """POST: Save recap data and move event to Recap Submitted."""
    if request.method != 'POST':
        return redirect('event_detail', pk=pk)

    visible = _get_visible_events(request.user)
    event = get_object_or_404(visible, pk=pk)

    if not _can_recap(request.user, event):
        return render(request, '403.html', status=403)

    if event.status not in _RECAP_ACTIVE_STATUSES:
        messages.error(request, 'Recap cannot be submitted in the current event status.')
        return redirect('event_detail', pk=pk)

    _save_recap_data(request, event)

    # Reload to get latest recap fields (saved by _save_recap_data)
    event.refresh_from_db()

    # Validate minimum required fields
    # No minimum submission requirement — any combination of filled or empty fields allowed
    has_content = True

    _apply_price_updates(event, request.user)

    Event.objects.filter(pk=event.pk).update(status=Event.Status.RECAP_SUBMITTED)
    messages.success(request, 'Recap submitted successfully.')
    return redirect('event_detail', pk=pk)


@login_required
def event_unlock_recap(request, pk):
    """POST: Recap Submitted → Recap In Progress."""
    if request.method != 'POST':
        return redirect('event_detail', pk=pk)

    visible = _get_visible_events(request.user)
    event = get_object_or_404(visible, pk=pk)

    if not _can_recap(request.user, event):
        return render(request, '403.html', status=403)

    if event.status != Event.Status.RECAP_SUBMITTED:
        messages.error(request, 'Only Recap Submitted events can be unlocked.')
        return redirect('event_detail', pk=pk)

    Event.objects.filter(pk=event.pk).update(status=Event.Status.RECAP_IN_PROGRESS)
    messages.success(request, 'Recap unlocked. You can now edit and resubmit.')
    return redirect('event_detail', pk=pk)


@login_required
def event_photo_delete(request, pk, photo_pk):
    """
    POST: Delete a single EventPhoto record and its file from storage.

    Access: same users who can fill out the recap (Ambassador, Event Manager,
    coverage-area users).  Only allowed when recap is editable:
    Recap In Progress or Revision Requested.
    """
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required.'}, status=405)

    company = request.user.company
    visible = _get_visible_events(request.user)
    event = get_object_or_404(visible, pk=pk, company=company)

    if not _can_recap(request.user, event):
        return JsonResponse({'error': 'Permission denied.'}, status=403)

    _PHOTO_DELETE_STATUSES = {Event.Status.RECAP_IN_PROGRESS, Event.Status.REVISION_REQUESTED}
    if event.status not in _PHOTO_DELETE_STATUSES:
        return JsonResponse({'error': 'Recap is not in an editable status.'}, status=400)

    photo = get_object_or_404(EventPhoto, pk=photo_pk, event=event)
    file_url = photo.file_url
    photo.delete()
    delete_event_photo(file_url)

    return JsonResponse({'success': True})


@login_required
def event_revert_complete(request, pk):
    """
    POST: Revert a Complete event back to Recap Submitted.

    Access: Supplier Admin, Sales Manager, or the assigned Event Manager
    on this specific event. The Event Manager check is per-event, not role-based.
    """
    if request.method != 'POST':
        return redirect('event_detail', pk=pk)

    company = request.user.company
    visible = _get_visible_events(request.user)
    event = get_object_or_404(visible, pk=pk, company=company)

    # Permission check: can_approve_event OR this event's Event Manager
    user = request.user
    can_revert = (
        user.has_permission('can_approve_event')
        or (event.event_manager_id and event.event_manager_id == user.pk)
    )
    if not can_revert:
        return render(request, '403.html', status=403)

    if event.status != Event.Status.COMPLETE:
        messages.error(request, 'Only Complete events can be reverted.')
        return redirect('event_detail', pk=pk)

    Event.objects.filter(pk=event.pk, status=Event.Status.COMPLETE).update(
        status=Event.Status.RECAP_SUBMITTED
    )
    messages.success(request, 'Event reverted to Recap Submitted.')
    return redirect('event_detail', pk=pk)


@login_required
def event_mark_ok_to_pay(request, pk):
    """
    POST: Transition a Complete event to Ok to Pay.

    Access: users with can_mark_ok_to_pay permission.
    """
    if request.method != 'POST':
        return redirect('event_detail', pk=pk)

    if not request.user.has_permission('can_mark_ok_to_pay'):
        return render(request, '403.html', status=403)

    company = request.user.company
    visible = _get_visible_events(request.user)
    event = get_object_or_404(visible, pk=pk, company=company)

    if event.status != Event.Status.COMPLETE:
        messages.error(request, 'Only Complete events can be marked Ok to Pay.')
        return redirect('event_detail', pk=pk)

    Event.objects.filter(pk=event.pk, status=Event.Status.COMPLETE).update(
        status=Event.Status.OK_TO_PAY
    )
    messages.success(request, 'Event marked Ok to Pay.')
    return redirect('event_detail', pk=pk)


@login_required
def event_revert_ok_to_pay(request, pk):
    """
    POST: Revert an Ok to Pay event back to Complete.

    Access: users with can_mark_ok_to_pay permission.
    """
    if request.method != 'POST':
        return redirect('event_detail', pk=pk)

    if not request.user.has_permission('can_mark_ok_to_pay'):
        return render(request, '403.html', status=403)

    company = request.user.company
    visible = _get_visible_events(request.user)
    event = get_object_or_404(visible, pk=pk, company=company)

    if event.status != Event.Status.OK_TO_PAY:
        messages.error(request, 'Only Ok to Pay events can be reverted to Complete.')
        return redirect('event_detail', pk=pk)

    Event.objects.filter(pk=event.pk, status=Event.Status.OK_TO_PAY).update(
        status=Event.Status.COMPLETE
    )
    messages.success(request, 'Event reverted to Complete.')
    return redirect('event_detail', pk=pk)


def _delete_recap_data(event):
    """
    Delete all recap data for an event: photos, expenses, item recaps,
    and clear all recap fields. Sets status to SCHEDULED.

    Called by both event_revert_recap_submitted and
    event_revert_revision_requested.
    """
    # Delete all EventPhoto records and their files from storage
    for photo in event.photos.all():
        delete_event_photo(photo.file_url)
    event.photos.all().delete()

    # Delete all Expense records and their receipt photos
    for expense in event.expenses.all():
        delete_event_photo(expense.receipt_photo_url)
    event.expenses.all().delete()

    # Delete all EventItemRecap records
    event.item_recaps.all().delete()

    # Clear recap fields and revert status
    event.recap_notes = ''
    event.recap_samples_poured = None
    event.recap_qr_codes_scanned = None
    event.recap_comment = ''
    event.revision_note = ''
    event.status = Event.Status.SCHEDULED
    event.save(update_fields=[
        'status', 'recap_notes', 'recap_samples_poured',
        'recap_qr_codes_scanned', 'recap_comment', 'revision_note', 'updated_at',
    ])


@login_required
def event_revert_recap_submitted(request, pk):
    """
    POST: Revert a Recap Submitted event back to Scheduled.

    Destructive: clears all recap fields, deletes all EventItemRecap records,
    EventPhoto records (and files), and Expense records (and receipt files).

    Access: Supplier Admin, Sales Manager, or the assigned Event Manager
    on this specific event.
    """
    if request.method != 'POST':
        return redirect('event_detail', pk=pk)

    company = request.user.company
    visible = _get_visible_events(request.user)
    event = get_object_or_404(visible, pk=pk, company=company)

    user = request.user
    can_revert = (
        user.has_permission('can_approve_event')
        or (event.event_manager_id and event.event_manager_id == user.pk)
    )
    if not can_revert:
        return render(request, '403.html', status=403)

    if event.status != Event.Status.RECAP_SUBMITTED:
        messages.error(request, 'Only Recap Submitted events can be reverted to Scheduled.')
        return redirect('event_detail', pk=pk)

    _delete_recap_data(event)
    messages.success(request, 'Event reverted to Scheduled. All recap data has been deleted.')
    return redirect('event_detail', pk=pk)


@login_required
def event_revert_revision_requested(request, pk):
    """
    POST: Revert a Revision Requested event back to Scheduled.

    Destructive: clears all recap fields, deletes all EventItemRecap records,
    EventPhoto records (and files), and Expense records (and receipt files).

    Access: Supplier Admin, Sales Manager, or the assigned Event Manager
    on this specific event.
    """
    if request.method != 'POST':
        return redirect('event_detail', pk=pk)

    company = request.user.company
    visible = _get_visible_events(request.user)
    event = get_object_or_404(visible, pk=pk, company=company)

    user = request.user
    can_revert = (
        user.has_permission('can_approve_event')
        or (event.event_manager_id and event.event_manager_id == user.pk)
    )
    if not can_revert:
        return render(request, '403.html', status=403)

    if event.status != Event.Status.REVISION_REQUESTED:
        messages.error(request, 'Only Revision Requested events can be reverted to Scheduled.')
        return redirect('event_detail', pk=pk)

    _delete_recap_data(event)
    messages.success(request, 'Event reverted to Scheduled. All recap data has been deleted.')
    return redirect('event_detail', pk=pk)


@login_required
def event_delete(request, pk):
    """POST: Permanently delete a Draft event."""
    if not request.user.has_permission('can_delete_event'):
        return render(request, '403.html', status=403)
    if request.method != 'POST':
        return redirect('event_detail', pk=pk)

    company = request.user.company
    visible = _get_visible_events(request.user)
    event = get_object_or_404(visible, pk=pk, company=company)

    if event.status != Event.Status.DRAFT:
        messages.error(request, 'Only Draft events can be deleted.')
        return redirect('event_detail', pk=pk)

    event.delete()
    messages.success(request, 'Event deleted successfully.')
    return redirect('event_list')


# ---------------------------------------------------------------------------
# AJAX endpoints
# ---------------------------------------------------------------------------

def _ambassador_list_response(users):
    return [{'id': u.pk, 'name': u.get_full_name() or u.username} for u in users]


@login_required
def ajax_ambassadors(request):
    """
    GET /events/ajax/ambassadors/?account_id=X
    Returns users eligible as ambassadors for the given account:
      - Ambassador, Ambassador Manager, Territory Manager, Sales Manager
        filtered by coverage area
      - Supplier Admin always included regardless of coverage area
    For Admin events (no account_id), returns all company users in those roles.
    """
    if not request.user.is_authenticated:
        return JsonResponse({'error': 'Authentication required.'}, status=403)

    company = request.user.company
    account_id = request.GET.get('account_id', '').strip()

    # Ambassador dropdown roles — SaaS Admin and Distributor Contact excluded
    roles = ['ambassador', 'ambassador_manager', 'territory_manager', 'sales_manager', 'supplier_admin']

    if account_id:
        try:
            account = Account.active_accounts.get(pk=account_id, company=company)
        except Account.DoesNotExist:
            return JsonResponse({'ambassadors': []})
        # get_users_covering_account handles Supplier Admin specially (always included)
        users = get_users_covering_account(account, roles)
    else:
        # Admin event: all company users in these roles (except Supplier Admin is included too)
        users = User.objects.filter(
            company=company, roles__codename__in=roles, is_active=True
        ).distinct().order_by('last_name', 'first_name')

    return JsonResponse({'ambassadors': _ambassador_list_response(users)})


@login_required
def ajax_event_managers(request):
    """
    GET /events/ajax/event_managers/?account_id=X
    Returns users eligible as event managers for the given account.
    Roles: Ambassador Manager, Territory Manager, Sales Manager (coverage-filtered)
           + Supplier Admin (always included regardless of coverage area).
    For Admin events (no account_id), returns all company users in those roles.
    Also returns current_user_id so the JS can pre-select the creating user.
    """
    if not request.user.is_authenticated:
        return JsonResponse({'error': 'Authentication required.'}, status=403)

    company = request.user.company
    account_id = request.GET.get('account_id', '').strip()

    roles = ['ambassador_manager', 'territory_manager', 'sales_manager', 'supplier_admin']

    if account_id:
        try:
            account = Account.active_accounts.get(pk=account_id, company=company)
        except Account.DoesNotExist:
            return JsonResponse({'event_managers': [], 'current_user_id': request.user.pk})
        users = get_users_covering_account(account, roles)
    else:
        users = User.objects.filter(
            company=company, roles__codename__in=roles, is_active=True
        ).distinct().order_by('last_name', 'first_name')

    return JsonResponse({
        'event_managers': _ambassador_list_response(users),
        'current_user_id': request.user.pk,
    })


@login_required
def ajax_event_accounts(request):
    """
    GET /events/ajax/accounts/?q=searchterm
    Returns accounts matching the search query, filtered through the user's
    coverage areas via get_accounts_for_user(). Max 20 results.
    Searches name, street, city, state (case insensitive).
    """
    from django.db.models import Q as DjangoQ

    q = request.GET.get('q', '').strip()
    if len(q) < 2:
        return JsonResponse({'accounts': []})

    company = request.user.company
    if not company:
        return JsonResponse({'accounts': []})

    # Build an AND-of-ORs Q: every whitespace-separated term must appear in
    # at least one of the four searchable fields (cross-field multi-word support).
    term_q = DjangoQ()
    for term in q.split():
        term_q &= (
            DjangoQ(name__icontains=term)
            | DjangoQ(street__icontains=term)
            | DjangoQ(city__icontains=term)
            | DjangoQ(state__icontains=term)
        )

    accounts = (
        get_accounts_for_user(request.user)
        .filter(term_q)
        .select_related('distributor')
        .order_by('name')
        [:20]
    )

    result = [
        {
            'id': a.pk,
            'name': a.name,
            'street': a.street or '',
            'city': a.city or '',
            'state': a.state or '',
            'distributor': a.distributor.name if a.distributor else '',
        }
        for a in accounts
    ]

    return JsonResponse({'accounts': result})


# ---------------------------------------------------------------------------
# Expense AJAX endpoints
# ---------------------------------------------------------------------------

@login_required
def expense_add(request, pk):
    """
    POST: Add an Expense to an event recap.

    Required fields: amount (decimal), description (str), receipt_photo (file).
    Access: same users who can fill out the recap.
    Only allowed when recap is in an editable status.
    """
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required.'}, status=405)

    company = request.user.company
    visible = _get_visible_events(request.user)
    event = get_object_or_404(visible, pk=pk, company=company)

    if not _can_recap(request.user, event):
        return JsonResponse({'error': 'Permission denied.'}, status=403)

    if event.status not in _RECAP_ACTIVE_STATUSES:
        return JsonResponse({'error': 'Recap is not in an editable status.'}, status=400)

    amount_str = request.POST.get('amount', '').strip()
    description = request.POST.get('description', '').strip()
    receipt_file = request.FILES.get('receipt_photo')

    if not amount_str:
        return JsonResponse({'error': 'Amount is required.'}, status=400)
    try:
        from decimal import Decimal, InvalidOperation
        amount = Decimal(amount_str)
        if amount <= 0:
            raise InvalidOperation
    except Exception:
        return JsonResponse({'error': 'Invalid amount.'}, status=400)

    if not description:
        return JsonResponse({'error': 'Description is required.'}, status=400)

    if not receipt_file:
        return JsonResponse({'error': 'Receipt photo is required.'}, status=400)

    receipt_url = save_event_photo(receipt_file, event.pk)
    expense = Expense.objects.create(
        event=event,
        amount=amount,
        description=description,
        receipt_photo_url=receipt_url,
        created_by=request.user,
    )

    return JsonResponse({
        'success': True,
        'expense': {
            'id': expense.pk,
            'amount': str(expense.amount),
            'description': expense.description,
            'receipt_photo_url': expense.receipt_photo_url,
        },
    })


@login_required
def expense_delete(request, pk, expense_pk):
    """
    POST: Delete an Expense record and its receipt photo from storage.

    Access: same users who can fill out the recap.
    Only allowed when recap is in an editable status.
    """
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required.'}, status=405)

    company = request.user.company
    visible = _get_visible_events(request.user)
    event = get_object_or_404(visible, pk=pk, company=company)

    if not _can_recap(request.user, event):
        return JsonResponse({'error': 'Permission denied.'}, status=403)

    if event.status not in _RECAP_ACTIVE_STATUSES:
        return JsonResponse({'error': 'Recap is not in an editable status.'}, status=400)

    expense = get_object_or_404(Expense, pk=expense_pk, event=event)
    file_url = expense.receipt_photo_url
    expense.delete()
    delete_event_photo(file_url)

    return JsonResponse({'success': True})
