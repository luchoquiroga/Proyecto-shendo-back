# Cara de repartidor de la app mobile. Montado en /api/repartidor/ (config/urls.py).
# Mismo login y token que el cliente (/api/cliente/login/); lo que lo hace repartidor
# es tener una fila en la tabla repartidor con su id_usuario.
#
# Flujo: el restaurante maneja pendiente/preparando/listo desde el panel.
# El repartidor ve los pedidos a domicilio sin repartidor y no terminados -> "tomar" (se asigna)
# -> "en camino" (lo retiró) -> "entregado" (se marca el pago como cobrado).
from functools import wraps

from django.db.models import Q
from django.utils import timezone
from rest_framework import status
from rest_framework.response import Response

from apps.core.models import Pagos, Pedidos, Repartidor
from .auth import get_cliente_from_token, repartidor_json
from .views import ClienteAPIView, error, pedidos_json, texto

EN_CAMINO = 'en camino'
ENTREGADO = 'entregado'
# Estados en los que el pedido ya no se puede tomar (el estado es texto libre: se compara sin mayúsculas).
TERMINADOS = (EN_CAMINO, ENTREGADO, 'cancelado', 'rechazado')


def estado_es(estado, *valores):
    return (estado or '').strip().lower() in valores


def sin_terminar():
    # Regex en vez de iexact: el estado es texto libre y puede traer espacios o saltos alrededor.
    return ~Q(estado__iregex=r'^\s*(' + '|'.join(TERMINADOS) + r')\s*$')


def requiere_repartidor(metodo):
    """401 sin token válido, 403 si la cuenta no es de repartidor. Pasa el Repartidor al método."""
    @wraps(metodo)
    def wrapper(self, request, *args, **kwargs):
        id_usuario = get_cliente_from_token(request)
        if not id_usuario:
            return error('Token inválido o no enviado', status.HTTP_401_UNAUTHORIZED)
        repartidor = Repartidor.objects.filter(id_usuario=id_usuario).first()
        if repartidor is None:
            return error('Esta cuenta no es de repartidor', status.HTTP_403_FORBIDDEN)
        return metodo(self, request, repartidor, *args, **kwargs)
    return wrapper


def entregas_json(pedidos, con_cliente):
    """pedidos_json + datos para ir a buscar (restaurante) y, si ya es suyo, para entregar (cliente)."""
    pedidos = list(pedidos)
    data = pedidos_json(pedidos)
    for d, p in zip(data, pedidos):
        r = p.id_restaurante
        d['restaurante_direccion'] = r.direccion if r else None
        d['restaurante_telefono'] = r.telefono if r else None
        if con_cliente:
            u = p.id_usuario
            d['cliente'] = {'nombre': u.nombre, 'telefono': u.telefono} if u else None
    return data


def pedidos_query():
    return Pedidos.objects.select_related('id_restaurante', 'id_usuario', 'id_repartidor__id_usuario')


class EstadoView(ClienteAPIView):
    """GET: mis datos de repartidor. PATCH: {disponible: bool, vehiculo: str} (parcial)."""

    @requiere_repartidor
    def get(self, request, repartidor):
        return Response(repartidor_json(repartidor))

    @requiere_repartidor
    def patch(self, request, repartidor):
        if 'disponible' in request.data:
            if not isinstance(request.data['disponible'], bool):
                return error('disponible tiene que ser true o false')
            repartidor.disponible = request.data['disponible']
        if 'vehiculo' in request.data:
            vehiculo = texto(request.data['vehiculo'])
            if not vehiculo or len(vehiculo) > 50:
                return error('El vehículo es requerido (hasta 50 caracteres)')
            repartidor.vehiculo = vehiculo
        repartidor.save()
        return Response(repartidor_json(repartidor))


class DisponiblesView(ClienteAPIView):
    """Pedidos a domicilio que nadie tomó todavía, del más viejo al más nuevo (sin datos del cliente)."""

    @requiere_repartidor
    def get(self, request, repartidor):
        pedidos = (
            pedidos_query()
            .filter(sin_terminar(), id_repartidor__isnull=True, tipo_entrega='domicilio')
            .order_by('fecha', 'id_pedido')
        )
        return Response(entregas_json(pedidos, con_cliente=False))


class MisEntregasView(ClienteAPIView):
    """Pedidos asignados a mí: primero los activos, después el historial."""

    @requiere_repartidor
    def get(self, request, repartidor):
        pedidos = list(pedidos_query().filter(id_repartidor=repartidor).order_by('-fecha', '-id_pedido'))
        pedidos.sort(key=lambda p: estado_es(p.estado, ENTREGADO, 'cancelado', 'rechazado'))  # sort estable
        return Response(entregas_json(pedidos, con_cliente=True))


class TomarView(ClienteAPIView):

    @requiere_repartidor
    def post(self, request, repartidor, id_pedido):
        if not repartidor.disponible:
            return error('Activá tu disponibilidad para tomar pedidos', status.HTTP_409_CONFLICT)
        # shortcut: no hay límite de pedidos activos por repartidor; agregarlo si hace falta.
        # UPDATE condicional: si dos repartidores lo toman a la vez, solo uno se lo lleva.
        tomados = (
            Pedidos.objects
            .filter(sin_terminar(), pk=id_pedido, id_repartidor__isnull=True, tipo_entrega='domicilio')
            .update(id_repartidor=repartidor)
        )
        if tomados == 0:
            return error('El pedido ya no está disponible', status.HTTP_409_CONFLICT)
        return Response(entregas_json(pedidos_query().filter(pk=id_pedido), con_cliente=True)[0])


class SoltarView(ClienteAPIView):
    """Devuelve el pedido a disponibles, solo si todavía no salió (no está en camino)."""

    @requiere_repartidor
    def post(self, request, repartidor, id_pedido):
        soltados = (
            Pedidos.objects
            .filter(sin_terminar(), pk=id_pedido, id_repartidor=repartidor)
            .update(id_repartidor=None)
        )
        if soltados == 0:
            return error('No podés soltar este pedido', status.HTTP_409_CONFLICT)
        return Response(status=status.HTTP_204_NO_CONTENT)


class EstadoPedidoView(ClienteAPIView):
    """{estado: "en camino" | "entregado"} sobre un pedido mío."""

    @requiere_repartidor
    def post(self, request, repartidor, id_pedido):
        nuevo = texto(request.data.get('estado')).lower()
        pedido = Pedidos.objects.filter(pk=id_pedido, id_repartidor=repartidor).first()
        if pedido is None:
            return error('Pedido no encontrado', status.HTTP_404_NOT_FOUND)

        if nuevo == EN_CAMINO:
            if estado_es(pedido.estado, *TERMINADOS):
                return error('El pedido ya salió o está terminado', status.HTTP_409_CONFLICT)
        elif nuevo == ENTREGADO:
            if not estado_es(pedido.estado, EN_CAMINO):
                return error('Primero marcá el pedido como en camino', status.HTTP_409_CONFLICT)
        else:
            return error('estado tiene que ser "en camino" o "entregado"')

        pedido.estado = nuevo
        pedido.save(update_fields=['estado'])
        if nuevo == ENTREGADO:
            # Se paga al recibir: al entregar, el pago queda cobrado.
            Pagos.objects.filter(id_pedido=pedido, estado_pago=False).update(estado_pago=True, fecha_pago=timezone.now())
        return Response(entregas_json(pedidos_query().filter(pk=id_pedido), con_cliente=True)[0])
