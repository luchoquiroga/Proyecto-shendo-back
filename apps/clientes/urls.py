from django.urls import path
from .views import LoginView, MenuView, MisPedidosView, PerfilView, RefreshView, RegistroView, RestaurantesView

# Montado en /api/cliente/ (config/urls.py)
urlpatterns = [
    path('registro/', RegistroView.as_view(), name='cliente_registro'),
    path('login/', LoginView.as_view(), name='cliente_login'),
    path('token/refresh/', RefreshView.as_view(), name='cliente_token_refresh'),
    path('perfil/', PerfilView.as_view(), name='cliente_perfil'),
    path('restaurantes/', RestaurantesView.as_view(), name='cliente_restaurantes'),
    path('restaurantes/<int:id_restaurante>/productos/', MenuView.as_view(), name='cliente_menu'),
    path('pedidos/', MisPedidosView.as_view(), name='cliente_pedidos'),
]
