from django.urls import path
from . import views

urlpatterns = [
    path('production/', views.production_home, name='production_home'),
]
