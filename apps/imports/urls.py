from django.urls import path
from . import views

urlpatterns = [
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
