from django.urls import path
from . import views

urlpatterns = [
    path('production/', views.production_home, name='production_home'),
    path('production/inventory/upload/', views.production_inventory_upload, name='production_inventory_upload'),
    path('production/inventory/snapshots/', views.production_inventory_snapshots, name='production_inventory_snapshots'),
    path('production/inventory/delete/', views.production_inventory_bulk_delete, name='production_inventory_bulk_delete'),
    path('production/demand/<int:year>/<int:month>/', views.production_demand_modal, name='production_demand_modal'),
]
