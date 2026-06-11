from django.urls import path
from . import views

urlpatterns = [
    # Distributor Groups
    path('distributor-groups/', views.distributor_group_list, name='distributor_group_list'),
    path('distributor-groups/create/', views.distributor_group_create, name='distributor_group_create'),
    path('distributor-groups/<int:pk>/edit/', views.distributor_group_edit, name='distributor_group_edit'),
    path('distributor-groups/<int:pk>/delete/', views.distributor_group_delete, name='distributor_group_delete'),
    # Distributors
    path('distributors/', views.distributor_list, name='distributor_list'),
    path('distributors/create/', views.distributor_create, name='distributor_create'),
    # Inventory snapshot import (Phase 2b-1) — static paths before <int:pk>
    path('distributors/inventory/upload/', views.inventory_upload, name='inventory_upload'),
    path('distributors/inventory/preview/', views.inventory_preview, name='inventory_preview'),
    path('distributors/inventory/confirm/', views.inventory_confirm, name='inventory_confirm'),
    path('distributors/inventory/delete/', views.inventory_bulk_delete, name='inventory_bulk_delete'),
    # Group PO endpoints (back the Forecast-tab #poModal in group mode) —
    # static sub-paths before <int:pk>. The standalone group forecast page was
    # retired; the group forecast renders in the Forecast tab (?forecast_group=N).
    path('distributors/group/<int:group_pk>/orders/save/',
         views.distributor_group_po_save, name='distributor_group_po_save'),
    path('distributors/group/<int:group_pk>/orders/<int:year>/<int:month>/',
         views.distributor_group_orders_modal_data, name='distributor_group_orders_modal_data'),
    path('distributors/group/<int:group_pk>/orders/<int:year>/<int:month>/suggest/',
         views.distributor_group_po_suggest, name='distributor_group_po_suggest'),
    # Inventory projection tool (Distributor POs tab) — static paths before <int:dist_pk>
    path('distributors/forecast-inventory/save/', views.save_forecast_inventory, name='save_forecast_inventory'),
    path('distributors/po/toggle-selection/', views.toggle_po_selection, name='toggle_po_selection'),
    path('distributors/po/bulk-toggle-selection/', views.bulk_toggle_po_selection, name='bulk_toggle_po_selection'),
    path('distributors/po/move/', views.move_distributor_po, name='move_distributor_po'),
    # PO modal endpoints (Phase 4-step-2b) — static sub-paths before <int:pk>
    path('distributors/<int:dist_pk>/po/<int:year>/<int:month>/',
         views.distributor_po_modal_data, name='distributor_po_modal_data'),
    path('distributors/<int:dist_pk>/po/<int:year>/<int:month>/suggest/',
         views.distributor_po_suggest, name='distributor_po_suggest'),
    path('distributors/<int:dist_pk>/po/save/',
         views.distributor_po_save, name='distributor_po_save'),
    path('distributors/<int:dist_pk>/po/<int:po_pk>/delete/',
         views.distributor_po_delete, name='distributor_po_delete'),
    # Distributor CRUD
    path('distributors/<int:pk>/', views.distributor_detail, name='distributor_detail'),
    path('distributors/<int:pk>/edit/', views.distributor_edit, name='distributor_edit'),
    path('distributors/<int:pk>/toggle/', views.distributor_toggle, name='distributor_toggle'),
    path('distributors/<int:pk>/order-profile/', views.distributor_order_profile_save, name='distributor_order_profile_save'),
    path('distributors/<int:pk>/safety-stock/', views.distributor_safety_stock_save, name='distributor_safety_stock_save'),
]
