"""URL patterns for the accounts app."""
from django.urls import path

from . import views

urlpatterns = [
    path('accounts/', views.account_list, name='account_list'),
    path('accounts/create/', views.account_create, name='account_create'),
    path('accounts/<int:pk>/', views.account_detail, name='account_detail'),
    path('accounts/<int:pk>/edit/', views.account_edit, name='account_edit'),
    path('accounts/<int:pk>/toggle/', views.account_toggle, name='account_toggle'),
]
