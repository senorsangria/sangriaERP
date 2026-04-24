"""
Routes API views: list and save routes.
"""
import json

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.http import require_GET, require_POST

from apps.accounts.models import Account
from apps.distribution.models import Distributor

from .models import Route, RouteAccount


@login_required
@require_GET
def route_list(request):
    """Return the requesting user's routes for a given distributor."""
    user = request.user

    if not user.has_permission('can_view_report_account_sales'):
        return JsonResponse({'error': 'Permission denied.'}, status=403)

    distributor_id = request.GET.get('distributor_id', '')
    if not distributor_id:
        return JsonResponse({'routes': []})

    routes = Route.objects.filter(
        created_by=user,
        distributor_id=distributor_id,
    ).prefetch_related('route_accounts')

    data = [
        {
            'id': r.pk,
            'name': r.name,
            'account_count': r.route_accounts.count(),
        }
        for r in routes
    ]
    return JsonResponse({'routes': data})


@login_required
@require_POST
def route_save(request):
    """Add accounts to a new or existing route."""
    user = request.user

    if not user.has_permission('can_view_report_account_sales'):
        return JsonResponse({'error': 'Permission denied.'}, status=403)

    try:
        body = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({'error': 'Invalid JSON.'}, status=400)

    account_ids = body.get('account_ids', [])
    distributor_id = body.get('distributor_id')
    action = body.get('action')

    # Validate distributor belongs to user's company
    try:
        distributor = Distributor.objects.get(pk=distributor_id, company=user.company)
    except Distributor.DoesNotExist:
        return JsonResponse({'error': 'Invalid distributor.'}, status=400)

    if action == 'new':
        route_name = (body.get('route_name') or '').strip()
        if not route_name:
            return JsonResponse({'error': 'Route name cannot be blank.'}, status=400)

        if Route.objects.filter(
            created_by=user,
            distributor=distributor,
            name__iexact=route_name,
        ).exists():
            return JsonResponse({'error': 'A route with this name already exists.'}, status=400)

        route = Route.objects.create(
            company=user.company,
            distributor=distributor,
            created_by=user,
            name=route_name,
        )

    elif action == 'existing':
        route_id = body.get('route_id')
        try:
            route = Route.objects.get(pk=route_id, created_by=user)
        except Route.DoesNotExist:
            return JsonResponse({'error': 'Route not found.'}, status=404)

    else:
        return JsonResponse({'error': 'Invalid action.'}, status=400)

    added = 0
    already_existed = 0

    for account_id in account_ids:
        # Verify account belongs to user's company
        if not Account.objects.filter(pk=account_id, company=user.company).exists():
            continue

        _, created = RouteAccount.objects.get_or_create(
            route=route,
            account_id=account_id,
            defaults={'position': 0},
        )
        if created:
            added += 1
        else:
            already_existed += 1

    return JsonResponse({
        'success': True,
        'added': added,
        'already_in_route': already_existed,
        'route_name': route.name,
    })


@login_required
def account_routes(request, account_pk):
    """GET /routes/account/<pk>/ — list routes an account is currently in."""
    if not request.user.has_permission('can_view_report_account_sales'):
        return JsonResponse({'error': 'Forbidden'}, status=403)

    account = get_object_or_404(Account, pk=account_pk, company=request.user.company)

    from apps.accounts.utils import get_accounts_for_user
    if account not in get_accounts_for_user(request.user):
        return JsonResponse({'error': 'Forbidden'}, status=403)

    route_accounts = RouteAccount.objects.filter(
        account=account
    ).select_related('route')

    return JsonResponse({
        'routes': [
            {
                'route_account_id': ra.pk,
                'route_id': ra.route.pk,
                'route_name': ra.route.name,
            }
            for ra in route_accounts
        ]
    })


@login_required
def remove_account_from_route(request, route_account_pk):
    """POST /routes/remove/<pk>/"""
    if not request.user.has_permission('can_view_report_account_sales'):
        return JsonResponse({'error': 'Forbidden'}, status=403)

    if request.method != 'POST':
        return JsonResponse({'error': 'POST required.'}, status=405)

    ra = get_object_or_404(RouteAccount, pk=route_account_pk)
    route = ra.route

    if route.created_by != request.user:
        return JsonResponse({'error': 'Forbidden'}, status=403)

    ra.delete()

    if not route.route_accounts.exists():
        route.delete()
        return JsonResponse({'success': True, 'route_deleted': True})

    return JsonResponse({'success': True, 'route_deleted': False})
