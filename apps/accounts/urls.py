"""URL patterns for the accounts app."""
from django.urls import path

from . import views

urlpatterns = [
    # Account management
    path('accounts/', views.account_list, name='account_list'),
    path('accounts/create/', views.account_create, name='account_create'),
    path('accounts/<int:pk>/', views.account_detail, name='account_detail'),
    path('accounts/<int:pk>/edit/', views.account_edit, name='account_edit'),
    path('accounts/<int:pk>/toggle/', views.account_toggle, name='account_toggle'),

    # Coverage area management (Supplier Admin only, AJAX-driven)
    path(
        'accounts/users/<int:user_pk>/coverage-areas/add/',
        views.coverage_area_add,
        name='coverage_area_add',
    ),
    path(
        'accounts/users/<int:user_pk>/coverage-areas/<int:ca_pk>/remove/',
        views.coverage_area_remove,
        name='coverage_area_remove',
    ),

    # AJAX endpoints
    path('accounts/ajax/states/', views.ajax_states, name='ajax_states'),
    path('accounts/ajax/counties/', views.ajax_counties, name='ajax_counties'),
    path('accounts/ajax/cities/', views.ajax_cities, name='ajax_cities'),
    path('accounts/ajax/search/', views.ajax_accounts_search, name='ajax_accounts_search'),
]
