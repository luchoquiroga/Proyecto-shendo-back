from django.test import SimpleTestCase
from rest_framework.test import APIRequestFactory
from rest_framework_simplejwt.tokens import RefreshToken

from apps.core.authentication import CustomJWTAuthentication, TokenUser
from apps.core.permissions import (
    IsAuthenticatedUser,
    IsRestaurantStaffOrAdmin,
    IsRestaurantStaffOrReadOnly,
    IsOrderParticipantOnly,
)


class DummyUser:
    def __init__(self, is_authenticated=True, rol=False, id_restaurante=None, id_usuario=1, is_superuser=False):
        self.is_authenticated = is_authenticated
        self.rol = rol
        self.id_restaurante = id_restaurante
        self.id_usuario = id_usuario
        self.is_superuser = is_superuser


class SecurityRBACTests(SimpleTestCase):
    def setUp(self):
        self.factory = APIRequestFactory()

    def test_token_user_creation(self):
        payload = {
            'id_usuario': 10,
            'nombre': 'admin_local',
            'rol': True,
            'id_restaurante': 5,
        }
        user = TokenUser(payload)
        self.assertTrue(user.is_authenticated)
        self.assertEqual(user.id_restaurante, 5)
        self.assertTrue(user.rol)

    def test_permission_unauthenticated_blocked(self):
        perm = IsAuthenticatedUser()
        request = self.factory.get('/api/products/')
        request.user = None
        self.assertFalse(perm.has_permission(request, None))

    def test_restaurant_staff_permission_allows_user_with_restaurant(self):
        perm = IsRestaurantStaffOrAdmin()
        request = self.factory.get('/api/products/')
        request.user = DummyUser(is_authenticated=True, id_restaurante=1)
        self.assertTrue(perm.has_permission(request, None))

    def test_restaurant_staff_permission_denies_user_without_restaurant(self):
        perm = IsRestaurantStaffOrAdmin()
        request = self.factory.get('/api/products/')
        request.user = DummyUser(is_authenticated=True, id_restaurante=None)
        self.assertFalse(perm.has_permission(request, None))

    def test_idor_protection_restaurant_isolation(self):
        perm = IsRestaurantStaffOrReadOnly()
        request = self.factory.put('/api/products/1/')
        request.user = DummyUser(is_authenticated=True, rol=True, id_restaurante=1)

        class MockProduct:
            def __init__(self, id_restaurante):
                self.id_restaurante = id_restaurante

        # Mismo restaurante: permitido
        self.assertTrue(perm.has_object_permission(request, None, MockProduct(id_restaurante=1)))
        # Diferente restaurante: denegado
        self.assertFalse(perm.has_object_permission(request, None, MockProduct(id_restaurante=2)))

    def test_order_participant_isolation(self):
        perm = IsOrderParticipantOnly()
        request = self.factory.put('/api/orders/1/')
        request.user = DummyUser(is_authenticated=True, id_usuario=42, id_restaurante=1)

        class MockOrder:
            def __init__(self, id_usuario, id_restaurante):
                self.id_usuario = id_usuario
                self.id_restaurante = id_restaurante

        # Pedido del mismo restaurante
        self.assertTrue(perm.has_object_permission(request, None, MockOrder(id_usuario=99, id_restaurante=1)))
        # Pedido de otro restaurante y otro usuario
        self.assertFalse(perm.has_object_permission(request, None, MockOrder(id_usuario=99, id_restaurante=2)))
