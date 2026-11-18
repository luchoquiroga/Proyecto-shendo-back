# python manage.py test apps.clientes.test_adversarial --settings=config.settings_test
#
# Tests adversariales (seguridad) sobre cliente, repartidor y panel.
# Los tests marcados con @unittest.expectedFailure documentan bugs reales (ver "# BUG:").
import base64
import json
import tempfile
import unittest
from datetime import timedelta
from unittest.mock import patch

import jwt
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APITestCase
from rest_framework.throttling import ScopedRateThrottle
from rest_framework_simplejwt.tokens import AccessToken, RefreshToken

from apps.clientes.auth import ClienteAccessToken, ClienteRefreshToken
from apps.core.models import (Categoria, DetallePedido, Pagos, Pedidos, Productos, Repartidor, Restaurantes,
                              Usuarios)

HASH_SECRETO = 'pbkdf2_sha256$1000$abcdefghijklmnopqrstuv$PpVBcVFaPEauYjw1N/ZRHhsUgyj6j6rR3geTJGtq/UU='
C = '/api/cliente/'
R = '/api/repartidor/'
P = '/api/'
MODELOS = [Usuarios, Repartidor, Restaurantes, Productos, Categoria, Pedidos, DetallePedido, Pagos]

# Cuerpos JSON que no son un objeto.
NO_OBJETO = [[], [1, 2], 'texto', 5, True, None]


# ---------- helpers de tokens ----------

def b64(d):
    return base64.urlsafe_b64encode(json.dumps(d, separators=(',', ':')).encode()).rstrip(b'=').decode()


def payload_de(token):
    p = token.split('.')[1]
    return json.loads(base64.urlsafe_b64decode(p + '=' * (-len(p) % 4)))


def alterado(token, **cambios):
    """Cambia el payload sin re-firmar."""
    h, _, s = token.split('.')
    return h + '.' + b64({**payload_de(token), **cambios}) + '.' + s


def sin_firma(token):
    """Mismo payload, alg none."""
    return b64({'alg': 'none', 'typ': 'JWT'}) + '.' + b64(payload_de(token)) + '.'


def firmado_con_otra_clave(token):
    return jwt.encode(payload_de(token), 'otra-clave-que-no-es-la-del-servidor-1234567890', algorithm='HS256')


def cliente_vencido(id_usuario):
    t = ClienteAccessToken()
    t['tipo'] = 'cliente'
    t['id_usuario'] = id_usuario
    t.set_exp(lifetime=-timedelta(seconds=30))
    return str(t)


def panel_vencido(id_restaurante, id_usuario=1):
    t = AccessToken()
    t['id_usuario'] = id_usuario
    t['id_restaurante'] = id_restaurante
    t.set_exp(lifetime=-timedelta(seconds=30))
    return str(t)


class Base(APITestCase):

    def setUp(self):
        self.client.raise_request_exception = False  # un 500 se ve como 500, no como excepción
        cache.clear()
        self.addCleanup(cache.clear)
        self.rest_a = Restaurantes.objects.create(nombre='A', direccion='Av A', telefono='111', costo_envio=800.0)
        self.rest_b = Restaurantes.objects.create(nombre='B', direccion='Av B', telefono='222', costo_envio=100.0)
        self.muzza = Productos.objects.create(nombre='Muzza', precio=1000.0, stock=5, disponible=True,
                                              id_restaurante=self.rest_a)
        self.coca = Productos.objects.create(nombre='Coca', precio=500.0, stock=None, disponible=True,
                                             id_restaurante=self.rest_a)
        self.de_b = Productos.objects.create(nombre='De B', precio=700.0, stock=9, disponible=True,
                                             id_restaurante=self.rest_b)
        self.cat_a = Categoria.objects.create(nombre='Pizzas', id_restaurante=self.rest_a)
        self.cat_b = Categoria.objects.create(nombre='Ajena', id_restaurante=self.rest_b)
        self.ana = self.usuario('ana@mail.com', 'Ana')
        self.beto = self.usuario('beto@mail.com', 'Beto')
        self.admin_a = self.usuario('aa@mail.com', 'admin_a', rol=True, rest=self.rest_a)
        self.admin_b = self.usuario('ab@mail.com', 'admin_b', rol=True, rest=self.rest_b)

    # --- datos ---
    def usuario(self, email, nombre, rol=False, rest=None):
        return Usuarios.objects.create(nombre=nombre, email=email, telefono='1155556666', direccion='Calle 1',
                                       contrasena=HASH_SECRETO, rol=rol, id_restaurante=rest)

    def pedido(self, usuario=None, rest=None, estado='pendiente', tipo='domicilio', repartidor=None):
        p = Pedidos.objects.create(fecha=timezone.now(), estado=estado, total=1500, direccion_entega='Calle Secreta 9',
                                   tipo_entrega=tipo, id_usuario=usuario or self.ana,
                                   id_restaurante=rest or self.rest_a, id_repartidor=repartidor)
        Pagos.objects.create(id_pedido=p, metodo_pago='efectivo', monto=1500, estado_pago=False)
        return p

    def foto(self):
        return {m.__name__: list(m.objects.order_by('pk').values()) for m in MODELOS}

    # --- sesiones ---
    def anon(self):
        self.client.credentials()

    def con(self, token):
        self.client.credentials(HTTP_AUTHORIZATION='Bearer ' + token)

    def login_cliente(self, email='ana@mail.com'):
        r = self.client.post(C + 'login/', {'email': email, 'contrasena': 'secreto123'}, format='json')
        self.assertEqual(r.status_code, 200, r.content)
        return r.data

    def entrar_cliente(self, email='ana@mail.com'):
        d = self.login_cliente(email)
        self.con(d['access'])
        return d

    def login_panel(self, nombre='admin_a'):
        r = self.client.post(P + 'login/', {'nombre': nombre, 'contrasena': 'secreto123'}, format='json')
        self.assertEqual(r.status_code, 200, r.content)
        return r.data

    def entrar_panel(self, nombre='admin_a'):
        d = self.login_panel(nombre)
        self.con(d['access'])
        return d

    def nuevo_repartidor(self, email='juan@mail.com', disponible=True):
        """Registra un repartidor por la API y deja sus credenciales puestas."""
        r = self.client.post(C + 'registro/', {'nombre': 'Juan', 'email': email, 'telefono': '1144443333',
                                               'contrasena': 'secreto123', 'repartidor': True, 'vehiculo': 'Moto'},
                             format='json')
        self.assertEqual(r.status_code, 201, r.content)
        self.con(r.data['access'])
        if disponible:
            self.assertEqual(self.client.patch(R + 'estado/', {'disponible': True}, format='json').status_code, 200)
        return r.data

    def assertNo500(self, r, contexto=''):
        self.assertLess(r.status_code, 500, f'{contexto}: {r.status_code} {r.content[:200]}')

    def assertSinSecretos(self, r):
        texto = r.content.decode().lower()
        for prohibido in ('contrasena', 'pbkdf2', 'secreto123'):
            self.assertNotIn(prohibido, texto)


# =====================================================================
# 1. Confusión de tokens
# =====================================================================

class ConfusionDeTokensTest(Base):

    CLIENTE_GET = [C + 'pedidos/', C + 'perfil/', R + 'estado/', R + 'disponibles/', R + 'pedidos/']

    def test_token_del_panel_no_sirve_en_cliente_ni_repartidor(self):
        access = self.login_panel()['access']
        self.con(access)
        antes = self.foto()
        for url in self.CLIENTE_GET:
            self.assertEqual(self.client.get(url).status_code, 401, url)
        self.assertEqual(self.client.patch(C + 'perfil/', {'nombre': 'Hack'}, format='json').status_code, 401)
        r = self.client.post(C + 'pedidos/', {'id_restaurante': self.rest_a.pk}, format='json')
        self.assertEqual(r.status_code, 401)
        self.assertEqual(self.client.post(R + f'pedidos/{self.pedido().pk}/tomar/').status_code, 401)
        antes['Pedidos'] = list(Pedidos.objects.order_by('pk').values())
        antes['Pagos'] = list(Pagos.objects.order_by('pk').values())
        self.assertEqual(self.foto(), antes)

    def test_token_del_cliente_no_sirve_en_el_panel(self):
        self.con(self.login_cliente()['access'])
        antes = self.foto()
        for url in [P + 'orders/', P + 'products/', P + 'categorias/', P + 'restaurante/']:
            self.assertEqual(self.client.get(url).status_code, 401, url)
        self.assertEqual(self.client.post(P + 'products/', {'nombre': 'X', 'precio': 1}, format='json').status_code, 401)
        self.assertEqual(self.client.post(P + 'categorias/', {'nombre': 'X'}, format='json').status_code, 401)
        self.assertEqual(self.client.patch(P + 'restaurante/', {'nombre': 'X'}, format='json').status_code, 401)
        p = self.pedido()
        self.assertEqual(self.client.put(P + f'orders/{p.pk}/', {'estado': 'x'}, format='json').status_code, 401)
        self.assertEqual(self.client.put(P + f'categorias/{self.cat_a.pk}/', {'nombre': 'x'}, format='json').status_code, 401)
        self.assertEqual(self.client.delete(P + f'categorias/{self.cat_a.pk}/').status_code, 401)
        self.assertEqual(Pedidos.objects.get(pk=p.pk).estado, 'pendiente')
        antes['Pedidos'] = self.foto()['Pedidos']
        antes['Pagos'] = self.foto()['Pagos']
        self.assertEqual(self.foto(), antes)

    def test_token_de_cliente_con_id_restaurante_inyectado_en_el_payload_no_se_acepta(self):
        # Alterar el payload (agregar id_restaurante) rompe la firma.
        t = alterado(self.login_cliente()['access'], id_restaurante=self.rest_a.pk)
        self.con(t)
        self.assertIn(self.client.get(P + 'orders/').status_code, (401, 403))

    def test_refresh_usado_como_access(self):
        d = self.login_cliente()
        panel = self.login_panel()
        for token in (d['refresh'], panel['refresh']):
            self.con(token)
            for url in [C + 'pedidos/', C + 'perfil/', R + 'estado/', P + 'orders/', P + 'products/', P + 'restaurante/']:
                r = self.client.get(url)
                self.assertIn(r.status_code, (401, 403), url)
                self.assertNotEqual(r.status_code, 200, url)

    def test_access_usado_como_refresh(self):
        self.anon()
        d = self.login_cliente()
        panel = self.login_panel()
        for url, token in [(C + 'token/refresh/', d['access']), (C + 'token/refresh/', panel['access']),
                           (P + 'token/refresh/', panel['access']), (P + 'token/refresh/', d['access'])]:
            r = self.client.post(url, {'refresh': token}, format='json')
            self.assertEqual(r.status_code, 401, (url, r.content))
            self.assertNotIn('access', r.data)

    def test_refresh_del_panel_no_renueva_cliente(self):
        panel = self.login_panel()
        r = self.client.post(C + 'token/refresh/', {'refresh': panel['refresh']}, format='json')
        self.assertEqual(r.status_code, 401)

    def test_refresh_de_cliente_en_refresh_del_panel_no_da_acceso_al_panel(self):
        d = self.login_cliente()
        r = self.client.post(P + 'token/refresh/', {'refresh': d['refresh']}, format='json')
        if r.status_code == 200:
            self.con(r.data['access'])
            self.assertEqual(self.client.get(P + 'orders/').status_code, 401)
            self.assertEqual(self.client.get(P + 'restaurante/').status_code, 401)
        else:
            self.assertIn(r.status_code, (400, 401))

    def test_firma_invalida(self):
        d, panel = self.login_cliente(), self.login_panel()
        for url, token in [(C + 'pedidos/', firmado_con_otra_clave(d['access'])),
                           (R + 'estado/', firmado_con_otra_clave(d['access'])),
                           (P + 'orders/', firmado_con_otra_clave(panel['access'])),
                           (P + 'restaurante/', firmado_con_otra_clave(panel['access']))]:
            self.con(token)
            self.assertIn(self.client.get(url).status_code, (401, 403), url)
            self.assertNotEqual(self.client.get(url).status_code, 200, url)

    def test_payload_alterado_sin_refirmar(self):
        d = self.login_cliente('ana@mail.com')
        self.pedido(self.beto)
        t = alterado(d['access'], id_usuario=self.beto.pk)  # ana intenta ser beto
        self.con(t)
        self.assertEqual(self.client.get(C + 'pedidos/').status_code, 401)
        self.assertEqual(self.client.get(C + 'perfil/').status_code, 401)
        self.anon()
        panel = self.login_panel('admin_a')
        self.con(alterado(panel['access'], id_restaurante=self.rest_b.pk))
        self.assertIn(self.client.get(P + 'orders/').status_code, (401, 403))
        self.assertNotEqual(self.client.get(P + 'orders/').status_code, 200)

    def test_alg_none(self):
        d, panel = self.login_cliente(), self.login_panel()
        for url, token in [(C + 'pedidos/', sin_firma(d['access'])), (R + 'estado/', sin_firma(d['access'])),
                           (P + 'orders/', sin_firma(panel['access'])), (P + 'products/', sin_firma(panel['access']))]:
            self.con(token)
            r = self.client.get(url)
            self.assertIn(r.status_code, (401, 403), url)

    def test_token_cliente_vencido(self):
        self.con(cliente_vencido(self.ana.pk))
        for url in [C + 'pedidos/', C + 'perfil/', R + 'estado/']:
            self.assertEqual(self.client.get(url).status_code, 401, url)

    def test_refresh_vencido(self):
        t = ClienteRefreshToken()
        t['tipo'] = 'cliente'
        t['id_usuario'] = self.ana.pk
        t.set_exp(lifetime=-timedelta(seconds=30))
        r = self.client.post(C + 'token/refresh/', {'refresh': str(t)}, format='json')
        self.assertEqual(r.status_code, 401)

    def test_token_del_panel_vencido(self):
        self.con(panel_vencido(self.rest_a.pk))
        for url in [P + 'orders/', P + 'products/', P + 'categorias/', P + 'restaurante/']:
            self.assertIn(self.client.get(url).status_code, (401, 403), url)

    def test_header_authorization_malformado_en_cliente(self):
        access = self.login_cliente()['access']
        self.assertEqual(self.client.get(C + 'pedidos/', HTTP_AUTHORIZATION='Bearer ' + access).status_code, 200)
        for h in ['', 'Bearer', 'Bearer ', access, 'bearer ' + access, 'Token ' + access, 'Basic ' + access,
                  'Bearer  ' + access, 'Bearer ' + access + ' extra', 'Bearer ' + access + 'x', 'Bearer null',
                  'Bearer undefined', 'Bearer ' + 'a' * 5000, 'Bearer a.b.c']:
            r = self.client.get(C + 'pedidos/', HTTP_AUTHORIZATION=h)
            self.assertEqual(r.status_code, 401, repr(h[:30]))

    def test_header_authorization_malformado_en_repartidor(self):
        access = self.nuevo_repartidor()['access']
        self.anon()
        for h in ['', 'Bearer', access, 'bearer ' + access, 'Bearer  ' + access, 'Bearer ' + access + ' extra']:
            r = self.client.get(R + 'estado/', HTTP_AUTHORIZATION=h)
            self.assertEqual(r.status_code, 401, repr(h[:30]))

    def test_header_authorization_malformado_en_panel(self):
        access = self.login_panel()['access']
        self.assertEqual(self.client.get(P + 'orders/', HTTP_AUTHORIZATION='Bearer ' + access).status_code, 200)
        for h in ['', 'Bearer', 'Bearer ', access, 'bearer ' + access, 'Token ' + access, 'Bearer  ' + access,
                  'Bearer a.b.c', 'Bearer ' + access + 'x']:
            r = self.client.get(P + 'orders/', HTTP_AUTHORIZATION=h)
            self.assertIn(r.status_code, (401, 403), repr(h[:30]))  # 401 vs 403: ver el BUG de abajo

    # BUG: apps/core/views.py:24 usa auth_header.split(' ')[1] sin exigir exactamente 2 partes: "Bearer <valido> basura"
    # se acepta en las vistas del panel (CustomJWTAuthentication sí lo rechaza). Inconsistente con el lado cliente.
    def test_panel_rechaza_header_con_partes_de_mas(self):
        access = self.login_panel()['access']
        r = self.client.get(P + 'orders/', HTTP_AUTHORIZATION='Bearer ' + access + ' basura')
        self.assertEqual(r.status_code, 401)

    # BUG: apps/core/authentication.py:47 lanza AuthenticationFailed pero el autenticador no define
    # authenticate_header(): DRF responde 403 (no 401) cuando el header lleva un token malo/vencido y la vista
    # usa el autenticador global (panel). API.md promete 401. (El caso "sin token" sí da 401 por la vista.)
    # El test anterior de vencido pasa porque get_restaurante_from_token corre antes... aquí se ve el 403 real.
    def test_panel_token_invalido_o_vencido_responde_401_y_no_403(self):
        for token in ('basura.que.no.es.jwt', panel_vencido(self.rest_a.pk)):
            self.con(token)
            self.assertEqual(self.client.get(P + 'orders/').status_code, 401, token[:15])


# =====================================================================
# 2. IDOR / BOLA
# =====================================================================

class IdorClienteTest(Base):

    def test_cliente_no_ve_pedidos_de_otro(self):
        mio, ajeno = self.pedido(self.ana), self.pedido(self.beto)
        self.entrar_cliente('ana@mail.com')
        ids = [p['id_pedido'] for p in self.client.get(C + 'pedidos/').data]
        self.assertEqual(ids, [mio.pk])
        self.assertNotIn(ajeno.pk, ids)

    def test_crear_pedido_ignora_id_usuario_y_campos_ajenos_del_body(self):
        self.entrar_cliente('ana@mail.com')
        r = self.client.post(C + 'pedidos/', {
            'id_restaurante': self.rest_a.pk, 'direccion_entrega': 'Calle 1', 'metodo_pago': 'efectivo',
            'items': [{'id_producto': self.muzza.pk, 'cantidad': 1, 'precio': 1, 'subtotal': 0}],
            'id_usuario': self.beto.pk, 'usuario': self.beto.pk, 'id_repartidor': 1, 'estado': 'entregado',
            'total': 1, 'costo_envio': 0, 'pagado': True, 'estado_pago': True,
        }, format='json')
        self.assertEqual(r.status_code, 201, r.content)
        p = Pedidos.objects.get()
        self.assertEqual((p.id_usuario_id, p.estado, p.id_repartidor_id, p.total), (self.ana.pk, 'pendiente', None, 1800.0))
        self.assertIs(Pagos.objects.get().estado_pago, False)
        self.assertEqual(Pagos.objects.get().monto, 1800.0)

    def test_patch_perfil_ignora_id_usuario_del_body(self):
        self.entrar_cliente('ana@mail.com')
        antes = Usuarios.objects.get(pk=self.beto.pk)
        r = self.client.patch(C + 'perfil/', {'id_usuario': self.beto.pk, 'nombre': 'Nuevo'}, format='json')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data['id_usuario'], self.ana.pk)
        self.assertEqual(Usuarios.objects.get(pk=self.ana.pk).nombre, 'Nuevo')
        self.assertEqual(Usuarios.objects.get(pk=self.beto.pk).nombre, antes.nombre)

    def test_cliente_comun_no_entra_a_repartidor(self):
        self.entrar_cliente()
        p = self.pedido()
        antes = self.foto()
        for metodo, url in [('get', R + 'estado/'), ('get', R + 'disponibles/'), ('get', R + 'pedidos/'),
                            ('patch', R + 'estado/'), ('post', R + f'pedidos/{p.pk}/tomar/'),
                            ('post', R + f'pedidos/{p.pk}/soltar/'), ('post', R + f'pedidos/{p.pk}/estado/')]:
            r = getattr(self.client, metodo)(url, {'disponible': True, 'estado': 'entregado'}, format='json')
            self.assertEqual(r.status_code, 403, url)
        self.assertEqual(self.foto(), antes)

    def test_admin_del_panel_no_puede_loguearse_como_cliente_ni_repartidor(self):
        r = self.client.post(C + 'login/', {'email': 'aa@mail.com', 'contrasena': 'secreto123'}, format='json')
        self.assertEqual(r.status_code, 401)

    # BUG: apps/clientes/auth.py:65-76 + views.py:431: el token de cliente solo se valida por firma; MisPedidosView.post
    # nunca comprueba que el usuario exista / siga siendo cliente (rol=False). Un token (hasta 30 días de refresh) de una
    # cuenta borrada, o de una cuenta luego promovida a admin, sigue creando pedidos a su nombre.
    def test_token_de_cuenta_promovida_a_admin_no_crea_pedidos(self):
        self.entrar_cliente('ana@mail.com')
        Usuarios.objects.filter(pk=self.ana.pk).update(rol=True)
        r = self.client.post(C + 'pedidos/', {
            'id_restaurante': self.rest_a.pk, 'direccion_entrega': 'Calle 1', 'metodo_pago': 'efectivo',
            'items': [{'id_producto': self.coca.pk, 'cantidad': 1}]}, format='json')
        self.assertIn(r.status_code, (401, 403, 404))
        self.assertEqual(Pedidos.objects.count(), 0)


class IdorRepartidorTest(Base):

    def setUp(self):
        super().setUp()
        self.juan = self.nuevo_repartidor('juan@mail.com')
        self.token_juan = self.juan['access']
        self.pepe = self.nuevo_repartidor('pepe@mail.com')
        self.token_pepe = self.pepe['access']
        self.p = self.pedido()

    def como(self, token):
        self.con(token)

    def rep(self, token):
        self.como(token)
        return self.client.get(R + 'estado/').data['id_repartidor']

    def test_repartidor_no_ve_entregas_de_otro(self):
        self.como(self.token_juan)
        self.assertEqual(self.client.post(R + f'pedidos/{self.p.pk}/tomar/').status_code, 200)
        self.como(self.token_pepe)
        self.assertEqual(self.client.get(R + 'pedidos/').data, [])
        self.assertEqual([x['id_pedido'] for x in self.client.get(R + 'disponibles/').data], [])

    def test_repartidor_no_suelta_ni_cambia_estado_de_pedido_ajeno(self):
        self.como(self.token_juan)
        self.client.post(R + f'pedidos/{self.p.pk}/tomar/')
        antes = self.foto()
        self.como(self.token_pepe)
        self.assertEqual(self.client.post(R + f'pedidos/{self.p.pk}/soltar/').status_code, 409)
        for estado in ('en camino', 'entregado'):
            r = self.client.post(R + f'pedidos/{self.p.pk}/estado/', {'estado': estado}, format='json')
            self.assertEqual(r.status_code, 404)
            self.assertNotIn('Calle Secreta', r.content.decode())
        self.assertEqual(self.foto(), antes)

    def test_repartidor_no_cambia_estado_de_pedido_sin_tomar(self):
        antes = self.foto()
        self.como(self.token_pepe)
        for estado in ('en camino', 'entregado'):
            self.assertEqual(self.client.post(R + f'pedidos/{self.p.pk}/estado/', {'estado': estado}, format='json').status_code, 404)
        self.assertEqual(self.client.post(R + f'pedidos/{self.p.pk}/soltar/').status_code, 409)
        self.assertEqual(self.foto(), antes)

    def test_tomar_pedido_ya_tomado_no_filtra_datos_del_cliente(self):
        self.como(self.token_juan)
        self.client.post(R + f'pedidos/{self.p.pk}/tomar/')
        self.como(self.token_pepe)
        r = self.client.post(R + f'pedidos/{self.p.pk}/tomar/')
        self.assertEqual(r.status_code, 409)
        self.assertEqual(r.data, {'error': 'El pedido ya no está disponible'})
        self.assertEqual(Pedidos.objects.get(pk=self.p.pk).id_repartidor_id, self.rep(self.token_juan))

    def test_doble_toma_dos_repartidores_el_segundo_recibe_409(self):
        self.como(self.token_juan)
        r1 = self.client.post(R + f'pedidos/{self.p.pk}/tomar/')
        self.como(self.token_pepe)
        r2 = self.client.post(R + f'pedidos/{self.p.pk}/tomar/')
        self.assertEqual((r1.status_code, r2.status_code), (200, 409))
        self.assertEqual(Pedidos.objects.get(pk=self.p.pk).id_repartidor_id, self.rep(self.token_juan))

    def test_repartidor_no_puede_tomar_dos_veces_el_mismo(self):
        self.como(self.token_juan)
        self.assertEqual(self.client.post(R + f'pedidos/{self.p.pk}/tomar/').status_code, 200)
        self.assertEqual(self.client.post(R + f'pedidos/{self.p.pk}/tomar/').status_code, 409)

    def test_despues_de_soltar_ya_no_ve_los_datos_del_cliente(self):
        self.como(self.token_juan)
        self.client.post(R + f'pedidos/{self.p.pk}/tomar/')
        self.assertEqual(self.client.post(R + f'pedidos/{self.p.pk}/soltar/').status_code, 204)
        self.assertEqual(self.client.get(R + 'pedidos/').data, [])
        self.assertEqual(self.client.post(R + f'pedidos/{self.p.pk}/estado/', {'estado': 'en camino'}, format='json').status_code, 404)

    def test_disponibles_no_trae_datos_personales_del_cliente(self):
        self.como(self.token_pepe)
        r = self.client.get(R + 'disponibles/')
        self.assertEqual(len(r.data), 1)
        self.assertNotIn('cliente', r.data[0])
        texto = r.content.decode()
        for dato in ('Ana', 'ana@mail.com', '1155556666'):
            self.assertNotIn(dato, texto)

    # BUG: apps/clientes/views.py:406 (reusado por repartidor.py:100): "disponibles" incluye direccion_entrega, la
    # dirección de la casa del cliente, para CUALQUIER repartidor antes de tomar el pedido (API.md dice "sin datos del
    # cliente" y "los ve solo después de tomar"). Como el registro de repartidor es abierto, cualquiera se registra con
    # repartidor:true y recorre las direcciones de todos los pedidos pendientes.
    def test_disponibles_no_expone_direccion_exacta_del_cliente(self):
        self.como(self.token_pepe)
        self.assertNotIn('Calle Secreta 9', self.client.get(R + 'disponibles/').content.decode())

    def test_repartidor_no_ve_pedidos_de_retiro_ni_ajenos_en_disponibles(self):
        self.pedido(tipo='retiro')
        self.como(self.token_pepe)
        self.assertEqual([x['id_pedido'] for x in self.client.get(R + 'disponibles/').data], [self.p.pk])


class IdorPanelTest(Base):

    def test_restaurante_solo_ve_sus_pedidos_productos_categorias(self):
        mio, ajeno = self.pedido(rest=self.rest_a), self.pedido(rest=self.rest_b)
        self.entrar_panel('admin_a')
        self.assertEqual([x['id_pedido'] for x in self.client.get(P + 'orders/').data], [mio.pk])
        self.assertNotIn(self.de_b.pk, [x['id_producto'] for x in self.client.get(P + 'products/').data])
        self.assertEqual([x['id_categoria'] for x in self.client.get(P + 'categorias/').data], [self.cat_a.pk])
        self.assertEqual(self.client.get(P + 'restaurante/').data['id_restaurante'], self.rest_a.pk)
        self.assertNotIn(ajeno.pk, [x['id_pedido'] for x in self.client.get(P + 'orders/').data])

    def test_no_edita_pedido_de_otro_restaurante(self):
        ajeno = self.pedido(rest=self.rest_b)
        self.entrar_panel('admin_a')
        antes = self.foto()
        r = self.client.put(P + f'orders/{ajeno.pk}/', {'estado': 'entregado'}, format='json')
        self.assertEqual(r.status_code, 404)
        self.assertEqual(self.foto(), antes)

    def test_no_edita_ni_borra_categoria_de_otro_restaurante(self):
        self.entrar_panel('admin_a')
        antes = self.foto()
        self.assertEqual(self.client.put(P + f'categorias/{self.cat_b.pk}/', {'nombre': 'X'}, format='json').status_code, 404)
        self.assertEqual(self.client.delete(P + f'categorias/{self.cat_b.pk}/').status_code, 404)
        self.assertEqual(self.foto(), antes)

    def test_crear_producto_y_categoria_ignora_id_restaurante_del_body(self):
        self.entrar_panel('admin_a')
        r = self.client.post(P + 'products/', {'nombre': 'Nuevo', 'precio': 10, 'stock': 1,
                                               'id_restaurante': self.rest_b.pk}, format='json')
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(Productos.objects.get(pk=r.data['id_producto']).id_restaurante_id, self.rest_a.pk)
        r = self.client.post(P + 'categorias/', {'nombre': 'N', 'id_restaurante': self.rest_b.pk}, format='json')
        self.assertEqual(Categoria.objects.get(pk=r.data['id_categoria']).id_restaurante_id, self.rest_a.pk)

    def test_patch_restaurante_ignora_id_y_no_toca_el_ajeno(self):
        self.entrar_panel('admin_a')
        antes_b = Restaurantes.objects.filter(pk=self.rest_b.pk).values().get()
        r = self.client.patch(P + 'restaurante/', {'id_restaurante': self.rest_b.pk, 'nombre': 'Renombrado'}, format='json')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data['id_restaurante'], self.rest_a.pk)
        self.assertEqual(Restaurantes.objects.filter(pk=self.rest_b.pk).values().get(), antes_b)
        self.assertEqual(Restaurantes.objects.get(pk=self.rest_a.pk).nombre, 'Renombrado')

    def test_put_pedido_ignora_otros_campos_del_body(self):
        mio = self.pedido(rest=self.rest_a)
        self.entrar_panel('admin_a')
        r = self.client.put(P + f'orders/{mio.pk}/', {'estado': 'listo', 'total': 1, 'id_usuario': self.beto.pk,
                                                       'id_restaurante': self.rest_b.pk, 'id_repartidor': 1}, format='json')
        self.assertEqual(r.status_code, 200)
        p = Pedidos.objects.get(pk=mio.pk)
        self.assertEqual((p.estado, p.total, p.id_usuario_id, p.id_restaurante_id, p.id_repartidor_id),
                         ('listo', 1500, self.ana.pk, self.rest_a.pk, None))


# =====================================================================
# 3. Escalada de privilegios / mass assignment
# =====================================================================

class MassAssignmentTest(Base):
    OK = {'nombre': 'Eve', 'email': 'eve@mail.com', 'telefono': '1144443333', 'contrasena': 'secreto123'}

    def registrar(self, **extra):
        self.anon()
        return self.client.post(C + 'registro/', {**self.OK, **extra}, format='json')

    def test_registro_ignora_rol_restaurante_id_y_perfil(self):
        r = self.registrar(rol=True, id_restaurante=self.rest_a.pk, id_usuario=999999, perfil='repartidor',
                           is_staff=True, is_superuser=True, tipo='admin')
        self.assertEqual(r.status_code, 201, r.content)
        u = Usuarios.objects.get(email='eve@mail.com')
        self.assertIs(u.rol, False)
        self.assertIsNone(u.id_restaurante_id)
        self.assertNotEqual(u.pk, 999999)
        self.assertFalse(Repartidor.objects.exists())
        self.assertEqual((r.data['perfil'], r.data['repartidor']), ('cliente', None))
        # y el token que recibió no sirve en el panel
        self.con(r.data['access'])
        self.assertEqual(self.client.get(P + 'orders/').status_code, 401)

    def test_repartidor_solo_con_bool_true(self):
        for i, valor in enumerate(['true', 'True', 1, '1', 'yes', ['x'], {'a': 1}, None, 0, False]):
            r = self.client.post(C + 'registro/', {**self.OK, 'email': f'e{i}@mail.com', 'repartidor': valor,
                                                   'vehiculo': 'Moto'}, format='json')
            self.assertEqual(r.status_code, 201, (valor, r.content))
            self.assertEqual(r.data['perfil'], 'cliente', valor)
        self.assertFalse(Repartidor.objects.exists())

    def test_registro_repartidor_sin_vehiculo_no_crea_nada(self):
        antes = self.foto()
        for veh in [None, '', '   ', 5, ['x']]:
            r = self.registrar(repartidor=True, vehiculo=veh)
            self.assertEqual(r.status_code, 400, veh)
        self.assertEqual(self.registrar(repartidor=True, vehiculo='x' * 51).status_code, 400)
        self.assertEqual(self.foto(), antes)

    def test_patch_perfil_no_cambia_email_rol_id_ni_contrasena(self):
        self.entrar_cliente('ana@mail.com')
        antes = Usuarios.objects.filter(pk=self.ana.pk).values().get()
        r = self.client.patch(C + 'perfil/', {'email': 'hack@mail.com', 'rol': True, 'id_usuario': 1,
                                              'id_restaurante': self.rest_a.pk, 'contrasena': 'nueva123',
                                              'contrasena_hash': 'x'}, format='json')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(Usuarios.objects.filter(pk=self.ana.pk).values().get(), antes)
        self.assertEqual(r.data['email'], 'ana@mail.com')
        self.login_cliente('ana@mail.com')  # sigue entrando con la contraseña de siempre

    def test_cambiar_contrasena_exige_la_actual_correcta(self):
        self.entrar_cliente('ana@mail.com')
        antes = Usuarios.objects.get(pk=self.ana.pk).contrasena
        for body in [{'contrasena_nueva': 'nueva123'},
                     {'contrasena_nueva': 'nueva123', 'contrasena_actual': 'mala'},
                     {'contrasena_nueva': 'nueva123', 'contrasena_actual': None},
                     {'contrasena_nueva': 'nueva123', 'contrasena_actual': 123},
                     {'contrasena_nueva': 'nueva123', 'contrasena_actual': ['secreto123']},
                     {'contrasena_nueva': 'corta', 'contrasena_actual': 'secreto123'},
                     {'contrasena_nueva': 123456, 'contrasena_actual': 'secreto123'},
                     {'contrasena_nueva': None, 'contrasena_actual': 'secreto123'},
                     {'contrasena_nueva': ['abcdefgh'], 'contrasena_actual': 'secreto123'}]:
            r = self.client.patch(C + 'perfil/', body, format='json')
            self.assertEqual(r.status_code, 400, body)
        self.assertEqual(Usuarios.objects.get(pk=self.ana.pk).contrasena, antes)

    def test_rechazo_parcial_del_patch_no_guarda_nada(self):
        self.entrar_cliente('ana@mail.com')
        antes = Usuarios.objects.filter(pk=self.ana.pk).values().get()
        r = self.client.patch(C + 'perfil/', {'nombre': 'Cambiado', 'telefono': 'abc'}, format='json')
        self.assertEqual(r.status_code, 400)
        self.assertEqual(Usuarios.objects.filter(pk=self.ana.pk).values().get(), antes)

    def test_patch_estado_repartidor_ignora_identidad(self):
        otro = self.nuevo_repartidor('otro@mail.com', disponible=False)
        self.nuevo_repartidor('juan@mail.com')
        antes_otro = Repartidor.objects.filter(pk=otro['repartidor']['id_repartidor']).values().get()
        r = self.client.patch(R + 'estado/', {'id_usuario': otro['id_usuario'], 'id_repartidor': otro['repartidor']['id_repartidor'],
                                              'nombre': 'Hack', 'vehiculo': 'Auto'}, format='json')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(Repartidor.objects.filter(pk=otro['repartidor']['id_repartidor']).values().get(), antes_otro)
        juan = Repartidor.objects.exclude(pk=otro['repartidor']['id_repartidor']).get()
        self.assertEqual((juan.vehiculo, juan.nombre), ('Auto', 'Juan'))

    def test_disponible_solo_acepta_bool(self):
        self.nuevo_repartidor(disponible=False)
        for valor in ['true', 'false', 1, 0, None, [], {}, 'yes']:
            r = self.client.patch(R + 'estado/', {'disponible': valor}, format='json')
            self.assertEqual(r.status_code, 400, valor)
        self.assertIs(Repartidor.objects.get().disponible, False)

    def test_vehiculo_invalido_no_se_guarda(self):
        self.nuevo_repartidor()
        for valor in ['', '   ', None, 5, ['x'], 'x' * 51]:
            self.assertEqual(self.client.patch(R + 'estado/', {'vehiculo': valor}, format='json').status_code, 400, valor)
        self.assertEqual(Repartidor.objects.get().vehiculo, 'Moto')

    def test_panel_patch_restaurante_no_tiene_campos_peligrosos(self):
        self.entrar_panel('admin_a')
        r = self.client.patch(P + 'restaurante/', {'categoria': 'x', 'id_restaurante': 99, 'is_admin': True}, format='json')
        self.assertEqual(r.status_code, 200)
        self.assertIsNone(Restaurantes.objects.get(pk=self.rest_a.pk).categoria)

    # BUG: apps/clientes/auth.py:30-49: ni el cambio de contraseña ni nada revoca los tokens ya emitidos: un access/refresh
    # robado sigue valiendo 30 días aunque la víctima cambie su contraseña.
    def test_cambiar_contrasena_invalida_el_refresh_anterior(self):
        d = self.entrar_cliente('ana@mail.com')
        r = self.client.patch(C + 'perfil/', {'contrasena_actual': 'secreto123', 'contrasena_nueva': 'otra-clave-1'}, format='json')
        self.assertEqual(r.status_code, 200)
        self.anon()
        self.assertEqual(self.client.post(C + 'token/refresh/', {'refresh': d['refresh']}, format='json').status_code, 401)


# =====================================================================
# 4. Manipulación de pedidos
# =====================================================================

class ManipulacionDePedidosTest(Base):
    URL = C + 'pedidos/'

    def setUp(self):
        super().setUp()
        self.entrar_cliente('ana@mail.com')

    def pedir(self, **cambios):
        datos = {'id_restaurante': self.rest_a.pk, 'direccion_entrega': 'Calle 1', 'metodo_pago': 'efectivo',
                 'items': [{'id_producto': self.muzza.pk, 'cantidad': 1}], **cambios}
        return self.client.post(self.URL, datos, format='json')

    def item(self, producto=None, cantidad=1):
        return [{'id_producto': (producto or self.muzza).pk, 'cantidad': cantidad}]

    def assertRechazado(self, r, codigos=(400, 404)):
        self.assertIn(r.status_code, codigos, r.content)
        self.assertEqual(Pedidos.objects.count(), 0)
        self.assertEqual(DetallePedido.objects.count(), 0)
        self.assertEqual(Pagos.objects.count(), 0)
        self.assertEqual(Productos.objects.get(pk=self.muzza.pk).stock, 5)

    def test_el_precio_del_body_se_ignora(self):
        r = self.pedir(items=[{'id_producto': self.muzza.pk, 'cantidad': 2, 'precio': 0.01, 'precio_unitario': 0,
                               'subtotal': 0, 'total': 0}], total=0.01, precio=1, costo_envio=0, subtotal=0)
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(r.data['total'], 2800.0)
        self.assertEqual(Pagos.objects.get().monto, 2800.0)

    def test_cantidades_invalidas(self):
        for cantidad in [0, -1, -10**9, 1.5, 2.0, 0.1, '', ' ', 'abc', '-1', '1.5', '1e3', True, False, None, [], [1], {}, '1 2']:
            with self.subTest(cantidad=cantidad):
                self.assertRechazado(self.pedir(items=self.item(cantidad=cantidad)))

    # BUG: apps/clientes/views.py:49 usa str.isdigit(): '²' (superíndice) es "dígito" para isdigit() pero int('²') lanza
    # ValueError -> 500 en cantidad, id_producto e id_restaurante. Cualquier cliente logueado (y sin loguear en
    # /restaurantes) provoca un 500 con {"cantidad": "²"}.
    def test_digito_unicode_no_da_500_en_pedido(self):
        r = self.pedir(items=self.item(cantidad='²'))
        self.assertLess(r.status_code, 500)
        r = self.pedir(id_restaurante='²')
        self.assertLess(r.status_code, 500)

    def test_cantidad_enorme_no_pasa_si_hay_stock_limitado(self):
        for cantidad in [10**9, 10**30]:
            with self.subTest(cantidad=cantidad):
                r = self.pedir(items=self.item(cantidad=cantidad))
                self.assertNo500(r)
                self.assertRechazado(r)

    # BUG: views.py:469 solo exige cantidad >= 1, sin tope. En un producto sin control de stock (stock null) se acepta
    # cantidad=10**9 -> pedido de $500.000.000.000 (y un repartidor lo ve en disponibles). No hay máximo por línea.
    def test_cantidad_absurda_en_producto_sin_stock_se_rechaza(self):
        r = self.pedir(items=self.item(self.coca, 10**9))
        self.assertEqual(r.status_code, 400)

    def test_id_producto_invalido(self):
        otro = Productos.objects.create(nombre='NoDisp', precio=1.0, disponible=False, id_restaurante=self.rest_a)
        nulo = Productos.objects.create(nombre='Nulo', precio=1.0, disponible=None, id_restaurante=self.rest_a)
        for idp in [self.de_b.pk, otro.pk, nulo.pk, 999999, 0, -1, None, 'x', '', 1.5, True, [], {}]:
            with self.subTest(id_producto=idp):
                r = self.pedir(items=[{'id_producto': idp, 'cantidad': 1}])
                self.assertNo500(r)
                self.assertRechazado(r)

    def test_producto_de_otro_restaurante_mezclado_con_uno_valido(self):
        r = self.pedir(items=[{'id_producto': self.muzza.pk, 'cantidad': 1}, {'id_producto': self.de_b.pk, 'cantidad': 1}])
        self.assertRechazado(r)
        self.assertEqual(Productos.objects.get(pk=self.de_b.pk).stock, 9)

    def test_items_invalidos(self):
        for items in [[], None, 'abc', {}, {'id_producto': 1}, 5, True, [1], [None], ['x'], [[]], [{}], [[1, 2]],
                      [{'id_producto': self.muzza.pk}], [{'cantidad': 1}]]:
            with self.subTest(items=items):
                self.assertRechazado(self.pedir(items=items))

    def test_sin_items_en_el_body(self):
        r = self.client.post(self.URL, {'id_restaurante': self.rest_a.pk, 'direccion_entrega': 'x', 'metodo_pago': 'efectivo'}, format='json')
        self.assertRechazado(r)

    # BUG: views.py:458-471 no limita la cantidad de líneas: una sola request con miles de items crea miles de
    # DetallePedido (y consultas) -> amplificación / DoS barato para un cliente autenticado.
    def test_lista_gigante_de_items_se_rechaza(self):
        r = self.pedir(items=[{'id_producto': self.coca.pk, 'cantidad': 1}] * 3000)
        self.assertEqual(r.status_code, 400)

    def test_mismo_producto_repetido_no_salta_el_stock(self):
        r = self.pedir(items=[{'id_producto': self.muzza.pk, 'cantidad': 1}] * 6)
        self.assertRechazado(r)
        r = self.pedir(items=[{'id_producto': self.muzza.pk, 'cantidad': 3}, {'id_producto': str(self.muzza.pk), 'cantidad': '3'}])
        self.assertRechazado(r)

    def test_mismo_producto_repetido_dentro_del_stock_descuenta_bien(self):
        r = self.pedir(items=[{'id_producto': self.muzza.pk, 'cantidad': 2}, {'id_producto': self.muzza.pk, 'cantidad': 3}])
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(Productos.objects.get(pk=self.muzza.pk).stock, 0)
        self.assertEqual(self.pedir().status_code, 400)  # y ahora ya no hay
        self.assertEqual(Pedidos.objects.count(), 1)

    def test_tipo_entrega_invalido(self):
        for valor in ['envio', 'RETIRO', 'Domicilio', 'domicilio ', 5, '', [], {}, True, False, 'retiro; DROP']:
            with self.subTest(tipo=valor):
                self.assertRechazado(self.pedir(tipo_entrega=valor))

    def test_metodo_pago_invalido(self):
        for valor in ['bitcoin', 'Efectivo', ' efectivo', '', None, 5, True, 'tarjeta ']:
            with self.subTest(metodo=valor):
                self.assertRechazado(self.pedir(metodo_pago=valor))

    # BUG: views.py:455 hace `metodo_pago not in METODOS_PAGO` (dict): con una lista o dict en el JSON el hash falla
    # (TypeError: unhashable) -> 500. {"metodo_pago": []} desde cualquier cliente logueado.
    def test_metodo_pago_lista_o_dict_no_da_500(self):
        for valor in [[], {}, ['efectivo'], {'a': 1}]:
            r = self.pedir(metodo_pago=valor)
            self.assertLess(r.status_code, 500, valor)

    def test_direccion_requerida_en_domicilio(self):
        for valor in [None, '', '   ', 5, [], {}, True]:
            with self.subTest(direccion=valor):
                self.assertRechazado(self.pedir(direccion_entrega=valor))

    # BUG: views.py:450 acepta direccion_entrega de cualquier largo (el perfil sí limita a 255, registro no limita).
    # Un POST con 1 MB de dirección se guarda tal cual en pedidos.direccion_entega (y se lista a repartidores y panel).
    def test_direccion_gigante_se_rechaza(self):
        self.assertEqual(self.pedir(direccion_entrega='x' * 100_000).status_code, 400)

    # BUG: sin validación de caracteres de control: '\x00' en texto libre; en PostgreSQL (la base real) un NUL en un
    # varchar da error de la DB -> 500. En SQLite de test se guarda sin queja, por eso acá se exige 400.
    def test_direccion_con_caracter_nulo_se_rechaza(self):
        self.assertEqual(self.pedir(direccion_entrega='Calle\x001').status_code, 400)

    def test_restaurante_invalido(self):
        for valor in [999999, 0, -1, None, '', 'abc', 1.5, True, [], {}, 10**30]:
            with self.subTest(restaurante=valor):
                r = self.pedir(id_restaurante=valor)
                self.assertNo500(r)
                self.assertRechazado(r)

    def test_restaurante_cerrado(self):
        Restaurantes.objects.filter(pk=self.rest_a.pk).update(estado=' CERRADO ')
        self.assertRechazado(self.pedir(), codigos=(400,))

    def test_pedido_minimo_justo_por_debajo_y_justo_alcanza(self):
        Restaurantes.objects.filter(pk=self.rest_a.pk).update(pedido_minimo=1000.01)
        self.assertRechazado(self.pedir(), codigos=(400,))
        self.assertRechazado(self.pedir(tipo_entrega='retiro'), codigos=(400,))
        Restaurantes.objects.filter(pk=self.rest_a.pk).update(pedido_minimo=1000.0)
        self.assertEqual(self.pedir().status_code, 201)

    def test_envio_no_cuenta_para_el_minimo(self):
        Restaurantes.objects.filter(pk=self.rest_a.pk).update(pedido_minimo=1500.0)
        self.assertRechazado(self.pedir(), codigos=(400,))  # 1000 + 800 de envío no alcanza el mínimo

    def test_retiro_ignora_costo_envio_del_body_y_direccion(self):
        r = self.pedir(tipo_entrega='retiro', costo_envio=99999, direccion_entrega='Otra')
        self.assertEqual(r.status_code, 201)
        self.assertEqual((r.data['total'], r.data['costo_envio'], r.data['direccion_entrega']), (1000.0, 0.0, None))

    def test_pedido_con_producto_no_disponible_despues_de_apagarlo(self):
        Productos.objects.filter(pk=self.muzza.pk).update(disponible=False)
        self.assertRechazado(self.pedir())

    def test_stock_en_cero_se_rechaza_y_no_queda_negativo(self):
        Productos.objects.filter(pk=self.muzza.pk).update(stock=0)
        r = self.pedir()
        self.assertEqual(r.status_code, 400)
        self.assertEqual(Productos.objects.get(pk=self.muzza.pk).stock, 0)
        self.assertEqual(Pedidos.objects.count(), 0)

    def test_pedido_rechazado_por_stock_en_segunda_linea_no_descuenta_la_primera(self):
        r = self.pedir(items=[{'id_producto': self.muzza.pk, 'cantidad': 2}, {'id_producto': self.muzza.pk, 'cantidad': 4}])
        self.assertRechazado(r)

    def test_json_con_campos_extra_enormes_no_rompe(self):
        r = self.pedir(basura='x' * 200_000, anidado={'a': [1] * 1000})
        self.assertEqual(r.status_code, 201, r.content)


# =====================================================================
# 5. Flujo del repartidor fuera de orden
# =====================================================================

class FlujoRepartidorTest(Base):

    def setUp(self):
        super().setUp()
        self.juan = self.nuevo_repartidor('juan@mail.com')

    def estado(self, p, valor, **extra):
        return self.client.post(R + f'pedidos/{p.pk}/estado/', {'estado': valor, **extra}, format='json')

    def tomar(self, p):
        return self.client.post(R + f'pedidos/{p.pk}/tomar/')

    def pedido_db(self, p):
        return Pedidos.objects.get(pk=p.pk)

    def test_entregado_sin_en_camino(self):
        p = self.pedido()
        self.assertEqual(self.tomar(p).status_code, 200)
        r = self.estado(p, 'entregado')
        self.assertEqual(r.status_code, 409)
        self.assertEqual(self.pedido_db(p).estado, 'pendiente')
        self.assertIs(Pagos.objects.get().estado_pago, False)

    def test_en_camino_dos_veces(self):
        p = self.pedido()
        self.tomar(p)
        self.assertEqual(self.estado(p, 'en camino').status_code, 200)
        self.assertEqual(self.estado(p, 'en camino').status_code, 409)
        self.assertEqual(self.pedido_db(p).estado, 'en camino')

    def test_soltar_estando_en_camino(self):
        p = self.pedido()
        self.tomar(p)
        self.estado(p, 'en camino')
        self.assertEqual(self.client.post(R + f'pedidos/{p.pk}/soltar/').status_code, 409)
        self.assertIsNotNone(self.pedido_db(p).id_repartidor_id)

    def test_entregado_dos_veces_no_vuelve_a_cobrar(self):
        p = self.pedido()
        self.tomar(p)
        self.estado(p, 'en camino')
        self.assertEqual(self.estado(p, 'entregado').status_code, 200)
        fecha = Pagos.objects.get().fecha_pago
        self.assertEqual(self.estado(p, 'entregado').status_code, 409)
        self.assertEqual(Pagos.objects.get().fecha_pago, fecha)

    def test_volver_a_en_camino_despues_de_entregado(self):
        p = self.pedido()
        self.tomar(p)
        self.estado(p, 'en camino')
        self.estado(p, 'entregado')
        self.assertEqual(self.estado(p, 'en camino').status_code, 409)
        self.assertEqual(self.pedido_db(p).estado, 'entregado')

    def test_soltar_despues_de_entregado(self):
        p = self.pedido()
        self.tomar(p)
        self.estado(p, 'en camino')
        self.estado(p, 'entregado')
        self.assertEqual(self.client.post(R + f'pedidos/{p.pk}/soltar/').status_code, 409)
        self.assertIsNotNone(self.pedido_db(p).id_repartidor_id)

    def test_estados_no_permitidos(self):
        p = self.pedido()
        self.tomar(p)
        antes = self.foto()
        for valor in ['cancelado', 'pendiente', 'listo', 'preparando', '', '   ', None, 5, True, ['en camino'], {}, 'en  camino',
                      'entregado; DROP TABLE pedidos']:
            with self.subTest(estado=valor):
                self.assertEqual(self.estado(p, valor).status_code, 400)
        self.assertEqual(self.foto(), antes)

    def test_estado_sin_campo(self):
        p = self.pedido()
        self.tomar(p)
        self.assertEqual(self.client.post(R + f'pedidos/{p.pk}/estado/', {}, format='json').status_code, 400)

    def test_estado_ignora_otros_campos(self):
        p = self.pedido()
        self.tomar(p)
        r = self.estado(p, 'en camino', id_usuario=self.beto.pk, total=1, id_repartidor=999, id_restaurante=self.rest_b.pk)
        self.assertEqual(r.status_code, 200)
        db = self.pedido_db(p)
        self.assertEqual((db.id_usuario_id, db.total, db.id_restaurante_id), (self.ana.pk, 1500, self.rest_a.pk))
        self.assertEqual(db.id_repartidor_id, Repartidor.objects.get(vehiculo='Moto').pk)

    def test_en_camino_en_pedido_cancelado_por_el_restaurante(self):
        p = self.pedido()
        self.tomar(p)
        Pedidos.objects.filter(pk=p.pk).update(estado='CANCELADO')
        self.assertEqual(self.estado(p, 'en camino').status_code, 409)
        self.assertEqual(self.estado(p, 'entregado').status_code, 409)
        self.assertEqual(self.pedido_db(p).estado, 'CANCELADO')

    def test_tomar_pedido_de_retiro(self):
        p = self.pedido(tipo='retiro')
        antes = self.foto()
        self.assertEqual(self.tomar(p).status_code, 409)
        self.assertEqual(self.foto(), antes)

    def test_tomar_pedidos_terminados_sin_importar_mayusculas(self):
        for estado in ['entregado', 'Entregado', 'ENTREGADO', 'cancelado', 'CANCELADO', 'Cancelado', 'rechazado',
                       'RECHAZADO', 'en camino', 'EN CAMINO']:
            with self.subTest(estado=estado):
                p = self.pedido(estado=estado)
                self.assertEqual(self.tomar(p).status_code, 409)
                self.assertIsNone(self.pedido_db(p).id_repartidor_id)

    # BUG: apps/clientes/repartidor.py:29-33 sin_terminar() compara con estado__iexact (sin strip) pero estado_es() sí
    # hace strip: un pedido con estado "Entregado " / " cancelado" (el estado es texto libre; la base lo puede tener
    # así) se puede TOMAR, queda asignado a un repartidor y aparece en "disponibles".
    def test_tomar_pedido_terminado_con_espacios_en_el_estado(self):
        for estado in ['Entregado ', ' cancelado', 'CANCELADO\n']:
            p = self.pedido(estado=estado)
            self.assertEqual(self.tomar(p).status_code, 409, estado)
            self.assertIsNone(self.pedido_db(p).id_repartidor_id, estado)

    def test_tomar_sin_estar_disponible(self):
        p = self.pedido()
        self.client.patch(R + 'estado/', {'disponible': False}, format='json')
        antes = self.foto()
        self.assertEqual(self.tomar(p).status_code, 409)
        self.assertEqual(self.foto(), antes)
        Repartidor.objects.update(disponible=None)
        self.assertEqual(self.tomar(p).status_code, 409)
        self.assertIsNone(self.pedido_db(p).id_repartidor_id)

    def test_tomar_pedido_inexistente_o_con_id_enorme(self):
        for pk in [999999, 0, 10**30]:
            r = self.client.post(R + f'pedidos/{pk}/tomar/')
            self.assertIn(r.status_code, (404, 409), pk)

    def test_ids_no_numericos_en_la_url(self):
        for url in ['pedidos/abc/tomar/', 'pedidos/-1/tomar/', 'pedidos/1.5/estado/', "pedidos/1' OR 1=1/soltar/",
                    'pedidos//tomar/', 'pedidos/%00/tomar/']:
            r = self.client.post(R + url)
            self.assertEqual(r.status_code, 404, url)

    def test_metodos_incorrectos(self):
        p = self.pedido()
        self.assertEqual(self.client.get(R + f'pedidos/{p.pk}/tomar/').status_code, 405)
        self.assertEqual(self.client.delete(R + f'pedidos/{p.pk}/soltar/').status_code, 405)
        self.assertEqual(self.client.put(R + 'estado/', {'disponible': True}, format='json').status_code, 405)
        self.assertIsNone(self.pedido_db(p).id_repartidor_id)

    def test_soltar_y_otro_lo_toma(self):
        p = self.pedido()
        self.tomar(p)
        self.client.post(R + f'pedidos/{p.pk}/soltar/')
        pepe = self.nuevo_repartidor('pepe@mail.com')
        self.assertEqual(self.tomar(p).status_code, 200)
        self.assertEqual(self.pedido_db(p).id_repartidor_id, pepe['repartidor']['id_repartidor'])

    def test_repartidor_no_disponible_igual_puede_actualizar_su_entrega(self):
        # Apagar la disponibilidad no debe romper el flujo de lo que ya tiene (no es un 5xx ni pierde datos).
        p = self.pedido()
        self.tomar(p)
        self.client.patch(R + 'estado/', {'disponible': False}, format='json')
        self.assertEqual(self.estado(p, 'en camino').status_code, 200)


# =====================================================================
# 6. Entradas hostiles
# =====================================================================

HOSTILES = ["' OR 1=1 --", "'; DROP TABLE usuarios; --", '<script>alert(1)</script>', '"><img src=x onerror=alert(1)>',
            'ñandú ĳ ǅ ᴀʙ', '😀🍕👨‍👩‍👧', '‮evil', 'á' * 20, '%s %d {0} {{x}}', '../../etc/passwd', '${7*7} {{7*7}}']


class EntradasHostilesTest(Base):

    def test_textos_hostiles_se_guardan_como_texto_o_se_rechazan_sin_500(self):
        for i, s in enumerate(HOSTILES):
            with self.subTest(s=s[:15]):
                self.anon()
                r = self.client.post(C + 'registro/', {'nombre': s, 'email': f'h{i}@mail.com', 'telefono': '1144443333',
                                                       'contrasena': 'secreto123', 'direccion': s}, format='json')
                self.assertEqual(r.status_code, 201, r.content)
                u = Usuarios.objects.get(email=f'h{i}@mail.com')
                self.assertEqual((u.nombre, u.direccion), (s.strip(), s.strip()))
                self.con(r.data['access'])
                self.assertEqual(self.client.get(C + 'perfil/').data['nombre'], s.strip())
        self.assertEqual(Usuarios.objects.count(), 4 + len(HOSTILES))

    def test_inyeccion_en_login_cliente_y_panel(self):
        antes = Usuarios.objects.count()
        for s in ["' OR 1=1 --", "admin' --", "x' OR '1'='1", "ana@mail.com' OR 1=1 --"]:
            r = self.client.post(C + 'login/', {'email': s, 'contrasena': s}, format='json')
            self.assertEqual(r.status_code, 401, s)
            r = self.client.post(C + 'login/', {'email': 'ana@mail.com', 'contrasena': s}, format='json')
            self.assertEqual(r.status_code, 401, s)
            r = self.client.post(P + 'login/', {'nombre': s, 'contrasena': s}, format='json')
            self.assertEqual(r.status_code, 401, s)
            r = self.client.post(P + 'login/', {'nombre': 'admin_a', 'contrasena': s}, format='json')
            self.assertEqual(r.status_code, 401, s)
        self.assertEqual(Usuarios.objects.count(), antes)

    def test_filtro_de_categoria_con_comodines_e_inyeccion(self):
        todos = len(self.client.get(C + 'restaurantes/').data)
        self.assertEqual(todos, 2)
        for q in ["' OR 1=1 --", '%', '_', '\\', '"', "');--", '😀']:
            r = self.client.get(C + 'restaurantes/', {'categoria': q})
            self.assertEqual(r.status_code, 200, q)
            self.assertEqual(r.data, [], q)

    def test_telefono_hostil_o_enorme(self):
        for tel in ["1' OR 1=1", '<script>', '1' * 10_000, '+' * 31, '1234567\x00890', '١٢٣٤٥٦٧٨٩', '1' * 31, 12345678, ['1155556666']]:
            with self.subTest(tel=str(tel)[:15]):
                r = self.client.post(C + 'registro/', {'nombre': 'X', 'email': 'tel@mail.com', 'telefono': tel,
                                                       'contrasena': 'secreto123'}, format='json')
                self.assertNo500(r)
                if r.status_code == 201:  # '١٢٣...' (dígitos árabes) cumple \d: se acepta pero tiene que caber en 30
                    self.assertLessEqual(len(Usuarios.objects.get(email='tel@mail.com').telefono), 30)
                    Usuarios.objects.filter(email='tel@mail.com').delete()
                else:
                    self.assertEqual(r.status_code, 400)
                    self.assertFalse(Usuarios.objects.filter(email='tel@mail.com').exists())

    def test_vehiculo_enorme_o_hostil(self):
        base = {'nombre': 'X', 'email': 'rep@mail.com', 'telefono': '1144443333', 'contrasena': 'secreto123', 'repartidor': True}
        for veh in ['x' * 51, 'x' * 100_000]:
            self.assertEqual(self.client.post(C + 'registro/', {**base, 'vehiculo': veh}, format='json').status_code, 400)
        self.assertFalse(Usuarios.objects.filter(email='rep@mail.com').exists())
        self.assertFalse(Repartidor.objects.exists())
        r = self.client.post(C + 'registro/', {**base, 'vehiculo': "Moto'; DROP TABLE repartidor;--"}, format='json')
        self.assertEqual(r.status_code, 201)
        self.assertEqual(Repartidor.objects.get().vehiculo, "Moto'; DROP TABLE repartidor;--")

    def test_perfil_largos_maximos(self):
        self.entrar_cliente()
        antes = Usuarios.objects.filter(pk=self.ana.pk).values().get()
        for body in [{'nombre': 'x' * 101}, {'direccion': 'x' * 256}, {'telefono': '1' * 31}, {'nombre': '   '}, {'nombre': 5},
                     {'nombre': None}, {'telefono': None}, {'telefono': ['1']}]:
            r = self.client.patch(C + 'perfil/', body, format='json')
            self.assertIn(r.status_code, (400,), body if len(str(body)) < 80 else 'largo')
        self.assertEqual(Usuarios.objects.filter(pk=self.ana.pk).values().get(), antes)

    # BUG: views.py:135-138: el registro no limita el largo de nombre ni de direccion (el PATCH de perfil sí: 100 y 255).
    # Un POST de registro con 1 MB de nombre se guarda en usuarios.nombre; con columnas acotadas en Postgres da 500.
    def test_registro_con_nombre_o_direccion_gigantes_se_rechaza(self):
        r = self.client.post(C + 'registro/', {'nombre': 'x' * 100_000, 'email': 'g@mail.com', 'telefono': '1144443333',
                                               'contrasena': 'secreto123', 'direccion': 'y' * 100_000}, format='json')
        self.assertEqual(r.status_code, 400)

    # BUG: views.py:135-139: el registro tampoco valida el email (largo ni formato): "x" o 1 MB sin '@' crean cuenta.
    def test_registro_valida_formato_y_largo_del_email(self):
        for email in ['sin-arroba', 'a@' + 'b' * 100_000]:
            r = self.client.post(C + 'registro/', {'nombre': 'X', 'email': email, 'telefono': '1144443333',
                                                   'contrasena': 'secreto123'}, format='json')
            self.assertEqual(r.status_code, 400, email[:20])

    # BUG: views.py:135-139 (registro) y views.py:450: '\x00' en nombre/dirección. Postgres rechaza NUL en text/varchar
    # (DataError -> 500); SQLite de test lo acepta. Debería rechazarse con 400 antes de tocar la base.
    def test_registro_con_caracter_nulo_se_rechaza(self):
        r = self.client.post(C + 'registro/', {'nombre': 'Ana\x00', 'email': 'nul@mail.com', 'telefono': '1144443333',
                                               'contrasena': 'secreto123'}, format='json')
        self.assertEqual(r.status_code, 400)

    def test_contrasena_con_nulo_o_unicode_no_da_500(self):
        for pwd in ['abc\x00def123', 'ñññññññ😀', ' ' * 10 + 'a']:
            r = self.client.post(C + 'registro/', {'nombre': 'X', 'email': 'p@mail.com', 'telefono': '1144443333',
                                                   'contrasena': pwd}, format='json')
            self.assertNo500(r)
            Usuarios.objects.filter(email='p@mail.com').delete()

    def test_categoria_del_panel_hostil(self):
        self.entrar_panel()
        antes = Categoria.objects.count()
        for nombre in ['x' * 101, 'x' * 1_000_000, '', '   ', None, 5, ['a'], {}]:
            self.assertEqual(self.client.post(P + 'categorias/', {'nombre': nombre}, format='json').status_code, 400)
        self.assertEqual(Categoria.objects.count(), antes)
        r = self.client.post(P + 'categorias/', {'nombre': "'; DROP TABLE categoria; --"}, format='json')
        self.assertEqual(r.status_code, 201)

    def test_panel_restaurante_patch_valores_invalidos(self):
        self.entrar_panel()
        antes = Restaurantes.objects.filter(pk=self.rest_a.pk).values().get()
        for body in [{'nombre': ''}, {'nombre': 'x' * 101}, {'nombre': 5}, {'nombre': ['a']}, {'direccion': 'x' * 256},
                     {'telefono': 'x' * 31}, {'estado': 'x' * 31}, {'estado': 5}, {'costo_envio': -1}, {'costo_envio': 'nan'},
                     {'costo_envio': 'inf'}, {'costo_envio': 'abc'}, {'costo_envio': True}, {'costo_envio': []}, {'costo_envio': {}},
                     {'pedido_minimo': 10**9}, {'pedido_minimo': '1e400'}, {'pedido_minimo': -0.01}]:
            r = self.client.patch(P + 'restaurante/', body, format='json')
            self.assertEqual(r.status_code, 400, str(body)[:60])
        self.assertEqual(Restaurantes.objects.filter(pk=self.rest_a.pk).values().get(), antes)

    def test_panel_pedido_estado_invalido(self):
        p = self.pedido()
        self.entrar_panel()
        for estado in ['', '   ', None, 5, ['x'], {}, True, 'x' * 51]:
            self.assertEqual(self.client.put(P + f'orders/{p.pk}/', {'estado': estado}, format='json').status_code, 400)
        self.assertEqual(Pedidos.objects.get(pk=p.pk).estado, 'pendiente')

    # BUG: apps/core/views.py:139-170 (POST products/): precio y stock se mandan a la base sin validar. precio="abc" o
    # stock="abc" -> ValueError/DataError -> 500; precio negativo se acepta (201), y un cliente que lo pide obtiene un
    # pedido con total negativo / descuento. Otros PATCH del panel sí validan; este no.
    def test_panel_producto_con_precio_o_stock_invalidos(self):
        self.entrar_panel()
        malos = []
        for body in [{'precio': 'abc'}, {'precio': -500}, {'precio': 'nan'}, {'precio': 10**15}, {'stock': 'abc'},
                     {'stock': -5}, {'stock': 10**9}, {'precio': ['1']}, {'nombre': ['x']}, {'nombre': 'x' * 100_000}]:
            r = self.client.post(P + 'products/', {'nombre': 'P', 'precio': 10, 'stock': 1, **body}, format='json')
            if r.status_code != 400:
                malos.append((body, r.status_code))
        self.assertEqual(malos, [])
        self.assertEqual(Productos.objects.filter(id_restaurante=self.rest_a).count(), 2)

    def test_panel_producto_valido_basico(self):
        self.entrar_panel()
        r = self.client.post(P + 'products/', {'nombre': "Pizza'; --", 'precio': 10, 'stock': 3}, format='json')
        self.assertEqual(r.status_code, 201, r.content)

    # BUG: apps/core/views.py:155-161: la imagen del producto se guarda sin validar tipo/extension y se sirve desde
    # /media/ con el mismo origen que la API: subir un .html/.svg con <script> da XSS almacenado (el nombre sí lo
    # sanea Django, el contenido no). Debería aceptar solo imagenes.
    def test_panel_no_acepta_archivos_que_no_son_imagen(self):
        self.entrar_panel()
        with tempfile.TemporaryDirectory() as tmp, override_settings(MEDIA_ROOT=tmp):
            f = SimpleUploadedFile('x.html', b'<script>alert(1)</script>', content_type='text/html')
            r = self.client.post(P + 'products/', {'nombre': 'P', 'precio': 10, 'stock': 1, 'imagen': f}, format='multipart')
            self.assertEqual(r.status_code, 400)

    def test_panel_nombre_de_archivo_con_path_traversal_no_sale_de_media(self):
        self.entrar_panel()
        with tempfile.TemporaryDirectory() as tmp, override_settings(MEDIA_ROOT=tmp):
            f = SimpleUploadedFile('../../../evil.png', b'\x89PNG', content_type='image/png')
            r = self.client.post(P + 'products/', {'nombre': 'P', 'precio': 10, 'stock': 1, 'imagen': f}, format='multipart')
            self.assertNo500(r)
            if r.status_code == 201:
                self.assertTrue(r.data['imagen_url'].split('/media/')[1].startswith('productos/'))
                self.assertNotIn('..', r.data['imagen_url'])

    def test_ids_no_numericos_en_urls_publicas_y_del_panel(self):
        self.entrar_panel()
        for url in [C + 'restaurantes/abc/productos/', C + "restaurantes/1'/productos/", C + 'restaurantes/-1/productos/',
                    P + 'orders/abc/', P + 'orders/-1/', P + 'categorias/abc/', P + "categorias/1' OR 1=1/"]:
            for metodo in ('get', 'put', 'delete'):
                r = getattr(self.client, metodo)(url)
                self.assertEqual(r.status_code, 404, (metodo, url))

    def test_ids_enormes_en_urls_del_panel(self):
        self.entrar_panel()
        big = 10**30
        for metodo, url in [('put', P + f'orders/{big}/'), ('put', P + f'categorias/{big}/'), ('delete', P + f'categorias/{big}/')]:
            r = getattr(self.client, metodo)(url, {'estado': 'x', 'nombre': 'x'}, format='json')
            self.assertEqual(r.status_code, 404, (metodo, url))

    # BUG: views.py:329 (MenuView) y views.py:473 (Productos.in_bulk): un id fuera del rango de bigint
    # (/api/cliente/restaurantes/1000000000000000000000000000000/productos/, o id_producto=10**30 en el body del
    # pedido) llega a la base y explota -> 500 (OverflowError en SQLite, "out of range" en Postgres).
    def test_ids_enormes_en_menu_y_en_pedido_no_dan_500(self):
        malos = []
        r = self.client.get(C + f'restaurantes/{10**30}/productos/')
        if r.status_code >= 500:
            malos.append('menu')
        self.entrar_cliente()
        r = self.client.post(C + 'pedidos/', {'id_restaurante': self.rest_a.pk, 'direccion_entrega': 'x', 'metodo_pago': 'efectivo',
                                              'items': [{'id_producto': 10**30, 'cantidad': 1}]}, format='json')
        if r.status_code >= 500:
            malos.append('pedido')
        self.assertEqual(malos, [])


class ContentTypeYCuerposTest(Base):

    def test_content_type_incorrecto_no_crea_nada(self):
        cuerpo = json.dumps({'nombre': 'X', 'email': 'ct@mail.com', 'telefono': '1144443333', 'contrasena': 'secreto123'})
        for ct in ['text/plain', 'application/xml', 'text/html', 'application/octet-stream']:
            r = self.client.post(C + 'registro/', data=cuerpo, content_type=ct)
            self.assertLess(r.status_code, 500, ct)
            self.assertNotEqual(r.status_code, 201, ct)
        self.assertFalse(Usuarios.objects.filter(email='ct@mail.com').exists())

    def test_formulario_se_acepta_pero_no_permite_bool_repartidor(self):
        r = self.client.post(C + 'registro/', {'nombre': 'X', 'email': 'f@mail.com', 'telefono': '1144443333',
                                               'contrasena': 'secreto123', 'repartidor': 'true', 'vehiculo': 'Moto', 'rol': 'true'})
        self.assertEqual(r.status_code, 201)
        self.assertEqual(r.data['perfil'], 'cliente')
        self.assertIs(Usuarios.objects.get(email='f@mail.com').rol, False)

    def test_json_malformado(self):
        for cuerpo in ['{', '{"a":', 'null null', '\x00\x00', '{"a": NaN}', '[' * 5000]:
            r = self.client.post(C + 'login/', data=cuerpo, content_type='application/json')
            self.assertEqual(r.status_code, 400, cuerpo[:10])

    def test_cuerpo_vacio(self):
        for url in [C + 'registro/', C + 'login/', C + 'token/refresh/', P + 'login/', P + 'token/refresh/']:
            r = self.client.post(url, data='', content_type='application/json')
            self.assertLess(r.status_code, 500, url)
            self.assertGreaterEqual(r.status_code, 400, url)

    # Cuerpos JSON que no son un objeto. Cada endpoint en su test para ver cuál rompe.
    def no500(self, metodo, url, token=None):
        if token:
            self.con(token)
        malos = []
        for cuerpo in NO_OBJETO:
            r = getattr(self.client, metodo)(url, data=json.dumps(cuerpo), content_type='application/json')
            if r.status_code >= 500:
                malos.append((cuerpo, r.status_code))
        self.assertEqual(malos, [], f'{metodo} {url}')

    # BUG: apps/clientes/views.py:135,185,213 y siguientes: request.data.get(...) asume un dict. Con un body JSON
    # que es lista/string/número/true/null request.data no tiene .get -> AttributeError -> 500 en registro, login y
    # refresh del cliente (sin autenticación), crear pedido, estado de pedido del repartidor y login del panel.
    # En PATCH perfil / PATCH estado repartidor (`'x' in datos`) rompen solo número/true/null.
    def test_registro_cliente_con_cuerpo_no_objeto(self):
        self.no500('post', C + 'registro/')

    def test_login_cliente_con_cuerpo_no_objeto(self):
        self.no500('post', C + 'login/')

    def test_refresh_cliente_con_cuerpo_no_objeto(self):
        self.no500('post', C + 'token/refresh/')

    def test_login_panel_con_cuerpo_no_objeto(self):
        self.no500('post', P + 'login/')

    def test_crear_pedido_con_cuerpo_no_objeto(self):
        self.no500('post', C + 'pedidos/', self.login_cliente()['access'])

    def test_patch_perfil_con_cuerpo_no_objeto(self):
        self.no500('patch', C + 'perfil/', self.login_cliente()['access'])

    def test_patch_estado_repartidor_con_cuerpo_no_objeto(self):
        self.no500('patch', R + 'estado/', self.nuevo_repartidor()['access'])

    def test_estado_pedido_repartidor_con_cuerpo_no_objeto(self):
        tok = self.nuevo_repartidor()['access']
        self.no500('post', R + f'pedidos/{self.pedido().pk}/estado/', tok)

    def test_panel_con_cuerpo_no_objeto(self):
        tok = self.login_panel()['access']
        p = self.pedido()
        for metodo, url in [('post', P + 'products/'), ('post', P + 'categorias/'), ('patch', P + 'restaurante/'),
                            ('put', P + f'orders/{p.pk}/'), ('put', P + f'categorias/{self.cat_a.pk}/')]:
            self.no500(metodo, url, tok)

    def test_login_panel_con_nombre_lista_o_dict(self):
        for nombre in [['admin_a'], {'a': 1}, 5, True]:
            r = self.client.post(P + 'login/', {'nombre': nombre, 'contrasena': 'secreto123'}, format='json')
            self.assertLess(r.status_code, 500, nombre)

    def test_login_cliente_con_tipos_raros_no_da_500(self):
        for email, pwd in [(['a'], 'x'), ({'a': 1}, 'x'), (5, 'x'), (None, None), ('ana@mail.com', ['x']), ('ana@mail.com', 5),
                           ('ana@mail.com', {'a': 1}), ('ana@mail.com', True)]:
            r = self.client.post(C + 'login/', {'email': email, 'contrasena': pwd}, format='json')
            self.assertIn(r.status_code, (400, 401), (email, pwd))

    def test_refresh_con_tipos_raros(self):
        for valor in [None, 5, [], {}, True, '', ' ', 'a.b.c', 'x' * 100_000]:
            for url in (C + 'token/refresh/', P + 'token/refresh/'):
                r = self.client.post(url, {'refresh': valor}, format='json')
                self.assertIn(r.status_code, (400, 401), (url, valor if not isinstance(valor, str) else valor[:10]))


# =====================================================================
# 7. Fuerza bruta
# =====================================================================

class FuerzaBrutaTest(Base):

    def limite(self, n=3):
        return patch.object(ScopedRateThrottle, 'THROTTLE_RATES', {'login': f'{n}/min'})

    def test_throttling_login_cliente(self):
        with self.limite():
            codigos = [self.client.post(C + 'login/', {'email': 'ana@mail.com', 'contrasena': 'mala'}, format='json').status_code
                       for _ in range(5)]
            # aun con la contraseña correcta, bloqueado
            ok = self.client.post(C + 'login/', {'email': 'ana@mail.com', 'contrasena': 'secreto123'}, format='json')
        self.assertEqual(codigos, [401, 401, 401, 429, 429])
        self.assertEqual(ok.status_code, 429)
        self.assertNotIn('access', ok.data)

    def test_throttling_login_panel(self):
        with self.limite():
            codigos = [self.client.post(P + 'login/', {'nombre': 'admin_a', 'contrasena': 'mala'}, format='json').status_code
                       for _ in range(5)]
            ok = self.client.post(P + 'login/', {'nombre': 'admin_a', 'contrasena': 'secreto123'}, format='json')
        self.assertEqual(codigos, [401, 401, 401, 429, 429])
        self.assertEqual(ok.status_code, 429)

    def test_throttling_registro_y_no_crea_cuentas_pasado_el_limite(self):
        antes = Usuarios.objects.count()
        with self.limite():
            codigos = [self.client.post(C + 'registro/', {'nombre': 'X', 'email': f'r{i}@mail.com', 'telefono': '1144443333',
                                                          'contrasena': 'secreto123'}, format='json').status_code
                       for i in range(6)]
        self.assertEqual(codigos, [201, 201, 201, 429, 429, 429])
        self.assertEqual(Usuarios.objects.count(), antes + 3)

    # BUG: config/settings.py: el throttling usa REMOTE_ADDR salvo que venga X-Forwarded-For y NUM_PROXIES no está
    # configurado: DRF entonces toma el header como identidad. Rotando X-Forwarded-For en cada intento un atacante
    # nunca llega al 429 y puede hacer fuerza bruta ilimitada a login/registro. Fix: NUM_PROXIES en REST_FRAMEWORK.
    def test_throttling_no_se_evade_rotando_x_forwarded_for(self):
        with self.limite():
            codigos = [self.client.post(C + 'login/', {'email': 'ana@mail.com', 'contrasena': 'mala'}, format='json',
                                        HTTP_X_FORWARDED_FOR=f'10.0.0.{i}').status_code for i in range(8)]
        self.assertIn(429, codigos)

    def test_el_limite_es_por_ip(self):
        with self.limite(2):
            for _ in range(3):
                self.client.post(C + 'login/', {'email': 'ana@mail.com', 'contrasena': 'mala'}, format='json')
            otra = self.client.post(C + 'login/', {'email': 'ana@mail.com', 'contrasena': 'secreto123'}, format='json',
                                    REMOTE_ADDR='203.0.113.7')
        self.assertEqual(otra.status_code, 200)


# =====================================================================
# 8. Fugas de información
# =====================================================================

class FugasDeInformacionTest(Base):

    def test_login_cliente_mismo_mensaje_para_email_inexistente_y_clave_mala(self):
        a = self.client.post(C + 'login/', {'email': 'ana@mail.com', 'contrasena': 'mala'}, format='json')
        b = self.client.post(C + 'login/', {'email': 'nadie@mail.com', 'contrasena': 'mala'}, format='json')
        c = self.client.post(C + 'login/', {'email': 'aa@mail.com', 'contrasena': 'secreto123'}, format='json')  # admin
        self.assertEqual((a.status_code, a.data), (b.status_code, b.data))
        self.assertEqual((a.status_code, a.data), (c.status_code, c.data))
        self.assertEqual(a.content, b.content)

    def test_login_panel_mismo_mensaje_para_usuario_inexistente_y_clave_mala(self):
        a = self.client.post(P + 'login/', {'nombre': 'admin_a', 'contrasena': 'mala'}, format='json')
        b = self.client.post(P + 'login/', {'nombre': 'nadie', 'contrasena': 'mala'}, format='json')
        self.assertEqual((a.status_code, a.content), (b.status_code, b.content))

    def test_registro_no_devuelve_hash_ni_contrasena(self):
        r = self.client.post(C + 'registro/', {'nombre': 'X', 'email': 'x@mail.com', 'telefono': '1144443333',
                                               'contrasena': 'secreto123', 'repartidor': True, 'vehiculo': 'Moto'}, format='json')
        self.assertEqual(r.status_code, 201)
        self.assertSinSecretos(r)
        self.assertNotIn('contrasena', r.data)

    def test_respuestas_de_cliente_sin_hashes(self):
        self.pedido()
        d = self.login_cliente()
        self.assertSinSecretos(self.client.post(C + 'login/', {'email': 'ana@mail.com', 'contrasena': 'secreto123'}, format='json'))
        self.con(d['access'])
        self.assertSinSecretos(self.client.get(C + 'perfil/'))
        self.assertSinSecretos(self.client.patch(C + 'perfil/', {'nombre': 'N'}, format='json'))
        self.assertSinSecretos(self.client.get(C + 'pedidos/'))
        r = self.client.post(C + 'pedidos/', {'id_restaurante': self.rest_a.pk, 'direccion_entrega': 'x', 'metodo_pago': 'efectivo',
                                              'items': [{'id_producto': self.coca.pk, 'cantidad': 1}]}, format='json')
        self.assertEqual(r.status_code, 201)
        self.assertSinSecretos(r)
        self.assertSinSecretos(self.client.get(C + 'restaurantes/'))
        self.assertSinSecretos(self.client.get(C + f'restaurantes/{self.rest_a.pk}/productos/'))
        self.assertSinSecretos(self.client.post(C + 'token/refresh/', {'refresh': d['refresh']}, format='json'))
        self.assertSinSecretos(self.client.patch(C + 'perfil/', {'contrasena_actual': 'secreto123', 'contrasena_nueva': 'nueva-123'}, format='json'))

    def test_respuestas_de_repartidor_sin_hashes_ni_datos_internos(self):
        p = self.pedido()
        self.nuevo_repartidor()
        for r in [self.client.get(R + 'estado/'), self.client.get(R + 'disponibles/'), self.client.post(R + f'pedidos/{p.pk}/tomar/'),
                  self.client.get(R + 'pedidos/'), self.client.post(R + f'pedidos/{p.pk}/estado/', {'estado': 'en camino'}, format='json'),
                  self.client.patch(R + 'estado/', {'vehiculo': 'Auto'}, format='json')]:
            self.assertSinSecretos(r)
            self.assertNotIn('"email"', r.content.decode())

    def test_el_cliente_ve_solo_datos_publicos_del_repartidor(self):
        juan = self.nuevo_repartidor()
        p = self.pedido()
        self.client.post(R + f'pedidos/{p.pk}/tomar/')
        self.entrar_cliente('ana@mail.com')
        rep = self.client.get(C + 'pedidos/').data[0]['repartidor']
        self.assertEqual(set(rep), {'nombre', 'telefono', 'vehiculo'})
        self.assertSinSecretos(self.client.get(C + 'pedidos/'))
        self.assertNotIn(str(juan['id_usuario']) + '"', json.dumps(rep))

    def test_respuestas_del_panel_sin_hashes(self):
        self.pedido()
        d = self.login_panel()
        self.assertSinSecretos(self.client.post(P + 'login/', {'nombre': 'admin_a', 'contrasena': 'secreto123'}, format='json'))
        self.con(d['access'])
        for url in [P + 'orders/', P + 'products/', P + 'categorias/', P + 'restaurante/']:
            self.assertSinSecretos(self.client.get(url))
        self.assertSinSecretos(self.client.post(P + 'token/refresh/', {'refresh': d['refresh']}, format='json'))

    def test_panel_login_no_da_pistas_sobre_cuentas_de_cliente(self):
        # Un cliente (sin restaurante) con clave mala recibe lo mismo que un usuario inexistente.
        a = self.client.post(P + 'login/', {'nombre': 'Ana', 'contrasena': 'mala'}, format='json')
        b = self.client.post(P + 'login/', {'nombre': 'Nadie', 'contrasena': 'mala'}, format='json')
        self.assertEqual((a.status_code, a.content), (b.status_code, b.content))

    def test_errores_no_filtran_trazas_ni_sql(self):
        self.entrar_cliente()
        for r in [self.client.get(C + 'perfil/'), self.client.post(C + 'pedidos/', {'id_restaurante': "1' OR 1=1"}, format='json'),
                  self.client.post(C + 'login/', {'email': "'", 'contrasena': "'"}, format='json')]:
            texto = r.content.decode().lower()
            for pista in ('traceback', 'select ', 'psycopg', 'sqlite', 'django.', 'file "'):
                self.assertNotIn(pista, texto)

    def test_respuesta_de_perfil_no_incluye_rol_ni_restaurante(self):
        self.entrar_cliente()
        r = self.client.get(C + 'perfil/')
        self.assertEqual(set(r.data), {'id_usuario', 'nombre', 'email', 'telefono', 'direccion'})

    def test_menu_publico_no_muestra_productos_ocultos_ni_ajenos(self):
        Productos.objects.create(nombre='Oculto', precio=1.0, disponible=False, id_restaurante=self.rest_a)
        r = self.client.get(C + f'restaurantes/{self.rest_a.pk}/productos/')
        self.assertEqual(sorted(p['nombre'] for p in r.data), ['Coca', 'Muzza'])
        for p in r.data:
            self.assertEqual(set(p), {'id_producto', 'nombre', 'descripcion', 'precio', 'imagen_url', 'stock'})


# =====================================================================
# 9. Arreglos posteriores a la auditoría
# =====================================================================

class ArreglosTest(Base):

    def test_cambiar_contrasena_devuelve_tokens_nuevos_que_sirven(self):
        self.entrar_cliente('ana@mail.com')
        r = self.client.patch(C + 'perfil/', {'contrasena_actual': 'secreto123', 'contrasena_nueva': 'otra-clave-1'}, format='json')
        self.assertEqual(r.status_code, 200)
        self.con(r.data['access'])
        self.assertEqual(self.client.get(C + 'perfil/').status_code, 200)
        self.anon()
        self.assertEqual(self.client.post(C + 'token/refresh/', {'refresh': r.data['refresh']}, format='json').status_code, 200)

    def test_perfil_sin_cambio_de_contrasena_no_trae_tokens(self):
        self.entrar_cliente('ana@mail.com')
        r = self.client.patch(C + 'perfil/', {'nombre': 'Ana B'}, format='json')
        self.assertNotIn('access', r.data)

    def test_pedido_con_mas_de_100_unidades_o_50_lineas(self):
        self.entrar_cliente('ana@mail.com')
        for items in ([{'id_producto': self.coca.pk, 'cantidad': 101}], [{'id_producto': self.coca.pk, 'cantidad': 1}] * 51):
            r = self.client.post(C + 'pedidos/', {'id_restaurante': self.rest_a.pk, 'direccion_entrega': 'x',
                                                  'metodo_pago': 'efectivo', 'items': items}, format='json')
            self.assertEqual(r.status_code, 400)

    def test_panel_acepta_png_real_con_nombre_generado(self):
        import io
        from PIL import Image
        buf = io.BytesIO()
        Image.new('RGB', (2, 2)).save(buf, 'PNG')
        self.entrar_panel()
        with tempfile.TemporaryDirectory() as tmp, override_settings(MEDIA_ROOT=tmp):
            f = SimpleUploadedFile('malo.html', buf.getvalue(), content_type='text/html')
            r = self.client.post(P + 'products/', {'nombre': 'P', 'precio': 10, 'stock': 1, 'imagen': f}, format='multipart')
            self.assertEqual(r.status_code, 201, r.content)
            self.assertTrue(r.data['imagen_url'].endswith('.png'))
            self.assertNotIn('malo', r.data['imagen_url'])
