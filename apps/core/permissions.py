from rest_framework.permissions import BasePermission, SAFE_METHODS

class IsAdminUserOnly(BasePermission):
    """Acceso restringido únicamente a administradores."""
    
    def has_permission(self, request, view):
        user = request.user
        return bool(user and user.is_authenticated and (getattr(user, 'is_superuser', False) or getattr(user, 'rol', False)))


class IsRestaurantStaffOrReadOnly(BasePermission):
    """
    Lectura libre para clientes (GET, HEAD, OPTIONS).
    Escritura (POST, PUT, DELETE) solo para el dueño del restaurante asignado.
    """
    def has_permission(self, request, view):
        if request.method in SAFE_METHODS:
            return True
        return bool(request.user and request.user.is_authenticated and getattr(request.user, 'rol', False))

    def has_object_permission(self, request, view, obj):
        if request.method in SAFE_METHODS or getattr(request.user, 'is_superuser', False):
            return True

        user_rest = getattr(request.user, 'id_restaurante', None)
        obj_rest = getattr(obj, 'id_restaurante', None)

        if not user_rest or not obj_rest:
            return False

        return user_rest.id_restaurante == obj_rest.id_restaurante


class IsOrderParticipantOnly(BasePermission):
    """
    Protección contra IDOR/BOLA en pedidos:
    - Cliente: solo ve/edita su propio pedido.
    - Restaurante: solo gestiona los pedidos asignados a su local.
    """
    def has_permission(self, request, view):
        return bool(request.user and request.user.is_authenticated)

    def has_object_permission(self, request, view, obj):
        user = request.user
        if getattr(user, 'is_superuser', False):
            return True

        # Valida si es el cliente dueño del pedido
        if obj.id_usuario and obj.id_usuario.id_usuario == getattr(user, 'id_usuario', None):
            return True

        # Valida si pertenece al restaurante que despacha
        user_rest = getattr(user, 'id_restaurante', None)
        if user_rest and obj.id_restaurante and user_rest.id_restaurante == obj.id_restaurante.id_restaurante:
            return True

        return False