"""URL patterns for the reports app."""
from django.urls import path

from .views import (
    account_detail_sales,
    account_sales_by_year,
    account_sales_by_year_csv,
    distributor_select_view,
)

urlpatterns = [
    path('', account_sales_by_year, name='report_account_sales_by_year'),
    path('distributor-select/', distributor_select_view, name='report_account_sales_distributor_select'),
    path('export/', account_sales_by_year_csv, name='report_account_sales_csv'),
    path('account/<int:account_id>/', account_detail_sales, name='report_account_detail'),
]
