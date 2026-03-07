from django.urls import path
from . import views, account_import_views

urlpatterns = [
    # Account Import (Phase 10.6)
    path('imports/accounts/upload/',  account_import_views.account_import_upload,  name='account_import_upload'),
    path('imports/accounts/preview/', account_import_views.account_import_preview, name='account_import_preview'),
    path('imports/accounts/execute/', account_import_views.account_import_execute, name='account_import_execute'),

    # Sales Data Import (two-step: upload → preview → success)
    path('imports/upload/', views.import_upload, name='import_upload'),
    path('imports/preview/', views.import_preview, name='import_preview'),
    path('imports/success/<int:batch_pk>/', views.import_success, name='import_success'),

    # Item Mapping
    path('imports/item-mappings/', views.mapping_list, name='mapping_list'),
    path('imports/item-mappings/create/', views.mapping_create, name='mapping_create'),
    path('imports/item-mappings/<int:pk>/edit/', views.mapping_edit, name='mapping_edit'),

    # Batch History
    path('imports/batches/', views.batch_list, name='batch_list'),
    path('imports/batches/<int:pk>/', views.batch_detail, name='batch_detail'),
    path('imports/batches/<int:pk>/delete/', views.batch_delete, name='batch_delete'),
]
