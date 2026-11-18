from rest_framework.authentication import BaseAuthentication
from rest_framework.exceptions import AuthenticationFailed
from rest_framework_simplejwt.tokens import AccessToken
from rest_framework_simplejwt.exceptions import InvalidToken, TokenError


class TokenUser:
    """
    Representación liviana del usuario autenticado extraído directamente del JWT,
    sin requerir consultas adicionales ni modificaciones al esquema de la base de datos.
    """
    def __init__(self, payload):
        self.payload = payload
        self.id_usuario = payload.get('id_usuario')
        self.nombre = payload.get('nombre', '')
        self.email = payload.get('email', '')
        self.id_restaurante = payload.get('id_restaurante')
        self.rol = payload.get('rol', False)
        self.is_authenticated = True
        self.is_anonymous = False
        self.is_superuser = bool(self.rol is True and self.id_restaurante is None)

    def __str__(self):
        return f"User({self.nombre}, id={self.id_usuario}, rol={self.rol})"


class CustomJWTAuthentication(BaseAuthentication):
    """
    Autenticador JWT compatible con el modelo y tokens existentes.
    Puebla request.user de manera estándar para que funcionen las permission_classes de DRF.
    """
    def authenticate(self, request):
        auth_header = request.headers.get('Authorization', '')
        if not auth_header or not auth_header.startswith('Bearer '):
            return None

        parts = auth_header.split(' ')
        if len(parts) != 2:
            return None

        token_str = parts[1]
        try:
            token = AccessToken(token_str)
            user = TokenUser(token)
            return (user, token)
        except (InvalidToken, TokenError):
            raise AuthenticationFailed('Token inválido o expirado.')
