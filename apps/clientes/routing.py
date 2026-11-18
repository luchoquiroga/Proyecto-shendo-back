from django.urls import path

from .avisos import ClienteConsumer, RepartidorConsumer, RestauranteConsumer

websocket_urlpatterns = [
    path('ws/cliente/', ClienteConsumer.as_asgi()),
    path('ws/repartidor/', RepartidorConsumer.as_asgi()),
    path('ws/restaurante/', RestauranteConsumer.as_asgi()),
]
