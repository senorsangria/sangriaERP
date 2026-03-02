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
from .models import Event, EventItemRecap, EventPhoto
from .storage import save_event_photo


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

    role = user.role
    qs = Event.objects.filter(company=company).select_related(
        'account', 'ambassador', 'event_manager', 'created_by',
        'account__distributor',
    )

    if role == User.Role.SUPPLIER_ADMIN:
        return qs

    if role == User.Role.SALES_MANAGER:
        # Sales Managers see all events with accounts, plus all admin events
        # (admin events have no account scoping per the product spec)
        return qs.filter(
            Q(account__isnull=False)
            | Q(event_type=Event.EventType.ADMIN)
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

    revision       = sorted([e for e in events if e.status == Event.Status.REVISION_REQUESTED], key=date_asc_key)
    drafts         = sorted([e for e in events if e.status == Event.Status.DRAFT],              key=date_asc_no_date_first_key)
    recap_in_prog  = sorted([e for e in events if e.status == Event.Status.RECAP_IN_PROGRESS], key=date_asc_key)
    recap          = sorted([e for e in events if e.status == Event.Status.RECAP_SUBMITTED],    key=date_asc_key)
    scheduled      = sorted([e for e in events if e.status == Event.Status.SCHEDULED],         key=date_asc_key)
    complete       = sorted([e for e in events if e.status == Event.Status.COMPLETE],           key=date_desc_key)

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
    can_recap = _can_recap(request.user, event)

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

    return render(request, 'events/event_detail.html', {
        'event':                  event,
        'can_edit':               can_edit,
        'can_action':             can_action,
        'can_recap':              can_recap,
        'recap_active':           recap_active,
        'show_recap':             show_recap,
        'tasting_items_by_brand': tasting_items_by_brand,
        'items_with_recaps':      items_with_recaps,
        'photos':                 photos,
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
    if user.role in (User.Role.SUPPLIER_ADMIN, User.Role.SAAS_ADMIN):
        return False
    # All other roles need coverage areas
    return not UserCoverageArea.objects.filter(
        user=user, company=user.company
    ).exists()


_VALID_EVENT_TYPES = {'tasting', 'special_event', 'admin'}


@login_required
def event_create(request):
    if request.user.role not in _CREATOR_ROLES:
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
    if request.user.role not in _ACTION_ROLES:
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
    """POST: Scheduled → Draft"""
    if request.user.role not in _ACTION_ROLES:
        return render(request, '403.html', status=403)
    if request.method != 'POST':
        return redirect('event_detail', pk=pk)

    company = request.user.company
    visible = _get_visible_events(request.user)
    event = get_object_or_404(visible, pk=pk, company=company)

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
    if request.user.role not in _ACTION_ROLES:
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
def event_delete(request, pk):
    """POST: Permanently delete a Draft event."""
    if request.user.role not in _ACTION_ROLES:
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

    # Ambassador dropdown roles (Change 7)
    # SaaS Admin and Distributor Contact are excluded
    roles = [
        User.Role.AMBASSADOR,
        User.Role.AMBASSADOR_MANAGER,
        User.Role.TERRITORY_MANAGER,
        User.Role.SALES_MANAGER,
        User.Role.SUPPLIER_ADMIN,
    ]

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
            company=company, role__in=roles, is_active=True
        ).order_by('last_name', 'first_name')

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

    roles = [
        User.Role.AMBASSADOR_MANAGER,
        User.Role.TERRITORY_MANAGER,
        User.Role.SALES_MANAGER,
        User.Role.SUPPLIER_ADMIN,
    ]

    if account_id:
        try:
            account = Account.active_accounts.get(pk=account_id, company=company)
        except Account.DoesNotExist:
            return JsonResponse({'event_managers': [], 'current_user_id': request.user.pk})
        users = get_users_covering_account(account, roles)
    else:
        users = User.objects.filter(
            company=company, role__in=roles, is_active=True
        ).order_by('last_name', 'first_name')

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

    accounts = (
        get_accounts_for_user(request.user)
        .filter(
            DjangoQ(name__icontains=q)
            | DjangoQ(street__icontains=q)
            | DjangoQ(city__icontains=q)
            | DjangoQ(state__icontains=q)
        )
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
