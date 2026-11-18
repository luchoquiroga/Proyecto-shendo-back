import hashlib
from datetime import timedelta
from functools import wraps

from rest_framework import status
from rest_framework.response import Response
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.tokens import AccessToken, RefreshToken


# Tokens de la app de clientes. Llevan tipo=cliente y no llevan id_restaurante,
# asi que no sirven en el panel del restaurante (ni los del panel sirven aca).
class ClienteAccessToken(AccessToken):
    # Corto: la app lo renueva sola con el refresh (30 días) cuando recibe 401.
    lifetime = timedelta(hours=1)


class ClienteRefreshToken(RefreshToken):
    lifetime = timedelta(days=30)
    access_token_class = ClienteAccessToken


DESCONECTADO = 'desconectado'


def repartidor_json(repartidor):
    return {
        'id_repartidor': repartidor.id_repartidor,
        'vehiculo': repartidor.vehiculo,
        'patente': repartidor.patente,
        'estado': repartidor.estado,
        # La app solo prende/apaga: "ocupado" (con pedidos en curso) sigue pudiendo tomar más.
        'disponible': repartidor.estado != DESCONECTADO,
    }


def huella(usuario):
    """Claim corto derivado de la contraseña actual: al cambiarla, los tokens viejos dejan de servir."""
    return hashlib.sha256((usuario.contrasena or '').encode()).hexdigest()[:16]


def tokens(usuario):
    refresh = ClienteRefreshToken()
    refresh['tipo'] = 'cliente'  # mismo token para las dos caras; el rol se mira en la base en cada pedido
    refresh['id_usuario'] = usuario.id_usuario
    refresh['nombre'] = usuario.nombre
    refresh['pw'] = huella(usuario)
    return {'access': str(refresh.access_token), 'refresh': str(refresh)}


def usuario_vigente(token):
    """id_usuario del token si la cuenta sigue existiendo, no es admin y no cambió la contraseña; si no, None."""
    from apps.core.models import Usuarios
    usuario = Usuarios.objects.filter(pk=token.get('id_usuario'), rol=False).first()
    if usuario is None or token.get('pw') != huella(usuario):
        return None
    return usuario.id_usuario


def sesion(usuario):
    """Respuesta de registro y login: tokens + datos de la cuenta.
    perfil = 'repartidor' si la cuenta tiene fila en repartidor (la app muestra esa cara); si no, 'cliente'."""
    from apps.core.models import Repartidor
    repartidor = Repartidor.objects.filter(id_usuario=usuario.id_usuario).first()
    return {
        **tokens(usuario),
        'id_usuario': usuario.id_usuario,
        'nombre': usuario.nombre,
        'email': usuario.email,
        'telefono': usuario.telefono,
        'direccion': usuario.direccion,
        'perfil': 'repartidor' if repartidor else 'cliente',
        'repartidor': repartidor_json(repartidor) if repartidor else None,
    }


def renovar(refresh_str):
    """Access nuevo a partir de un refresh de cliente, o None si no es válido."""
    if not isinstance(refresh_str, str):
        return None
    try:
        refresh = ClienteRefreshToken(refresh_str)
    except TokenError:
        return None
    if refresh.get('tipo') != 'cliente' or usuario_vigente(refresh) is None:
        return None
    return str(refresh.access_token)


def get_cliente_from_token(request):
    """Devuelve el id_usuario del cliente del token, o None si el token no es valido."""
    auth_header = request.headers.get('Authorization', '')
    if not auth_header.startswith('Bearer '):
        return None
    try:
        token = ClienteAccessToken(auth_header.split(' ', 1)[1])
    except TokenError:
        return None
    if token.get('tipo') != 'cliente':
        return None
    return usuario_vigente(token)


def requiere_cliente(metodo):
    """Para metodos de APIView: responde 401 sin token de cliente y si no pasa id_usuario."""
    @wraps(metodo)
    def wrapper(self, request, *args, **kwargs):
        id_usuario = get_cliente_from_token(request)
        if not id_usuario:
            return Response(
                {'error': 'Token inválido o no enviado'},
                status=status.HTTP_401_UNAUTHORIZED
            )
        return metodo(self, request, id_usuario, *args, **kwargs)
    return wrapper
