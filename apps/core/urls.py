from django.urls import path
from rest_framework_simplejwt.views import TokenRefreshView
from .views import ProductosView, LoginView, PedidosView, CategoriasView, CategoriaDetailView, RestauranteView

urlpatterns = [
    path('login/', LoginView.as_view(), name='login_api'),
    # Body {"refresh": "..."} -> {"access": "..."}. El access del panel dura 5 minutos.
    path('token/refresh/', TokenRefreshView.as_view(), name='token_refresh'),
    path('products/', ProductosView.as_view(), name='productos_api'),
    path('restaurante/', RestauranteView.as_view(), name='restaurante_perfil'),
    path('orders/', PedidosView.as_view(), name='pedidos_api'),
    path('orders/<int:pedido_id>/', PedidosView.as_view(), name='pedido_update'),
    path("categorias/", CategoriasView.as_view()),
    path( 'categorias/<int:categoria_id>/',  CategoriaDetailView.as_view(), name='categoria_detail' ),
]