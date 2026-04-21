"""URL patterns for the accounts app."""
from django.urls import path

from . import views

urlpatterns = [
    # Account management
    path('accounts/', views.account_list, name='account_list'),
    path('accounts/bulk-delete/', views.account_bulk_delete, name='account_bulk_delete'),
    path('accounts/create/', views.account_create, name='account_create'),
    path('accounts/<int:pk>/', views.account_detail, name='account_detail'),
    path('accounts/<int:pk>/detail/', views.account_detail_combined, name='account_detail_combined'),
    path('accounts/<int:pk>/edit/', views.account_edit, name='account_edit'),
    path('accounts/<int:pk>/toggle/', views.account_toggle, name='account_toggle'),
    path('accounts/<int:pk>/delete/', views.account_delete, name='account_delete'),

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

    # Note API
    path('accounts/<int:pk>/notes/', views.note_list, name='note_list'),
    path('accounts/<int:pk>/notes/create/', views.note_create, name='note_create'),
    path('accounts/<int:pk>/notes/assignees/', views.assignee_list, name='note_assignee_list'),
    path('accounts/<int:pk>/notes/<int:npk>/update/', views.note_update, name='note_update'),
    path('accounts/<int:pk>/notes/<int:npk>/delete/', views.note_delete, name='note_delete'),

    # Contact API
    path('accounts/<int:pk>/contacts/', views.contact_list, name='contact_list'),
    path('accounts/<int:pk>/contacts/create/', views.contact_create, name='contact_create'),
    path('accounts/<int:pk>/contacts/<int:cpk>/update/', views.contact_update, name='contact_update'),
    path('accounts/<int:pk>/contacts/<int:cpk>/delete/', views.contact_delete, name='contact_delete'),

    # AJAX endpoints
    path('accounts/ajax/states/', views.ajax_states, name='ajax_states'),
    path('accounts/ajax/counties/', views.ajax_counties, name='ajax_counties'),
    path('accounts/ajax/cities/', views.ajax_cities, name='ajax_cities'),
    path('accounts/ajax/search/', views.ajax_accounts_search, name='ajax_accounts_search'),
]
