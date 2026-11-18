from django.urls import path
from .repartidor import DisponiblesView, EstadoPedidoView, EstadoView, MisEntregasView, SoltarView, TomarView

# Montado en /api/repartidor/ (config/urls.py)
urlpatterns = [
    path('estado/', EstadoView.as_view(), name='repartidor_estado'),
    path('disponibles/', DisponiblesView.as_view(), name='repartidor_disponibles'),
    path('pedidos/', MisEntregasView.as_view(), name='repartidor_pedidos'),
    path('pedidos/<int:id_pedido>/tomar/', TomarView.as_view(), name='repartidor_tomar'),
    path('pedidos/<int:id_pedido>/soltar/', SoltarView.as_view(), name='repartidor_soltar'),
    path('pedidos/<int:id_pedido>/estado/', EstadoPedidoView.as_view(), name='repartidor_estado_pedido'),
]
