import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from django.contrib.auth.hashers import check_password, make_password
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import transaction
from django.db.models import F, Min, Prefetch, Q
from django.utils import timezone
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.models import Categoria, DetallePedido, HorariosRestaurante, Pagos, Pedidos, Productos, Repartidor, Restaurantes, Usuarios
from apps.core.cuerpo import CuerpoObjetoMixin
from .auth import renovar, requiere_cliente, sesion, tokens

# Endpoints de la app mobile de clientes (la persona que hace el pedido).
# El panel del restaurante sigue en apps/core; esto no lo toca.

# Clave que manda la app -> texto que se le muestra al cliente.
METODOS_PAGO = {
    'efectivo': 'Efectivo',
    'tarjeta': 'Tarjeta (al recibir)',
    'transferencia': 'Transferencia',
}

TELEFONO_VALIDO = re.compile(r'^[\d\s()+-]+$')


def telefono_valido(telefono):
    digitos = sum(c.isdigit() for c in telefono)
    # 30 = varchar(30) de usuarios.telefono en la base.
    return bool(TELEFONO_VALIDO.match(telefono)) and 6 <= digitos <= 15 and len(telefono) <= 30


MAX_ID = 2**63 - 1
MAX_LINEAS = 50
MAX_CANTIDAD = 100


def error(mensaje, codigo=status.HTTP_400_BAD_REQUEST):
    return Response({'error': mensaje}, status=codigo)


def texto(valor):
    return valor.strip() if isinstance(valor, str) else ''


def entero(valor):
    """int para ids y cantidades que vienen en el JSON; None si no es un entero."""
    if isinstance(valor, str):
        # isdecimal + ascii: str.isdigit() acepta '²' e int() falla con eso.
        valor = int(valor) if valor.isascii() and valor.strip().isdecimal() else None
    if isinstance(valor, bool) or not isinstance(valor, int):
        return None
    return valor if abs(valor) <= MAX_ID else None  # bigint de la base


TZ_AR = ZoneInfo('America/Argentina/Buenos_Aires')
TIPOS_ENTREGA = ('domicilio', 'retiro')


def ahora_ar():
    """Hora local de Argentina (aparte para poder mockearla en los tests)."""
    return datetime.now(TZ_AR)


def horarios_activos(restaurante):
    # Usa el prefetch si existe (listado); si no, consulta.
    if 'horarios' in getattr(restaurante, '_prefetched_objects_cache', {}):
        horarios = restaurante.horarios.all()
    else:
        horarios = HorariosRestaurante.objects.filter(id_restaurante=restaurante.pk, activo=True)
    return [h for h in horarios if h.activo and h.hora_apertura and h.hora_cierre]


def horario_hoy(restaurante, ahora=None):
    """Texto con los rangos de hoy; None si no tiene horarios cargados."""
    horarios = horarios_activos(restaurante)
    if not horarios:
        return None
    ahora = ahora or ahora_ar()
    # dia_semana ISO (supuesto): 1 = lunes ... 7 = domingo, igual que isoweekday().
    rangos = sorted((h for h in horarios if h.dia_semana == ahora.isoweekday()), key=lambda h: h.hora_apertura)
    if not rangos:
        return 'Cerrado hoy'
    return ' y '.join(
        f"{h.hora_apertura.strftime('%H:%M')} a {h.hora_cierre.strftime('%H:%M')}" for h in rangos
    )


def esta_abierto(restaurante, ahora=None):
    # estado es texto libre: solo "cerrado" cuenta como cerrado.
    if (restaurante.estado or '').strip().lower() == 'cerrado':
        return False
    horarios = horarios_activos(restaurante)
    if not horarios:
        return True  # sin horarios cargados: se comporta como siempre
    ahora = ahora or ahora_ar()
    hora = ahora.time().replace(tzinfo=None)
    hoy = ahora.isoweekday()
    ayer = (ahora - timedelta(days=1)).isoweekday()
    for h in horarios:
        cruza = h.hora_cierre < h.hora_apertura  # cierra pasada la medianoche
        if h.dia_semana == hoy and (
            (h.hora_apertura <= hora < h.hora_cierre) or (cruza and hora >= h.hora_apertura)
        ):
            return True
        if cruza and h.dia_semana == ayer and hora < h.hora_cierre:
            return True
    return False


def imagen_absoluta(request, ruta):
    ruta = (ruta or '').strip()
    if not ruta:
        return None
    if ruta.lower().startswith(('http://', 'https://')):
        return ruta
    return request.build_absolute_uri(ruta)


def numero(valor):
    return float(valor) if valor is not None else 0.0


# ---------- Cuenta ----------

class ClienteAPIView(CuerpoObjetoMixin, APIView):
    # La app valida su propio token (ver auth.py). Sin esto, el autenticador global
    # del panel (CustomJWTAuthentication) responde 403 con token vencido y la app
    # espera 401 para cerrar la sesión.
    authentication_classes = []


class RegistroView(ClienteAPIView):
    throttle_scope = 'login'

    def post(self, request):
        nombre = texto(request.data.get('nombre'))
        email = texto(request.data.get('email')).lower()
        telefono = texto(request.data.get('telefono'))
        direccion = texto(request.data.get('direccion'))
        contrasena = request.data.get('contrasena')
        contrasena = contrasena if isinstance(contrasena, str) else ''

        if not all([nombre, email, telefono, contrasena.strip()]):
            return error('Nombre, email, teléfono y contraseña son requeridos')

        if not telefono_valido(telefono):
            return error('El teléfono no es válido')

        if len(contrasena) < 6:
            return error('La contraseña debe tener al menos 6 caracteres')

        if len(nombre) > 100 or len(direccion) > 255:
            return error('El nombre o la dirección son demasiado largos')

        try:
            validate_email(email)
        except ValidationError:
            return error('El email no es válido')
        if len(email) > 254:
            return error('El email no es válido')

        # Contra todos los usuarios (admins incluidos): no hay UNIQUE en la base.
        if Usuarios.objects.filter(email__iexact=email).exists():
            return error('Ya existe una cuenta con ese email')

        # "repartidor": true -> la cuenta entra a la cara de repartidor de la app.
        es_repartidor = request.data.get('repartidor') is True
        vehiculo = texto(request.data.get('vehiculo'))
        if es_repartidor and not vehiculo:
            return error('El vehículo es requerido para repartidores')
        if len(vehiculo) > 50:
            return error('El vehículo es demasiado largo')

        with transaction.atomic():
            usuario = Usuarios.objects.create(
                nombre=nombre,
                email=email,
                telefono=telefono,
                direccion=direccion,
                contrasena=make_password(contrasena),
                rol=False,  # false = no es admin (cliente o repartidor)
            )
            if es_repartidor:
                Repartidor.objects.create(
                    nombre=nombre, telefono=telefono, vehiculo=vehiculo,
                    disponible=False,  # arranca no disponible; lo prende desde la app
                    id_usuario=usuario,
                )
        return Response(sesion(usuario), status=status.HTTP_201_CREATED)


class LoginView(ClienteAPIView):
    throttle_scope = 'login'

    def post(self, request):
        email = texto(request.data.get('email'))
        contrasena = request.data.get('contrasena')

        if not email or not isinstance(contrasena, str) or not contrasena:
            return error('Email y contraseña son requeridos')

        usuario = (
            Usuarios.objects
            .filter(email__iexact=email, rol=False)
            .order_by('id_usuario')
            .first()
        )
        if usuario is None:
            # Calcula un hash igual, para que el tiempo de respuesta no revele qué emails existen.
            make_password(contrasena)
            return error('Email o contraseña incorrectos', status.HTTP_401_UNAUTHORIZED)

        # check_password da False con contraseñas en texto plano (las de los admins viejos).
        if not check_password(contrasena, usuario.contrasena):
            return error('Email o contraseña incorrectos', status.HTTP_401_UNAUTHORIZED)

        return Response(sesion(usuario))


class RefreshView(ClienteAPIView):
    """Body {"refresh": "..."} -> {"access": "..."}."""

    def post(self, request):
        access = renovar(request.data.get('refresh'))
        if access is None:
            return error('Refresh token inválido o vencido', status.HTTP_401_UNAUTHORIZED)
        return Response({'access': access})


def perfil_json(usuario):
    return {
        'id_usuario': usuario.id_usuario,
        'nombre': usuario.nombre,
        'email': usuario.email,
        'telefono': usuario.telefono,
        'direccion': usuario.direccion,
    }


class PerfilView(ClienteAPIView):
    """GET: datos del cliente. PATCH: cambia nombre, telefono, direccion y/o contraseña
    (contrasena_nueva + contrasena_actual). El email no se cambia: es el usuario de login."""

    @requiere_cliente
    def get(self, request, id_usuario):
        usuario = Usuarios.objects.filter(pk=id_usuario, rol=False).first()
        if usuario is None:
            return error('Usuario no encontrado', status.HTTP_404_NOT_FOUND)
        return Response(perfil_json(usuario))

    @requiere_cliente
    def patch(self, request, id_usuario):
        usuario = Usuarios.objects.filter(pk=id_usuario, rol=False).first()
        if usuario is None:
            return error('Usuario no encontrado', status.HTTP_404_NOT_FOUND)
        datos = request.data
        cambio_contrasena = False

        if 'nombre' in datos:
            nombre = texto(datos['nombre'])
            if not nombre or len(nombre) > 100:
                return error('El nombre es requerido (hasta 100 caracteres)')
            usuario.nombre = nombre
        if 'telefono' in datos:
            telefono = texto(datos['telefono'])
            if not telefono_valido(telefono):
                return error('El teléfono no es válido')
            usuario.telefono = telefono
        if 'direccion' in datos:
            direccion = texto(datos['direccion'])
            if len(direccion) > 255:
                return error('La dirección es demasiado larga')
            usuario.direccion = direccion
        if 'contrasena_nueva' in datos:
            nueva = datos['contrasena_nueva']
            actual = datos.get('contrasena_actual')
            if not isinstance(actual, str) or not check_password(actual, usuario.contrasena):
                return error('La contraseña actual es incorrecta')
            if not isinstance(nueva, str) or len(nueva) < 6:
                return error('La contraseña debe tener al menos 6 caracteres')
            usuario.contrasena = make_password(nueva)
            cambio_contrasena = True

        usuario.save()
        respuesta = perfil_json(usuario)
        if cambio_contrasena:
            # Los tokens viejos dejan de valer: la app guarda estos para seguir logueada.
            respuesta.update(tokens(usuario))
        return Response(respuesta)


# ---------- Catálogo (público) ----------

def rubros(restaurante):
    """Categorías del restaurante como lista limpia. Hay filas cargadas con varios rubros
    separados por coma ("pizza, empanadas"): se separan, se recortan y se sacan repetidos."""
    vistos = {}
    for c in restaurante.categorias.all():
        for nombre in (c.nombre or '').split(','):
            nombre = nombre.strip()
            if nombre:
                vistos.setdefault(nombre.lower(), nombre)
    return sorted(vistos.values(), key=str.lower)


class RestaurantesView(ClienteAPIView):

    def get(self, request):
        restaurantes = Restaurantes.objects.annotate(
            precio_desde=Min('productos__precio', filter=Q(productos__disponible=True))
        ).prefetch_related(
            'categorias',
            Prefetch('horarios', queryset=HorariosRestaurante.objects.filter(activo=True)),
        ).order_by('nombre')

        # ?categoria=hamburguesa -> solo los que tienen esa categoría (parcial, sin importar mayúsculas).
        buscada = texto(request.query_params.get('categoria'))
        if buscada:
            restaurantes = restaurantes.filter(
                pk__in=Categoria.objects.filter(nombre__icontains=buscada).values('id_restaurante')
            )

        ahora = ahora_ar()
        data = [
            {
                'id_restaurante': r.id_restaurante,
                'nombre': r.nombre,
                'direccion': r.direccion,
                'categoria': r.categoria,  # texto viejo: queda mientras la app lo siga usando
                'categorias': rubros(r),
                'abierto': esta_abierto(r, ahora),
                'horario_hoy': horario_hoy(r, ahora),
                'costo_envio': numero(r.costo_envio),
                'pedido_minimo': numero(r.pedido_minimo),
                # Precio más barato del menú (para el "Desde $X"); null si no tiene productos disponibles.
                'precio_desde': float(r.precio_desde) if r.precio_desde is not None else None,
            }
            for r in restaurantes
        ]
        return Response(data)


class MenuView(ClienteAPIView):

    def get(self, request, id_restaurante):
        if id_restaurante > MAX_ID:
            return error('Restaurante no encontrado', status.HTTP_404_NOT_FOUND)
        productos = Productos.objects.filter(
            id_restaurante=id_restaurante,
            disponible=True,
        ).order_by('nombre')

        data = [
            {
                'id_producto': p.id_producto,
                'nombre': p.nombre,
                'descripcion': p.descripcion,
                'precio': float(p.precio) if p.precio is not None else 0.0,
                'imagen_url': imagen_absoluta(request, p.imagen_url),
                'stock': p.stock,  # null = el restaurante no controla stock de este producto
            }
            for p in productos
        ]
        return Response(data)


# ---------- Pedidos del cliente ----------

class SinStock(Exception):
    def __init__(self, producto):
        self.producto = producto


def repartidor_publico(repartidor):
    """Lo que el cliente ve de quien le lleva el pedido. Nombre y teléfono salen de la cuenta (los edita en su perfil)."""
    if repartidor is None:
        return None
    u = repartidor.id_usuario
    return {
        'nombre': u.nombre if u else repartidor.nombre,
        'telefono': u.telefono if u else repartidor.telefono,
        'vehiculo': repartidor.vehiculo,
    }


def pedidos_json(pedidos):
    """Arma la respuesta de una lista de pedidos con sus items y su pago (3 consultas en total)."""
    pedidos = list(pedidos)
    if not pedidos:
        return []
    ids = [p.id_pedido for p in pedidos]

    items = {}
    detalles = (
        DetallePedido.objects
        .filter(id_pedido__in=ids)
        .select_related('id_producto')
        .order_by('id_detalle')
    )
    for d in detalles:
        items.setdefault(d.id_pedido_id, []).append({
            'nombre': d.id_producto.nombre if d.id_producto else 'Producto eliminado',
            'cantidad': int(d.cantidad),
            'subtotal': float(d.subtotal) if d.subtotal is not None else 0.0,
        })

    pagos = {}
    for pago in Pagos.objects.filter(id_pedido__in=ids).order_by('id_pago'):
        pagos.setdefault(pago.id_pedido_id, pago)  # si hay más de uno, el primero

    data = []
    for p in pedidos:
        pago = pagos.get(p.id_pedido)
        lineas = items.get(p.id_pedido, [])
        total = float(p.total) if p.total is not None else 0.0
        retiro = p.tipo_entrega == 'retiro'
        envio = 0.0 if retiro else max(0.0, total - sum(i['subtotal'] for i in lineas))
        data.append({
            'id_pedido': p.id_pedido,
            'fecha': p.fecha,
            'estado': p.estado or 'pendiente',
            'total': total,
            'tipo_entrega': p.tipo_entrega or 'domicilio',
            'costo_envio': envio,
            'direccion_entrega': p.direccion_entega,  # (sic) la columna se llama así
            'restaurante': p.id_restaurante.nombre if p.id_restaurante else None,
            'metodo_pago': METODOS_PAGO.get(pago.metodo_pago, pago.metodo_pago) if pago else None,
            'pagado': bool(pago and pago.estado_pago),
            'repartidor': repartidor_publico(p.id_repartidor),
            'items': lineas,
        })
    return data


def pedidos_de(**filtro):
    return (
        Pedidos.objects
        .filter(**filtro)
        .select_related('id_restaurante', 'id_repartidor__id_usuario')
        .order_by('-fecha', '-id_pedido')
    )


class MisPedidosView(ClienteAPIView):

    @requiere_cliente
    def get(self, request, id_usuario):
        return Response(pedidos_json(pedidos_de(id_usuario=id_usuario)))

    @requiere_cliente
    def post(self, request, id_usuario):
        id_restaurante = entero(request.data.get('id_restaurante'))
        restaurante = (
            Restaurantes.objects.filter(pk=id_restaurante).first()
            if id_restaurante is not None else None
        )
        if restaurante is None:
            return error('Restaurante no encontrado', status.HTTP_404_NOT_FOUND)
        if not esta_abierto(restaurante):
            return error('El restaurante está cerrado')

        tipo_entrega = request.data.get('tipo_entrega')
        if tipo_entrega is None:
            tipo_entrega = 'domicilio'
        if tipo_entrega not in TIPOS_ENTREGA:
            return error('Tipo de entrega inválido')
        retiro = tipo_entrega == 'retiro'

        direccion = texto(request.data.get('direccion_entrega'))
        if not retiro and not direccion:
            return error('La dirección de entrega es requerida')
        if len(direccion) > 255:
            return error('La dirección es demasiado larga')

        metodo_pago = request.data.get('metodo_pago')
        if not isinstance(metodo_pago, str) or metodo_pago not in METODOS_PAGO:
            return error('Elegí un método de pago')

        items = request.data.get('items')
        if not isinstance(items, list) or not items:
            return error('El pedido está vacío')
        if len(items) > MAX_LINEAS:
            return error(f'El pedido puede tener hasta {MAX_LINEAS} productos')

        # Cada item tiene que ser un producto disponible de este restaurante, con cantidad >= 1.
        pedidas = []
        for item in items:
            if not isinstance(item, dict):
                return error('Hay productos que ya no están disponibles')
            id_producto = entero(item.get('id_producto'))
            cantidad = entero(item.get('cantidad'))
            if id_producto is None or cantidad is None or not 1 <= cantidad <= MAX_CANTIDAD:
                return error('Hay productos que ya no están disponibles')
            pedidas.append((id_producto, cantidad))

        productos = Productos.objects.in_bulk([id_producto for id_producto, _ in pedidas])
        lineas = []
        for id_producto, cantidad in pedidas:
            p = productos.get(id_producto)
            if p is None or p.id_restaurante_id != restaurante.id_restaurante or p.disponible is not True:
                return error('Hay productos que ya no están disponibles')
            # Los precios salen de la base, nunca del request.
            lineas.append((p, cantidad, float(p.precio or 0) * cantidad))
        subtotal_productos = sum(subtotal for _, _, subtotal in lineas)
        minimo = float(restaurante.pedido_minimo or 0)
        if subtotal_productos < minimo:
            monto = f'{minimo:.0f}' if float(minimo).is_integer() else f'{minimo:g}'
            return error(f'El pedido mínimo es ${monto}')
        envio = 0.0 if retiro else float(restaurante.costo_envio or 0)
        total = subtotal_productos + envio

        try:
            with transaction.atomic():
                for producto, cantidad, _ in lineas:
                    if producto.stock is None:
                        continue  # sin control de stock
                    # Descuento condicional en un solo UPDATE: si dos clientes piden
                    # la última unidad a la vez, solo uno se la lleva.
                    descontados = Productos.objects.filter(
                        pk=producto.id_producto, stock__gte=cantidad
                    ).update(stock=F('stock') - cantidad)
                    if descontados == 0:
                        raise SinStock(producto)  # rollback de todo lo anterior

                pedido = Pedidos.objects.create(
                    fecha=timezone.now(),
                    estado='pendiente',
                    total=total,
                    direccion_entega=None if retiro else direccion,
                    tipo_entrega=tipo_entrega,
                    id_usuario_id=id_usuario,
                    id_restaurante=restaurante,
                )
                for producto, cantidad, subtotal in lineas:
                    DetallePedido.objects.create(
                        id_pedido=pedido,
                        id_producto=producto,
                        cantidad=cantidad,
                        subtotal=subtotal,
                    )
                # Se paga al recibir: queda registrado cómo, sin cobrar.
                Pagos.objects.create(
                    id_pedido=pedido,
                    metodo_pago=metodo_pago,
                    monto=total,
                    estado_pago=False,
                )
        except SinStock as e:
            quedan = (
                Productos.objects.filter(pk=e.producto.id_producto)
                .values_list('stock', flat=True).first()
            )
            return error(f'No hay stock suficiente de "{e.producto.nombre}" (quedan {quedan or 0})')

        creado = pedidos_json(pedidos_de(pk=pedido.id_pedido))[0]
        return Response(creado, status=status.HTTP_201_CREATED)
