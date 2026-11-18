# Avisos por WebSocket: quién puede conectarse y quién recibe cada aviso.
from datetime import timedelta
from unittest.mock import patch

from asgiref.sync import sync_to_async
from channels.testing import WebsocketCommunicator
from django.test import TransactionTestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.clientes.auth import ClienteRefreshToken, tokens
from apps.core.models import Pedidos, Productos, Repartidor, Restaurantes, Usuarios
from config.asgi import application

HASH_SECRETO = 'pbkdf2_sha256$1000$abcdefghijklmnopqrstuv$PpVBcVFaPEauYjw1N/ZRHhsUgyj6j6rR3geTJGtq/UU='


def cliente_http(token):
    c = APIClient()
    c.credentials(HTTP_AUTHORIZATION='Bearer ' + token)
    return c


class AvisosBase(TransactionTestCase):

    def setUp(self):
        self.resto = Restaurantes.objects.create(nombre='Pizzería')
        self.otro_resto = Restaurantes.objects.create(nombre='Otro')
        self.muzza = Productos.objects.create(nombre='Muzza', precio=1000, stock=None, disponible=True,
                                              id_restaurante=self.resto)
        self.ana = self.usuario('ana@mail.com')
        self.beto = self.usuario('beto@mail.com')
        self.juan = self.usuario('juan@mail.com')
        self.rep_juan = Repartidor.objects.create(nombre='Juan', disponible=True, id_usuario=self.juan)
        self.pepe = self.usuario('pepe@mail.com')
        self.rep_pepe = Repartidor.objects.create(nombre='Pepe', disponible=True, id_usuario=self.pepe)
        Usuarios.objects.create(nombre='admin', email='admin@a.com', telefono='1', contrasena=HASH_SECRETO,
                                rol=True, id_restaurante=self.resto)
        self.token_panel = APIClient().post('/api/login/', {'nombre': 'admin', 'contrasena': 'secreto123'},
                                            format='json').data['access']

    def usuario(self, email):
        return Usuarios.objects.create(nombre=email.split('@')[0], email=email, telefono='1155556666',
                                       contrasena=HASH_SECRETO, rol=False)

    def access(self, usuario):
        return tokens(usuario)['access']

    async def conectar(self, ruta, token):
        com = WebsocketCommunicator(application, f'/ws/{ruta}/' + (f'?token={token}' if token else ''))
        conectado, codigo = await com.connect()
        return com, conectado, codigo

    async def rechazado(self, ruta, token):
        com, conectado, codigo = await self.conectar(ruta, token)
        self.assertFalse(conectado)
        self.assertEqual(codigo, 4401)

    def pedido(self, usuario=None, repartidor=None, estado='pendiente'):
        return Pedidos.objects.create(fecha=timezone.now(), estado=estado, total=1000, direccion_entega='Calle 1',
                                      tipo_entrega='domicilio', id_usuario=usuario or self.ana,
                                      id_restaurante=self.resto, id_repartidor=repartidor)


class ConexionTest(AvisosBase):

    async def test_cada_uno_conecta_con_su_token(self):
        for ruta, token in [('cliente', self.access(self.ana)), ('repartidor', self.access(self.juan)),
                            ('restaurante', self.token_panel)]:
            com, conectado, _ = await self.conectar(ruta, token)
            self.assertTrue(conectado, ruta)
            await com.disconnect()

    async def test_sin_token_o_basura_se_rechaza(self):
        for ruta in ('cliente', 'repartidor', 'restaurante'):
            await self.rechazado(ruta, None)
            await self.rechazado(ruta, 'basura')

    async def test_tokens_cruzados_se_rechazan(self):
        await self.rechazado('cliente', self.token_panel)
        await self.rechazado('repartidor', self.token_panel)
        await self.rechazado('restaurante', self.access(self.ana))

    async def test_cliente_no_entra_a_ws_repartidor(self):
        await self.rechazado('repartidor', self.access(self.ana))

    async def test_token_vencido_se_rechaza_al_conectar(self):
        refresh = await sync_to_async(lambda: tokens(self.ana)['refresh'])()
        vencido = ClienteRefreshToken(refresh).access_token
        vencido.set_exp(lifetime=-timedelta(seconds=1))
        await self.rechazado('cliente', str(vencido))

    async def test_cuenta_con_contrasena_cambiada_se_rechaza(self):
        token = self.access(self.ana)
        await sync_to_async(Usuarios.objects.filter(pk=self.ana.pk).update)(contrasena='otro-hash')
        await self.rechazado('cliente', token)

    async def test_lo_que_manda_el_cliente_se_ignora(self):
        com, conectado, _ = await self.conectar('cliente', self.access(self.ana))
        self.assertTrue(conectado)
        await com.send_json_to({'tipo': 'pedido', 'id_pedido': 1})
        self.assertTrue(await com.receive_nothing())
        await com.disconnect()


class RuteoTest(AvisosBase):

    async def conectar_todos(self):
        self.c_ana, *_ = await self.conectar('cliente', self.access(self.ana))
        self.c_beto, *_ = await self.conectar('cliente', self.access(self.beto))
        self.c_juan, *_ = await self.conectar('repartidor', self.access(self.juan))
        self.c_pepe, *_ = await self.conectar('repartidor', self.access(self.pepe))
        self.c_resto, *_ = await self.conectar('restaurante', self.token_panel)

    async def desconectar_todos(self):
        for c in (self.c_ana, self.c_beto, self.c_juan, self.c_pepe, self.c_resto):
            await c.disconnect()

    async def recibe(self, com, id_pedido):
        self.assertEqual(await com.receive_json_from(timeout=1), {'tipo': 'pedido', 'id_pedido': id_pedido})

    async def nada(self, *coms):
        for com in coms:
            self.assertTrue(await com.receive_nothing(timeout=0.2))

    async def http(self, metodo, token, ruta, body=None):
        c = cliente_http(token)
        return await sync_to_async(getattr(c, metodo))(ruta, body, format='json')

    async def test_pedido_nuevo_avisa_al_cliente_restaurante_y_repartidores(self):
        await self.conectar_todos()
        r = await self.http('post', self.access(self.ana), '/api/cliente/pedidos/', {
            'id_restaurante': self.resto.pk, 'tipo_entrega': 'domicilio', 'direccion_entrega': 'Calle 1',
            'metodo_pago': 'efectivo', 'items': [{'id_producto': self.muzza.pk, 'cantidad': 1}]})
        self.assertEqual(r.status_code, 201, r.data)
        for com in (self.c_ana, self.c_resto, self.c_juan, self.c_pepe):
            await self.recibe(com, r.data['id_pedido'])
        await self.nada(self.c_beto)
        await self.desconectar_todos()

    async def test_panel_cambia_estado_avisa_al_cliente_y_repartidores(self):
        p = await sync_to_async(self.pedido)()
        await self.conectar_todos()
        r = await self.http('put', self.token_panel, f'/api/orders/{p.pk}/', {'estado': 'preparando'})
        self.assertEqual(r.status_code, 200)
        for com in (self.c_ana, self.c_resto, self.c_juan, self.c_pepe):
            await self.recibe(com, p.pk)
        await self.nada(self.c_beto)
        await self.desconectar_todos()

    async def test_tomar_avisa_al_cliente_y_a_los_repartidores(self):
        p = await sync_to_async(self.pedido)()
        await self.conectar_todos()
        r = await self.http('post', self.access(self.juan), f'/api/repartidor/pedidos/{p.pk}/tomar/')
        self.assertEqual(r.status_code, 200)
        for com in (self.c_ana, self.c_resto, self.c_juan, self.c_pepe):
            await self.recibe(com, p.pk)
        await self.desconectar_todos()

    async def test_soltar_avisa_al_que_lo_solto_y_a_los_repartidores(self):
        p = await sync_to_async(self.pedido)(repartidor=self.rep_juan)
        await self.conectar_todos()
        r = await self.http('post', self.access(self.juan), f'/api/repartidor/pedidos/{p.pk}/soltar/')
        self.assertEqual(r.status_code, 204)
        for com in (self.c_ana, self.c_resto, self.c_pepe):
            await self.recibe(com, p.pk)
        # Juan está en "repartidores" y en su grupo propio: le llega (al menos) un aviso.
        await self.recibe(self.c_juan, p.pk)
        await self.desconectar_todos()

    async def test_en_camino_y_entregado_no_avisan_a_los_demas_repartidores(self):
        p = await sync_to_async(self.pedido)(repartidor=self.rep_juan)
        await self.conectar_todos()
        for estado in ('en camino', 'entregado'):
            r = await self.http('post', self.access(self.juan), f'/api/repartidor/pedidos/{p.pk}/estado/',
                                {'estado': estado})
            self.assertEqual(r.status_code, 200, r.data)
            for com in (self.c_ana, self.c_resto, self.c_juan):
                await self.recibe(com, p.pk)
            await self.nada(self.c_pepe, self.c_beto)
        await self.desconectar_todos()

    async def test_cliente_no_recibe_avisos_de_pedidos_ajenos(self):
        p = await sync_to_async(self.pedido)(usuario=self.beto)
        await self.conectar_todos()
        await self.http('put', self.token_panel, f'/api/orders/{p.pk}/', {'estado': 'listo'})
        await self.recibe(self.c_beto, p.pk)
        await self.nada(self.c_ana)
        await self.desconectar_todos()

    def test_si_falla_el_aviso_la_request_igual_responde(self):
        with patch('apps.clientes.avisos.get_channel_layer', side_effect=RuntimeError('caído')):
            r = cliente_http(self.access(self.ana)).post('/api/cliente/pedidos/', {
                'id_restaurante': self.resto.pk, 'tipo_entrega': 'domicilio', 'direccion_entrega': 'Calle 1',
                'metodo_pago': 'efectivo', 'items': [{'id_producto': self.muzza.pk, 'cantidad': 1}]}, format='json')
        self.assertEqual(r.status_code, 201, r.data)
