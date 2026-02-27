"""URL patterns for the events app."""
from django.urls import path

from . import views

urlpatterns = [
    # Event CRUD
    path('events/', views.event_list, name='event_list'),
    path('events/create/', views.event_create, name='event_create'),
    path('events/<int:pk>/', views.event_detail, name='event_detail'),
    path('events/<int:pk>/edit/', views.event_edit, name='event_edit'),

    # Status transitions
    path('events/<int:pk>/release/', views.event_release, name='event_release'),
    path('events/<int:pk>/request-revision/', views.event_request_revision, name='event_request_revision'),
    path('events/<int:pk>/approve/', views.event_approve, name='event_approve'),

    # AJAX
    path('events/ajax/ambassadors/', views.ajax_ambassadors, name='ajax_event_ambassadors'),
    path('events/ajax/event_managers/', views.ajax_event_managers, name='ajax_event_managers'),
]
