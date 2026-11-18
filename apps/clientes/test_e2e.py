# Ciclo completo de punta a punta, solo por HTTP (como lo usan la app y el panel):
# cliente se registra y pide -> repartidor se registra y lo entrega -> cliente y panel ven el resultado.
from datetime import timedelta

from rest_framework.test import APIClient, APITestCase

from apps.clientes.auth import ClienteRefreshToken
from apps.core.models import Pagos, Productos, Restaurantes, Usuarios

HASH_SECRETO = 'pbkdf2_sha256$1000$abcdefghijklmnopqrstuv$PpVBcVFaPEauYjw1N/ZRHhsUgyj6j6rR3geTJGtq/UU='


def con_token(token):
    c = APIClient()
    c.credentials(HTTP_AUTHORIZATION='Bearer ' + token)
    return c


class CicloCompletoTest(APITestCase):

    def setUp(self):
        self.resto = Restaurantes.objects.create(nombre='Pizzería', direccion='Av. 1', telefono='111',
                                                 costo_envio=300, pedido_minimo=1000)
        self.muzza = Productos.objects.create(nombre='Muzza', precio=1200, stock=5, disponible=True,
                                              id_restaurante=self.resto)
        Usuarios.objects.create(nombre='admin', email='admin@a.com', telefono='1', contrasena=HASH_SECRETO,
                                rol=True, id_restaurante=self.resto)

    def registrar(self, **datos):
        r = APIClient().post('/api/cliente/registro/', {'contrasena': 'secreto123', **datos}, format='json')
        self.assertEqual(r.status_code, 201, r.data)
        return r.data

    def test_ciclo_completo(self):
        # 1. Cliente: se registra, ve el restaurante y su menú, y pide 2 muzzas a domicilio.
        sesion_c = self.registrar(nombre='Ana', email='ana@mail.com', telefono='1155556666', direccion='Calle 9')
        self.assertEqual(sesion_c['perfil'], 'cliente')
        cliente = con_token(sesion_c['access'])

        restos = cliente.get('/api/cliente/restaurantes/').data
        self.assertEqual([(r['nombre'], r['abierto'], r['costo_envio']) for r in restos], [('Pizzería', True, 300.0)])
        menu = cliente.get(f'/api/cliente/restaurantes/{self.resto.pk}/productos/').data
        self.assertEqual([p['nombre'] for p in menu], ['Muzza'])

        r = cliente.post('/api/cliente/pedidos/', {
            'id_restaurante': self.resto.pk, 'tipo_entrega': 'domicilio', 'direccion_entrega': 'Calle 9',
            'metodo_pago': 'efectivo', 'items': [{'id_producto': self.muzza.pk, 'cantidad': 2}],
        }, format='json')
        self.assertEqual(r.status_code, 201, r.data)
        id_pedido = r.data['id_pedido']
        self.assertEqual((r.data['total'], r.data['costo_envio'], r.data['estado']), (2700.0, 300.0, 'pendiente'))
        self.muzza.refresh_from_db()
        self.assertEqual(self.muzza.stock, 3)

        # 2. Panel: el restaurante lo ve y lo pasa a preparando.
        panel_login = APIClient().post('/api/login/', {'nombre': 'admin', 'contrasena': 'secreto123'}, format='json').data
        panel = con_token(panel_login['access'])
        self.assertEqual([p['id_pedido'] for p in panel.get('/api/orders/').data], [id_pedido])
        self.assertEqual(panel.put(f'/api/orders/{id_pedido}/', {'estado': 'preparando'}, format='json').status_code, 200)

        # 3. Repartidor: se registra, se pone disponible, ve el pedido (sin datos del cliente) y lo toma.
        sesion_r = self.registrar(nombre='Juan', email='juan@mail.com', telefono='1144443333',
                                  repartidor=True, vehiculo='Moto')
        self.assertEqual(sesion_r['perfil'], 'repartidor')
        rep = con_token(sesion_r['access'])
        self.assertEqual(rep.patch('/api/repartidor/estado/', {'disponible': True}, format='json').data['disponible'], True)

        disponibles = rep.get('/api/repartidor/disponibles/').data
        self.assertEqual([d['id_pedido'] for d in disponibles], [id_pedido])
        self.assertNotIn('cliente', disponibles[0])
        tomado = rep.post(f'/api/repartidor/pedidos/{id_pedido}/tomar/').data
        self.assertEqual(tomado['cliente'], {'nombre': 'Ana', 'telefono': '1155556666'})

        # 4. El cliente ve quién se lo lleva; el panel también.
        pedido_c = cliente.get('/api/cliente/pedidos/').data[0]
        self.assertEqual(pedido_c['repartidor'], {'nombre': 'Juan', 'telefono': '1144443333', 'vehiculo': 'Moto'})
        self.assertEqual(panel.get('/api/orders/').data[0]['repartidor'], 'Juan')

        # 5. Retira y entrega.
        self.assertEqual(rep.post(f'/api/repartidor/pedidos/{id_pedido}/estado/', {'estado': 'en camino'}, format='json').status_code, 200)
        self.assertEqual(cliente.get('/api/cliente/pedidos/').data[0]['estado'], 'en camino')
        entregado = rep.post(f'/api/repartidor/pedidos/{id_pedido}/estado/', {'estado': 'entregado'}, format='json').data
        self.assertEqual((entregado['estado'], entregado['pagado']), ('entregado', True))

        # 6. Resultado final para cada uno.
        final_c = cliente.get('/api/cliente/pedidos/').data[0]
        self.assertEqual((final_c['estado'], final_c['pagado'], final_c['repartidor']['nombre']), ('entregado', True, 'Juan'))
        self.assertEqual(rep.get('/api/repartidor/disponibles/').data, [])
        self.assertEqual([m['estado'] for m in rep.get('/api/repartidor/pedidos/').data], ['entregado'])
        self.assertEqual(panel.get('/api/orders/').data[0]['estado'], 'entregado')
        self.assertTrue(Pagos.objects.get(id_pedido=id_pedido).estado_pago)

    def test_token_vencido_se_renueva_con_refresh(self):
        # Lo que hace la app ante un 401: pide un access nuevo con el refresh y reintenta.
        sesion = self.registrar(nombre='Ana', email='ana@mail.com', telefono='1155556666')
        # Un access de esta sesión que ya venció (mover el reloj no sirve: PyJWT valida iat con la hora real).
        vencido = ClienteRefreshToken(sesion['refresh']).access_token
        vencido.set_exp(lifetime=-timedelta(seconds=1))
        self.assertEqual(con_token(str(vencido)).get('/api/cliente/pedidos/').status_code, 401)

        nuevo = APIClient().post('/api/cliente/token/refresh/', {'refresh': sesion['refresh']}, format='json')
        self.assertEqual(nuevo.status_code, 200, nuevo.data)
        self.assertEqual(con_token(nuevo.data['access']).get('/api/cliente/pedidos/').status_code, 200)
