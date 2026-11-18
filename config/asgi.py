"""ASGI: HTTP (Django) + WebSocket (avisos de pedidos, ver apps/clientes/avisos.py)."""
import os

from django.core.asgi import get_asgi_application

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django_asgi = get_asgi_application()  # antes de importar código que usa modelos

from channels.routing import ProtocolTypeRouter, URLRouter  # noqa: E402

from apps.clientes.routing import websocket_urlpatterns  # noqa: E402

application = ProtocolTypeRouter({
    'http': django_asgi,
    'websocket': URLRouter(websocket_urlpatterns),
})
