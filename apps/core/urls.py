from django.urls import path
from . import views

urlpatterns = [
    # Auth
    path('login/', views.login_view, name='login'),
    path('logout/', views.logout_view, name='logout'),
    path('password-reset/', views.password_reset_stub, name='password_reset_stub'),

    # Dashboard (root)
    path('', views.dashboard, name='dashboard'),

    # User management
    path('users/', views.user_list, name='user_list'),
    path('users/create/', views.user_create, name='user_create'),
    path('users/<int:pk>/edit/', views.user_edit, name='user_edit'),
    path('users/<int:pk>/deactivate/', views.user_deactivate, name='user_deactivate'),
    path('users/<int:pk>/password/', views.user_password_reset, name='user_password_reset'),

    # My profile
    path('profile/', views.profile, name='profile'),
    path('profile/edit/', views.profile_edit, name='profile_edit'),
    path('profile/password/', views.password_change, name='password_change'),

    # Access denied
    path('access-denied/', views.access_denied, name='access_denied'),

    # UI state persistence
    path('ui/admin-tools-state/', views.save_admin_tools_state, name='save_admin_tools_state'),
]
