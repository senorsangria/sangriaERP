from django.urls import path
from . import views

urlpatterns = [
    # Brands
    path('brands/', views.brand_list, name='brand_list'),
    path('brands/create/', views.brand_create, name='brand_create'),
    path('brands/<int:pk>/', views.brand_detail, name='brand_detail'),
    path('brands/<int:pk>/edit/', views.brand_edit, name='brand_edit'),
    path('brands/<int:pk>/toggle/', views.brand_toggle, name='brand_toggle'),

    # Items (nested under brand)
    path('brands/<int:brand_pk>/items/create/', views.item_create, name='item_create'),
    path('brands/<int:brand_pk>/items/<int:pk>/edit/', views.item_edit, name='item_edit'),
    path('brands/<int:brand_pk>/items/<int:pk>/toggle/', views.item_toggle, name='item_toggle'),

    # Item sort order AJAX
    path('brands/<int:brand_pk>/items/<int:pk>/move-up/', views.item_move_up, name='item_move_up'),
    path('brands/<int:brand_pk>/items/<int:pk>/move-down/', views.item_move_down, name='item_move_down'),
]
