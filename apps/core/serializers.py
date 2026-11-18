from rest_framework_simplejwt.serializers import TokenObtainPairSerializer
from rest_framework_simplejwt.views import TokenObtainPairView

class CustomTokenObtainPairSerializer(TokenObtainPairSerializer):
    @classmethod
    def get_token(cls, user):
        token = super().get_token(user)

        # Inyecta claims en el payload del JWT
        token['nombre'] = getattr(user, 'nombre', getattr(user, 'username', ''))
        token['email'] = getattr(user, 'email', '')
        token['rol'] = bool(getattr(user, 'rol', False))
        
        restaurante = getattr(user, 'id_restaurante', None)
        token['id_restaurante'] = restaurante.id_restaurante if restaurante else None

        return token

class CustomTokenObtainPairView(TokenObtainPairView):
    serializer_class = CustomTokenObtainPairSerializer