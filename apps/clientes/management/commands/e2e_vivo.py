"""Prueba de punta a punta contra el servidor real (y su base).

    python manage.py runserver 0.0.0.0:8000          # en otra terminal
    python manage.py e2e_vivo --confirmar [--url http://127.0.0.1:8000]

Por HTTP, como la app: registra un cliente y un repartidor de prueba, el cliente pide 1 unidad de un
producto con stock de un restaurante abierto, el repartidor lo toma, lo retira y lo entrega, y se
verifica lo que ve cada uno. Al final (pase o falle) borra todo lo que creó y repone el stock.
Escribe en la base configurada en el .env: por eso exige --confirmar.
"""
import json
import secrets
import time
import urllib.error
import urllib.request

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import F

from apps.core.models import DetallePedido, Pagos, Pedidos, Productos, Repartidor, Usuarios


class Fallo(Exception):
    pass


class Command(BaseCommand):
    help = 'Ciclo completo cliente -> repartidor -> entregado contra el servidor real, con limpieza.'

    def add_arguments(self, parser):
        parser.add_argument('--url', default='http://127.0.0.1:8000')
        parser.add_argument('--confirmar', action='store_true', help='Necesario: escribe en la base real.')

    def handle(self, *args, **opts):
        if not opts['confirmar']:
            raise CommandError('Escribe en la base real (cuentas, pedido, stock). Corré con --confirmar.')
        self.base = opts['url'].rstrip('/') + '/api/'
        sufijo = f'{int(time.time())}{secrets.token_hex(2)}'
        self.emails = [f'e2e.cliente.{sufijo}@shendo.test', f'e2e.repartidor.{sufijo}@shendo.test']
        self.id_pedido = None
        try:
            self.ciclo(secrets.token_urlsafe(12))
            self.stdout.write(self.style.SUCCESS('OK: ciclo completo de punta a punta'))
        except Fallo as e:
            raise CommandError(f'FALLÓ: {e}')
        finally:
            self.limpiar()

    # ---------- HTTP ----------

    def http(self, metodo, ruta, token=None, body=None, esperado=200):
        req = urllib.request.Request(self.base + ruta, method=metodo,
                                     data=json.dumps(body).encode() if body is not None else None)
        req.add_header('Content-Type', 'application/json')
        if token:
            req.add_header('Authorization', f'Bearer {token}')
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                codigo, texto = r.status, r.read().decode()
        except urllib.error.HTTPError as e:
            codigo, texto = e.code, e.read().decode()
        except urllib.error.URLError as e:
            raise Fallo(f'no se pudo conectar a {self.base} ({e.reason}). ¿Está corriendo runserver?')
        if codigo != esperado:
            raise Fallo(f'{metodo} {ruta}: esperaba {esperado} y llegó {codigo}: {texto[:300]}')
        return json.loads(texto) if texto else None

    def paso(self, texto):
        self.stdout.write(f'  [ok] {texto}')

    def verificar(self, condicion, texto):
        if not condicion:
            raise Fallo(texto)

    # ---------- Ciclo ----------

    def ciclo(self, clave):
        restos = self.http('GET', 'cliente/restaurantes/')
        elegido = None
        for r in (r for r in restos if r['abierto']):
            menu = self.http('GET', f"cliente/restaurantes/{r['id_restaurante']}/productos/")
            con_stock = [p for p in menu if p['stock'] is None or p['stock'] > 0]
            minimo = r['pedido_minimo'] or 0
            con_stock = [p for p in con_stock if p['precio'] >= minimo]
            if con_stock:
                elegido = (r, con_stock[0])
                break
        self.verificar(elegido, 'no hay ningún restaurante abierto con un producto con stock que llegue al mínimo')
        resto, producto = elegido
        self.paso(f"restaurante abierto con stock: {resto['nombre']} / {producto['nombre']}")

        cliente = self.http('POST', 'cliente/registro/', esperado=201, body={
            'nombre': 'E2E Cliente', 'email': self.emails[0], 'telefono': '1100000001',
            'contrasena': clave, 'direccion': 'Calle E2E 1'})
        self.verificar(cliente['perfil'] == 'cliente', f"perfil del cliente: {cliente['perfil']}")
        self.paso('registro de cliente')

        pedido = self.http('POST', 'cliente/pedidos/', cliente['access'], esperado=201, body={
            'id_restaurante': resto['id_restaurante'], 'tipo_entrega': 'domicilio',
            'direccion_entrega': 'Calle E2E 1', 'metodo_pago': 'efectivo',
            'items': [{'id_producto': producto['id_producto'], 'cantidad': 1}]})
        self.id_pedido = pedido['id_pedido']
        envio = resto['costo_envio'] or 0
        self.verificar(abs(pedido['total'] - (producto['precio'] + envio)) < 0.01,
                       f"total {pedido['total']} != precio {producto['precio']} + envío {envio}")
        self.paso(f'pedido #{self.id_pedido} creado (total {pedido["total"]})')

        rep = self.http('POST', 'cliente/registro/', esperado=201, body={
            'nombre': 'E2E Repartidor', 'email': self.emails[1], 'telefono': '1100000002',
            'contrasena': clave, 'repartidor': True, 'vehiculo': 'Bici'})
        self.verificar(rep['perfil'] == 'repartidor', f"perfil del repartidor: {rep['perfil']}")
        self.http('POST', f'repartidor/pedidos/{self.id_pedido}/tomar/', rep['access'], esperado=409)
        self.paso('registro de repartidor (y no puede tomar sin estar disponible)')

        self.http('PATCH', 'repartidor/estado/', rep['access'], body={'disponible': True})
        disponibles = self.http('GET', 'repartidor/disponibles/', rep['access'])
        mio = next((d for d in disponibles if d['id_pedido'] == self.id_pedido), None)
        self.verificar(mio and 'cliente' not in mio, 'el pedido no aparece en disponibles o expone al cliente')
        tomado = self.http('POST', f'repartidor/pedidos/{self.id_pedido}/tomar/', rep['access'])
        self.verificar(tomado['cliente']['nombre'] == 'E2E Cliente', 'al tomarlo no ve al cliente')
        self.paso('repartidor disponible y pedido tomado')

        visto = self.http('GET', 'cliente/pedidos/', cliente['access'])[0]
        self.verificar(visto['repartidor'] and visto['repartidor']['nombre'] == 'E2E Repartidor',
                       f"el cliente no ve al repartidor: {visto['repartidor']}")
        self.paso('el cliente ve quién se lo lleva')

        self.http('POST', f'repartidor/pedidos/{self.id_pedido}/estado/', rep['access'], body={'estado': 'en camino'})
        entregado = self.http('POST', f'repartidor/pedidos/{self.id_pedido}/estado/', rep['access'],
                              body={'estado': 'entregado'})
        self.verificar(entregado['estado'] == 'entregado' and entregado['pagado'], 'no quedó entregado y pagado')
        final = self.http('GET', 'cliente/pedidos/', cliente['access'])[0]
        self.verificar(final['estado'] == 'entregado' and final['pagado'], f"el cliente ve: {final['estado']}")
        self.paso('en camino -> entregado; el cliente lo ve entregado y pagado')

        nuevo = self.http('POST', 'cliente/token/refresh/', body={'refresh': cliente['refresh']})
        self.http('GET', 'cliente/pedidos/', nuevo['access'])
        self.paso('refresh de token')

    # ---------- Limpieza ----------

    def limpiar(self):
        with transaction.atomic():
            if self.id_pedido:
                for d in DetallePedido.objects.filter(id_pedido=self.id_pedido):
                    if d.id_producto_id:
                        Productos.objects.filter(pk=d.id_producto_id, stock__isnull=False) \
                            .update(stock=F('stock') + int(d.cantidad))
                DetallePedido.objects.filter(id_pedido=self.id_pedido).delete()
                Pagos.objects.filter(id_pedido=self.id_pedido).delete()
                Pedidos.objects.filter(pk=self.id_pedido).delete()
            usuarios = Usuarios.objects.filter(email__in=self.emails)
            Repartidor.objects.filter(id_usuario__in=usuarios).delete()
            borrados = usuarios.delete()[0]
        self.stdout.write(f'  limpieza: pedido {self.id_pedido or "-"} borrado, stock repuesto, {borrados} cuentas borradas')
