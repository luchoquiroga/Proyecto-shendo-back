# python manage.py test apps.clientes --settings=config.settings_test
from datetime import datetime, time, timedelta
from unittest.mock import patch
from zoneinfo import ZoneInfo

from django.utils import timezone
from rest_framework.test import APITestCase
from rest_framework_simplejwt.tokens import RefreshToken

from apps.core.models import Categoria, DetallePedido, HorariosRestaurante, Pagos, Pedidos, Productos, Restaurantes, Usuarios

# Hash real de Django para "secreto123" (1000 iteraciones).
HASH_SECRETO = 'pbkdf2_sha256$1000$abcdefghijklmnopqrstuv$PpVBcVFaPEauYjw1N/ZRHhsUgyj6j6rR3geTJGtq/UU='

BASE = '/api/cliente/'


class ClienteTest(APITestCase):

    def restaurante(self, nombre='Pizzería', estado=None):
        return Restaurantes.objects.create(nombre=nombre, estado=estado)

    def producto(self, restaurante, nombre='Muzza', precio=1000.0, stock=None, disponible=True):
        return Productos.objects.create(
            nombre=nombre, precio=precio, stock=stock, disponible=disponible, id_restaurante=restaurante
        )

    def usuario(self, email='ana@mail.com', rol=False, contrasena=HASH_SECRETO, restaurante=None):
        return Usuarios.objects.create(
            nombre='Ana', email=email, telefono='1155556666', direccion='Calle 1',
            contrasena=contrasena, rol=rol, id_restaurante=restaurante,
        )

    def loguear(self, email='ana@mail.com'):
        r = self.client.post(BASE + 'login/', {'email': email, 'contrasena': 'secreto123'}, format='json')
        self.assertEqual(r.status_code, 200, r.data)
        self.client.credentials(HTTP_AUTHORIZATION='Bearer ' + r.data['access'])
        return r.data

    def assertError(self, r, codigo, mensaje):
        self.assertEqual(r.status_code, codigo, r.data)
        self.assertEqual(r.data, {'error': mensaje})


class RegistroTest(ClienteTest):
    URL = BASE + 'registro/'
    OK = {
        'nombre': ' Ana Pérez ', 'email': ' Ana@Mail.com ', 'contrasena': 'secreto123',
        'telefono': '+54 9 11 1234-5678', 'direccion': 'Av. Siempreviva 742',
    }

    def test_registro_ok(self):
        r = self.client.post(self.URL, self.OK, format='json')
        self.assertEqual(r.status_code, 201, r.data)
        self.assertEqual(r.data['nombre'], 'Ana Pérez')
        self.assertEqual(r.data['email'], 'ana@mail.com')
        self.assertEqual(r.data['telefono'], '+54 9 11 1234-5678')
        self.assertTrue(r.data['access'] and r.data['refresh'])
        u = Usuarios.objects.get(pk=r.data['id_usuario'])
        self.assertIs(u.rol, False)
        self.assertIsNone(u.id_restaurante)
        self.assertTrue(u.contrasena.startswith('pbkdf2_sha256$'))

    def test_direccion_opcional(self):
        datos = {k: v for k, v in self.OK.items() if k != 'direccion'}
        r = self.client.post(self.URL, datos, format='json')
        self.assertEqual(r.status_code, 201, r.data)
        self.assertEqual(r.data['direccion'], '')

    def test_errores(self):
        casos = [
            ({'nombre': '  '}, 'Nombre, email, teléfono y contraseña son requeridos'),
            ({'telefono': None}, 'Nombre, email, teléfono y contraseña son requeridos'),
            ({'telefono': '11-ABC-2222'}, 'El teléfono no es válido'),
            ({'telefono': '12345'}, 'El teléfono no es válido'),
            ({'telefono': '1234567890123456'}, 'El teléfono no es válido'),
            ({'contrasena': '12345'}, 'La contraseña debe tener al menos 6 caracteres'),
        ]
        for cambio, mensaje in casos:
            with self.subTest(cambio=cambio):
                r = self.client.post(self.URL, {**self.OK, **cambio}, format='json')
                self.assertError(r, 400, mensaje)
        self.assertEqual(Usuarios.objects.count(), 0)

    def test_email_repetido_sin_importar_mayusculas_ni_rol(self):
        self.usuario(email='ana@mail.com', rol=True, contrasena='plano')
        r = self.client.post(self.URL, self.OK, format='json')
        self.assertError(r, 400, 'Ya existe una cuenta con ese email')


class LoginTest(ClienteTest):
    URL = BASE + 'login/'

    def test_login_ok_con_hash_de_django(self):
        u = self.usuario(email='Ana@Mail.com')
        r = self.client.post(self.URL, {'email': ' ana@mail.com ', 'contrasena': 'secreto123'}, format='json')
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual(r.data['id_usuario'], u.id_usuario)
        self.assertEqual(set(r.data), {'access', 'refresh', 'id_usuario', 'nombre', 'email', 'telefono', 'direccion', 'perfil', 'repartidor'})
        self.assertEqual((r.data['perfil'], r.data['repartidor']), ('cliente', None))

    def test_registro_y_login(self):
        self.client.post(RegistroTest.URL, RegistroTest.OK, format='json')
        self.loguear()

    def test_requeridos(self):
        r = self.client.post(self.URL, {'email': 'ana@mail.com'}, format='json')
        self.assertError(r, 400, 'Email y contraseña son requeridos')

    def test_credenciales_invalidas(self):
        self.usuario()
        self.usuario(email='admin@mail.com', rol=True)
        self.usuario(email='plano@mail.com', contrasena='secreto123')
        for email, contrasena in [
            ('ana@mail.com', 'otra'),
            ('nadie@mail.com', 'secreto123'),
            ('admin@mail.com', 'secreto123'),  # los admins no entran a la app
            ('plano@mail.com', 'secreto123'),  # contraseña en texto plano no valida
        ]:
            with self.subTest(email=email):
                r = self.client.post(self.URL, {'email': email, 'contrasena': contrasena}, format='json')
                self.assertError(r, 401, 'Email o contraseña incorrectos')


class CatalogoTest(ClienteTest):

    def test_restaurantes(self):
        self.restaurante('B Abierto', estado='abierto')
        self.restaurante('A Cerrado', estado=' Cerrado ')
        self.restaurante('C Sin estado')
        r = self.client.get(BASE + 'restaurantes/')
        self.assertEqual(r.status_code, 200)
        self.assertEqual([(x['nombre'], x['abierto']) for x in r.data],
                         [('A Cerrado', False), ('B Abierto', True), ('C Sin estado', True)])

    def test_precio_desde_solo_cuenta_disponibles(self):
        rest, vacio = self.restaurante('A'), self.restaurante('B')
        self.producto(rest, precio=900.0)
        self.producto(rest, precio=500.0, disponible=False)
        self.producto(rest, precio=1200.0)
        r = self.client.get(BASE + 'restaurantes/')
        self.assertEqual([x['precio_desde'] for x in r.data], [900.0, None])

    def test_categorias_como_lista_y_filtro(self):
        variado = self.restaurante('A Variado')
        burger = self.restaurante('B Burger')
        self.restaurante('C Sin categorías')
        for rest, nombres in [(variado, ['Pizza', 'Empanadas', 'Hamburguesas']), (burger, ['Hamburguesas', 'Papas'])]:
            for nombre in nombres:
                Categoria.objects.create(nombre=nombre, id_restaurante=rest)

        r = self.client.get(BASE + 'restaurantes/')
        self.assertEqual([x['categorias'] for x in r.data],
                         [['Empanadas', 'Hamburguesas', 'Pizza'], ['Hamburguesas', 'Papas'], []])

        r = self.client.get(BASE + 'restaurantes/', {'categoria': 'hamburguesa'})
        self.assertEqual([x['nombre'] for x in r.data], ['A Variado', 'B Burger'])

        r = self.client.get(BASE + 'restaurantes/', {'categoria': 'PIZZA'})
        self.assertEqual([x['nombre'] for x in r.data], ['A Variado'])

    def test_categorias_cargadas_con_comas_se_separan(self):
        rest = self.restaurante('A')
        Categoria.objects.create(nombre='pizza, empanadas,  Hamburguesas ,', id_restaurante=rest)
        Categoria.objects.create(nombre='Pizza', id_restaurante=rest)
        r = self.client.get(BASE + 'restaurantes/')
        self.assertEqual(r.data[0]['categorias'], ['empanadas', 'Hamburguesas', 'pizza'])

    def test_menu_solo_disponibles_del_restaurante(self):
        rest, otro = self.restaurante(), self.restaurante('Otro')
        self.producto(rest, 'B Fugazza', precio=None, stock=3)
        self.producto(rest, 'A Muzza')
        self.producto(rest, 'No disponible', disponible=False)
        self.producto(otro, 'De otro')
        r = self.client.get(f'{BASE}restaurantes/{rest.id_restaurante}/productos/')
        self.assertEqual(r.status_code, 200)
        self.assertEqual([p['nombre'] for p in r.data], ['A Muzza', 'B Fugazza'])
        self.assertEqual(r.data[0]['stock'], None)
        self.assertEqual(r.data[1]['precio'], 0.0)
        self.assertEqual(r.data[1]['stock'], 3)

    def test_menu_de_restaurante_inexistente(self):
        r = self.client.get(BASE + 'restaurantes/999/productos/')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data, [])


class PedidosTest(ClienteTest):
    URL = BASE + 'pedidos/'

    def setUp(self):
        self.rest = self.restaurante()
        self.muzza = self.producto(self.rest, 'Muzza', precio=1000.0, stock=5)
        self.coca = self.producto(self.rest, 'Coca', precio=500.0)  # sin control de stock
        self.cliente = self.usuario()
        self.loguear()

    def pedir(self, **cambios):
        datos = {
            'id_restaurante': self.rest.id_restaurante,
            'direccion_entrega': ' Calle 1 ',
            'metodo_pago': 'efectivo',
            'items': [
                {'id_producto': self.muzza.id_producto, 'cantidad': 2, 'precio': 1},
                {'id_producto': self.coca.id_producto, 'cantidad': 30},
            ],
            **cambios,
        }
        return self.client.post(self.URL, datos, format='json')

    def stock(self, producto):
        return Productos.objects.get(pk=producto.pk).stock

    def assertNadaCreado(self):
        self.assertEqual(Pedidos.objects.count(), 0)
        self.assertEqual(DetallePedido.objects.count(), 0)
        self.assertEqual(Pagos.objects.count(), 0)
        self.assertEqual(self.stock(self.muzza), 5)

    def test_sin_token(self):
        self.client.credentials()
        self.assertError(self.client.get(self.URL), 401, 'Token inválido o no enviado')
        self.assertError(self.pedir(), 401, 'Token inválido o no enviado')

    def test_token_invalido_o_de_otro_tipo(self):
        refresh = RefreshToken()
        refresh['tipo'] = 'cliente'
        refresh['id_usuario'] = self.cliente.id_usuario
        panel = RefreshToken()
        panel['id_restaurante'] = self.rest.id_restaurante
        panel['id_usuario'] = self.cliente.id_usuario
        for token in ['basura', str(refresh), str(panel.access_token)]:
            with self.subTest(token=token[:20]):
                self.client.credentials(HTTP_AUTHORIZATION='Bearer ' + token)
                self.assertError(self.client.get(self.URL), 401, 'Token inválido o no enviado')

    def test_sin_pedidos(self):
        r = self.client.get(self.URL)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data, [])

    def test_crear_pedido(self):
        r = self.pedir()
        self.assertEqual(r.status_code, 201, r.data)
        self.assertEqual(r.data['total'], 17000.0)  # precios de la base: 2*1000 + 30*500
        self.assertEqual(r.data['estado'], 'pendiente')
        self.assertEqual(r.data['direccion_entrega'], 'Calle 1')
        self.assertEqual(r.data['restaurante'], 'Pizzería')
        self.assertEqual(r.data['metodo_pago'], 'Efectivo')
        self.assertIs(r.data['pagado'], False)
        self.assertEqual(r.data['items'], [
            {'nombre': 'Muzza', 'cantidad': 2, 'subtotal': 2000.0},
            {'nombre': 'Coca', 'cantidad': 30, 'subtotal': 15000.0},
        ])
        self.assertEqual(self.stock(self.muzza), 3)
        self.assertIsNone(self.stock(self.coca))

        pedido = Pedidos.objects.get()
        self.assertEqual(pedido.id_usuario_id, self.cliente.id_usuario)
        self.assertEqual(pedido.estado, 'pendiente')
        pago = Pagos.objects.get()
        self.assertEqual((pago.metodo_pago, pago.monto, pago.estado_pago), ('efectivo', 17000.0, 'pendiente'))

        listado = self.client.get(self.URL)
        self.assertEqual(listado.data, [r.data])

    def test_errores_de_validacion(self):
        cerrado = self.restaurante('Cerrado', estado='cerrado')
        otro = self.restaurante('Otro')
        de_otro = self.producto(otro, 'De otro')
        no_disp = self.producto(self.rest, 'No disp', disponible=False)
        casos = [
            ({'id_restaurante': 999}, 404, 'Restaurante no encontrado'),
            ({'id_restaurante': None}, 404, 'Restaurante no encontrado'),
            ({'id_restaurante': cerrado.id_restaurante}, 400, 'El restaurante está cerrado'),
            ({'direccion_entrega': '   '}, 400, 'La dirección de entrega es requerida'),
            ({'metodo_pago': 'Efectivo'}, 400, 'Elegí un método de pago'),
            ({'items': []}, 400, 'El pedido está vacío'),
            ({'items': [{'id_producto': de_otro.id_producto, 'cantidad': 1}]}, 400,
             'Hay productos que ya no están disponibles'),
            ({'items': [{'id_producto': no_disp.id_producto, 'cantidad': 1}]}, 400,
             'Hay productos que ya no están disponibles'),
            ({'items': [{'id_producto': 999, 'cantidad': 1}]}, 400,
             'Hay productos que ya no están disponibles'),
            ({'items': [{'id_producto': self.muzza.id_producto, 'cantidad': 0}]}, 400,
             'Hay productos que ya no están disponibles'),
            ({'items': [{'id_producto': self.muzza.id_producto}]}, 400,
             'Hay productos que ya no están disponibles'),
        ]
        for cambio, codigo, mensaje in casos:
            with self.subTest(cambio=cambio):
                self.assertError(self.pedir(**cambio), codigo, mensaje)
        self.assertNadaCreado()

    def test_sin_stock_en_la_segunda_linea_no_descuenta_la_primera(self):
        fugazza = self.producto(self.rest, 'Fugazza', stock=1)
        r = self.pedir(items=[
            {'id_producto': self.muzza.id_producto, 'cantidad': 2},
            {'id_producto': fugazza.id_producto, 'cantidad': 2},
        ])
        self.assertError(r, 400, 'No hay stock suficiente de "Fugazza" (quedan 1)')
        self.assertNadaCreado()
        self.assertEqual(self.stock(fugazza), 1)

    def test_mismo_producto_en_dos_lineas_suma_para_el_stock(self):
        r = self.pedir(items=[
            {'id_producto': self.muzza.id_producto, 'cantidad': 3},
            {'id_producto': self.muzza.id_producto, 'cantidad': 3},
        ])
        # el stock se lee después del rollback: vuelve a 5
        self.assertError(r, 400, 'No hay stock suficiente de "Muzza" (quedan 5)')
        self.assertNadaCreado()

    def test_listado_solo_mios_y_del_mas_nuevo_al_mas_viejo(self):
        otro = self.usuario(email='otro@mail.com')
        ahora = timezone.now()
        viejo = Pedidos.objects.create(fecha=ahora - timedelta(days=1), estado=None, total=None,
                                       id_usuario=self.cliente, id_restaurante=self.rest)
        nuevo = Pedidos.objects.create(fecha=ahora, estado='en camino', total=10.0,
                                       id_usuario=self.cliente, id_restaurante=self.rest)
        Pedidos.objects.create(fecha=ahora, estado='pendiente', id_usuario=otro, id_restaurante=self.rest)
        DetallePedido.objects.create(id_pedido=nuevo, id_producto=None, cantidad=2, precio_unitario=None)
        Pagos.objects.create(id_pedido=nuevo, metodo_pago='transferencia', monto=10.0, estado_pago='pagado')

        r = self.client.get(self.URL)
        self.assertEqual([p['id_pedido'] for p in r.data], [nuevo.id_pedido, viejo.id_pedido])
        n, v = r.data
        self.assertEqual(n['items'], [{'nombre': 'Producto eliminado', 'cantidad': 2, 'subtotal': 0.0}])
        self.assertEqual((n['metodo_pago'], n['pagado']), ('Transferencia', True))
        self.assertEqual((v['estado'], v['total'], v['metodo_pago'], v['pagado'], v['items']),
                         ('pendiente', 0.0, None, False, []))


# ---------- Horarios, envío, retiro, pedido mínimo, imágenes ----------

def ar(dia, hora, minuto=0):
    """Fecha local de Argentina. 2026-11-16 es lunes (ISO 1); dia = 1..7."""
    return datetime(2026, 11, 15 + dia, hora, minuto, tzinfo=ZoneInfo('America/Argentina/Buenos_Aires'))


def con_ahora(momento):
    return patch('apps.clientes.views.ahora_ar', return_value=momento)


def horario(rest, dia, apertura, cierre, activo=True):
    return HorariosRestaurante.objects.create(
        id_restaurante=rest, dia_semana=dia, hora_apertura=apertura, hora_cierre=cierre, activo=activo
    )


class HorariosTest(ClienteTest):

    def listar(self, momento):
        with con_ahora(momento):
            r = self.client.get(BASE + 'restaurantes/')
        self.assertEqual(r.status_code, 200)
        return r.data

    def test_campos_nuevos_con_valores_por_defecto(self):
        self.restaurante('Sin datos')
        x = self.client.get(BASE + 'restaurantes/').data[0]
        self.assertEqual((x['costo_envio'], x['pedido_minimo'], x['horario_hoy'], x['abierto']),
                         (0.0, 0.0, None, True))

    def test_costo_envio_y_pedido_minimo_en_listado(self):
        Restaurantes.objects.create(nombre='A', costo_envio=800.0, pedido_minimo=2500.0)
        x = self.client.get(BASE + 'restaurantes/').data[0]
        self.assertEqual((x['costo_envio'], x['pedido_minimo']), (800.0, 2500.0))

    def test_abierto_y_cerrado_segun_la_hora(self):
        rest = self.restaurante('A')
        horario(rest, 1, time(9), time(13))
        horario(rest, 1, time(20), time(23, 30))
        casos = [(ar(1, 10), True), (ar(1, 9), True), (ar(1, 13), False), (ar(1, 15), False),
                 (ar(1, 21), True), (ar(1, 23, 45), False), (ar(1, 8, 59), False)]
        for momento, esperado in casos:
            with self.subTest(momento=momento):
                self.assertIs(self.listar(momento)[0]['abierto'], esperado)

    def test_horario_hoy_texto(self):
        rest = self.restaurante('A')
        horario(rest, 1, time(20), time(23, 30))  # cargado primero a propósito
        horario(rest, 1, time(9), time(13))
        horario(rest, 2, time(10), time(12), activo=False)
        self.assertEqual(self.listar(ar(1, 10))[0]['horario_hoy'], '09:00 a 13:00 y 20:00 a 23:30')
        self.assertEqual(self.listar(ar(3, 10))[0]['horario_hoy'], 'Cerrado hoy')
        self.assertIs(self.listar(ar(3, 10))[0]['abierto'], False)

    def test_horarios_inactivos_no_cuentan(self):
        rest = self.restaurante('A')
        horario(rest, 1, time(9), time(13), activo=False)
        x = self.listar(ar(1, 22))[0]
        self.assertEqual((x['horario_hoy'], x['abierto']), (None, True))

    def test_cruce_de_medianoche(self):
        rest = self.restaurante('A')
        horario(rest, 5, time(20), time(2))  # viernes 20:00 a sábado 02:00
        casos = [(ar(5, 21), True), (ar(5, 19), False), (ar(6, 1), True),
                 (ar(6, 2), False), (ar(6, 3), False), (ar(4, 1), False)]
        for momento, esperado in casos:
            with self.subTest(momento=momento):
                self.assertIs(self.listar(momento)[0]['abierto'], esperado)

    def test_cruce_de_domingo_a_lunes(self):
        rest = self.restaurante('A')
        horario(rest, 7, time(22), time(1))
        self.assertIs(self.listar(ar(1, 0, 30))[0]['abierto'], True)
        self.assertIs(self.listar(ar(1, 1, 30))[0]['abierto'], False)

    def test_estado_cerrado_gana_sobre_los_horarios(self):
        rest = self.restaurante('A', estado='cerrado')
        horario(rest, 1, time(9), time(13))
        self.assertIs(self.listar(ar(1, 10))[0]['abierto'], False)

    def test_sin_n_mas_1_en_el_listado(self):
        for i in range(4):
            horario(self.restaurante(f'R{i}'), 1, time(9), time(13))
        with con_ahora(ar(1, 10)), self.assertNumQueries(3):  # restaurantes + categorias + horarios
            self.client.get(BASE + 'restaurantes/')

    def test_pedido_en_restaurante_fuera_de_horario(self):
        rest = self.restaurante('A')
        horario(rest, 1, time(9), time(13))
        prod = self.producto(rest)
        self.usuario()
        self.loguear()
        datos = {'id_restaurante': rest.id_restaurante, 'direccion_entrega': 'Calle 1', 'metodo_pago': 'efectivo',
                 'items': [{'id_producto': prod.id_producto, 'cantidad': 1}]}
        with con_ahora(ar(1, 15)):
            self.assertError(self.client.post(BASE + 'pedidos/', datos, format='json'), 400,
                             'El restaurante está cerrado')
        with con_ahora(ar(1, 10)):
            self.assertEqual(self.client.post(BASE + 'pedidos/', datos, format='json').status_code, 201)


class ImagenMenuTest(ClienteTest):

    def test_imagen_url_absoluta_http_y_null(self):
        rest = self.restaurante()
        Productos.objects.create(nombre='A', precio=1.0, disponible=True, id_restaurante=rest,
                                 imagen_url='/media/productos/foto.avif')
        Productos.objects.create(nombre='B', precio=1.0, disponible=True, id_restaurante=rest,
                                 imagen_url='https://cdn.example.com/b.jpg')
        Productos.objects.create(nombre='C', precio=1.0, disponible=True, id_restaurante=rest, imagen_url='')
        Productos.objects.create(nombre='D', precio=1.0, disponible=True, id_restaurante=rest)
        r = self.client.get(f'{BASE}restaurantes/{rest.id_restaurante}/productos/')
        self.assertEqual([p['imagen_url'] for p in r.data],
                         ['http://testserver/media/productos/foto.avif', 'https://cdn.example.com/b.jpg',
                          None, None])


class EnvioYRetiroTest(ClienteTest):
    URL = BASE + 'pedidos/'

    def setUp(self):
        self.rest = Restaurantes.objects.create(nombre='Pizzería', costo_envio=800.0, pedido_minimo=1500.0)
        self.muzza = self.producto(self.rest, 'Muzza', precio=1000.0, stock=5)
        self.usuario()
        self.loguear()

    def pedir(self, cantidad=2, **cambios):
        datos = {'id_restaurante': self.rest.id_restaurante, 'direccion_entrega': 'Calle 1',
                 'metodo_pago': 'efectivo',
                 'items': [{'id_producto': self.muzza.id_producto, 'cantidad': cantidad}], **cambios}
        return self.client.post(self.URL, datos, format='json')

    def test_domicilio_por_defecto_suma_envio(self):
        r = self.pedir()
        self.assertEqual(r.status_code, 201, r.data)
        self.assertEqual((r.data['tipo_entrega'], r.data['costo_envio'], r.data['total']),
                         ('domicilio', 800.0, 2800.0))
        self.assertEqual(Pagos.objects.get().monto, 2800.0)
        self.assertEqual(Pedidos.objects.get().tipo_entrega, 'domicilio')
        self.assertEqual(self.client.get(self.URL).data, [r.data])

    def test_domicilio_explicito(self):
        r = self.pedir(tipo_entrega='domicilio')
        self.assertEqual((r.data['tipo_entrega'], r.data['total']), ('domicilio', 2800.0))

    def test_retiro_sin_direccion_ni_envio(self):
        r = self.pedir(tipo_entrega='retiro', direccion_entrega=None)
        self.assertEqual(r.status_code, 201, r.data)
        self.assertEqual((r.data['tipo_entrega'], r.data['costo_envio'], r.data['total']),
                         ('retiro', 0.0, 2000.0))
        self.assertIsNone(r.data['direccion_entrega'])
        pedido = Pedidos.objects.get()
        self.assertEqual((pedido.tipo_entrega, pedido.direccion_entrega), ('retiro', None))
        self.assertEqual(Pagos.objects.get().monto, 2000.0)

    def test_retiro_ignora_direccion_enviada(self):
        r = self.pedir(tipo_entrega='retiro')
        self.assertIsNone(r.data['direccion_entrega'])

    def test_domicilio_exige_direccion(self):
        self.assertError(self.pedir(direccion_entrega=''), 400, 'La dirección de entrega es requerida')

    def test_tipo_invalido(self):
        for valor in ['envio', 'RETIRO', 5, '']:
            with self.subTest(valor=valor):
                self.assertError(self.pedir(tipo_entrega=valor), 400, 'Tipo de entrega inválido')
        self.assertEqual(Pedidos.objects.count(), 0)

    def test_pedido_minimo_no_cuenta_el_envio(self):
        r = self.pedir(cantidad=1)  # 1000 < 1500 aunque con envío serían 1800
        self.assertError(r, 400, 'El pedido mínimo es $1500')
        self.assertEqual(Pedidos.objects.count(), 0)
        self.assertEqual(Pagos.objects.count(), 0)
        self.assertEqual(Productos.objects.get(pk=self.muzza.pk).stock, 5)
        self.assertError(self.pedir(cantidad=1, tipo_entrega='retiro'), 400, 'El pedido mínimo es $1500')

    def test_pedido_minimo_con_decimales(self):
        Restaurantes.objects.filter(pk=self.rest.pk).update(pedido_minimo=1500.5)
        self.assertError(self.pedir(cantidad=1), 400, 'El pedido mínimo es $1500.5')

    def test_pedido_minimo_justo_alcanza(self):
        Restaurantes.objects.filter(pk=self.rest.pk).update(pedido_minimo=2000.0)
        self.assertEqual(self.pedir().status_code, 201)

    def test_listado_con_pedido_viejo_sin_tipo(self):
        # Pedido cargado sin items ni envío: total 10, sin items -> costo_envio = total (domicilio).
        Pedidos.objects.create(fecha=timezone.now(), total=10.0, id_usuario=Usuarios.objects.get(),
                               id_restaurante=self.rest)
        p = self.client.get(self.URL).data[0]
        self.assertEqual((p['tipo_entrega'], p['costo_envio']), ('domicilio', 10.0))
