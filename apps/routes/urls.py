"""URL patterns for the routes app."""
from django.urls import path

from .views import account_routes, remove_account_from_route, route_list, route_save

urlpatterns = [
    path('', route_list, name='route_list'),
    path('save/', route_save, name='route_save'),
    path('account/<int:account_pk>/', account_routes, name='account_routes'),
    path('remove/<int:route_account_pk>/', remove_account_from_route, name='remove_account_from_route'),
]
