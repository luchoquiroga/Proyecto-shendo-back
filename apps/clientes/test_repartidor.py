from django.utils import timezone
from rest_framework.test import APITestCase

from apps.core.models import Pagos, Pedidos, Repartidor, Restaurantes, Usuarios

R = '/api/repartidor/'


class RepartidorTest(APITestCase):

    def setUp(self):
        self.resto = Restaurantes.objects.create(nombre='Pizzería', direccion='Av. 1', telefono='111')
        self.cliente = Usuarios.objects.create(nombre='Ana', email='ana@mail.com', telefono='1155556666', rol=False)

    def registrar(self, email='juan@mail.com', **extra):
        body = {'nombre': 'Juan', 'email': email, 'telefono': '1144443333', 'contrasena': 'secreto123',
                'repartidor': True, 'vehiculo': 'Moto'}
        body.update(extra)
        r = self.client.post('/api/cliente/registro/', body, format='json')
        return r

    def entrar(self, email='juan@mail.com', disponible=True):
        r = self.registrar(email)
        self.assertEqual(r.status_code, 201, r.data)
        self.client.credentials(HTTP_AUTHORIZATION='Bearer ' + r.data['access'])
        if disponible:
            self.client.patch(R + 'estado/', {'disponible': True}, format='json')
        return r.data

    def pedido(self, tipo='domicilio', estado='pendiente'):
        p = Pedidos.objects.create(fecha=timezone.now(), estado=estado, total=1500, direccion_entrega='Calle 9',
                                   tipo_entrega=tipo, id_usuario=self.cliente, id_restaurante=self.resto)
        Pagos.objects.create(id_pedido=p, metodo_pago='efectivo', monto=1500, estado_pago='pendiente')
        return p


class RegistroRepartidorTest(RepartidorTest):

    def test_registro_crea_repartidor_y_login_dice_perfil(self):
        r = self.registrar()
        self.assertEqual(r.status_code, 201, r.data)
        self.assertEqual(r.data['perfil'], 'repartidor')
        self.assertEqual(r.data['repartidor']['vehiculo'], 'Moto')
        self.assertIs(r.data['repartidor']['disponible'], False)
        rep = Repartidor.objects.get(id_usuario=r.data['id_usuario'])
        self.assertEqual((rep.nombre, rep.telefono), ('Juan', '1144443333'))
        login = self.client.post('/api/cliente/login/', {'email': 'juan@mail.com', 'contrasena': 'secreto123'}, format='json')
        self.assertEqual(login.data['perfil'], 'repartidor')

    def test_vehiculo_requerido(self):
        r = self.registrar(vehiculo='')
        self.assertEqual(r.status_code, 400)
        self.assertFalse(Usuarios.objects.filter(email='juan@mail.com').exists())

    def test_cliente_no_entra_a_repartidor(self):
        r = self.registrar(repartidor=False)
        self.client.credentials(HTTP_AUTHORIZATION='Bearer ' + r.data['access'])
        self.assertEqual(r.data['perfil'], 'cliente')
        self.assertEqual(self.client.get(R + 'disponibles/').status_code, 403)

    def test_sin_token(self):
        self.assertEqual(self.client.get(R + 'disponibles/').status_code, 401)


class FlujoTest(RepartidorTest):

    def test_ciclo_completo(self):
        p = self.pedido()
        self.entrar()

        disp = self.client.get(R + 'disponibles/').data
        self.assertEqual([d['id_pedido'] for d in disp], [p.pk])
        self.assertEqual(disp[0]['restaurante_direccion'], 'Av. 1')
        self.assertNotIn('cliente', disp[0])  # antes de tomarlo no ve al cliente

        r = self.client.post(R + f'pedidos/{p.pk}/tomar/')
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual(r.data['cliente'], {'nombre': 'Ana', 'telefono': '1155556666'})
        self.assertEqual(self.client.get(R + 'disponibles/').data, [])

        # El cliente ve quién se lo lleva.
        self.assertEqual(self.client.post(R + f'pedidos/{p.pk}/estado/', {'estado': 'entregado'}, format='json').status_code, 409)
        r = self.client.post(R + f'pedidos/{p.pk}/estado/', {'estado': 'En camino'}, format='json')
        self.assertEqual(r.data['estado'], 'en camino')
        self.assertEqual(self.client.post(R + f'pedidos/{p.pk}/soltar/').status_code, 409)  # ya salió

        r = self.client.post(R + f'pedidos/{p.pk}/estado/', {'estado': 'entregado'}, format='json')
        self.assertEqual(r.status_code, 200, r.data)
        self.assertIs(r.data['pagado'], True)
        self.assertIsNotNone(Pagos.objects.get(id_pedido=p).fecha_pago)

        mis = self.client.get(R + 'pedidos/').data
        self.assertEqual([m['estado'] for m in mis], ['entregado'])

    def test_cliente_ve_al_repartidor(self):
        p = self.pedido()
        self.entrar()
        self.client.post(R + f'pedidos/{p.pk}/tomar/')
        from apps.clientes.views import pedidos_de, pedidos_json
        self.assertEqual(pedidos_json(pedidos_de(pk=p.pk))[0]['repartidor'],
                         {'nombre': 'Juan', 'telefono': '1144443333', 'vehiculo': 'Moto'})

    def test_no_disponible_no_toma(self):
        p = self.pedido()
        self.entrar(disponible=False)
        self.assertEqual(self.client.post(R + f'pedidos/{p.pk}/tomar/').status_code, 409)

    def test_disponibles_excluye_retiro_terminados_y_tomados(self):
        ok = self.pedido(estado='Listo')
        self.pedido(tipo='retiro')
        self.pedido(estado='Entregado')
        self.pedido(estado='cancelado')
        otro = Repartidor.objects.create(nombre='Otro', estado='disponible', id_usuario=Usuarios.objects.create(nombre='Otro', email='otro@mail.com', rol=False))
        tomado = self.pedido()
        tomado.id_repartidor = otro
        tomado.save()
        self.entrar()
        self.assertEqual([d['id_pedido'] for d in self.client.get(R + 'disponibles/').data], [ok.pk])
        self.assertEqual(self.client.post(R + f'pedidos/{tomado.pk}/tomar/').status_code, 409)

    def test_pedido_ajeno(self):
        p = self.pedido()
        otro = Repartidor.objects.create(nombre='Otro', estado='disponible', id_usuario=Usuarios.objects.create(nombre='Otro', email='otro@mail.com', rol=False))
        p.id_repartidor = otro
        p.save()
        self.entrar()
        self.assertEqual(self.client.post(R + f'pedidos/{p.pk}/estado/', {'estado': 'en camino'}, format='json').status_code, 404)
        self.assertEqual(self.client.post(R + f'pedidos/{p.pk}/soltar/').status_code, 409)
        self.assertEqual(self.client.get(R + 'pedidos/').data, [])

    def test_soltar_vuelve_a_disponibles(self):
        p = self.pedido()
        self.entrar()
        self.client.post(R + f'pedidos/{p.pk}/tomar/')
        self.assertEqual(self.client.post(R + f'pedidos/{p.pk}/soltar/').status_code, 204)
        self.assertEqual(len(self.client.get(R + 'disponibles/').data), 1)

    def test_estado_invalido(self):
        p = self.pedido()
        self.entrar()
        self.client.post(R + f'pedidos/{p.pk}/tomar/')
        for malo in ['cancelado', '', None, 5]:
            self.assertEqual(self.client.post(R + f'pedidos/{p.pk}/estado/', {'estado': malo}, format='json').status_code, 400, malo)

    def test_editar_estado_repartidor(self):
        self.entrar(disponible=False)
        r = self.client.patch(R + 'estado/', {'disponible': True, 'vehiculo': 'Bici'}, format='json')
        self.assertEqual((r.data['disponible'], r.data['vehiculo']), (True, 'Bici'))
        for malo in [{'disponible': 'si'}, {'vehiculo': ''}]:
            self.assertEqual(self.client.patch(R + 'estado/', malo, format='json').status_code, 400, malo)


class EstadoYPatenteTest(RepartidorTest):

    def estado(self):
        return self.client.get(R + 'estado/').data['estado']

    def test_ocupado_mientras_tiene_pedidos_en_curso(self):
        self.entrar()
        a, b = self.pedido(), self.pedido()
        self.assertEqual(self.estado(), 'disponible')
        self.client.post(R + f'pedidos/{a.pk}/tomar/')
        self.assertEqual(self.client.post(R + f'pedidos/{b.pk}/tomar/').status_code, 200)  # ocupado puede tomar más
        self.assertEqual(self.estado(), 'ocupado')
        self.client.post(R + f'pedidos/{b.pk}/soltar/')
        self.assertEqual(self.estado(), 'ocupado')  # le queda a
        self.client.post(R + f'pedidos/{a.pk}/estado/', {'estado': 'en camino'}, format='json')
        self.assertEqual(self.estado(), 'ocupado')
        a.refresh_from_db()
        self.assertIsNotNone(a.hora_salida)
        self.assertIsNone(a.hora_entrega)
        r = self.client.post(R + f'pedidos/{a.pk}/estado/', {'estado': 'entregado'}, format='json')
        self.assertEqual(self.estado(), 'disponible')
        self.assertEqual(Pagos.objects.get(id_pedido=a).estado_pago, 'pagado')
        a.refresh_from_db()
        self.assertGreaterEqual(a.hora_entrega, a.hora_salida)
        self.assertIsNotNone(r.data['hora_entrega'])

    def test_desconectado_no_cambia_al_entregar(self):
        self.entrar()
        p = self.pedido()
        self.client.post(R + f'pedidos/{p.pk}/tomar/')
        r = self.client.patch(R + 'estado/', {'disponible': False}, format='json')
        self.assertEqual((r.data['estado'], r.data['disponible']), ('desconectado', False))
        self.client.post(R + f'pedidos/{p.pk}/estado/', {'estado': 'en camino'}, format='json')
        self.client.post(R + f'pedidos/{p.pk}/estado/', {'estado': 'entregado'}, format='json')
        self.assertEqual(self.estado(), 'desconectado')
        r = self.client.patch(R + 'estado/', {'disponible': True}, format='json')
        self.assertEqual(r.data['estado'], 'disponible')

    def test_patente_en_registro_y_edicion(self):
        r = self.registrar(patente=' AB123CD ')
        self.assertEqual(r.data['repartidor']['patente'], 'AB123CD')
        self.client.credentials(HTTP_AUTHORIZATION='Bearer ' + r.data['access'])
        self.assertEqual(self.client.patch(R + 'estado/', {'patente': 'X' * 16}, format='json').status_code, 400)
        self.assertIsNone(self.client.patch(R + 'estado/', {'patente': ''}, format='json').data['patente'])
        self.assertEqual(self.registrar('otro@mail.com', patente='X' * 16).status_code, 400)


class SoloCalleTest(RepartidorTest):

    def test_solo_calle(self):
        from apps.clientes.repartidor import solo_calle
        casos = {
            'Calle Falsa 456': 'Calle Falsa',
            'Av. San Martín 1234, depto 5B': 'Av. San Martín',
            'Belgrano N° 50': 'Belgrano N',
            'Calle Sin Número': 'Calle Sin Número',
            '1234': None,
            '': None,
            None: None,
        }
        for entrada, esperado in casos.items():
            self.assertEqual(solo_calle(entrada), esperado, entrada)

    def test_disponibles_muestra_solo_la_calle_y_al_tomarlo_la_completa(self):
        p = self.pedido()  # direccion_entrega = 'Calle 9'
        Pedidos.objects.filter(pk=p.pk).update(direccion_entrega='Av. Siempreviva 742')
        self.entrar()
        self.assertEqual(self.client.get(R + 'disponibles/').data[0]['direccion_entrega'], 'Av. Siempreviva')
        r = self.client.post(R + f'pedidos/{p.pk}/tomar/')
        self.assertEqual(r.data['direccion_entrega'], 'Av. Siempreviva 742')
