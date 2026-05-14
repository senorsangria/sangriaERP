"""
Production views. Supplier Admin only (can_manage_production permission).
Phase A: placeholder page only.
"""
from django.contrib.auth.decorators import login_required
from django.shortcuts import render
from django.http import HttpResponseForbidden


@login_required
def production_home(request):
    if not request.user.has_permission('can_manage_production'):
        return HttpResponseForbidden('You do not have permission to access this page.')
    return render(request, 'production/production_home.html', {
        'company': request.user.company,
    })
