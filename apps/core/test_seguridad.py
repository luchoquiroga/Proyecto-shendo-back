# python manage.py test apps.clientes apps.core.test_seguridad --settings=config.settings_test
# Va en un archivo aparte porque apps/core/tests.py (del equipo) no importa.
from unittest.mock import patch

from django.contrib.auth.hashers import check_password
from django.core.cache import cache
from django.utils import timezone
from rest_framework.test import APITestCase
from rest_framework.throttling import ScopedRateThrottle

from apps.core.models import Categoria, Pedidos, Productos, Restaurantes, Usuarios

# Hash real de Django para "secreto123" (1000 iteraciones).
HASH_SECRETO = 'pbkdf2_sha256$1000$abcdefghijklmnopqrstuv$PpVBcVFaPEauYjw1N/ZRHhsUgyj6j6rR3geTJGtq/UU='


class PanelTest(APITestCase):

    def setUp(self):
        self.resto = Restaurantes.objects.create(nombre='Pizzería')
        self.otro = Restaurantes.objects.create(nombre='Otro')

    def admin(self, contrasena='secreto123', nombre='admin'):
        return Usuarios.objects.create(
            nombre=nombre, email='a@a.com', telefono='1', contrasena=contrasena, rol=True,
            id_restaurante=self.resto,
        )

    def login(self, contrasena='secreto123', nombre='admin'):
        return self.client.post('/api/login/', {'nombre': nombre, 'contrasena': contrasena}, format='json')

    def loguear(self):
        r = self.login()
        self.assertEqual(r.status_code, 200, r.data)
        self.client.credentials(HTTP_AUTHORIZATION='Bearer ' + r.data['access'])
        return r.data


class LoginPanelTest(PanelTest):

    def test_texto_plano_entra_y_se_migra_a_hash(self):
        u = self.admin(contrasena='secreto123')
        self.assertEqual(self.login().status_code, 200)
        u.refresh_from_db()
        self.assertNotEqual(u.contrasena, 'secreto123')
        self.assertTrue(check_password('secreto123', u.contrasena))
        self.assertEqual(self.login().status_code, 200)  # ahora con hash

    def test_hash_entra(self):
        self.admin(contrasena=HASH_SECRETO)
        self.assertEqual(self.login().status_code, 200)

    def test_contrasena_incorrecta(self):
        u = self.admin(contrasena='secreto123')
        self.assertEqual(self.login('otra').status_code, 401)
        self.admin(contrasena=HASH_SECRETO, nombre='admin2')
        self.assertEqual(self.login('otra', nombre='admin2').status_code, 401)
        u.refresh_from_db()
        self.assertEqual(u.contrasena, 'secreto123')  # si no coincide, no se toca

    def test_mismo_nombre_dos_usuarios(self):
        self.admin(contrasena='otra')
        self.admin(contrasena='secreto123')
        self.assertEqual(self.login().status_code, 200)

    def test_contrasena_no_texto(self):
        self.admin()
        self.assertEqual(self.login(contrasena=123).status_code, 400)

    def test_refresh(self):
        self.admin()
        data = self.login().data
        r = self.client.post('/api/token/refresh/', {'refresh': data['refresh']}, format='json')
        self.assertEqual(r.status_code, 200, r.data)
        self.client.credentials(HTTP_AUTHORIZATION='Bearer ' + r.data['access'])
        self.assertEqual(self.client.get('/api/orders/').status_code, 200)

    def test_throttling(self):
        cache.clear()
        self.admin()
        with patch.object(ScopedRateThrottle, 'THROTTLE_RATES', {'login': '3/min'}):
            codigos = [self.login('otra').status_code for _ in range(4)]
        cache.clear()
        self.assertEqual(codigos, [401, 401, 401, 429])


class RefreshClienteTest(PanelTest):

    def test_refresh_cliente(self):
        Usuarios.objects.create(nombre='Ana', email='ana@mail.com', telefono='1', contrasena=HASH_SECRETO, rol=False)
        data = self.client.post('/api/cliente/login/', {'email': 'ana@mail.com', 'contrasena': 'secreto123'}, format='json').data
        r = self.client.post('/api/cliente/token/refresh/', {'refresh': data['refresh']}, format='json')
        self.assertEqual(r.status_code, 200, r.data)
        self.client.credentials(HTTP_AUTHORIZATION='Bearer ' + r.data['access'])
        self.assertEqual(self.client.get('/api/cliente/pedidos/').status_code, 200)

    def test_refresh_invalido_o_del_panel(self):
        self.admin()
        panel = self.login().data['refresh']
        for malo in [panel, 'abc', None]:
            r = self.client.post('/api/cliente/token/refresh/', {'refresh': malo}, format='json')
            self.assertEqual(r.status_code, 401, malo)


class CategoriasTest(PanelTest):

    def test_sin_token(self):
        c = Categoria.objects.create(nombre='Pizzas', id_restaurante=self.resto)
        self.assertEqual(self.client.get('/api/categorias/').status_code, 401)
        self.assertEqual(self.client.post('/api/categorias/', {'nombre': 'X'}).status_code, 401)
        self.assertEqual(self.client.put(f'/api/categorias/{c.pk}/', {'nombre': 'X'}).status_code, 401)
        self.assertEqual(self.client.delete(f'/api/categorias/{c.pk}/').status_code, 401)
        self.assertTrue(Categoria.objects.filter(pk=c.pk, nombre='Pizzas').exists())

    def test_crear_editar_listar_borrar(self):
        self.admin()
        self.loguear()
        r = self.client.post('/api/categorias/', {'nombre': ' Pizzas '}, format='json')
        self.assertEqual(r.status_code, 201, r.data)
        pk = r.data['id_categoria']
        self.assertEqual(Categoria.objects.get(pk=pk).id_restaurante_id, self.resto.pk)
        r = self.client.put(f'/api/categorias/{pk}/', {'nombre': 'Empanadas'}, format='json')
        self.assertEqual(r.data['nombre'], 'Empanadas')
        self.assertEqual(self.client.get('/api/categorias/').data, [{'id_categoria': pk, 'nombre': 'Empanadas'}])
        self.assertEqual(self.client.delete(f'/api/categorias/{pk}/').status_code, 204)
        self.assertFalse(Categoria.objects.filter(pk=pk).exists())

    def test_nombre_invalido(self):
        self.admin()
        self.loguear()
        for nombre in ['', '   ', None, 5, 'x' * 101]:
            r = self.client.post('/api/categorias/', {'nombre': nombre}, format='json')
            self.assertEqual(r.status_code, 400, nombre)

    def test_categoria_de_otro_restaurante(self):
        c = Categoria.objects.create(nombre='Ajena', id_restaurante=self.otro)
        self.admin()
        self.loguear()
        self.assertEqual(self.client.put(f'/api/categorias/{c.pk}/', {'nombre': 'X'}, format='json').status_code, 404)
        self.assertEqual(self.client.delete(f'/api/categorias/{c.pk}/').status_code, 404)
        self.assertTrue(Categoria.objects.filter(pk=c.pk, nombre='Ajena').exists())

    def test_no_borra_categoria_con_productos(self):
        c = Categoria.objects.create(nombre='Pizzas', id_restaurante=self.resto)
        Productos.objects.create(nombre='Muzza', precio=1, id_restaurante=self.resto, id_categoria=c)
        self.admin()
        self.loguear()
        self.assertEqual(self.client.delete(f'/api/categorias/{c.pk}/').status_code, 409)
        self.assertTrue(Categoria.objects.filter(pk=c.pk).exists())


class PanelVariosTest(PanelTest):

    def test_estado_de_pedido(self):
        p = Pedidos.objects.create(fecha=timezone.now(), id_restaurante=self.resto, estado='pendiente', total=10)
        self.admin()
        self.loguear()
        url = f'/api/orders/{p.pk}/'
        for malo in [None, '', '  ', 5, 'x' * 51]:
            self.assertEqual(self.client.put(url, {'estado': malo}, format='json').status_code, 400, malo)
        r = self.client.put(url, {'estado': ' en camino '}, format='json')
        self.assertEqual(r.status_code, 200, r.data)
        p.refresh_from_db()
        self.assertEqual(p.estado, 'en camino')

    def test_crear_producto_con_json(self):
        self.admin()
        self.loguear()
        r = self.client.post('/api/products/', {'nombre': 'Muzza', 'precio': 1000, 'disponible': True}, format='json')
        self.assertEqual(r.status_code, 201, r.data)
        self.assertEqual(r.data['imagen_url'], '')


class PerfilClienteTest(PanelTest):
    URL = '/api/cliente/perfil/'

    def setUp(self):
        super().setUp()
        self.ana = Usuarios.objects.create(nombre='Ana', email='ana@mail.com', telefono='1155556666',
                                           contrasena=HASH_SECRETO, rol=False)
        data = self.client.post('/api/cliente/login/', {'email': 'ana@mail.com', 'contrasena': 'secreto123'}, format='json').data
        self.client.credentials(HTTP_AUTHORIZATION='Bearer ' + data['access'])

    def test_ver_y_editar(self):
        self.assertEqual(self.client.get(self.URL).data['email'], 'ana@mail.com')
        r = self.client.patch(self.URL, {'nombre': ' Ana P ', 'direccion': 'Calle 2', 'email': 'otro@mail.com'}, format='json')
        self.assertEqual(r.status_code, 200, r.data)
        self.ana.refresh_from_db()
        self.assertEqual((self.ana.nombre, self.ana.direccion, self.ana.email), ('Ana P', 'Calle 2', 'ana@mail.com'))

    def test_validaciones(self):
        for body in [{'nombre': ''}, {'telefono': 'abc'}, {'telefono': '1' * 31}, {'nombre': 5}]:
            self.assertEqual(self.client.patch(self.URL, body, format='json').status_code, 400, body)
        self.ana.refresh_from_db()
        self.assertEqual(self.ana.nombre, 'Ana')

    def test_cambio_de_contrasena(self):
        mala = self.client.patch(self.URL, {'contrasena_actual': 'nop', 'contrasena_nueva': 'nueva123'}, format='json')
        self.assertEqual(mala.status_code, 400)
        corta = self.client.patch(self.URL, {'contrasena_actual': 'secreto123', 'contrasena_nueva': '123'}, format='json')
        self.assertEqual(corta.status_code, 400)
        ok = self.client.patch(self.URL, {'contrasena_actual': 'secreto123', 'contrasena_nueva': 'nueva123'}, format='json')
        self.assertEqual(ok.status_code, 200, ok.data)
        self.ana.refresh_from_db()
        self.assertTrue(check_password('nueva123', self.ana.contrasena))

    def test_sin_token(self):
        self.client.credentials()
        self.assertEqual(self.client.get(self.URL).status_code, 401)
        self.assertEqual(self.client.patch(self.URL, {'nombre': 'X'}, format='json').status_code, 401)


class PerfilLocalTest(PanelTest):
    URL = '/api/restaurante/'

    def test_ver_y_editar(self):
        self.admin()
        self.loguear()
        self.assertEqual(self.client.get(self.URL).data['nombre'], 'Pizzería')
        r = self.client.patch(self.URL, {'costo_envio': '500', 'pedido_minimo': 3000, 'estado': 'cerrado'}, format='json')
        self.assertEqual(r.status_code, 200, r.data)
        self.resto.refresh_from_db()
        self.assertEqual((self.resto.costo_envio, self.resto.pedido_minimo, self.resto.estado), (500.0, 3000.0, 'cerrado'))
        self.assertEqual(self.client.patch(self.URL, {'costo_envio': None}, format='json').status_code, 200)

    def test_validaciones(self):
        self.admin()
        self.loguear()
        for body in [{'costo_envio': -1}, {'costo_envio': 'abc'}, {'pedido_minimo': True}, {'nombre': ''}, {'telefono': 5}]:
            self.assertEqual(self.client.patch(self.URL, body, format='json').status_code, 400, body)

    def test_sin_token(self):
        self.assertEqual(self.client.get(self.URL).status_code, 401)
