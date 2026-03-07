"""URL patterns for the events app."""
from django.urls import path

from . import views

urlpatterns = [
    # Event CRUD
    path('events/', views.event_list, name='event_list'),
    path('events/create/', views.event_create, name='event_create'),
    path('events/export-csv/', views.event_export_csv, name='event_export_csv'),
    path('events/<int:pk>/', views.event_detail, name='event_detail'),
    path('events/<int:pk>/edit/', views.event_edit, name='event_edit'),

    # Status transitions
    path('events/<int:pk>/release/', views.event_release, name='event_release'),
    path('events/<int:pk>/unrelease/', views.event_unrelease, name='event_unrelease'),
    path('events/<int:pk>/request-revision/', views.event_request_revision, name='event_request_revision'),
    path('events/<int:pk>/approve/', views.event_approve, name='event_approve'),
    path('events/<int:pk>/revert-complete/', views.event_revert_complete, name='event_revert_complete'),
    path('events/<int:pk>/mark-ok-to-pay/', views.event_mark_ok_to_pay, name='event_mark_ok_to_pay'),
    path('events/<int:pk>/revert-ok-to-pay/', views.event_revert_ok_to_pay, name='event_revert_ok_to_pay'),
    path('events/<int:pk>/revert-recap-submitted/', views.event_revert_recap_submitted, name='event_revert_recap_submitted'),
    path('events/<int:pk>/revert-revision-requested/', views.event_revert_revision_requested, name='event_revert_revision_requested'),
    path('events/<int:pk>/delete/', views.event_delete, name='event_delete'),

    # Recap
    path('events/<int:pk>/save-recap/', views.event_save_recap, name='event_save_recap'),
    path('events/<int:pk>/submit-recap/', views.event_submit_recap, name='event_submit_recap'),
    path('events/<int:pk>/unlock-recap/', views.event_unlock_recap, name='event_unlock_recap'),
    path('events/<int:pk>/photos/<int:photo_pk>/delete/', views.event_photo_delete, name='event_photo_delete'),

    # Expenses (AJAX)
    path('events/<int:pk>/expenses/add/', views.expense_add, name='expense_add'),
    path('events/<int:pk>/expenses/<int:expense_pk>/delete/', views.expense_delete, name='expense_delete'),

    # AJAX
    path('events/ajax/ambassadors/', views.ajax_ambassadors, name='ajax_event_ambassadors'),
    path('events/ajax/event_managers/', views.ajax_event_managers, name='ajax_event_managers'),
    path('events/ajax/accounts/', views.ajax_event_accounts, name='ajax_event_accounts'),
]
