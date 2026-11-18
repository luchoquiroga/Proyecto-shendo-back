# Avisos en tiempo real por WebSocket. El socket solo avisa "cambió el pedido N": la app o el
# panel recargan por la API REST, que sigue siendo la única fuente de datos.
import logging
from urllib.parse import parse_qs

from asgiref.sync import async_to_sync
from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncJsonWebsocketConsumer
from channels.layers import get_channel_layer
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.tokens import AccessToken

from apps.core.models import Repartidor
from .auth import ClienteAccessToken, usuario_vigente

logger = logging.getLogger(__name__)

GRUPO_REPARTIDORES = 'repartidores'
NO_AUTORIZADO = 4401


def grupo_cliente(id_usuario):
    return f'cliente_{id_usuario}'


def grupo_repartidor(id_repartidor):
    return f'repartidor_{id_repartidor}'


def grupo_restaurante(id_restaurante):
    return f'restaurante_{id_restaurante}'


def avisar_pedido(pedido, *, repartidores=False, tambien_repartidor=None):
    """Avisa a quienes miran el pedido. repartidores=True cuando cambia la lista de disponibles;
    tambien_repartidor = id de un repartidor que ya no está asignado (soltó el pedido) pero tiene que enterarse.
    Nunca rompe la request: si falla, el polling de la app lo cubre."""
    grupos = set()
    if pedido.id_usuario_id:
        grupos.add(grupo_cliente(pedido.id_usuario_id))
    if pedido.id_restaurante_id:
        grupos.add(grupo_restaurante(pedido.id_restaurante_id))
    for id_rep in (pedido.id_repartidor_id, tambien_repartidor):
        if id_rep:
            grupos.add(grupo_repartidor(id_rep))
    if repartidores:
        grupos.add(GRUPO_REPARTIDORES)
    try:
        enviar = async_to_sync(get_channel_layer().group_send)
        for grupo in grupos:
            enviar(grupo, {'type': 'pedido', 'id_pedido': pedido.id_pedido})
    except Exception:
        logger.exception('No se pudo avisar el pedido %s', pedido.id_pedido)


class AvisosConsumer(AsyncJsonWebsocketConsumer):
    """Solo de salida. El token va en la URL (?token=): el navegador no puede mandar headers en un WebSocket."""

    async def connect(self):
        token = parse_qs(self.scope['query_string'].decode()).get('token', [''])[0]
        self.grupos = await self.grupos_para(token) if token else None
        if not self.grupos:
            await self.close(code=NO_AUTORIZADO)
            return
        for grupo in self.grupos:
            await self.channel_layer.group_add(grupo, self.channel_name)
        await self.accept()

    async def disconnect(self, code):
        for grupo in getattr(self, 'grupos', None) or []:
            await self.channel_layer.group_discard(grupo, self.channel_name)

    async def receive_json(self, content, **kwargs):
        pass  # lo que mande el cliente se ignora

    async def pedido(self, event):
        await self.send_json({'tipo': 'pedido', 'id_pedido': event['id_pedido']})

    async def grupos_para(self, token):
        raise NotImplementedError


def id_usuario_cliente(token):
    try:
        t = ClienteAccessToken(token)
    except TokenError:
        return None
    return usuario_vigente(t) if t.get('tipo') == 'cliente' else None


class ClienteConsumer(AvisosConsumer):

    @database_sync_to_async
    def grupos_para(self, token):
        id_usuario = id_usuario_cliente(token)
        return [grupo_cliente(id_usuario)] if id_usuario else None


class RepartidorConsumer(AvisosConsumer):

    @database_sync_to_async
    def grupos_para(self, token):
        id_usuario = id_usuario_cliente(token)
        rep = Repartidor.objects.filter(id_usuario=id_usuario).first() if id_usuario else None
        return [grupo_repartidor(rep.id_repartidor), GRUPO_REPARTIDORES] if rep else None


class RestauranteConsumer(AvisosConsumer):

    async def grupos_para(self, token):
        try:
            id_restaurante = AccessToken(token).get('id_restaurante')
        except TokenError:
            return None
        return [grupo_restaurante(id_restaurante)] if id_restaurante else None
