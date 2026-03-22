"""URL patterns for the routes app."""
from django.urls import path

from .views import route_list, route_save

urlpatterns = [
    path('', route_list, name='route_list'),
    path('save/', route_save, name='route_save'),
]
