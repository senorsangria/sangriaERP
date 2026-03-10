"""URL patterns for the reports app."""
from django.urls import path

from .views import account_sales_by_year, distributor_select_view

urlpatterns = [
    path('', account_sales_by_year, name='report_account_sales_by_year'),
    path('distributor-select/', distributor_select_view, name='report_account_sales_distributor_select'),
]
