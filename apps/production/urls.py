from django.urls import path
from . import views

urlpatterns = [
    path('production/', views.production_home, name='production_home'),
    path('production/inventory/upload/', views.production_inventory_upload, name='production_inventory_upload'),
    path('production/inventory/snapshots/', views.production_inventory_snapshots, name='production_inventory_snapshots'),
    path('production/inventory/delete/', views.production_inventory_bulk_delete, name='production_inventory_bulk_delete'),
    path('production/demand/<int:year>/<int:month>/', views.production_demand_modal, name='production_demand_modal'),
    # Phase D — Production PO modal endpoints
    path('production/po/save/', views.production_po_save, name='production_po_save'),
    path('production/po/<int:po_pk>/delete/', views.production_po_delete, name='production_po_delete'),
    path('production/po/<int:year>/<int:month>/', views.production_po_modal_data, name='production_po_modal_data'),
]
