from django.urls import path
from . import views

urlpatterns = [
    path('distributors/', views.distributor_list, name='distributor_list'),
    path('distributors/create/', views.distributor_create, name='distributor_create'),
    path('distributors/<int:pk>/', views.distributor_detail, name='distributor_detail'),
    path('distributors/<int:pk>/edit/', views.distributor_edit, name='distributor_edit'),
    path('distributors/<int:pk>/toggle/', views.distributor_toggle, name='distributor_toggle'),
]
